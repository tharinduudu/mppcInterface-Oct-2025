#!/usr/bin/env python3
# biasAdj.py - Temperature-compensated DAC control for Hamamatsu S13360-2050VE
#
# What it does (summary):
#   - Reads BME280 @ 0x77 (CircuitPython basic) in high-accuracy mode
#   - Samples every 10 s; aggregates 5-minute block averages
#   - First full block locks T_ref; keeps SiPM over-voltage constant
#   - Per-channel DAC updates: <= 1 per block with anti-dither thresholds
#   - DRY_RUN switch (top or env DRY_RUN=1)
#   - Exactly TWO CSVs per run:
#       1) /home/cosmic/logs/bmelogs/bme_*.csv  -> timestamp, temp_C, pressure_hPa, humidity_%
#       2) /home/cosmic/logs/tempcomp/dac_adj_*.csv -> one line per adjustment incl. deltaT
#
# Deps:
#   sudo pip3 install --upgrade adafruit-blinka adafruit-circuitpython-bme280

import os
import sys
import time
import csv
import math
import shutil
import subprocess
from datetime import datetime
from statistics import mean, pstdev

# Optional: make stdout/stderr UTF-8 if supported. Safe no-op on older Pythons.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# --------------------------- EASY SWITCHES ---------------------------
# Changed to "False" on October 23, 2025 at 11:13am by hexc
DRY_RUN = False  # set True to simulate (no hardware writes). Env can override.
if os.getenv("DRY_RUN") in ("1", "true", "True", "YES", "yes"):
    DRY_RUN = True

# Where to save CSV logs (directories are created if missing). Env can override.
BME_LOG_DIR = os.getenv("BME_LOG_DIR", "/home/cosmic/logs/bmelogs")
ADJ_LOG_DIR = os.getenv("TEMPCOMP_LOG_DIR", "/home/cosmic/logs/tempcomp")

# ----------------------------- CONFIG -------------------------------
# DAC channels to actively compensate:
CHANNELS = [0, 1, 2, 3]

# Path to your working dac.py (relative or absolute)
DAC_SCRIPT = "./dac.py"

# Initial 10-bit DAC codes at the reference temperature (defines Vlow_ref)
START_CODES = {
    0: 0x2F1,
    1: 0x2F1,
    2: 0x2F1,
    3: 0x2F1,
}

# SiPM temperature coefficient (V/degC). Hamamatsu S13360 typical approx +0.054 V/degC
TEMP_COEFF_V_PER_C = 0.054

# === DAC calibration from your 20-point table ===
# Vlow approx VOFF + SPAN * (code/1023)
VOFF = -0.0226786515445685   # volts (intercept)
SPAN = 2.3527073030891374    # volts (full-scale span)

# Derived helpers (do not edit)
V_PER_CODE_V = SPAN / 1023.0                     # approx 0.002300 V/code
CODES_PER_DEG = TEMP_COEFF_V_PER_C / V_PER_CODE_V  # approx 23.48 codes/degC

# BME280 sensor config (fixed at 0x77)
BME_I2C_ADDR = 0x77
SAMPLE_EVERY_SEC = 10               # sensor sample period
LOG_BLOCK_SEC = 5 * 60              # averaging window (5 minutes)
MIN_STEP_VOLTS = 0.004              # >=4 mV required to apply an update
MIN_STEP_CODES = 1                  # >=1 code required to apply an update

# --------------------------- Helper funcs ---------------------------

def code_to_vlow(code: int) -> float:
    """Predict Vlow using VOFF/SPAN model, clamped at >= 0 V."""
    frac = max(0.0, min(1.0, (int(code) & 0x3FF) / 1023.0))
    v = VOFF + SPAN * frac
    return max(0.0, v)

def vlow_to_code(v: float) -> int:
    """Invert VOFF/SPAN model to 10-bit code, clamped to 0..1023."""
    v = max(0.0, float(v))
    code = int(round(((v - VOFF) / SPAN) * 1023.0))
    return max(0, min(1023, code))

def parse_dac_stdout(text: str):
    """Return (HV, Vlow, Vbias); NaN if not found. Case-insensitive."""
    hv = vlow = vbias = math.nan
    for line in text.splitlines():
        s = line.strip().lower()
        try:
            if s.startswith("high voltage:"):
                hv = float(line.split(":")[1].split()[0])
            elif s.startswith("dac output:"):
                vlow = float(line.split(":")[1].split()[0])
            elif s.startswith("effective bias:"):
                vbias = float(line.split(":")[1].split()[0])
        except Exception:
            pass
    return hv, vlow, vbias

def set_dac_and_read(ch: int, code: int):
    """Run dac.py <ch> 0x<code> and parse printed voltages. Honors DRY_RUN."""
    if DRY_RUN:
        v = code_to_vlow(code)
        return (math.nan, v, math.nan, f"[dry-run] CH{ch} -> code 0x{code:03X}, Vlow~{v:.3f} V")
    try:
        proc = subprocess.run(
            [sys.executable, DAC_SCRIPT, str(ch), f"0x{code:03X}"],
            capture_output=True, text=True, check=True, timeout=10
        )
        hv, vlow, vbias = parse_dac_stdout(proc.stdout)
        return hv, vlow, vbias, proc.stdout.strip()
    except subprocess.CalledProcessError as e:
        return math.nan, math.nan, math.nan, f"[dac.py error] CH{ch} code=0x{code:03X}: {e.stderr}"
    except Exception as e:
        return math.nan, math.nan, math.nan, f"[dac.py error] CH{ch} code=0x{code:03X}: {e}"

def align_to_next_boundary(period=LOG_BLOCK_SEC):
    """Sleep to the next absolute period boundary; return that boundary (epoch seconds)."""
    now = time.time()
    boundary = (math.floor(now / period) + 1) * period
    time.sleep(max(0.0, boundary - now))
    return boundary

# ----------------------------- BME280 -------------------------------
def init_bme():
    """Initialize BME280 using the CircuitPython 'basic' module, with a safe fallback."""
    try:
        import board
        import busio
        i2c = busio.I2C(board.SCL, board.SDA)
        try:
            from adafruit_bme280 import basic as bme_mod
            sensor = bme_mod.Adafruit_BME280_I2C(i2c, address=BME_I2C_ADDR)
        except Exception as e_basic:
            try:
                import adafruit_bme280 as bme_mod
                sensor = bme_mod.Adafruit_BME280_I2C(i2c, address=BME_I2C_ADDR)
            except Exception as e_top:
                raise RuntimeError(f"{e_basic} ; fallback failed: {e_top}")

        # High-accuracy config (best effort; tolerate older libs)
        try:
            sensor.mode = getattr(bme_mod, "MODE_NORMAL", getattr(sensor, "mode", None))
        except Exception:
            pass
        for attr, val in (
            ("overscan_temperature", getattr(bme_mod, "OVERSCAN_X16", None)),
            ("overscan_pressure", getattr(bme_mod, "OVERSCAN_X16", None)),
            ("overscan_humidity", getattr(bme_mod, "OVERSCAN_X16", None)),
            ("iir_filter", getattr(bme_mod, "IIR_FILTER_X16", None)),
        ):
            try:
                if val is not None:
                    setattr(sensor, attr, val)
            except Exception:
                pass

        print(f"[INFO] BME280 ready at 0x{BME_I2C_ADDR:02X}")
        return sensor
    except Exception as e:
        print(f"[fatal] BME280 init failed at 0x{BME_I2C_ADDR:02X}: {e}")
        sys.exit(1)

def read_bme(sensor):
    """Return tuple (temp_C, pressure_hPa, humidity_%) or (None, None, None) on failure."""
    try:
        t = float(sensor.temperature)    # degC
        p = float(sensor.pressure)       # hPa
        h = float(sensor.humidity)       # %RH
        if any([(v != v) for v in (t, p, h)]):  # NaN guard
            raise ValueError("NaN")
        return t, p, h
    except Exception as e:
        print(f"[warn] BME280 read failed: {e}")
        return None, None, None

# ------------------------------- Main -------------------------------
def main():
    # preflight
    if not shutil.which(DAC_SCRIPT) and not os.path.exists(DAC_SCRIPT):
        if DRY_RUN:
            print(f"[warn] dac.py not found at '{DAC_SCRIPT}', continuing (DRY_RUN)")
        else:
            print(f"[fatal] dac.py not found at '{DAC_SCRIPT}'")
            sys.exit(1)
    for ch in CHANNELS:
        if ch not in START_CODES:
            print(f"[fatal] Missing START_CODES entry for channel {ch}")
            sys.exit(1)

    # Prepare log directories and filenames
    start_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(BME_LOG_DIR, exist_ok=True)
    os.makedirs(ADJ_LOG_DIR, exist_ok=True)
    bme_log_path = os.path.join(BME_LOG_DIR, f"bme_log_{start_tag}.csv")
    adj_log_path = os.path.join(ADJ_LOG_DIR, f"dac_adj_{start_tag}.csv")

    # Init BME280
    sensor = init_bme()

    # Headers
    with open(bme_log_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_started_at", datetime.now().isoformat(timespec="seconds")])
        w.writerow(["timestamp", "temp_C_avg", "temp_C_std", "pressure_hPa_avg", "humidity_%_avg", "n_samples"])

    with open(adj_log_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_started_at", datetime.now().isoformat(timespec="seconds")])
        w.writerow([
            "timestamp", "channel",
            "old_code_dec", "old_code_hex",
            "new_code_dec", "new_code_hex",
            "vlow_before_V", "vlow_after_V",
            "deltaT_C", "high_voltage_V", "effective_bias_V"
        ])

    print(f"[INFO] BME log      -> {bme_log_path}")
    print(f"[INFO] DAC adj log  -> {adj_log_path}")
    print(f"[INFO] DAC calib: VOFF={VOFF:.6f} V, SPAN={SPAN:.6f} V, "
          f"{V_PER_CODE_V*1e3:.3f} mV/code, {CODES_PER_DEG:.2f} codes/degC")
    print("[INFO] Aligning to the next 5-minute boundary...")
    current_boundary = align_to_next_boundary()

    # Reference state
    ref_temp = None
    vlow_ref_by_ch = {ch: code_to_vlow(START_CODES[ch]) for ch in CHANNELS}
    last_code_by_ch = START_CODES.copy()

    # Pre-apply starting codes (or simulate) and print readings
    print("[INFO] Applying START_CODES...")
    for ch in CHANNELS:
        hv, vlow, vbias, _ = set_dac_and_read(ch, last_code_by_ch[ch])
        vlow_disp = f"{vlow:.3f} V" if not math.isnan(vlow) else f"~{code_to_vlow(last_code_by_ch[ch]):.3f} V"
        hv_disp = f"{hv:.2f} V" if not math.isnan(hv) else "N/A"
        vb_disp = f"{vbias:.2f} V" if not math.isnan(vbias) else "N/A"
        print(f"  CH{ch} -> code 0x{last_code_by_ch[ch]:03X} | HV={hv_disp}  Vlow={vlow_disp}  Vbias={vb_disp}")

    # Continuous 5-minute blocks
    while True:
        block_end = current_boundary + LOG_BLOCK_SEC
        t_samples, p_samples, h_samples = [], [], []

        # Sample loop
        while True:
            t, p, h = read_bme(sensor)
            if t is not None:
                t_samples.append(t)
                p_samples.append(p)
                h_samples.append(h)
            now = time.time()
            if now >= block_end:
                break
            time.sleep(min(SAMPLE_EVERY_SEC, max(0.0, block_end - now)))

        if not t_samples:
            print("[warn] No BME samples in block; skipping.")
            current_boundary = block_end
            continue

        # Block statistics
        T_avg = mean(t_samples)
        T_std = pstdev(t_samples) if len(t_samples) > 1 else 0.0
        P_avg = mean(p_samples)
        H_avg = mean(h_samples)
        ts = datetime.fromtimestamp(block_end).strftime("%Y-%m-%d %H:%M:00")

        # First block defines the reference temperature
        if ref_temp is None:
            ref_temp = T_avg
            print(f"[INFO] Reference temperature locked: T_ref = {ref_temp:.2f} degC")

        # Write BME block record
        with open(bme_log_path, "a", newline="") as f:
            csv.writer(f).writerow([ts, f"{T_avg:.2f}", f"{T_std:.2f}", f"{P_avg:.2f}", f"{H_avg:.2f}", len(t_samples)])

        # Compute common shift for this block
        dT = T_avg - ref_temp                      # degC
        # Vlow change needed to keep over-voltage constant
        dV = -TEMP_COEFF_V_PER_C * dT              # V (negative if T rises)

        # Per-channel adjustments (<= 1 per block)
        for ch in CHANNELS:
            vlow_ref = vlow_ref_by_ch[ch]
            vlow_tgt = vlow_ref + dV
            code_tgt = vlow_to_code(vlow_tgt)
            code_prev = last_code_by_ch[ch]

            # Skip tiny adjustments to avoid dithering
            if (abs(code_tgt - code_prev) < MIN_STEP_CODES
                and abs(vlow_tgt - code_to_vlow(code_prev)) < MIN_STEP_VOLTS):
                continue

            # Read current, then set new
            hv, vlow_before, vbias_before, _ = set_dac_and_read(ch, code_prev)  # read
            hv, vlow_after, vbias_after, _ = set_dac_and_read(ch, code_tgt)     # write+read
            last_code_by_ch[ch] = code_tgt

            vlow_before_fallback = code_to_vlow(code_prev)
            vlow_after_fallback = code_to_vlow(code_tgt)

            # DAC-only log: one line per adjustment including deltaT
            with open(adj_log_path, "a", newline="") as f:
                csv.writer(f).writerow([
                    ts, ch,
                    code_prev, f"0x{code_prev:03X}",
                    code_tgt, f"0x{code_tgt:03X}",
                    f"{(vlow_before if not math.isnan(vlow_before) else vlow_before_fallback):.3f}",
                    f"{(vlow_after if not math.isnan(vlow_after) else vlow_after_fallback):.3f}",
                    f"{dT:.2f}",
                    f"{hv:.2f}" if not math.isnan(hv) else "",
                    f"{vbias_after:.2f}" if not math.isnan(vbias_after) else ""
                ])

            print(
                f"[ADJ] {ts}  CH{ch}: Tavg={T_avg:.2f} degC  dT={dT:.2f} degC  "
                f"Vlow -> {(vlow_after if not math.isnan(vlow_after) else vlow_after_fallback):.3f} V  "
                f"code 0x{code_tgt:03X}"
            )

        current_boundary = block_end  # advance window

if __name__ == "__main__":
    try:
        print("[INFO] Starting bias adjustment daemon...")
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
