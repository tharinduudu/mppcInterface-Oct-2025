#!/usr/bin/env python3
"""Set one DACx578 channel using a 10-bit code.

The DAC controls the low side of the SiPM.  This script can calculate that
voltage from the measured DAC calibration.  It cannot measure the MAX1932
high-side voltage, so an effective bias is only reported when HIGH_SIDE_V is
provided explicitly in the environment.
"""

import math
import os
import sys

DAC_ADDR = 0x47  # I2C address of the DACx578

# Calibration from the 0x000..0x3FF measured DAC sweep.
# Vlow ≈ VOFF + SPAN * (code/1023)
VOFF = 0.0005  # volts (intercept)
SPAN = 2.9968  # volts per full-scale fraction

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

    # Hardware imports stay here so conversion functions can be tested away
    # from a Raspberry Pi.
    import board
    import busio
    import adafruit_dacx578

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

    # Compute outputs. HIGH_SIDE_V must come from a measured or calibrated
    # MAX1932 setting; silently assuming 57 V gives a false effective bias.
    vlow = predicted_vlow(code10)
    high_side_text = os.getenv("HIGH_SIDE_V")
    high_side = math.nan
    if high_side_text is not None:
        try:
            high_side = float(high_side_text)
        except ValueError:
            print("Error: HIGH_SIDE_V must be a number in volts.", file=sys.stderr)
            sys.exit(4)

    if math.isfinite(high_side):
        print(f"High voltage: {high_side:.4f} V")
        print(f"DAC output: {vlow:.4f} V")
        print(f"Effective bias: {high_side - vlow:.4f} V")
    else:
        print("High voltage: unknown")
        print(f"DAC output: {vlow:.4f} V")
        print("Effective bias: unknown")

if __name__ == "__main__":
    main()
