#!/usr/bin/env bash
# DetectorInstall-Oct2025.sh — one-shot setup for mppcInterface-Oct-2025
set -euo pipefail

USER_NAME="cosmic"
USER_HOME="/home/${USER_NAME}"
REPO="tharinduudu/mppcInterface-Oct-2025"

log(){ printf "\n[%s] %s\n" "$(date '+%F %T')" "$*"; }
need_root(){ [[ $EUID -eq 0 ]] || { echo "Run with sudo"; exit 1; }; }
need_root

log "APT update + base packages"
apt-get update -y
apt-get install -y git build-essential curl ca-certificates pkg-config \
                   python3-pip python3-venv python3-dev i2c-tools

log "Put ${USER_NAME} in gpio/i2c/spi groups"
usermod -aG gpio,i2c,spi "${USER_NAME}" || true

# --- Enable I2C/SPI persistently ---
for f in /boot/config.txt /boot/firmware/config.txt; do
  [[ -f "$f" ]] || continue
  sed -i -E '/^\s*dtparam=i2c_arm=/d;/^\s*dtparam=spi=/d' "$f"
  grep -q '^dtparam=i2c_arm=on' "$f" || echo 'dtparam=i2c_arm=on' >> "$f"
  grep -q '^dtparam=spi=on'     "$f" || echo 'dtparam=spi=on'     >> "$f"
  grep -q '^dtoverlay=spi0-2cs' "$f" || echo 'dtoverlay=spi0-2cs' >> "$f"
done

log "Enable I2C/SPI immediately (no reboot)"
modprobe i2c-bcm2835 || true
modprobe i2c-dev      || true
modprobe spi_bcm2835  || true
modprobe spidev       || true

log "Fetch repo files to ${USER_HOME}"
sudo -u "${USER_NAME}" bash -lc 'cd ~ && rm -rf mppcInterface && \
  curl -L https://github.com/'"${REPO}"'/archive/refs/heads/main.tar.gz \
  | tar -xz -f - --strip-components=1 '"${REPO}"'-main/mppcInterface \
                     '"${REPO}"'-main/rc.local \
                     '"${REPO}"'-main/DataTransfer.sh \
                     '"${REPO}"'-main/Display.sh \
                     '"${REPO}"'-main/dac.py \
                     '"${REPO}"'-main/biasAdj.py'

# Make helper scripts handy
chmod 755 "${USER_HOME}/DataTransfer.sh" "${USER_HOME}/Display.sh"
chown "${USER_NAME}:${USER_NAME}" "${USER_HOME}/DataTransfer.sh" "${USER_HOME}/Display.sh"
chown "${USER_NAME}:${USER_NAME}" "${USER_HOME}/dac.py" "${USER_HOME}/biasAdj.py"

# --- Python libraries (CircuitPython & SMBus) ---
log "Install Python libs: Blinka, BME280, DACx5678, smbus2"
python3 -m pip install --upgrade pip
python3 -m pip install --upgrade adafruit-blinka adafruit-circuitpython-bme280 \
    adafruit-circuitpython-dacx5678 smbus2

# --- WiringPi from source ---
log "Install WiringPi"
sudo -u "${USER_NAME}" bash -lc 'cd ~ && rm -rf WiringPi && git clone --depth=1 https://github.com/WiringPi/WiringPi.git'
bash -lc "cd ${USER_HOME}/WiringPi && ./build"

# --- Build firmware helpers ---
build_dir(){ local d="$1"; log "Build: $d"; bash -lc "cd '$d' && make clean || true && make -j\$(nproc)"; }
build_dir "${USER_HOME}/mppcInterface/firmware/libraries/ice40"
build_dir "${USER_HOME}/mppcInterface/firmware/libraries/max1932"
build_dir "${USER_HOME}/mppcInterface/firmware/libraries/dac60508"
bash -lc "cd ${USER_HOME}/mppcInterface/firmware/libraries/slowControl && make clean || true && make -j\$(nproc) || true"
# Relink fallback (fixes library order)
bash -lc "cd ${USER_HOME}/mppcInterface/firmware/libraries/slowControl && rm -f main.o main && g++ -c main.cpp -std=c++11 -I. && g++ main.o -lwiringPi -o main"

# --- Install rc.local from repo & enable compat service ---
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
# Do not start it now; it will run on next boot without blocking.

# --- Install DataTransfer cron (every 6h, no redirects) ---
log "Crontab entry for DataTransfer.sh (every 6h)"
bash -lc '(crontab -u '"${USER_NAME}"' -l 2>/dev/null | grep -v -F "/home/'"${USER_NAME}"'/DataTransfer.sh"; echo "0 */6 * * * /home/'"${USER_NAME}"'/DataTransfer.sh") | crontab -u '"${USER_NAME}"' -'

# --- Make Display.sh executable (already) and owned by user ---
chmod 755 "${USER_HOME}/Display.sh"
chown "${USER_NAME}:${USER_NAME}" "${USER_HOME}/Display.sh"

# --- Final hints ---
echo
echo "✅ Install done. If /dev/i2c-1 or /dev/spidev0.0 are missing, please reboot: sudo reboot"
echo "To tail slowControl later: /home/${USER_NAME}/Display.sh"
