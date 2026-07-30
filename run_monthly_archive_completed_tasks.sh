#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/Telegram-VS-Cursor-CLI
source ./env.sh
export HOME=/home/ubuntu
export PATH="/home/ubuntu/.local/bin:$PATH"
exec .venv/bin/python monthly_archive_completed_tasks.py
