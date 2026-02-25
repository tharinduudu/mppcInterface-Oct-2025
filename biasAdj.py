#!/usr/bin/env python3
# biasAdj.py
# Temperature based DAC control for Hamamatsu S13360-2050VE
#
# Simple summary
# - Reads BME280 at 0x77
# - Samples every 10 s and makes 5 minute blocks
# - Uses fixed reference temperature (default 20.0 C)
# - Changes low side DAC to keep effective bias stable
# - Uses anti dither with a 5 code minimum step
# - Limits max DAC change per 5 minute block
# - Ramps DAC slowly in round robin, 1 code at a time
# - Keeps 0.5 s gap between any two DAC writes
# - Removes temperature outliers before control math
# - Writes 2 csv files per run

import os
import sys
import time
import csv
import math
import shutil
import subprocess
from datetime import datetime
from statistics import mean, pstdev, median

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

# These are the start codes at REF_TEMP_C
START_CODES = {
    0: 0x2F1,
    1: 0x2F1,
    2: 0x2F1,
    3: 0x2F1,
}

# Fixed reference temperature
REF_TEMP_C = float(os.getenv("REF_TEMP_C", "20.0"))

# SiPM temp slope in V per C
TEMP_COEFF_V_PER_C = float(os.getenv("TEMP_COEFF_V_PER_C", "0.054"))

# DAC model
# Vlow ~= VOFF + SPAN * (code / 1023)
VOFF = float(os.getenv("VOFF", "-0.0226786515445685"))
SPAN = float(os.getenv("SPAN", "2.3527073030891374"))
V_PER_CODE_V = SPAN / 1023.0

# BME settings
BME_I2C_ADDR = 0x77
SAMPLE_EVERY_SEC = int(os.getenv("SAMPLE_EVERY_SEC", "10"))
LOG_BLOCK_SEC = int(os.getenv("LOG_BLOCK_SEC", str(5 * 60)))

# Anti dither
MIN_STEP_CODES = int(os.getenv("MIN_STEP_CODES", "5"))
MIN_STEP_VOLTS = float(os.getenv("MIN_STEP_VOLTS", "0.012"))

# Block checks and safety limits
EXPECTED_SAMPLES_PER_BLOCK = int(round(LOG_BLOCK_SEC / max(1, SAMPLE_EVERY_SEC)))
MIN_SAMPLES_PER_BLOCK = int(
    os.getenv("MIN_SAMPLES_PER_BLOCK", str(max(1, int(0.80 * EXPECTED_SAMPLES_PER_BLOCK))))
)
T_STD_MAX_C = float(os.getenv("T_STD_MAX_C", "0.20"))
MAX_DT_ABS_C = float(os.getenv("MAX_DT_ABS_C", "10.0"))
MAX_CODES_STEP_PER_BLOCK = int(os.getenv("MAX_CODES_STEP_PER_BLOCK", "120"))

# Slow DAC ramp
# This is the gap after every DAC write
INTER_DAC_WRITE_GAP_SEC = float(os.getenv("INTER_DAC_WRITE_GAP_SEC", "0.5"))

# BME outlier filter
OUTLIER_FILTER_ENABLE = os.getenv("OUTLIER_FILTER_ENABLE", "1") in ("1", "true", "True", "YES", "yes")
OUTLIER_MIN_POINTS = int(os.getenv("OUTLIER_MIN_POINTS", "8"))
OUTLIER_MAD_Z = float(os.getenv("OUTLIER_MAD_Z", "3.5"))

# --------------------------- Helper funcs ---------------------------

def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x

def code_to_vlow(code):
    frac = max(0.0, min(1.0, (int(code) & 0x3FF) / 1023.0))
    v = VOFF + SPAN * frac
    return max(0.0, v)

def vlow_to_code(v):
    v = max(0.0, float(v))
    code = int(round(((v - VOFF) / SPAN) * 1023.0))
    return max(0, min(1023, code))

def parse_dac_stdout(text):
    hv = math.nan
    vlow = math.nan
    vbias = math.nan

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

def set_dac_and_read(ch, code):
    if DRY_RUN:
        v = code_to_vlow(code)
        msg = f"[dry-run] CH{ch} code 0x{code:03X} Vlow~{v:.3f} V"
        return math.nan, v, math.nan, msg

    try:
        proc = subprocess.run(
            [sys.executable, DAC_SCRIPT, str(ch), f"0x{code:03X}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )
        hv, vlow, vbias = parse_dac_stdout(proc.stdout)
        return hv, vlow, vbias, proc.stdout.strip()
    except subprocess.CalledProcessError as e:
        return math.nan, math.nan, math.nan, f"[dac.py error] CH{ch} code=0x{code:03X}: {e.stderr}"
    except Exception as e:
        return math.nan, math.nan, math.nan, f"[dac.py error] CH{ch} code=0x{code:03X}: {e}"

def align_to_next_boundary(period=300):
    now = time.time()
    boundary = (math.floor(now / period) + 1) * period
    time.sleep(max(0.0, boundary - now))
    return boundary

def filter_temp_outliers(block_rows):
    """
    block_rows is a list of (temp_C, pressure_hPa, humidity_pct)
    Filter uses temperature only.
    Returns filtered_rows and removed_count.
    """
    if (not OUTLIER_FILTER_ENABLE) or len(block_rows) < OUTLIER_MIN_POINTS:
        return block_rows, 0

    temps = [r[0] for r in block_rows]
    t_med = median(temps)
    abs_dev = [abs(t - t_med) for t in temps]
    mad = median(abs_dev)

    if mad <= 1e-9:
        return block_rows, 0

    sigma_like = 1.4826 * mad
    thresh = OUTLIER_MAD_Z * sigma_like

    filtered = []
    removed = 0
    for row in block_rows:
        if abs(row[0] - t_med) <= thresh:
            filtered.append(row)
        else:
            removed += 1

    return filtered, removed

def ramp_codes_round_robin(ch_order, code_start_by_ch, code_target_by_ch):
    """
    Move DAC slowly in round robin.
    Each pass moves each channel by 1 code toward target.
    There is one fixed gap after each DAC write.
    """
    code_now = {ch: int(code_start_by_ch[ch]) for ch in ch_order}
    code_tgt = {ch: int(code_target_by_ch[ch]) for ch in ch_order}

    last_hv = {ch: math.nan for ch in ch_order}
    last_vlow = {ch: math.nan for ch in ch_order}
    last_vbias = {ch: math.nan for ch in ch_order}

    def any_pending():
        for c in ch_order:
            if code_now[c] != code_tgt[c]:
                return True
        return False

    while any_pending():
        for ch in ch_order:
            if code_now[ch] == code_tgt[ch]:
                continue

            if code_tgt[ch] > code_now[ch]:
                code_now[ch] += 1
                if code_now[ch] > code_tgt[ch]:
                    code_now[ch] = code_tgt[ch]
            else:
                code_now[ch] -= 1
                if code_now[ch] < code_tgt[ch]:
                    code_now[ch] = code_tgt[ch]

            hv, vlow, vbias, _ = set_dac_and_read(ch, code_now[ch])
            last_hv[ch] = hv
            last_vlow[ch] = vlow
            last_vbias[ch] = vbias

            if INTER_DAC_WRITE_GAP_SEC > 0 and any_pending():
                time.sleep(INTER_DAC_WRITE_GAP_SEC)

    return code_now, last_hv, last_vlow, last_vbias

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
    ref_temp = REF_TEMP_C

    with open(bme_log_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_started_at", datetime.now().isoformat(timespec="seconds")])
        w.writerow(["ref_temp_C", f"{ref_temp:.2f}"])
        w.writerow(["timestamp", "temp_C_avg", "temp_C_std", "pressure_hPa_avg", "humidity_pct_avg", "n_samples"])

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

    print(f"[INFO] BME log -> {bme_log_path}")
    print(f"[INFO] DAC log -> {adj_log_path}")
    print(f"[INFO] Fixed ref temp: {ref_temp:.2f} C")
    print(f"[INFO] DAC model V per code: {V_PER_CODE_V * 1000.0:.3f} mV")
    print(f"[INFO] Min DAC step gate: {MIN_STEP_CODES} codes")
    print(f"[INFO] Min voltage gate: {MIN_STEP_VOLTS * 1000.0:.1f} mV")
    print(f"[INFO] Temp std gate: {T_STD_MAX_C:.2f} C")
    print(f"[INFO] Max code move per 5 min block: {MAX_CODES_STEP_PER_BLOCK} codes")
    print(f"[INFO] Gap between DAC writes: {INTER_DAC_WRITE_GAP_SEC:.3f} s")
    print("[INFO] Aligning to next 5 minute boundary...")
    current_boundary = align_to_next_boundary(LOG_BLOCK_SEC)

    # Keep state
    last_code_by_ch = START_CODES.copy()
    vlow_ref_by_ch = {}
    last_meas_vlow_by_ch = {}
    last_meas_hv_by_ch = {}
    last_meas_vbias_by_ch = {}

    print("[INFO] Applying start codes...")
    for ch in CHANNELS:
        hv, vlow, vbias, _ = set_dac_and_read(ch, last_code_by_ch[ch])

        if not math.isnan(vlow):
            vlow_ref_by_ch[ch] = float(vlow)
            last_meas_vlow_by_ch[ch] = float(vlow)
        else:
            vlow_ref_by_ch[ch] = code_to_vlow(last_code_by_ch[ch])
            last_meas_vlow_by_ch[ch] = math.nan

        last_meas_hv_by_ch[ch] = hv
        last_meas_vbias_by_ch[ch] = vbias

        hv_disp = f"{hv:.2f} V" if not math.isnan(hv) else "N/A"
        vb_disp = f"{vbias:.2f} V" if not math.isnan(vbias) else "N/A"
        print(
            f"  CH{ch} code 0x{last_code_by_ch[ch]:03X} "
            f"HV={hv_disp} Vlow_ref={vlow_ref_by_ch[ch]:.3f} V Vbias={vb_disp}"
        )

    while True:
        block_end = current_boundary + LOG_BLOCK_SEC
        block_rows = []

        # Read one block
        while True:
            t, p, h = read_bme(sensor)
            if t is not None:
                block_rows.append((t, p, h))

            now = time.time()
            if now >= block_end:
                break

            time.sleep(min(SAMPLE_EVERY_SEC, max(0.0, block_end - now)))

        if not block_rows:
            print("[warn] No BME samples in block. Skip.")
            current_boundary = block_end
            continue

        raw_count = len(block_rows)
        block_rows_filt, outliers_removed = filter_temp_outliers(block_rows)
        used_count = len(block_rows_filt)

        if used_count == 0:
            print("[warn] All BME samples filtered out. Skip.")
            current_boundary = block_end
            continue

        t_samples = [r[0] for r in block_rows_filt]
        p_samples = [r[1] for r in block_rows_filt]
        h_samples = [r[2] for r in block_rows_filt]

        T_avg = mean(t_samples)
        T_std = pstdev(t_samples) if len(t_samples) > 1 else 0.0
        P_avg = mean(p_samples) if p_samples else math.nan
        H_avg = mean(h_samples) if h_samples else math.nan
        ts = datetime.fromtimestamp(block_end).strftime("%Y-%m-%d %H:%M:00")

        with open(bme_log_path, "a", newline="") as f:
            csv.writer(f).writerow([
                ts,
                f"{T_avg:.2f}",
                f"{T_std:.2f}",
                f"{P_avg:.2f}",
                f"{H_avg:.2f}",
                used_count
            ])

        if outliers_removed > 0:
            print(f"[INFO] BME outliers removed: {outliers_removed} raw={raw_count} used={used_count}")

        if used_count < MIN_SAMPLES_PER_BLOCK:
            print(f"[warn] Too few good samples ({used_count}/{EXPECTED_SAMPLES_PER_BLOCK}). Skip DAC updates.")
            current_boundary = block_end
            continue

        if T_std > T_STD_MAX_C:
            print(f"[warn] Temp std too high ({T_std:.2f} C). Skip DAC updates.")
            current_boundary = block_end
            continue

        # Bias math
        dT_raw = T_avg - ref_temp
        dT = clamp(dT_raw, -MAX_DT_ABS_C, MAX_DT_ABS_C)

        # Effective bias is HV - Vlow.
        # If temp goes up, reduce Vlow to increase effective bias.
        dV = -TEMP_COEFF_V_PER_C * dT

        # Plan updates for this block
        pending = {}
        before_info = {}

        for ch in CHANNELS:
            vlow_ref = vlow_ref_by_ch[ch]
            vlow_tgt_abs = vlow_ref + dV
            code_tgt_abs = vlow_to_code(vlow_tgt_abs)

            code_prev = last_code_by_ch[ch]

            # Limit max code move in one 5 minute block
            delta_codes_abs = code_tgt_abs - code_prev
            delta_codes = int(clamp(delta_codes_abs, -MAX_CODES_STEP_PER_BLOCK, MAX_CODES_STEP_PER_BLOCK))
            code_tgt = max(0, min(1023, code_prev + delta_codes))

            # Anti dither checks
            if abs(code_tgt - code_prev) < MIN_STEP_CODES:
                continue

            if abs(code_to_vlow(code_tgt) - code_to_vlow(code_prev)) < MIN_STEP_VOLTS:
                continue

            vlow_before = last_meas_vlow_by_ch.get(ch, math.nan)
            if math.isnan(vlow_before):
                vlow_before = code_to_vlow(code_prev)

            pending[ch] = code_tgt
            before_info[ch] = {
                "code_prev": code_prev,
                "vlow_before": float(vlow_before),
            }

        # Apply updates in round robin slow ramp
        if pending:
            ch_order = [ch for ch in CHANNELS if ch in pending]
            code_start_by_ch = {ch: last_code_by_ch[ch] for ch in ch_order}
            code_target_by_ch = {ch: pending[ch] for ch in ch_order}

            final_code_by_ch, last_hv_by_ch, last_vlow_by_ch, last_vbias_by_ch = ramp_codes_round_robin(
                ch_order,
                code_start_by_ch,
                code_target_by_ch
            )

            # Log one row per changed channel
            for ch in ch_order:
                code_prev = before_info[ch]["code_prev"]
                code_new = final_code_by_ch[ch]

                hv = last_hv_by_ch[ch]
                vlow_after = last_vlow_by_ch[ch]
                vbias_after = last_vbias_by_ch[ch]

                if math.isnan(vlow_after):
                    vlow_after = code_to_vlow(code_new)

                last_code_by_ch[ch] = code_new
                last_meas_hv_by_ch[ch] = hv
                last_meas_vbias_by_ch[ch] = vbias_after
                last_meas_vlow_by_ch[ch] = vlow_after

                with open(adj_log_path, "a", newline="") as f:
                    csv.writer(f).writerow([
                        ts, ch,
                        code_prev, f"0x{code_prev:03X}",
                        code_new, f"0x{code_new:03X}",
                        f"{before_info[ch]['vlow_before']:.3f}",
                        f"{vlow_after:.3f}",
                        f"{dT:.2f}",
                        f"{hv:.2f}" if not math.isnan(hv) else "",
                        f"{vbias_after:.2f}" if not math.isnan(vbias_after) else ""
                    ])

                print(
                    f"[ADJ] {ts} CH{ch} Tavg={T_avg:.2f}C dT={dT:.2f}C "
                    f"Vlow={vlow_after:.3f}V code=0x{code_new:03X}"
                )

        # Re align after slow ramps
        current_boundary = int(math.floor(time.time() / LOG_BLOCK_SEC)) * LOG_BLOCK_SEC

if __name__ == "__main__":
    try:
        print("[INFO] Starting bias adjustment daemon...")
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
