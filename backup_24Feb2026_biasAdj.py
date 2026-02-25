#!/usr/bin/env python3
# biasAdj.py - Temperature compensated DAC control for Hamamatsu S13360-2050VE
#
# What it does (summary):
# - Reads BME280 at 0x77 in high accuracy mode (best effort)
# - Samples every 10 s, aggregates 5 minute block averages
# - Uses a fixed reference temperature (default 20.0 C)
# - Keeps SiPM over voltage constant by adjusting the low side DAC (Vlow)
# - Per channel DAC updates: <= 1 per block with anti dither thresholds
# - Adds stability gates:
#   - Minimum sample count per block
#   - Skip updates if temperature std is too high in that block
#   - Clamp deltaT to avoid sensor spikes
#   - Limit max DAC code movement per block
# - DRY_RUN switch (top or env DRY_RUN=1)
# - Exactly TWO CSVs per run:
#   1) /home/cosmic/logs/bmelogs/bme_log_*.csv
#   2) /home/cosmic/logs/tempcomp/dac_adj_*.csv
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

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# --------------------------- EASY SWITCHES ---------------------------

DRY_RUN = False
if os.getenv("DRY_RUN") in ("1", "true", "True", "YES", "yes"):
    DRY_RUN = True

BME_LOG_DIR = os.getenv("BME_LOG_DIR", "/home/cosmic/logs/bmelogs")
ADJ_LOG_DIR = os.getenv("TEMPCOMP_LOG_DIR", "/home/cosmic/logs/tempcomp")

# ----------------------------- CONFIG -------------------------------

CHANNELS = [0, 1, 2, 3]
DAC_SCRIPT = "./dac.py"

# START_CODES should represent your desired baseline at REF_TEMP_C
START_CODES = {
    0: 0x2F1,
    1: 0x2F1,
    2: 0x2F1,
    3: 0x2F1,
}

# Fixed reference temperature (C)
REF_TEMP_C = float(os.getenv("REF_TEMP_C", "20.0"))

# SiPM temperature coefficient (V per C). Typical approx +0.054 V per C
TEMP_COEFF_V_PER_C = float(os.getenv("TEMP_COEFF_V_PER_C", "0.054"))

# DAC calibration (10 bit model)
# Vlow approx VOFF + SPAN * (code/1023)
VOFF = float(os.getenv("VOFF", "-0.0226786515445685"))
SPAN = float(os.getenv("SPAN", "2.3527073030891374"))

V_PER_CODE_V = SPAN / 1023.0

# BME280 config
BME_I2C_ADDR = 0x77
SAMPLE_EVERY_SEC = int(os.getenv("SAMPLE_EVERY_SEC", "10"))
LOG_BLOCK_SEC = int(os.getenv("LOG_BLOCK_SEC", str(5 * 60)))

# Anti dither thresholds (5 code minimum)
MIN_STEP_CODES = int(os.getenv("MIN_STEP_CODES", "5"))
# Match 5 codes (~11.5 mV) with ~12 mV voltage gate
MIN_STEP_VOLTS = float(os.getenv("MIN_STEP_VOLTS", "0.012"))

# Block quality and safety clamps
EXPECTED_SAMPLES_PER_BLOCK = int(round(LOG_BLOCK_SEC / max(1, SAMPLE_EVERY_SEC)))
MIN_SAMPLES_PER_BLOCK = int(os.getenv("MIN_SAMPLES_PER_BLOCK", str(max(1, int(0.80 * EXPECTED_SAMPLES_PER_BLOCK)))))

# Skip updates if temperature std in the 5 minute block is too high
T_STD_MAX_C = float(os.getenv("T_STD_MAX_C", "0.20"))

# Clamp deltaT used for compensation to avoid spikes (C)
MAX_DT_ABS_C = float(os.getenv("MAX_DT_ABS_C", "10.0"))

# Limit how much the DAC can move per block (codes)
MAX_CODES_STEP_PER_BLOCK = int(os.getenv("MAX_CODES_STEP_PER_BLOCK", "120"))

# --------------------------- Helper funcs ---------------------------

def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x

def code_to_vlow(code: int) -> float:
    frac = max(0.0, min(1.0, (int(code) & 0x3FF) / 1023.0))
    v = VOFF + SPAN * frac
    return max(0.0, v)

def vlow_to_code(v: float) -> int:
    v = max(0.0, float(v))
    code = int(round(((v - VOFF) / SPAN) * 1023.0))
    return max(0, min(1023, code))

def parse_dac_stdout(text: str):
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
    if DRY_RUN:
        v = code_to_vlow(code)
        msg = f"[dry-run] CH{ch} code 0x{code:03X} Vlow~{v:.3f} V"
        return (math.nan, v, math.nan, msg)

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
    now = time.time()
    boundary = (math.floor(now / period) + 1) * period
    time.sleep(max(0.0, boundary - now))
    return boundary

# ----------------------------- BME280 -------------------------------

def init_bme():
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

        # High accuracy config (best effort)
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
    try:
        t = float(sensor.temperature)
        p = float(sensor.pressure)
        h = float(sensor.humidity)
        if any([(v != v) for v in (t, p, h)]):
            raise ValueError("NaN")
        return t, p, h
    except Exception as e:
        print(f"[warn] BME280 read failed: {e}")
        return None, None, None

# ------------------------------- Main -------------------------------

def main():
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

    start_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(BME_LOG_DIR, exist_ok=True)
    os.makedirs(ADJ_LOG_DIR, exist_ok=True)

    bme_log_path = os.path.join(BME_LOG_DIR, f"bme_log_{start_tag}.csv")
    adj_log_path = os.path.join(ADJ_LOG_DIR, f"dac_adj_{start_tag}.csv")

    sensor = init_bme()

    # Fixed reference temperature
    ref_temp = REF_TEMP_C

    # Prepare logs
    with open(bme_log_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_started_at", datetime.now().isoformat(timespec="seconds")])
        w.writerow(["ref_temp_C", f"{ref_temp:.2f}"])
        w.writerow(["timestamp", "temp_C_avg", "temp_C_std", "pressure_hPa_avg", "humidity_%_avg", "n_samples"])

    with open(adj_log_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_started_at", datetime.now().isoformat(timespec="seconds")])
        w.writerow(["ref_temp_C", f"{ref_temp:.2f}"])
        w.writerow([
            "timestamp", "channel",
            "old_code_dec", "old_code_hex",
            "new_code_dec", "new_code_hex",
            "vlow_before_V", "vlow_after_V",
            "deltaT_C", "high_voltage_V", "effective_bias_V"
        ])

    print(f"[INFO] BME log     -> {bme_log_path}")
    print(f"[INFO] DAC adj log -> {adj_log_path}")
    print(f"[INFO] Using fixed reference temperature: {ref_temp:.2f} C")
    print(f"[INFO] DAC model: VOFF={VOFF:.6f} V SPAN={SPAN:.6f} V V_per_code={V_PER_CODE_V*1e3:.3f} mV")
    print(f"[INFO] Min step: {MIN_STEP_CODES} codes (about {(MIN_STEP_CODES * V_PER_CODE_V)*1e3:.1f} mV)")
    print(f"[INFO] Gates: min_samples={MIN_SAMPLES_PER_BLOCK}/{EXPECTED_SAMPLES_PER_BLOCK} T_std_max={T_STD_MAX_C:.2f} C")
    print("[INFO] Aligning to the next 5 minute boundary...")
    current_boundary = align_to_next_boundary()

    # Apply START_CODES and measure Vlow reference from hardware if available
    last_code_by_ch = START_CODES.copy()
    vlow_ref_by_ch = {}
    last_meas_vlow_by_ch = {}
    last_meas_hv_by_ch = {}
    last_meas_vbias_by_ch = {}

    print("[INFO] Applying START_CODES...")
    for ch in CHANNELS:
        hv, vlow, vbias, _ = set_dac_and_read(ch, last_code_by_ch[ch])

        # Use measured Vlow as reference if it exists, otherwise use model
        if not math.isnan(vlow):
            vlow_ref_by_ch[ch] = float(vlow)
            last_meas_vlow_by_ch[ch] = float(vlow)
        else:
            vlow_ref_by_ch[ch] = code_to_vlow(last_code_by_ch[ch])
            last_meas_vlow_by_ch[ch] = math.nan

        last_meas_hv_by_ch[ch] = hv
        last_meas_vbias_by_ch[ch] = vbias

        vlow_disp = f"{vlow_ref_by_ch[ch]:.3f} V"
        hv_disp = f"{hv:.2f} V" if not math.isnan(hv) else "N/A"
        vb_disp = f"{vbias:.2f} V" if not math.isnan(vbias) else "N/A"
        print(f"  CH{ch} code 0x{last_code_by_ch[ch]:03X} HV={hv_disp} Vlow_ref={vlow_disp} Vbias={vb_disp}")

    # Continuous 5 minute blocks
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

        T_avg = mean(t_samples)
        T_std = pstdev(t_samples) if len(t_samples) > 1 else 0.0
        P_avg = mean(p_samples) if p_samples else math.nan
        H_avg = mean(h_samples) if h_samples else math.nan
        ts = datetime.fromtimestamp(block_end).strftime("%Y-%m-%d %H:%M:00")

        # Write BME block record always
        with open(bme_log_path, "a", newline="") as f:
            csv.writer(f).writerow([ts, f"{T_avg:.2f}", f"{T_std:.2f}", f"{P_avg:.2f}", f"{H_avg:.2f}", len(t_samples)])

        # Block quality gates (skip DAC updates)
        if len(t_samples) < MIN_SAMPLES_PER_BLOCK:
            print(f"[warn] Too few samples ({len(t_samples)}/{EXPECTED_SAMPLES_PER_BLOCK}); skipping DAC updates.")
            current_boundary = block_end
            continue

        if T_std > T_STD_MAX_C:
            print(f"[warn] Temp std too high (std={T_std:.2f} C); skipping DAC updates.")
            current_boundary = block_end
            continue

        # Compensation math
        dT_raw = T_avg - ref_temp
        dT = clamp(dT_raw, -MAX_DT_ABS_C, MAX_DT_ABS_C)

        # Sign assumes decreasing Vlow increases effective SiPM bias in your HV control chain
        dV = -TEMP_COEFF_V_PER_C * dT

        # Per channel adjustments (<= 1 per block)
        for ch in CHANNELS:
            vlow_ref = vlow_ref_by_ch[ch]
            vlow_tgt = vlow_ref + dV
            code_tgt_abs = vlow_to_code(vlow_tgt)

            code_prev = last_code_by_ch[ch]

            # Limit how much we move per block
            delta_codes_abs = code_tgt_abs - code_prev
            delta_codes = int(clamp(delta_codes_abs, -MAX_CODES_STEP_PER_BLOCK, MAX_CODES_STEP_PER_BLOCK))
            code_tgt = max(0, min(1023, code_prev + delta_codes))

            # Anti dither: require minimum code movement
            if abs(code_tgt - code_prev) < MIN_STEP_CODES:
                continue

            # Anti dither: require minimum modeled voltage movement
            if abs(code_to_vlow(code_tgt) - code_to_vlow(code_prev)) < MIN_STEP_VOLTS:
                continue

            # Before values (do not rewrite old code)
            vlow_before = last_meas_vlow_by_ch.get(ch, math.nan)
            if math.isnan(vlow_before):
                vlow_before = code_to_vlow(code_prev)

            # Apply new code once and read back what dac.py reports
            hv, vlow_after, vbias_after, _ = set_dac_and_read(ch, code_tgt)

            # Update tracking
            last_code_by_ch[ch] = code_tgt
            last_meas_hv_by_ch[ch] = hv
            last_meas_vbias_by_ch[ch] = vbias_after
            if not math.isnan(vlow_after):
                last_meas_vlow_by_ch[ch] = vlow_after
                vlow_after_use = vlow_after
            else:
                vlow_after_use = code_to_vlow(code_tgt)

            # Log one line per adjustment
            with open(adj_log_path, "a", newline="") as f:
                csv.writer(f).writerow([
                    ts, ch,
                    code_prev, f"0x{code_prev:03X}",
                    code_tgt, f"0x{code_tgt:03X}",
                    f"{vlow_before:.3f}",
                    f"{vlow_after_use:.3f}",
                    f"{dT:.2f}",
                    f"{hv:.2f}" if not math.isnan(hv) else "",
                    f"{vbias_after:.2f}" if not math.isnan(vbias_after) else ""
                ])

            print(f"[ADJ] {ts} CH{ch} Tavg={T_avg:.2f}C dT={dT:.2f}C Vlow={vlow_after_use:.3f}V code=0x{code_tgt:03X}")

        current_boundary = block_end

if __name__ == "__main__":
    try:
        print("[INFO] Starting bias adjustment daemon...")
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
