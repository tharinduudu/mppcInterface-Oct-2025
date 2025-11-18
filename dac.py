#!/usr/bin/env python3
# dac.py — Set DACx578 by CHANNEL + 10-bit CODE; print High voltage, DAC output, Effective bias (2 decimals)
#
# Usage:
#   python3 dac.py 0 0x23A
#   python3 dac.py 3 672
#
# Prints exactly three lines (two decimal places each):
#   High voltage: <V>
#   DAC output: <V>
#   Effective bias: <V>

HIGH_SIDE = 57.0  # volts (edit this as needed)

import sys
import board
import busio
import adafruit_dacx578

DAC_ADDR = 0x47  # I2C address of the DACx578

# Calibration from your 20-point measurement table:
# Vlow ≈ VOFF + SPAN * (code/1023)
VOFF = -0.0226786515445685  # volts (intercept)
SPAN =  2.3527073030891374  # volts per full-scale fraction

def to_scaled16(code10: int) -> int:
    """Map 10-bit code (0..1023) to the driver's 16-bit .value (0..65535)."""
    return int(round((code10 & 0x3FF) * 65535 / 1023))

def predicted_vlow(code10: int) -> float:
    """Calibrated DAC pin voltage for the given 10-bit code."""
    frac = max(0.0, min(1.0, (code10 & 0x3FF) / 1023.0))
    v = VOFF + SPAN * frac
    return max(0.0, v)

def main():
    # Expect: python3 dac.py <channel 0..7> <code 0..1023 or 0x000..0x3FF>
    if len(sys.argv) != 3:
        print("Usage: python3 dac.py <channel 0..7> <code 0..1023 or 0x000..0x3FF>", file=sys.stderr)
        sys.exit(1)

    # Channel
    try:
        ch = int(sys.argv[1], 0)
        if not (0 <= ch <= 7):
            raise ValueError
    except Exception:
        print("Error: channel must be 0..7.", file=sys.stderr)
        sys.exit(2)

    # 10-bit code
    try:
        code10 = int(sys.argv[2], 0)
        if not (0 <= code10 <= 1023):
            raise ValueError
    except Exception:
        print("Error: code must be 0..1023 or 0x000..0x3FF.", file=sys.stderr)
        sys.exit(3)

    # I2C + DAC
    i2c = busio.I2C(board.SCL, board.SDA)
    dac = adafruit_dacx578.DACx578(i2c, address=DAC_ADDR)

    # Prefer external reference & 1x span if exposed (ignore if absent)
    try: dac.use_internal_reference = False
    except Exception: pass
    try: dac.gain = 1
    except Exception: pass

    # Backstop config after init (safe no-op on some variants)
    try:
        while not i2c.try_lock(): pass
        i2c.writeto(DAC_ADDR, bytes([0x10, 0x00]))
    finally:
        try: i2c.unlock()
        except Exception: pass

    # Set the channel value
    dac.channels[ch].value = to_scaled16(code10)

    # Compute outputs
    vlow = predicted_vlow(code10)
    vbias = HIGH_SIDE - vlow

    # Print exactly the three requested lines with two decimals
    print(f"High voltage: {HIGH_SIDE:.2f} V")
    print(f"DAC output: {vlow:.2f} V")
    print(f"Effective bias: {vbias:.2f} V")

if __name__ == "__main__":
    main()
