#!/usr/bin/env bash
# detectorInstall.sh — one-shot setup for this detector variant (no dac60508 C helper)
# - Enables I2C/SPI (persist + immediate)
# - Fetches repo files (mppcInterface/, rc.local, DataTransfer.sh, Display.sh, dac.py, biasAdj.py)
# - Installs WiringPi
# - Builds ice40 + max1932 + slowControl (with relink fallback)
# - Installs your repo's rc.local (biasAdjust first, then slowControl) + enables rc-local.service
# - Adds DataTransfer cron (6h) and installs Display.sh
# - Generates SSH key once and prints public key


set -euo pipefail

USER_NAME="cosmic"
USER_HOME="/home/${USER_NAME}"
REPO_SLUG="tharinduudu/mppcInterface-Oct-2025"      # <-- change if your repo differs
REPO_TOP="${USER_HOME}/mppcInterface"               # repo extracts here

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
done

# ---- Enable now (no reboot) ----
log "Enable I2C/SPI immediately"
modprobe i2c-bcm2835 || true
modprobe i2c-dev      || true
modprobe spi_bcm2835  || true
modprobe spidev       || true
udevadm settle || true

# ---- Fetch repo content (auto-detect topdir in tarball) ----
log "Fetch repo files to ${USER_HOME}"
sudo -u "${USER_NAME}" bash -lc '
  set -e
  cd ~
  TMP=$(mktemp -d)
  curl -fLo "$TMP/repo.tgz" https://github.com/'"${REPO_SLUG}"'/archive/refs/heads/main.tar.gz
  TOPDIR=$(tar -tzf "$TMP/repo.tgz" | head -1 | cut -d/ -f1)
  tar -xzf "$TMP/repo.tgz" -C ~ --strip-components=1 \
      "$TOPDIR/mppcInterface" \
      "$TOPDIR/rc.local" \
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

# ---- Python libraries (Blinka, BME280, DACx578, smbus2) WITHOUT upgrading pip ----
log "Install Python libs (Blinka, BME280, DACx578, smbus2) — no pip upgrade"
python3 -m pip install --break-system-packages --root-user-action=ignore \
  adafruit-blinka adafruit-circuitpython-bme280 adafruit-circuitpython-dacx578 smbus2

# (Optional) quick import check (non-fatal)
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
# relink fallback if Makefile link order is off
bash -lc "cd ${REPO_TOP}/firmware/libraries/slowControl && rm -f main.o main && g++ -c main.cpp -std=c++11 -I. && g++ main.o -lwiringPi -o main"

# ---- Install your repo's rc.local verbatim and enable the compatibility unit ----
log "Install rc.local from repo and enable rc-local.service"
install -m 755 -o root -g root "${USER_HOME}/rc.local" /etc/rc.local

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
# (Don’t start now; it will run on next boot.)

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
  echo "Please share the public key below with GSU to enable secure data transfer access. Thank you."
  echo "==== PUBLIC KEY ===="
  cat ~/.ssh/id_ed25519.pub
  echo "===================="
'

echo
echo "Install complete."
echo "If /dev/i2c-1 or /dev/spidev0.* are missing, reboot now: sudo reboot"
echo "To tail slowControl later: /home/${USER_NAME}/Display.sh"
