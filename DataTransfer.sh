#!/usr/bin/env bash
set -o errexit
set -o nounset
set -o pipefail

echo "Copying files to Phys2 server"

# --- Source dirs ---
source_dir_muon_data="/home/cosmic/mppcinterface-oct-2022/firmware/libraries/slowControl"
source_dir_press_data="/home/cosmic/logs/bmelogs"
source_dir_bias_data="/home/cosmic/logs/tempcomp"

# --- Destination dirs ---
dest_dir_muon_data="/home/dsk3/xiaochun/Cosmic/tempTest/muonData"
dest_dir_press_data="/home/dsk3/xiaochun/Cosmic/tempTest/prsData"
dest_dir_bias_data="/home/dsk3/xiaochun/Cosmic/tempTest/biasData"

# --- Remote host/auth ---
source_host="131.96.55.85"
ssh_key="/home/cosmic/.ssh/id_rsa"
ssh_port="2998"
remote_user="xiaochun"

# --- Pick newest files with required extensions ---
# Newest .log from slowControl
file_muon="$(ls -1t "${source_dir_muon_data}"/*.log 2>/dev/null | head -n 1 || true)"
# Newest .csv from pressure logs
file_press="$(ls -1t "${source_dir_press_data}"/*.csv 2>/dev/null | head -n 1 || true)"
# Newest .csv from tempcomp (bias) logs
file_bias="$(ls -1t "${source_dir_bias_data}"/*.csv 2>/dev/null | head -n 1 || true)"

# --- Sanity checks ---
if [[ -z "${file_muon}" ]]; then
  echo "ERROR: No .log files found in ${source_dir_muon_data}" >&2
  exit 1
fi
if [[ -z "${file_press}" ]]; then
  echo "ERROR: No .csv files found in ${source_dir_press_data}" >&2
  exit 1
fi
if [[ -z "${file_bias}" ]]; then
  echo "ERROR: No .csv files found in ${source_dir_bias_data}" >&2
  exit 1
fi

echo "Selected files:"
echo "  muon (log):  ${file_muon}"
echo "  press (csv): ${file_press}"
echo "  bias  (csv): ${file_bias}"

# --- Copy ---
scp -P "${ssh_port}" -i "${ssh_key}" -- "${file_muon}"  "${remote_user}@${source_host}:${dest_dir_muon_data}"
scp -P "${ssh_port}" -i "${ssh_key}" -- "${file_press}" "${remote_user}@${source_host}:${dest_dir_press_data}"
scp -P "${ssh_port}" -i "${ssh_key}" -- "${file_bias}"  "${remote_user}@${source_host}:${dest_dir_bias_data}"

echo "Transfers completed."
