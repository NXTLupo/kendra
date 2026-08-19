#!/usr/bin/env bash
set -euo pipefail
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y \
  git python3 python3-venv python3-pip build-essential cmake ninja-build pkg-config \
  libopenblas-dev sqlite3 alsa-utils ffmpeg curl jq docker.io docker-compose-plugin \
  i2c-tools minisign
printf '\nBase Pi packages installed. Camera packages may be installed separately with:\n'
printf '  sudo apt install -y python3-picamera2 python3-opencv\n'
