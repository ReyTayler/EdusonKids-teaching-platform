#!/bin/sh
# Первый запуск базы: если рядом лежит снимок seed/eduson.dump — залить его.
# Официальный образ postgres выполняет скрипты из /docker-entrypoint-initdb.d/
# один раз, на пустом томе, от пользователя postgres (uid 70 в alpine). Нет
# снимка — база остаётся пустой, её создаст migrate.
#
# Защита от «тихо пустой» и «наполовину залитой» базы:
#   - каталог /seed подключён, но недоступен postgres → ошибка, а не «снимка нет»;
#   - заливка упала → маркер RESTORE_FAILED в каталоге данных. После рестарта
#     контейнера init уже не повторится, но healthcheck видит маркер и держит
#     базу «нездоровой» → migrate и приложение не стартуют на мусоре.
set -eu

DUMP=/seed/eduson.dump
MARKER="$PGDATA/RESTORE_FAILED"

if [ -d /seed ] && { [ ! -x /seed ] || { [ -e "$DUMP" ] && [ ! -r "$DUMP" ]; }; }; then
  echo "10-restore-seed: ОШИБКА — /seed недоступен пользователю $(id -un) (uid $(id -u)); проверьте владельца" >&2
  touch "$MARKER"
  exit 1
fi

if [ -s "$DUMP" ]; then
  echo "10-restore-seed: заливаю $DUMP в $POSTGRES_DB"
  touch "$MARKER"
  pg_restore --no-owner --no-privileges --exit-on-error \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$DUMP"
  rm -f "$MARKER"
  echo "10-restore-seed: готово"
else
  echo "10-restore-seed: снимка нет — база пустая, схему создаст migrate"
fi
