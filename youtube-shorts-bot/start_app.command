#!/bin/bash
# Çift tıklayarak YouTube Shorts Bot arayüzünü başlatır.
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Sanal ortam (.venv) bulunamadı. Önce README.md'deki kurulum adımlarını tamamlayın."
  read -p "Kapatmak için Enter'a basın..."
  exit 1
fi

source .venv/bin/activate
streamlit run app.py
