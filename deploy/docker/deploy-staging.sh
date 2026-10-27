#!/usr/bin/env bash
# Выкатка staging Eduson Kids на сервер журнала KOTOKOD (запуск с машины
# разработчика, Git Bash; нужен Docker Desktop и SSH-алиас сервера).
#
#   deploy/docker/deploy-staging.sh sync      # плейбук, конфиги, секреты, снимок базы → сервер
#   deploy/docker/deploy-staging.sh release   # собрать образы, привезти, перезапустить, убрать старые
#                                             # (старые = вне журнала релизов $REMOTE/releases.log:
#                                             # KEEP последних тегов + текущий + пин BOT_TAG; при
#                                             # неудачной проверке перезапусков чистки не будет)
#   deploy/docker/deploy-staging.sh status    # что запущено на сервере
#
# sync кладёт на сервер три env-файла (секреты, каждый — только своему контейнеру):
#   app.env  — приложение (всё, кроме пароля базы);
#   db.env   — только пароль базы;
#   bot.env  — только то, что нужно Telegram-боту (токен бота и секрет журнал↔бот).
#
# Порядок первого запуска (и при КАЖДОМ изменении compose или конфигов: compose.yml
# на сервере обновляет только плейбук, release его не копирует):
#   1) sync
#   2) на сервере: cd /opt/eduson-staging/src/deploy/ansible-staging
#                  /opt/ansible/bin/ansible-playbook site.yml --check --diff  → показать владельцу
#                  /opt/ansible/bin/ansible-playbook site.yml                 → с его согласия
#   3) release
#
# Скрипт трогает на сервере только /opt/eduson-staging.
#
# ⚠️ Секреты сервера живут ТОЛЬКО локально в deploy/docker/.env.server (вне git).
# Если файл потерян и сгенерирован заново, новый пароль базы не совпадёт с
# паролем в уже созданном томе pgdata — приложение не подключится. Тогда либо
# вернуть старый файл, либо пересоздать базу (down -v на сервере + sync + release).
set -euo pipefail

HOST="${EDUSON_STAGING_HOST:-kotokod-vps}"
REMOTE=/opt/eduson-staging
DOMAIN=eduson-staging.develop-kotokod.ru
KEEP_RELEASES=3
POSTGRES_IMAGE=postgres:15-alpine
REDIS_IMAGE=redis:7.4-alpine            # брокер Celery и кэш; в образах не собирается, только тянется
POSTGRES_UID=70                         # пользователь postgres в образе alpine

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SECRETS="$HERE/.env.server"            # секреты сервера — только локально, вне git
DOCKER="${DOCKER:-/c/Program Files/Docker/Docker/resources/bin/docker.exe}"
[ -x "$DOCKER" ] || DOCKER=docker

say() { printf '\n== %s\n' "$*"; }
rnd() { openssl rand -hex "$1"; }
# Файл, сохранённый в Блокноте, получает CRLF — \r не должен попасть в пароли.
clean() { tr -d '\r' < "$1"; }
# Значение ключа из файла секретов; пусто, если ключа нет (и это не ошибка скрипта).
# Значения никуда не печатаются — только передаются дальше в ssh.
secret() { { clean "$SECRETS" | grep "^$1=" || true; } | tail -n1 | cut -d= -f2-; }
# Обязательный секрет: если его нет, sync останавливается ДО передачи чего-либо на сервер.
require_secret() {
  if [ -z "$(secret "$1")" ]; then
    # Подсказка — что вписать. Значения самих секретов здесь не выводятся.
    local hint
    case "$1" in
      REDIS_URL)          hint='значение: redis://redis:6379/0' ;;
      BOT_SERVICE_TOKEN)  hint='сгенерировать: openssl rand -hex 32' ;;
      TELEGRAM_BOT_TOKEN) hint='токен от @BotFather (тот же уходит боту как BOT_TOKEN)' ;;
      *)                  hint='' ;;
    esac
    echo "В $SECRETS не задан $1 — заполните его и повторите sync.${hint:+ ($hint)}" >&2
    exit 1
  fi
}

ensure_local_secrets() {
  if [ ! -s "$SECRETS" ]; then
    say "Генерирую секреты сервера: $SECRETS"
    local pw; pw="$(rnd 24)"
    (umask 077 && cat > "$SECRETS" <<EOF
POSTGRES_PASSWORD=$pw
DATABASE_URL=postgresql://eduson:$pw@db:5432/eduson_staging
SECRET_KEY=$(rnd 64)
ADMIN_COOKIE_SECRET=$(rnd 64)
ALLOWED_HOSTS=$DOMAIN,127.0.0.1
CSRF_TRUSTED_ORIGINS_LIST=https://$DOMAIN
CORS_ORIGINS=
KNOWLEDGE_MEDIA_ROOT=/srv/media
KNOWLEDGE_X_ACCEL_PREFIX=/internal-media/
REDIS_URL=redis://redis:6379/0
SMTP_HOST=
SMTP_USER=
SMTP_PASS=
SMTP_FROM=
TELEGRAM_BOT_TOKEN=
TELEGRAM_GENERAL_CHAT_ID=0
BOT_SERVICE_TOKEN=$(rnd 32)
PAYMENT_BALANCE_GUARD=false
EDUSON_PROD_REFRESH_BUTTON=false
EOF
    )
  fi
}

cmd_sync() {
  ensure_local_secrets
  # Проверка до первой передачи на сервер: без них бот или Celery не заработают.
  require_secret TELEGRAM_BOT_TOKEN
  require_secret BOT_SERVICE_TOKEN
  require_secret REDIS_URL
  [ -s "$HERE/seed/eduson.dump" ] || { echo "Нет снимка базы $HERE/seed/eduson.dump"; exit 1; }

  say "Код плейбука и конфиги → $HOST:$REMOTE/src"
  ssh "$HOST" "mkdir -p $REMOTE/src"
  (cd "$REPO" && tar -cf - deploy/ansible-staging deploy/docker/compose.server.yml \
      deploy/docker/nginx deploy/docker/db-init) \
    | ssh "$HOST" "rm -rf $REMOTE/src/deploy && tar --no-same-owner -xf - -C $REMOTE/src"

  say "Секреты → $REMOTE/app.env и $REMOTE/db.env (600, только root)"
  # Контейнер базы получает только свой пароль, приложение — всё, кроме него.
  clean "$SECRETS" | grep -v '^POSTGRES_PASSWORD=' \
    | ssh "$HOST" "umask 077 && cat > $REMOTE/app.env"
  clean "$SECRETS" | grep '^POSTGRES_PASSWORD=' \
    | ssh "$HOST" "umask 077 && cat > $REMOTE/db.env"

  say "Секреты бота → $REMOTE/bot.env (600) — только то, что нужно боту"
  {
    printf 'BOT_TOKEN=%s\n' "$(secret TELEGRAM_BOT_TOKEN)"
    printf 'BOT_SERVICE_TOKEN=%s\n' "$(secret BOT_SERVICE_TOKEN)"
    printf 'JOURNAL_API_URL=http://app:8000\nJOURNAL_HOST=%s\nTIMEZONE=Europe/Moscow\nLOG_LEVEL=INFO\n' "$DOMAIN"
  } | ssh "$HOST" "umask 077 && cat > $REMOTE/bot.env"

  say "Снимок базы → $REMOTE/seed (владелец — postgres контейнера, uid $POSTGRES_UID)"
  ssh "$HOST" "mkdir -p $REMOTE/seed && umask 077 && cat > $REMOTE/seed/eduson.dump \
    && chown -R $POSTGRES_UID:$POSTGRES_UID $REMOTE/seed && chmod 700 $REMOTE/seed && chmod 600 $REMOTE/seed/eduson.dump" \
    < "$HERE/seed/eduson.dump"

  say "Готово. Дальше — плейбук на сервере (--check --diff), затем: $0 release"
}

cmd_release() {
  local tag; tag="$(git -C "$REPO" rev-parse --short HEAD)"
  # Образ собирается из чистой выгрузки коммита (git archive), а не из рабочей
  # папки: в ней бывают чужие незакоммиченные правки, и в образ попало бы то,
  # чего нет ни в одном коммите. Так версия на сервере = ровно коммит $tag.
  if [ -n "$(git -C "$REPO" status --porcelain -- journal_django bot Dockerfile .dockerignore deploy/nginx deploy/docker/nginx)" ]; then
    echo "(в рабочей папке есть незакоммиченные правки — в образ они НЕ попадут, собирается коммит $tag)"
  fi
  local build_dir; build_dir="$(mktemp -d)"
  trap 'rm -rf "$build_dir"' RETURN
  git -C "$REPO" archive --format=tar HEAD | tar -xf - -C "$build_dir"
  # Compose проверяет наличие env-файлов даже при сборке, а в коммите их нет (секреты вне git).
  # Пустые заглушки — только в этом временном каталоге: образы копируют свои каталоги
  # поимённо и содержимое не читают, на сервер заглушки не уходят.
  : > "$build_dir/deploy/docker/.env.staging"
  : > "$build_dir/deploy/docker/bot.env"

  # --profile bot: образ бота собирается только этой командой (сервис вне профиля
  # в обычной сборке не участвует). Остальные образы — из того же контекста.
  say "Сборка образов локально (версия $tag, из коммита)"
  "$DOCKER" compose -f "$build_dir/deploy/docker/compose.staging.yml" --profile bot build
  "$DOCKER" tag eduson-staging-app:latest "eduson-staging-app:$tag"
  "$DOCKER" tag eduson-staging-web:latest "eduson-staging-web:$tag"
  "$DOCKER" tag eduson-staging-bot:latest "eduson-staging-bot:$tag"
  "$DOCKER" pull -q "$POSTGRES_IMAGE"
  "$DOCKER" pull -q "$REDIS_IMAGE"

  say "Образы → сервер (docker save | gzip | ssh docker load)"
  "$DOCKER" save "eduson-staging-app:$tag" "eduson-staging-web:$tag" "eduson-staging-bot:$tag" \
    "$REDIS_IMAGE" "$POSTGRES_IMAGE" | gzip -1 \
    | ssh "$HOST" "gunzip | docker load"

  say "Запуск версии $tag"
  ssh "$HOST" bash -s -- "$REMOTE" "$tag" "$KEEP_RELEASES" <<'REMOTE_SCRIPT'
set -euo pipefail
REMOTE="$1"; TAG="$2"; KEEP="$3"
cd "$REMOTE"
# BOT_TAG из прежнего .env — пин образа бота для отката (см. шапку compose.server.yml).
# Читаем ДО перезаписи: новый .env пишется только с TAG, то есть релиз снимает пин.
# Образ с этим тегом чистка ниже не удаляет. Нет файла или ключа — пусто.
PINNED_BOT_TAG="$(sed -n 's/^BOT_TAG=//p' .env 2>/dev/null | tail -n1 | tr -d '\r' || true)"
printf 'TAG=%s\n' "$TAG" > .env
# Счётчики перезапусков ДО up: RestartCount копится за всё время жизни контейнера,
# поэтому проверка ниже смотрит на прирост, а не на абсолютное значение.
restart_counts() {
  for id in $(docker compose ps -a -q); do
    echo "$id $(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}} {{.RestartCount}}' "$id")"
  done
}
BEFORE="$(restart_counts || true)"
# --wait ждёт только состояние «running» (у worker, beat и bot нет healthcheck), то есть
# цикл перезапусков он не ловит. Это проверяет блок «Перезапуски» ниже.
# Сервис, упавший насовсем, не даёт зависнуть: по таймауту 300 с команда завершится с ошибкой.
docker compose up -d --wait --wait-timeout 300 --remove-orphans
# Конфиги nginx смонтированы файлами — после sync контейнер видит старые копии.
docker compose restart web
docker compose ps -a

# Снимок базы с ПДн нужен только для первой заливки пустой базы — после
# успешного старта удаляем его с сервера.
if [ -f seed/eduson.dump ] && docker compose exec -T db sh -c 'test ! -f "$PGDATA/RESTORE_FAILED"'; then
  rm -f seed/eduson.dump && echo "Снимок базы удалён с сервера (база уже залита)"
fi

# Перезапуски. --wait их не видит, поэтому смотрим RestartCount через 20 с и сравниваем
# с базой по ID контейнера (см. BEFORE выше): старый перезапуск неизменного сервиса
# релиз не валит, новый — валит. Любой новый перезапуск — неудача: чистку версий не
# делаем, чтобы откат был под рукой.
sleep 20
restarted=0
while read -r id svc count; do
  base="$(printf '%s\n' "$BEFORE" | awk -v i="$id" '$1 == i {print $3}')"
  base="${base:-0}"
  echo "перезапусков: $svc $count (было $base)"
  if [ "$count" -gt "$base" ]; then
    echo "сервис перезапускается — смотрите: docker compose logs $svc"
    restarted=1
  fi
done < <(restart_counts)
[ "$restarted" -eq 0 ] || { echo "Релиз $TAG не принят: есть перезапускающиеся сервисы."; exit 1; }

# Чистка версий — по журналу релизов, а не по дате образа. При сборке из кэша у всех
# тегов одна дата: сортировка по дате могла оставить три «больших хеша» и удалить тег
# предыдущего релиза, и откат ломался бы. Журнал — по одному тегу на строку, в порядке
# выкатки. Текущий тег дописываем, если он ещё не последний (повтор того же коммита — без дубля).
[ "$(tail -n1 releases.log 2>/dev/null || true)" = "$TAG" ] || echo "$TAG" >> releases.log
uniq_tags() { tac releases.log | awk '!seen[$0]++'; }   # уникальные, от новых к старым
# Пока в журнале меньше KEEP уникальных тегов, чистку не делаем: теги от времени до появления
# журнала могут быть последними рабочими версиями, их не трогаем.
if [ "$(uniq_tags | wc -l)" -lt "$KEEP" ]; then
  echo "журнал релизов короче $KEEP — чистка версий пропущена"
else
  # Оставляем: KEEP последних тегов журнала, текущий и пин образа бота (BOT_TAG прежнего .env).
  # Занятые контейнерами образы docker rmi всё равно не удалит — это ошибка, она проглатывается.
  keep="$(uniq_tags | awk -v n="$KEEP" 'NR <= n')"$'\n'"$TAG"$'\n'"$PINNED_BOT_TAG"
  for repo in eduson-staging-app eduson-staging-web eduson-staging-bot; do
    docker images "$repo" --format '{{.Tag}}' | while read -r old; do
      [ -n "$old" ] || continue
      if printf '%s\n' "$keep" | grep -qxF "$old"; then continue; fi
      echo "удаляю образ $repo:$old (вне журнала релизов)"
      docker rmi "$repo:$old" || true
    done
  done
fi
docker image prune -f >/dev/null
docker system df
REMOTE_SCRIPT

  say "Проверка снаружи"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/health")"
  echo "https://$DOMAIN/health → $code"
  [ "$code" = 200 ] || { echo "Staging не отвечает 200 — смотрите: $0 status"; exit 1; }
}

cmd_status() {
  ssh "$HOST" "cd $REMOTE && cat .env 2>/dev/null; docker compose ps -a; docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}'; free -m"
}

case "${1:-}" in
  sync) cmd_sync ;;
  release) cmd_release ;;
  status) cmd_status ;;
  *) sed -n '2,/^set -euo pipefail/{/^set -euo/!p}' "$0"; exit 1 ;;
esac
