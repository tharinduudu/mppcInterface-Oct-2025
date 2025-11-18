#!/usr/bin/env bash
# set_pi_clock.sh — set/stop the Pi hardware clock on GPIO4 (WiringPi pin 7)
# Requires: wiringPi's `gpio` utility (run with sudo)
#
# Usage:
#   sudo ./setClk.sh 9600000   # set GPCLK0 on GPIO4 to 9.6 MHz
#   sudo ./setClk.sh 2250000   # set to 2.25 MHz
#   sudo ./setClk.sh 1680000   # set to 1.68 MHz
#   sudo ./setClk.sh off       # disable clock on GPIO4
#
# Notes:
# - Uses BCM numbering with `-g`. BCM GPIO4 == physical pin 7 == WiringPi pin 7.
# - To stop the clock, we return the pin to input mode.

set -euo pipefail

PIN_BCM=4  # GPIO4 (BCM numbering)

if [[ $# -ne 1 ]]; then
  echo "Usage: sudo $0 <frequency_hz|off>"
  exit 1
fi

arg="$1"
if [[ "$arg" == "off" ]]; then
  gpio -g mode "$PIN_BCM" input
  echo "Clock on GPIO$PIN_BCM disabled (pin set to input)."
  exit 0
fi

# Validate integer frequency
if ! [[ "$arg" =~ ^[0-9]+$ ]]; then
  echo "Error: frequency must be an integer in Hz, or 'off'."
  exit 1
fi

freq="$arg"

# Put pin into clock alternate function (GPCLK0) and set frequency
gpio -g mode "$PIN_BCM" alt0
gpio -g clock "$PIN_BCM" "$freq"

echo "GPIO$PIN_BCM (WiringPi pin 7) set to hardware clock @ ${freq} Hz."
