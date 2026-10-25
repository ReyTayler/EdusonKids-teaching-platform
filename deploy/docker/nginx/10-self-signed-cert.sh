#!/bin/sh
# Выпускает самоподписанный сертификат при первом старте контейнера nginx.
# Официальный образ nginx запускает все скрипты из /docker-entrypoint.d/ перед
# стартом сервера. Сертификат лежит в томе — при перезапуске не перевыпускается.
# Браузер покажет предупреждение «соединение не защищено»: для локального
# staging это ожидаемо (настоящий сертификат нужен только с реальным доменом).
set -eu

CERT_DIR=/etc/nginx/certs
if [ ! -s "$CERT_DIR/staging.crt" ]; then
  mkdir -p "$CERT_DIR"
  openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
    -keyout "$CERT_DIR/staging.key" -out "$CERT_DIR/staging.crt" 2>/dev/null
  echo "10-self-signed-cert: выпущен самоподписанный сертификат"
fi
