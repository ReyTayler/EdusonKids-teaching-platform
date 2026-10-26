#!/usr/bin/env bash
# Выкатка staging Eduson Kids на сервер журнала KOTOKOD (запуск с машины
# разработчика, Git Bash; нужен Docker Desktop и SSH-алиас сервера).
#
#   deploy/docker/deploy-staging.sh sync      # плейбук, конфиги, секреты, снимок базы → сервер
#   deploy/docker/deploy-staging.sh release   # собрать образы, привезти, перезапустить, убрать старые
#   deploy/docker/deploy-staging.sh status    # что запущено на сервере
#
# Порядок первого запуска:
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
PUBLIC_URL=https://$DOMAIN
KNOWLEDGE_MEDIA_ROOT=/srv/media
KNOWLEDGE_X_ACCEL_PREFIX=/internal-media/
REDIS_URL=
SMTP_HOST=
SMTP_USER=
SMTP_PASS=
SMTP_FROM=
TELEGRAM_BOT_TOKEN=
TELEGRAM_GENERAL_CHAT_ID=0
BOT_SERVICE_TOKEN=
PAYMENT_BALANCE_GUARD=false
EDUSON_PROD_REFRESH_BUTTON=false
EOF
    )
  fi
}

cmd_sync() {
  ensure_local_secrets
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
  if [ -n "$(git -C "$REPO" status --porcelain -- journal_django Dockerfile .dockerignore deploy/nginx deploy/docker/nginx)" ]; then
    echo "(в рабочей папке есть незакоммиченные правки — в образ они НЕ попадут, собирается коммит $tag)"
  fi
  local build_dir; build_dir="$(mktemp -d)"
  trap 'rm -rf "$build_dir"' RETURN
  git -C "$REPO" archive --format=tar HEAD | tar -xf - -C "$build_dir"

  say "Сборка образов локально (версия $tag, из коммита)"
  "$DOCKER" compose -f "$build_dir/deploy/docker/compose.staging.yml" build
  "$DOCKER" tag eduson-staging-app:latest "eduson-staging-app:$tag"
  "$DOCKER" tag eduson-staging-web:latest "eduson-staging-web:$tag"
  "$DOCKER" pull -q "$POSTGRES_IMAGE"

  say "Образы → сервер (docker save | gzip | ssh docker load)"
  "$DOCKER" save "eduson-staging-app:$tag" "eduson-staging-web:$tag" "$POSTGRES_IMAGE" | gzip -1 \
    | ssh "$HOST" "gunzip | docker load"

  say "Запуск версии $tag"
  ssh "$HOST" bash -s -- "$REMOTE" "$tag" "$KEEP_RELEASES" <<'REMOTE_SCRIPT'
set -euo pipefail
REMOTE="$1"; TAG="$2"; KEEP="$3"
cd "$REMOTE"
printf 'TAG=%s\n' "$TAG" > .env
docker compose up -d --wait --wait-timeout 300
# Конфиги nginx смонтированы файлами — после sync контейнер видит старые копии.
docker compose restart web
docker compose ps -a

# Снимок базы с ПДн нужен только для первой заливки пустой базы — после
# успешного старта удаляем его с сервера.
if [ -f seed/eduson.dump ] && docker compose exec -T db sh -c 'test ! -f "$PGDATA/RESTORE_FAILED"'; then
  rm -f seed/eduson.dump && echo "Снимок базы удалён с сервера (база уже залита)"
fi

# Хранить KEEP последних версий (для быстрого отката), остальные удалить.
for repo in eduson-staging-app eduson-staging-web; do
  docker images "$repo" --format '{{.CreatedAt}}\t{{.Tag}}' | sort -r | cut -f2 \
    | tail -n +$((KEEP + 1)) | while read -r old; do
        [ "$old" = "$TAG" ] || docker rmi "$repo:$old" || true
      done
done
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
  *) sed -n 2,21p "$0"; exit 1 ;;
esac
