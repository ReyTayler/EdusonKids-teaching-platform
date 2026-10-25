#!/bin/sh
# Первый запуск базы: если рядом лежит снимок seed/eduson.dump — залить его.
# Официальный образ postgres выполняет скрипты из /docker-entrypoint-initdb.d/
# один раз, на пустом томе. Нет снимка — база остаётся пустой, её создаст migrate.
set -eu

DUMP=/seed/eduson.dump
if [ -s "$DUMP" ]; then
  echo "10-restore-seed: заливаю $DUMP в $POSTGRES_DB"
  pg_restore --no-owner --no-privileges --exit-on-error \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$DUMP"
  echo "10-restore-seed: готово"
else
  echo "10-restore-seed: снимка нет — база пустая, схему создаст migrate"
fi
