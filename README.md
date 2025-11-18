# mppcInterface (Oct-2025) 

>**!!Please contact the gLOWCOST team before running detectorInstall.sh for data transfer arrangements!!**

> **!! Please send the public key printed at the end of the procedure.**

End-to-end setup for a Raspberry Pi–based cosmic muon detector (new layout).  
This repo includes a one-step installer that enables **I²C/SPI**, installs dependencies, builds firmware helpers, configures boot-time services, schedules data transfers, and prints an SSH **public key** for secure access.

## Quick start

### Download the installer
```bash
curl -fL -o detectorInstall.sh https://raw.githubusercontent.com/tharinduudu/mppcInterface-Oct-2025/main/detectorInstall.sh
```

### (Optional) Review the script
```bash
less detectorInstall.sh
```

### Run it
```bash
chmod +x detectorInstall.sh
sudo ./detectorInstall.sh
```

The script is **idempotent**: you can re-run it safely if needed.

---

## What the installer does

- **Enables I²C & SPI**
  - Persists settings in `/boot/config.txt` (and `/boot/firmware/config.txt` if present)
  - Loads overlays/modules immediately so you can proceed without reboot (when possible)

- **Fetches the repo contents needed for the detector** into `/home/cosmic`:
  - `mppcInterface/` (firmware helpers & `slowControl`)
  - `rc.local` (boot-time bring-up: **biasAdj.py first, then slowControl**)
  - `dac.py`, `biasAdj.py`
  - `DataTransfer.sh`, `Display.sh`

- **Installs WiringPi** from source

- **Builds firmware helpers**
  - C helpers: `ice40`, `max1932`  
  - `slowControl` binary via `make`; includes a **safe relink fallback** to ensure WiringPi links correctly  
  - **Note:** this system **does not use `dac60508`** C helper

- **Installs a non-blocking `/etc/rc.local`** from the repo and enables systemd’s **rc-local compatibility** unit for next boot  
  (long-running jobs are backgrounded; `rc.local` exits with `0`)

- **Marks `slowControl/run.sh` executable** (and sets correct ownership)

- **Installs `DataTransfer.sh`** to `/home/cosmic` and schedules it via **cron every 6 hours**

- **Installs Python libraries** for this detector (no pip self-upgrade; uses Pi OS Bookworm flags):
  - `adafruit-blinka`, `adafruit-circuitpython-bme280`, `adafruit-circuitpython-dacx578`, `smbus2`

- **Generates an SSH key (once) and prints the public key** with a friendly message to share

---

## Requirements

- **Hardware:** Raspberry Pi 4 (others may work, not tested here)
- **OS:** Raspberry Pi OS Bullseye/Bookworm
- **User:** assumes primary user `cosmic` (edit the script if different)
- **Network:** internet access during install
- **Privileges:** run the installer with `sudo`

---

## After install

If the script notes missing device nodes, **reboot** once:
```bash
sudo reboot
```

### Verify devices
```bash
ls -l /dev/i2c-1
ls -l /dev/spidev*
```

### rc.local status (enabled for next boot)
```bash
systemctl status rc-local
```

### Confirm cron job (runs every 6h)
```bash
crontab -u cosmic -l
```

### Get your public key again (if needed)
```bash
cat /home/cosmic/.ssh/id_ed25519.pub
```

---

## Where things live

- **Detector code & helpers:** `/home/cosmic/mppcInterface/`
  - Firmware helpers:  
    `/home/cosmic/mppcInterface/firmware/libraries/ice40/`  
    `/home/cosmic/mppcInterface/firmware/libraries/max1932/`
  - Slow control:  
    `/home/cosmic/mppcInterface/firmware/libraries/slowControl/` (`main`, `run.sh`)
- **Boot-time startup:** `/etc/rc.local`  
  (programs FPGA, sets HV/DACs as your scripts dictate, then starts **biasAdj.py** followed by **slowControl**)
- **Display helper:** `/home/cosmic/Display.sh` (tails most-recent slowControl file)
- **Data transfer script:** `/home/cosmic/DataTransfer.sh` (cron runs it every 6h)
- **Python helpers:** `/home/cosmic/dac.py`, `/home/cosmic/biasAdj.py`
- **Logs (typical):**
  - Repo `rc.local` main log: `/var/log/detector.log`
  - bias adjust logs: `/home/cosmic/logs/tempcomp/…`
  - SlowControl logging behavior is controlled by your `run.sh`

---

## Customize

Open `detectorInstall.sh` and adjust:

- `USER_NAME` (default `cosmic`)
- Any site-specific settings in your **`rc.local`** or data scripts
- Bitstream filename/paths in your bring-up tools, if you change them

Re-run the installer after changes.

---

## Troubleshooting

### PEP 668 / “externally-managed-environment”
The installer uses:
```bash
python3 -m pip install --break-system-packages --root-user-action=ignore ...
```
to work cleanly on Raspberry Pi OS (Bookworm).

### Missing `/dev/spidev0.0` or `/dev/i2c-1`
Reboot once after install:
```bash
sudo reboot
```
Then re-check device nodes.

### `slowControl` fails to link with WiringPi
A one-time relink fallback is already included:
```bash
cd /home/cosmic/mppcInterface/firmware/libraries/slowControl
rm -f main main.o
g++ -c main.cpp -std=c++11 -I. && g++ main.o -lwiringPi -o main
```

### `rc.local` appears to “hang”
This repo’s `rc.local` **backgrounds** long-running tasks and **ends with `exit 0`**.  
If you modify it, ensure long commands end with `&` and the file ends with `exit 0`.

### Cron not running the transfer
Check the user’s crontab and system log:
```bash
crontab -u cosmic -l
grep CRON /var/log/syslog | tail -n 50
```

---

**Reminder:**  
**!! Please send the public key printed at the end of the procedure.**
