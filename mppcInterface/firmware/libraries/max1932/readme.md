# MAX1932 bias supply

Build with `make` on the Raspberry Pi after installing WiringPi.
The detector uses BCM13 (header pin 33) for chip select and SPI channel 0.

```sh
./main --predict 0xEA                 # Show what the code means; no GPIO or SPI access
./main --predict --voltage 55.13      # Find the nearest code for the board's high side
./main 0x00                          # Send the HV-off command
```

A raw hex code without `--predict` is sent to the chip as-is. Do not use a raw
code from another board as a voltage calibration. `--voltage` (V) and `--mV`
require a board calibration for nonzero targets and reject out-of-range
requests rather than selecting a higher voltage. The target is the **high
side**, not the effective SiPM bias (high side minus low side).

## Board calibration

For a measured board, install a root-owned
`/etc/glowcost/max1932-calibration.conf`, or use `--calibration FILE` for
an explicit file. The file contains one row:

```text
intercept_V  slope_V_per_code  lowest_calibrated_hex_code  highest_calibrated_hex_code
```

Fit high-side meter readings against the decimal code over the linear region.
Use only the measured range. See [the archived curve](calibration.example.conf)
for the format; its coefficients apply only to the board used to measure it.
Without a file, raw-code commands still work and the display clearly labels
the original circuit calculation as nominal.

Neither an SPI write nor the displayed estimate verifies the actual voltage.
Check the high side with a meter before using the result to set SiPM bias.
