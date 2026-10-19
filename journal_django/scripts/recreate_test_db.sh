#!/usr/bin/env bash
# recreate_test_db.sh — (пере)создать eduson_test из миграций (форк Eduson).
# Только eduson*_test; БД KOTOKOD (journal*) не трогает. .env — platform/.env.
set -euo pipefail
PSQL="${PSQL:-/c/Program Files/PostgreSQL/15/bin/psql}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # platform/
# Имя берётся так же, как в config/settings/test.py: окружение → .env → default.
# Иначе при расхождении DROP/CREATE ушёл бы в одну БД, а migrate — в другую.
if [ -z "${TEST_DB_NAME:-}" ]; then
  TEST_DB_NAME="$(grep -E '^TEST_DB_NAME=' "$ROOT/.env" | head -n1 | cut -d'=' -f2- | tr -d '\r\n' || true)"
fi
TEST_DB="${TEST_DB_NAME:-eduson_test}"
# Белый список имён форка — тот же шаблон, что в config/settings/test.py. Он же
# защищает SQL ниже: имя подставляется в DROP/CREATE как идентификатор, поэтому
# допускаем только [a-z0-9_], а тестовые БД KOTOKOD (journal_test) не проходят.
[[ "$TEST_DB" =~ ^eduson[a-z0-9_]*_test$ ]] \
  || { echo "refuse: '$TEST_DB' is not an Eduson test DB (eduson*_test)" >&2; exit 1; }
export TEST_DB_NAME="$TEST_DB"
# Окружение важнее .env — как у Django (read_env не перетирает заданные переменные).
DBURL="${DATABASE_URL:-$(grep -E '^DATABASE_URL=' "$ROOT/.env" | head -n1 | cut -d'=' -f2- | tr -d '\r\n' || true)}"
[ -n "$DBURL" ] || { echo "DATABASE_URL not found (env or $ROOT/.env)" >&2; exit 1; }
ADMIN_URL="$(echo "$DBURL" | sed -E 's#/[^/]+$#/postgres#')"
printf 'DROP DATABASE IF EXISTS %s;\nCREATE DATABASE %s;\n' "$TEST_DB" "$TEST_DB" \
  | "$PSQL" -v ON_ERROR_STOP=1 -d "$ADMIN_URL"
cd "$ROOT/journal_django"
DJANGO_SETTINGS_MODULE=config.settings.test "$ROOT/.venv/Scripts/python" manage.py migrate --noinput
echo "Done: $TEST_DB migrated."
