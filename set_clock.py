#!/usr/bin/env python3
"""
Set/stop the Raspberry Pi hardware clock on a GPIO (default: GPCLK0 on BCM4).

Usage examples:
  sudo pigpiod
  python3 set_clock.py 9600000          # 9.6 MHz on GPIO4
  python3 set_clock.py 50000000          # 50 MHz on GPIO4
  python3 set_clock.py --gpio 5 25000000 # 25 MHz on GPIO5 (GPCLK1)
  python3 set_clock.py --stop            # stop clock on GPIO4
"""

import argparse
import sys
import time

try:
    import pigpio
except ImportError:
    print("Missing pigpio. Install with: sudo apt-get install pigpio python3-pigpio")
    sys.exit(1)

def main():
    p = argparse.ArgumentParser(description="Set Raspberry Pi hardware clock on a GPIO.")
    p.add_argument("freq", nargs="?", type=float, help="Frequency in Hz (e.g., 9600000 or 9.6e6).")
    p.add_argument("--gpio", type=int, default=4, help="BCM GPIO (4=GPCLK0, 5=GPCLK1, 6=GPCLK2). Default 4.")
    p.add_argument("--stop", action="store_true", help="Stop the hardware clock on the selected GPIO.")
    p.add_argument("--wait", type=float, default=0.0, help="Keep script alive for N seconds (optional).")
    args = p.parse_args()

    pi = pigpio.pi()
    if not pi.connected:
        print("pigpio daemon not running. Start it with: sudo pigpiod")
        sys.exit(1)

    if args.stop:
        pi.hardware_clock(args.gpio, 0)  # 0 stops the clock
        print(f"Stopped hardware clock on GPIO{args.gpio}")
        pi.stop()
        return

    if args.freq is None:
        print("Please provide a frequency in Hz (e.g., 9600000).")
        pi.stop()
        sys.exit(1)

    freq = int(args.freq)
    rc = pi.hardware_clock(args.gpio, freq)
    if rc != 0:
        print(f"pigpio hardware_clock() returned error code {rc}")
        pi.stop()
        sys.exit(1)

    print(f"Set hardware clock: GPIO{args.gpio} -> {freq} Hz")
    if args.wait > 0:
        try:
            time.sleep(args.wait)
        except KeyboardInterrupt:
            pass

    pi.stop()

if __name__ == "__main__":
    main()
