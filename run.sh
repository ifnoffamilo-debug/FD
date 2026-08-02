#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Создан .env. Заполните BOT_TOKEN, ADMIN_IDS и при необходимости MOONSHOT_API_KEY."
  exit 1
fi
python -m app.main
