#!/usr/bin/env bash
# detectorInstall.sh — one-shot setup for this detector variant (adds pigpio + set_clock.py)
# - Enables I2C/SPI (persist + immediate)
# - Fetches repo files (mppcInterface/, DataTransfer.sh, Display.sh, dac.py, biasAdj.py)  [no rc.local from repo]
# - Installs WiringPi
# - Builds ice40 + max1932 + slowControl (with relink fallback)
# - Installs YOUR /home/cosmic/rc.local (the pigpio-aware one) + enables rc-local.service
# - Builds & installs pigpio from source (daemon + CLI + Python)
# - Starts pigpio now and sets GPCLK0 (BCM4) to 50 MHz
# - Adds DataTransfer cron (6h) and prints SSH pubkey

set -euo pipefail

USER_NAME="cosmic"
USER_HOME="/home/${USER_NAME}"
REPO_SLUG="tharinduudu/mppcInterface-Oct-2025"
REPO_TOP="${USER_HOME}/mppcInterface"

log(){ printf "\n[%s] %s\n" "$(date '+%F %T')" "$*"; }
need_root(){ [[ $EUID -eq 0 ]] || { echo "Run with sudo"; exit 1; }; }
need_root

log "APT update + base packages"
apt-get update -y
apt-get install -y git build-essential curl ca-certificates pkg-config \
                   python3-pip python3-venv python3-dev i2c-tools

log "Add ${USER_NAME} to gpio/i2c/spi groups"
usermod -aG gpio,i2c,spi "${USER_NAME}" || true

# ---- Enable I2C/SPI persistently ----
log "Enable I2C/SPI in boot config (persist)"
for f in /boot/config.txt /boot/firmware/config.txt; do
  [[ -f "$f" ]] || continue
  sed -i -E '/^\s*dtparam=i2c_arm=/d;/^\s*dtparam=spi=/d;/^\s*dtoverlay=spi0-2cs/d' "$f"
  echo 'dtparam=i2c_arm=on' >> "$f"
  echo 'dtparam=spi=on'     >> "$f"
  echo 'dtoverlay=spi0-2cs' >> "$f"
  # Important: do NOT add dtoverlay=gpclk — pigpio will own GPIO4 for GPCLK0
done

# ---- Enable now (no reboot) ----
log "Enable I2C/SPI immediately"
modprobe i2c-bcm2835 || true
modprobe i2c-dev      || true
modprobe spi_bcm2835  || true
modprobe spidev       || true
udevadm settle || true

# ---- Fetch repo content (exclude rc.local; you will provide your own) ----
log "Fetch repo files to ${USER_HOME}"
sudo -u "${USER_NAME}" bash -lc '
  set -e
  cd ~
  TMP=$(mktemp -d)
  curl -fLo "$TMP/repo.tgz" https://github.com/'"${REPO_SLUG}"'/archive/refs/heads/main.tar.gz
  TOPDIR=$(tar -tzf "$TMP/repo.tgz" | head -1 | cut -d/ -f1)
  tar -xzf "$TMP/repo.tgz" -C ~ --strip-components=1 \
      "$TOPDIR/mppcInterface" \
      "$TOPDIR/DataTransfer.sh" \
      "$TOPDIR/Display.sh" \
      "$TOPDIR/dac.py" \
      "$TOPDIR/biasAdj.py"
  rm -rf "$TMP"
'

# Ownership + exec for helpers
chown "${USER_NAME}:${USER_NAME}" "${USER_HOME}/DataTransfer.sh" "${USER_HOME}/Display.sh" \
                                   "${USER_HOME}/dac.py" "${USER_HOME}/biasAdj.py"
chmod 755 "${USER_HOME}/DataTransfer.sh" "${USER_HOME}/Display.sh"

# ---- Python libs (Blinka, BME280, DACx578, smbus2) WITHOUT upgrading pip ----
log "Install Python libs (Blinka, BME280, DACx578, smbus2) — no pip upgrade"
python3 -m pip install --break-system-packages --root-user-action=ignore \
  adafruit-blinka adafruit-circuitpython-bme280 adafruit-circuitpython-dacx578 smbus2

python3 - <<'PY' || true
try:
    import adafruit_blinka, adafruit_bme280, adafruit_dacx578  # type: ignore
    print("[OK] CircuitPython libs import")
except Exception as e:
    print("[WARN] Import check failed:", e)
PY

# ---- WiringPi ----
log "Install WiringPi"
sudo -u "${USER_NAME}" bash -lc 'cd ~ && rm -rf WiringPi && git clone --depth=1 https://github.com/WiringPi/WiringPi.git'
bash -lc "cd ${USER_HOME}/WiringPi && ./build"

# ---- Build firmware helpers (NO dac60508 here) ----
build_dir(){ local d="$1"; log "Build: $d"; bash -lc "cd '$d' && make clean || true && make -j\$(nproc)"; }

log "Build ice40 and max1932"
build_dir "${REPO_TOP}/firmware/libraries/ice40"
build_dir "${REPO_TOP}/firmware/libraries/max1932"

log "Build slowControl"
bash -lc "cd ${REPO_TOP}/firmware/libraries/slowControl && make clean || true && make -j\$(nproc) || true"
bash -lc "cd ${REPO_TOP}/firmware/libraries/slowControl && rm -f main.o main && g++ -c main.cpp -std=c++11 -I. && g++ main.o -lwiringPi -o main"

# ---- pigpio (daemon + CLI + python) from source ----
log "Build & install pigpio from source"
sudo -u "${USER_NAME}" bash -lc 'cd ~ && rm -rf pigpio && git clone https://github.com/joan2937/pigpio.git'
bash -lc "cd ${USER_HOME}/pigpio && make"
bash -lc "cd ${USER_HOME}/pigpio && make install"
ldconfig
python3 -m pip install --break-system-packages --root-user-action=ignore pigpio || true

# ---- Install set_clock.py into the user's home ----
log "Install set_clock.py helper"
cat > "${USER_HOME}/set_clock.py" <<"PY"
#!/usr/bin/env python3
"""
Set/stop the Raspberry Pi hardware clock on a GPIO (default: GPCLK0 on BCM4).

Usage:
  python3 set_clock.py 9600000           # 9.6 MHz on GPIO4
  python3 set_clock.py 50000000          # 50 MHz on GPIO4
  python3 set_clock.py --gpio 5 25000000 # 25 MHz on GPIO5 (GPCLK1)
  python3 set_clock.py --stop            # stop clock on GPIO4
"""
import argparse, sys, time
try:
    import pigpio
except ImportError:
    print("Missing pigpio. Install/build pigpio and start pigpiod.")
    sys.exit(1)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("freq", nargs="?", type=float, help="Frequency in Hz")
    p.add_argument("--gpio", type=int, default=4, help="BCM GPIO (4=GPCLK0, 5=GPCLK1, 6=GPCLK2)")
    p.add_argument("--stop", action="store_true", help="Stop the hardware clock on the pin")
    p.add_argument("--wait", type=float, default=0.0, help="Keep script alive N seconds (optional)")
    args = p.parse_args()

    pi = pigpio.pi()
    if not pi.connected:
        print("pigpio daemon not running. Start it with: sudo /usr/local/bin/pigpiod")
        sys.exit(1)

    if args.stop:
        pi.hardware_clock(args.gpio, 0)
        print(f"Stopped hardware clock on GPIO{args.gpio}")
        pi.stop(); return

    if args.freq is None:
        print("Provide a frequency in Hz, e.g., 50000000")
        pi.stop(); sys.exit(1)

    freq = int(args.freq)
    rc = pi.hardware_clock(args.gpio, freq)
    if rc != 0:
        print(f"hardware_clock() error {rc}")
        pi.stop(); sys.exit(1)

    print(f"Set hardware clock: GPIO{args.gpio} -> {freq} Hz")
    if args.wait > 0:
        try: time.sleep(args.wait)
        except KeyboardInterrupt: pass
    pi.stop()

if __name__ == "__main__":
    main()
PY
chown "${USER_NAME}:${USER_NAME}" "${USER_HOME}/set_clock.py"
chmod 755 "${USER_HOME}/set_clock.py"

# ---- Start pigpio now and set GPCLK0 to 50 MHz (immediate test) ----
log "Start pigpio daemon and set GPCLK0 to 50 MHz (now)"
if ! pgrep pigpiod >/dev/null 2>&1; then
  /usr/local/bin/pigpiod >/dev/null 2>&1 || true
fi
# wait until the daemon answers
for n in 1 2 3 4 5; do
  /usr/local/bin/pigs t >/dev/null 2>&1 && break
  sleep 1
done
/usr/local/bin/pigs hc 4 50000000 >/dev/null 2>&1 || true

# ---- Install YOUR rc.local and enable rc-local.service ----
# Expecting /home/cosmic/rc.local to be the pigpio-aware version we discussed.
if [[ -f "${USER_HOME}/rc.local" ]]; then
  log "Install rc.local from ${USER_HOME}/rc.local and enable rc-local.service"
  install -m 755 -o root -g root "${USER_HOME}/rc.local" /etc/rc.local
else
  log "WARNING: ${USER_HOME}/rc.local not found — skipping install (you can install it later)."
fi

cat >/etc/systemd/system/rc-local.service <<'UNIT'
[Unit]
Description=/etc/rc.local Compatibility
ConditionPathExists=/etc/rc.local
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/etc/rc.local
TimeoutSec=0
RemainAfterExit=yes
StandardOutput=journal
StandardError=journal
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable rc-local.service

# ---- Cron for DataTransfer.sh (every 6h) ----
log "Install crontab entry for DataTransfer.sh (every 6 hours)"
bash -lc '(crontab -u '"${USER_NAME}"' -l 2>/dev/null | grep -v -F "/home/'"${USER_NAME}"'/DataTransfer.sh"; echo "0 */6 * * * /home/'"${USER_NAME}"'/DataTransfer.sh") | crontab -u '"${USER_NAME}"' -'

# ---- SSH key: create once, then reuse ----
log "Ensure SSH key exists and print public key"
sudo -u "${USER_NAME}" bash -lc '
  mkdir -p ~/.ssh && chmod 700 ~/.ssh
  if [ ! -f ~/.ssh/id_ed25519 ]; then
    ssh-keygen -t ed25519 -a 100 -C "$(whoami)@$(hostname)" -N "" -f ~/.ssh/id_ed25519 -q
  fi
  echo
  echo "==== PUBLIC KEY ===="
  cat ~/.ssh/id_ed25519.pub
  echo "===================="
'

echo
echo "Install complete."
echo "→ pigpio is running; GPCLK0 (GPIO4) set to 50 MHz now. Scope pin 7 to confirm."
echo "→ On boot, /etc/rc.local (your version) will start pigpio and set the clock."
echo "→ You can tweak later with: /home/${USER_NAME}/set_clock.py 9600000  (or --stop)"
