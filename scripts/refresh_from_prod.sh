#!/usr/bin/env bash
# refresh_from_prod.sh — пересобрать локальную БД Eduson из свежего снимка прода
# старой системы (Express + node:sqlite в Docker). Прод — источник правды на время
# переработки платформы; локальная база целиком пересобирается импортом.
#
#   bash scripts/refresh_from_prod.sh          # полная пересборка (задача 06:30), из platform/ или откуда угодно
#   bash scripts/refresh_from_prod.sh --soft   # мягкое обновление: без подмены базы (кнопка в «Синхро»)
#
# МЯГКИЙ РЕЖИМ (--soft, решение владельца 2026-10-20). Снимок и таблицы — те же, но
# вместо сборки <имя>_next и подмены — manage.py sync_eduson против рабочей базы в
# одной транзакции: обновляются только ученики, группы, уроки, отзывы и связанное с
# уроками; учётки, продления, настройки остаются. Соединения не рвутся. Ошибка — откат,
# база как была. Нужны связи со старой системой (таблица eduson_links), их пишет полная
# пересборка.
#
# Подробно: docs/eduson/refresh-from-prod.md. Конфиг — окружение или platform/.env
# (образец — .env.example, раздел «Обновление из прода»).
#
# ПРОД — ТОЛЬКО ЧТЕНИЕ. На сервере выполняются ровно три вещи: снимок через
# node:sqlite с readOnly + VACUUM INTO во временный файл контейнера, docker cp на
# хост и удаление этих двух временных файлов. Боевую базу ничто не открывает на запись.
#
# РАБОЧАЯ БАЗА НЕ ТРОГАЕТСЯ, ПОКА НОВАЯ НЕ ГОТОВА. Импорт идёт в <имя>_next; упал
# импорт или сверка — рабочая остаётся как была, _next — для разбора. Подмена —
# переименованием: рабочая → <имя>_prev (откат), _next → <имя>.
#
# ТАБЛИЦЫ EDUSON. Перед импортом — CSV вкладок «Июнь 2026» и «Июнь 2027» четырёх
# публичных Google-таблиц (EDUSON_SHEET_*): по ним import_eduson --sheets-dir заводит
# уроки до запуска старой системы. Не заданы ID или не скачалось — импорт идёт без
# таблиц, с предупреждением в лог: обновление из-за таблиц не валится.
#
# ВЫВОД И ЛОГ — без ПДн и секретов: ни адреса сервера, ни DATABASE_URL, ни почт
# --superadmin, ни ID таблиц. Отчёт импорта сам по себе без ПДн (только id Eduson, коды групп, числа).
set -Eeuo pipefail

MODE=full
case "${1:-}" in
  '') ;;
  --soft) MODE=soft ;;
  *) echo "использование: bash scripts/refresh_from_prod.sh [--soft]" >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { echo "использование: bash scripts/refresh_from_prod.sh [--soft]" >&2; exit 2; }
# Шагов: полная — снимок, скачивание, таблицы, migrate, импорт, подмена; мягкая — первые три + синк.
if [ "$MODE" = soft ]; then STEPS=4; else STEPS=6; fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # platform/
ENV_FILE="$ROOT/.env"
RETRIES=5          # SSH к серверу нестабилен («banner exchange timeout»)
RETRY_PAUSE=20
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=30 -o ServerAliveInterval=15 -o ServerAliveCountMax=4)

# ── конфиг ──────────────────────────────────────────────────────────────────

dotenv_get() {
  # Значение ключа из platform/.env: первая строка KEY=, без \r и внешних кавычек.
  [ -f "$ENV_FILE" ] || return 0
  local v
  v="$(grep -E "^[[:space:]]*$1=" "$ENV_FILE" | head -n1 | cut -d'=' -f2- | tr -d '\r' || true)"
  v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
  case "$v" in \"*\") v="${v:1:${#v}-2}" ;; \'*\') v="${v:1:${#v}-2}" ;; esac
  printf '%s' "$v"
}

conf() {
  # Окружение важнее .env — как у Django (read_env не перетирает заданные переменные).
  local v="${!1:-}"
  [ -n "$v" ] || v="$(dotenv_get "$1")"
  printf '%s' "${v:-$2}"
}

die() { log "ОШИБКА: $*"; exit 1; }

LOG_FILE=""
log() {
  local line
  line="$(date '+%Y-%m-%d %H:%M:%S') $*"
  printf '%s\n' "$line" >&2
  if [ -n "$LOG_FILE" ]; then printf '%s\n' "$line" >>"$LOG_FILE"; fi
}

SSH_TARGET="$(conf EDUSON_PROD_SSH '')"
CONTAINER="$(conf EDUSON_PROD_CONTAINER aiokk-web)"
PROD_DB="$(conf EDUSON_PROD_DB /app/server/data/okk.db)"
SNAP_DIR="$(conf EDUSON_SNAPSHOT_DIR ../data/prod-snapshots)"
KEEP="$(conf EDUSON_SNAPSHOT_KEEP 7)"
SUPERADMINS="$(conf EDUSON_SUPERADMINS '')"
DBURL="$(conf DATABASE_URL '')"

[ -n "$SSH_TARGET" ] || die "не задан EDUSON_PROD_SSH (user@host сервера старой системы) — в окружении или $ENV_FILE"
# Значения уходят в командную строку на сервере — только безопасные символы, без кавычек
# и пробелов: подстановка в удалённую команду не должна уметь ничего, кроме имени/пути.
# Первый символ — не «-»: иначе ssh/scp прочли бы значение как свою опцию.
[[ "$SSH_TARGET" =~ ^([A-Za-z0-9._][A-Za-z0-9._-]*@)?[A-Za-z0-9._][A-Za-z0-9._-]*$ ]] || die "EDUSON_PROD_SSH: ожидается user@host"
[[ "$CONTAINER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die "EDUSON_PROD_CONTAINER: недопустимое имя контейнера"
[[ "$PROD_DB" =~ ^/[A-Za-z0-9_./-]+$ ]] || die "EDUSON_PROD_DB: ожидается абсолютный путь внутри контейнера"
[[ "$KEEP" =~ ^[1-9][0-9]*$ ]] || die "EDUSON_SNAPSHOT_KEEP: ожидается целое ≥ 1"
[ -n "$DBURL" ] || die "не найден DATABASE_URL (окружение или $ENV_FILE)"

# Снимки — ПДн: только вне репозитория. Относительный путь — от platform/.
case "$SNAP_DIR" in /*|[A-Za-z]:*) ;; *) SNAP_DIR="$ROOT/$SNAP_DIR" ;; esac
# Проверка — до mkdir, чтобы отказ не оставлял пустой каталог в репозитории.
# cygpath: C:\… и C:/… → /c/…, иначе сравнение с ROOT (/c/…) промахнётся.
if command -v cygpath >/dev/null; then SNAP_DIR="$(cygpath -u "$SNAP_DIR")"; fi
SNAP_DIR="$(realpath -m "$SNAP_DIR")"
case "$SNAP_DIR/" in "$ROOT/"*) die "EDUSON_SNAPSHOT_DIR внутри репозитория — снимки с ПДн держим вне platform/" ;; esac
mkdir -p "$SNAP_DIR"
LOG_FILE="$SNAP_DIR/refresh.log"

# ── инструменты ─────────────────────────────────────────────────────────────

PSQL="${PSQL:-$(command -v psql || true)}"
PSQL="${PSQL:-/c/Program Files/PostgreSQL/15/bin/psql}"
[ -x "$PSQL" ] || command -v "$PSQL" >/dev/null || die "psql не найден: добавьте в PATH или задайте PSQL"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for p in "$ROOT/.venv/Scripts/python.exe" "$ROOT/.venv/Scripts/python" "$ROOT/.venv/bin/python"; do
    if [ -x "$p" ]; then PY="$p"; break; fi
  done
fi
[ -n "$PY" ] || die "не найден venv платформы ($ROOT/.venv) — задайте PYTHON"
for tool in ssh scp sha256sum curl; do command -v "$tool" >/dev/null || die "нет $tool в PATH (нужен Git Bash)"; done

native_path() {
  # Python под Windows не понимает /c/...: отдаём ему родной путь.
  if command -v cygpath >/dev/null; then cygpath -w "$1"; else printf '%s' "$1"; fi
}

# ── базы ────────────────────────────────────────────────────────────────────

DB_NAME="${DBURL##*/}"; DB_NAME="${DB_NAME%%\?*}"
DB_QUERY=""; case "$DBURL" in *\?*) DB_QUERY="?${DBURL#*\?}" ;; esac
DB_PREFIX="${DBURL%/*}"
# Защита БД KOTOKOD (journal*) и тестовых баз: пересобираем только рабочую eduson*.
# Имя подставляется в DDL как идентификатор — поэтому строго [a-z0-9_].
[[ "$DB_NAME" =~ ^eduson[a-z0-9_]*$ ]] || die "рабочая БД '$DB_NAME' не Eduson (имя обязано начинаться с eduson) — отказ"
[[ "$DB_NAME" =~ _(test|next|prev)$ ]] && die "рабочая БД '$DB_NAME' — служебная (_test/_next/_prev), отказ"
[ "${#DB_NAME}" -le 58 ] || die "имя БД '$DB_NAME' слишком длинное для суффиксов _next/_prev"
NEXT_DB="${DB_NAME}_next"
PREV_DB="${DB_NAME}_prev"
NEXT_URL="$DB_PREFIX/$NEXT_DB$DB_QUERY"
ADMIN_URL="$DB_PREFIX/postgres$DB_QUERY"

pg() { PGOPTIONS='-c client_min_messages=warning' "$PSQL" -X -q -v ON_ERROR_STOP=1 -d "$ADMIN_URL" "$@"; }
db_exists() { [ "$(pg -tAc "SELECT 1 FROM pg_database WHERE datname = '$1'")" = 1 ]; }

# ── ретраи SSH ──────────────────────────────────────────────────────────────

remote() { ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$1"; }

retry_ssh() {
  # Повтор только при сбое соединения (ssh → 255). Ошибку самой удалённой команды
  # (нет контейнера, нет файла) повтор не исправит — сразу наружу.
  local attempt=1 rc
  while :; do
    rc=0; remote "$1" || rc=$?
    if [ "$rc" -eq 0 ]; then return 0; fi
    if [ "$rc" -ne 255 ] || [ "$attempt" -ge "$RETRIES" ]; then return "$rc"; fi
    log "  ssh: соединение не удалось (попытка $attempt/$RETRIES), пауза ${RETRY_PAUSE} с"
    sleep "$RETRY_PAUSE"; attempt=$((attempt + 1))
  done
}

retry_scp() {
  # scp не различает сбой соединения и прочие ошибки — скачивание идемпотентно, повторяем любой.
  local attempt=1
  until scp -q "${SSH_OPTS[@]}" "$SSH_TARGET:$1" "$2"; do
    [ "$attempt" -lt "$RETRIES" ] || return 1
    log "  scp: не удалось (попытка $attempt/$RETRIES), пауза ${RETRY_PAUSE} с"
    sleep "$RETRY_PAUSE"; attempt=$((attempt + 1))
  done
}

# ── блокировка и уборка ─────────────────────────────────────────────────────

LOCK_DIR="$SNAP_DIR/.refresh.lock"
LOCKED=0
REMOTE_TMP=""
REMOTE_PENDING=0      # на сервере может лежать наш временный файл
LOCAL_PART=""
SHEETS_PART=""        # недокачанный каталог таблиц
IMPORT_OUT=""
SWAPPING=0            # идёт подмена баз: прерывание обязано вернуть рабочую под своё имя

cleanup() {
  local rc=$?
  set +e
  if [ "$REMOTE_PENDING" = 1 ]; then
    # Даже при ошибке: временный снимок на сервере — копия ПДн, оставлять нельзя.
    log "уборка: удаляю временный снимок на сервере"
    retry_ssh "docker exec $CONTAINER rm -f $REMOTE_TMP >/dev/null 2>&1; rm -f $REMOTE_TMP" \
      || log "ВНИМАНИЕ: временный файл на сервере не удалён ($REMOTE_TMP в контейнере и на хосте) — удалите вручную"
  fi
  if [ -n "$LOCAL_PART" ]; then rm -f "$LOCAL_PART" "$LOCAL_PART-shm"; fi
  if [ -n "$SHEETS_PART" ]; then rm -rf "$SHEETS_PART"; fi
  if [ -n "$IMPORT_OUT" ]; then rm -f "$IMPORT_OUT"; fi
  # Ctrl+C / TERM посреди подмены: trap срабатывает после psql, swap_recover из
  # обработчика ошибки подмены уже не позовётся — зовём здесь.
  if [ "$SWAPPING" = 1 ]; then swap_recover; fi
  if [ "$LOCKED" = 1 ]; then rm -rf "$LOCK_DIR"; fi
  if [ "$rc" -ne 0 ]; then log "обновление прервано (код $rc)"; fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  # Задача по расписанию и ручной запуск не должны пересобирать базу одновременно.
  # Блокировку умершего процесса (kill, перезагрузка) забираем себе.
  other="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [ -n "$other" ] && kill -0 "$other" 2>/dev/null; then
    die "обновление уже идёт (pid $other)"
  fi
  log "снимаю устаревшую блокировку (pid ${other:-?})"
  rm -rf "$LOCK_DIR"; mkdir "$LOCK_DIR"
fi
LOCKED=1
echo $$ >"$LOCK_DIR/pid"

# Москва — UTC+3 без перехода на летнее время (с 2014): не зависим от tzdata в Git Bash.
STAMP="$(date -u -d '+3 hours' '+%Y%m%d-%H%M')"
TODAY_MSK="$(date -u -d '+3 hours' '+%F')"
started=$SECONDS
if [ "$MODE" = soft ]; then TITLE="мягкое обновление"; else TITLE="обновление"; fi
log "=== $TITLE $DB_NAME из прода Eduson ($STAMP МСК) ==="

# ── 1. снимок на проде (только чтение) ──────────────────────────────────────

REMOTE_TMP="/tmp/okk-refresh-$(date -u '+%Y%m%d%H%M%S')-$$-$RANDOM.db"
# Аргументы node -e: process.argv[1] — боевая база, [2] — файл снимка. readOnly — боевую
# базу не открыть на запись; VACUUM INTO — согласованная копия с учётом WAL, в обычном
# (не WAL) режиме. Путь снимка — параметром, не склейкой SQL.
JS='const{DatabaseSync}=require("node:sqlite");const db=new DatabaseSync(process.argv[1],{readOnly:true});db.prepare("VACUUM INTO ?").run(process.argv[2]);db.close();'
# Повторный запуск после обрыва идёт с тем же именем: rm -f в начале делает его идемпотентным
# (VACUUM INTO в существующий файл отказывает).
SNAPSHOT_CMD="set -eu
trap 'docker exec $CONTAINER rm -f $REMOTE_TMP >/dev/null 2>&1 || true' EXIT
docker exec $CONTAINER rm -f $REMOTE_TMP
rm -f $REMOTE_TMP
docker exec $CONTAINER node --no-warnings -e '$JS' $PROD_DB $REMOTE_TMP
docker cp $CONTAINER:$REMOTE_TMP $REMOTE_TMP
chmod 600 $REMOTE_TMP
sha256sum $REMOTE_TMP"

log "1/$STEPS снимок на проде (readOnly + VACUUM INTO)"
REMOTE_PENDING=1
out="$(retry_ssh "$SNAPSHOT_CMD")" || die "снимок на проде не снят"
REMOTE_SHA="$(printf '%s\n' "$out" | awk 'END { print $1 }')"
[[ "$REMOTE_SHA" =~ ^[0-9a-f]{64}$ ]] || die "сервер не вернул sha256 снимка"

log "2/$STEPS скачивание"
LOCAL_PART="$SNAP_DIR/.okk-$STAMP.db.part"
retry_scp "$REMOTE_TMP" "$LOCAL_PART" || die "снимок не скачан"
retry_ssh "rm -f $REMOTE_TMP" || die "не удалось удалить временный снимок на сервере"
REMOTE_PENDING=0

LOCAL_SHA="$(sha256sum "$LOCAL_PART" | awk '{ print $1 }')"
[ "$LOCAL_SHA" = "$REMOTE_SHA" ] || die "sha256 скачанного снимка не совпал с серверным"
"$PY" -I -c '
import pathlib, sqlite3, sys
con = sqlite3.connect(pathlib.Path(sys.argv[1]).resolve().as_uri() + "?mode=ro", uri=True)
result = con.execute("PRAGMA quick_check").fetchone()[0]
con.close()
sys.exit(0 if result == "ok" else 1)
' "$(native_path "$LOCAL_PART")" || die "снимок не прошёл PRAGMA quick_check"

SNAPSHOT="$SNAP_DIR/okk-$STAMP.db"
rm -f "$LOCAL_PART-shm"
mv -f "$LOCAL_PART" "$SNAPSHOT"
LOCAL_PART=""
SIZE_BYTES="$(stat -c %s "$SNAPSHOT")"
SIZE_MB="$(awk -v b="$SIZE_BYTES" 'BEGIN { printf "%.1f", b / 1048576 }')"
log "  снимок $(basename "$SNAPSHOT"): $SIZE_MB МБ, sha256 $LOCAL_SHA"

# Ротация: имена с меткой времени сортируются хронологически.
mapfile -t old < <(find "$SNAP_DIR" -maxdepth 1 -name 'okk-*.db' -printf '%f\n' | sort -r | tail -n +"$((KEEP + 1))")
for f in "${old[@]}"; do
  rm -f "$SNAP_DIR/$f" "$SNAP_DIR/$f-shm" "$SNAP_DIR/$f-wal"
done
[ "${#old[@]}" -eq 0 ] || log "  удалено старых снимков: ${#old[@]} (храним $KEEP)"

# ── 1б. таблицы Eduson (необязательно) ──────────────────────────────────────

SHEET_COURSES=(scratch python vibe groups)
# «Имя вкладки|суффикс файла» — файлы {course}-{суффикс}.csv ждёт import_eduson --sheets-dir.
SHEET_TABS=("Июнь 2026|2026-06" "Июнь 2027|2027-06")
SHEET_HEADER='"Название группы"'
SHEETS_DIR=""
CURL_CT=""

retry_curl() {
  # Публичная выгрузка Google, без ключа. Повторы — как у scp: скачивание идемпотентно.
  # -f: HTTP-ошибка = неудача; без -S: текст ошибки curl в лог не идёт (в нём бывает URL с ID).
  local attempt=1
  until CURL_CT="$(curl -fsL --proto '=https' --max-time 60 -A 'Mozilla/5.0' -o "$2" -w '%{content_type}' "$1" 2>/dev/null)"; do
    [ "$attempt" -lt "$RETRIES" ] || return 1
    log "  таблицы: загрузка не удалась (попытка $attempt/$RETRIES), пауза ${RETRY_PAUSE} с"
    sleep "$RETRY_PAUSE"; attempt=$((attempt + 1))
  done
}

fetch_sheets() {
  # 0 — все 8 вкладок скачаны и проверены, каталог в SHEETS_DIR; иначе 1 (причина — в логе).
  # Зовётся из if: errexit внутри не действует, поэтому каждый шаг проверяется явно.
  local course key id tab suffix base html out first
  local -A ids=()
  local -a gids
  for course in "${SHEET_COURSES[@]}"; do
    key="EDUSON_SHEET_${course^^}"
    id="$(conf "$key" '')"
    if [ -z "$id" ]; then log "  не задан $key"; return 1; fi
    # ID уходит в URL — только символы ID Google, без «/?&#» и пробелов.
    if [[ ! "$id" =~ ^[A-Za-z0-9_-]+$ ]]; then log "  $key: недопустимые символы в ID таблицы"; return 1; fi
    ids[$course]="$id"
  done
  SHEETS_PART="$SNAP_DIR/.sheets-$STAMP.part"
  rm -rf "$SHEETS_PART" && mkdir -p "$SHEETS_PART" || return 1
  for course in "${SHEET_COURSES[@]}"; do
    base="https://docs.google.com/spreadsheets/d/${ids[$course]}"
    html="$SHEETS_PART/$course-htmlview.html"
    if ! retry_curl "$base/htmlview" "$html"; then log "  $course: список вкладок не скачан"; return 1; fi
    for tab in "${SHEET_TABS[@]}"; do
      suffix="${tab#*|}"; tab="${tab%%|*}"
      # gviz по ИМЕНИ вкладки при опечатке или без такой вкладки молча отдаёт ПЕРВУЮ
      # вкладку. Поэтому имя ищется в списке вкладок htmlview (items.push({name, pageUrl,
      # gid})), и CSV берётся по её gid: вкладка гарантированно существует и именно она.
      mapfile -t gids < <(grep -o "name: \"$tab\", pageUrl: \"[^\"]*\", gid: \"[0-9-]*\"" "$html" \
        | sed -E 's/.*gid: "([0-9-]+)"$/\1/')
      if [ "${#gids[@]}" -ne 1 ]; then log "  $course: вкладка «$tab» не найдена в списке вкладок"; return 1; fi
      out="$SHEETS_PART/$course-$suffix.csv"
      if ! retry_curl "$base/gviz/tq?tqx=out:csv&gid=${gids[0]}" "$out"; then
        log "  $course: вкладка «$tab» не скачана"; return 1
      fi
      # Нет доступа по ссылке — Google отвечает страницей входа (HTML), а не CSV.
      case "$CURL_CT" in text/csv*) ;; *) log "  $course: «$tab» — ответ не CSV (${CURL_CT:-без типа})"; return 1 ;; esac
      if head -c 512 "$out" | grep -qiE '<html|<!doctype'; then log "  $course: «$tab» — ответ HTML"; return 1; fi
      first="$(head -n1 "$out")"; first="${first#$'\xef\xbb\xbf'}"; first="${first%%,*}"; first="${first%$'\r'}"
      if [ "$first" != "$SHEET_HEADER" ]; then log "  $course: «$tab» — неожиданная шапка, это не вкладка месяца"; return 1; fi
    done
    rm -f "$html"
    # Вторая защита от «gviz отдал первую вкладку»: две разные вкладки не совпадают байт в байт.
    if cmp -s "$SHEETS_PART/$course-2026-06.csv" "$SHEETS_PART/$course-2027-06.csv"; then
      log "  $course: вкладки «Июнь 2026» и «Июнь 2027» совпадают — выгрузка не той вкладки"; return 1
    fi
  done
  SHEETS_DIR="$SNAP_DIR/sheets-$STAMP"
  rm -rf "$SHEETS_DIR" && mv "$SHEETS_PART" "$SHEETS_DIR" || { SHEETS_DIR=""; return 1; }
  SHEETS_PART=""
}

log "3/$STEPS таблицы Eduson (вкладки «Июнь 2026», «Июнь 2027»)"
if fetch_sheets; then
  log "  таблицы: $(basename "$SHEETS_DIR") (${#SHEET_COURSES[@]} × ${#SHEET_TABS[@]} вкладок)"
else
  if [ -n "$SHEETS_PART" ]; then rm -rf "$SHEETS_PART"; fi
  SHEETS_PART=""; SHEETS_DIR=""
  log "  ВНИМАНИЕ: импорт без --sheets-dir — уроки до запуска старой системы останутся в «пройдено до учёта»"
fi
# Каталоги таблиц — тоже ПДн: храним столько же, сколько снимков.
mapfile -t old < <(find "$SNAP_DIR" -maxdepth 1 -type d -name 'sheets-*' -printf '%f\n' | sort -r | tail -n +"$((KEEP + 1))")
for f in "${old[@]}"; do rm -rf "${SNAP_DIR:?}/$f"; done
[ "${#old[@]}" -eq 0 ] || log "  удалено старых каталогов таблиц: ${#old[@]} (храним $KEEP)"

# ── 2м. мягкий режим: синк в рабочую базу, без подмены ──────────────────────

if [ "$MODE" = soft ]; then
  log "4/$STEPS мягкое обновление $DB_NAME (sync_eduson --as-of $TODAY_MSK)"
  cd "$ROOT/journal_django"
  export PYTHONUTF8=1
  sync_args=("$(native_path "$SNAPSHOT")" --as-of "$TODAY_MSK")
  if [ -n "$SHEETS_DIR" ]; then sync_args+=(--sheets-dir "$(native_path "$SHEETS_DIR")"); fi
  IMPORT_OUT="$(mktemp)"
  rc=0
  DATABASE_URL="$DBURL" "$PY" manage.py sync_eduson "${sync_args[@]}" >"$IMPORT_OUT" 2>&1 || rc=$?
  cat "$IMPORT_OUT" >>"$LOG_FILE"
  if [ "$rc" -ne 0 ]; then
    tail -n 30 "$IMPORT_OUT" >&2
    rm -f "$IMPORT_OUT"; IMPORT_OUT=""
    die "мягкое обновление не прошло — транзакция откатана, база как была (вывод — в $LOG_FILE)"
  fi
  elapsed=$((SECONDS - started))
  {
    echo
    echo "Готово за $((elapsed / 60)) мин $((elapsed % 60)) с: $DB_NAME обновлена из снимка прода (мягко, без подмены)."
    echo "  снимок:  $SNAPSHOT ($SIZE_MB МБ)"
    if [ -n "$SHEETS_DIR" ]; then echo "  таблицы: $SHEETS_DIR"; else echo "  таблицы: не использованы (см. лог)"; fi
    grep -E "преподаватели:|учётки:|группы:|ученики:" "$IMPORT_OUT" || true
  } | tee -a "$LOG_FILE"
  rm -f "$IMPORT_OUT"; IMPORT_OUT=""
  log "=== мягкое обновление завершено за ${elapsed} с ==="
  exit 0
fi

# ── 2. сборка новой базы рядом с рабочей ────────────────────────────────────

log "4/$STEPS сборка $NEXT_DB: migrate"
pg -c "DROP DATABASE IF EXISTS $NEXT_DB WITH (FORCE)" -c "CREATE DATABASE $NEXT_DB"
cd "$ROOT/journal_django"
export PYTHONUTF8=1
DATABASE_URL="$NEXT_URL" "$PY" manage.py migrate --noinput 2>&1 | tee -a "$LOG_FILE" >/dev/null \
  || die "migrate в $NEXT_DB упал — рабочая база не тронута"

log "5/$STEPS импорт снимка (--as-of $TODAY_MSK)"
import_args=("$(native_path "$SNAPSHOT")" --as-of "$TODAY_MSK")
IFS=',' read -r -a admins <<<"$SUPERADMINS"
for a in "${admins[@]}"; do
  a="${a//[[:space:]]/}"
  if [ -n "$a" ]; then import_args+=(--superadmin "$a"); fi
done
if [ -n "$SHEETS_DIR" ]; then import_args+=(--sheets-dir "$(native_path "$SHEETS_DIR")"); fi
IMPORT_OUT="$(mktemp)"
rc=0
DATABASE_URL="$NEXT_URL" "$PY" manage.py import_eduson "${import_args[@]}" >"$IMPORT_OUT" 2>&1 || rc=$?
cat "$IMPORT_OUT" >>"$LOG_FILE"
if [ "$rc" -ne 0 ]; then
  tail -n 30 "$IMPORT_OUT" >&2
  rm -f "$IMPORT_OUT"; IMPORT_OUT=""
  die "импорт или сверка не прошли — рабочая база не тронута, $NEXT_DB оставлена для разбора (вывод — в $LOG_FILE)"
fi

# ── 3. подмена ──────────────────────────────────────────────────────────────

swap_recover() {
  # Подмена оборвалась: рабочая база не должна остаться без имени или закрытой для входа.
  set +e
  if ! db_exists "$DB_NAME" && db_exists "$PREV_DB"; then
    pg -c "ALTER DATABASE $PREV_DB RENAME TO $DB_NAME"
  fi
  if db_exists "$DB_NAME"; then pg -c "ALTER DATABASE $DB_NAME WITH ALLOW_CONNECTIONS true"; fi
  set -e
}

log "6/$STEPS подмена: $DB_NAME → $PREV_DB, $NEXT_DB → $DB_NAME"
if db_exists "$DB_NAME"; then
  SWAPPING=1
  # Сначала закрываем вход, потом рвём соединения (runserver, Celery переподключились бы
  # между terminate и RENAME); pg_terminate_backend(pid, 5000) ждёт завершения (PG 14+).
  if ! pg >/dev/null <<SQL
ALTER DATABASE $DB_NAME WITH ALLOW_CONNECTIONS false;
SELECT count(pg_terminate_backend(pid, 5000)) FROM pg_stat_activity
 WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS $PREV_DB WITH (FORCE);
ALTER DATABASE $DB_NAME RENAME TO $PREV_DB;
ALTER DATABASE $PREV_DB WITH ALLOW_CONNECTIONS true;
ALTER DATABASE $NEXT_DB RENAME TO $DB_NAME;
SQL
  then
    swap_recover
    SWAPPING=0
    die "подмена не удалась — рабочая база восстановлена под своим именем, $NEXT_DB оставлена"
  fi
else
  pg -c "ALTER DATABASE $NEXT_DB RENAME TO $DB_NAME" || die "переименование $NEXT_DB → $DB_NAME не удалось"
fi
SWAPPING=0

# ── итог ────────────────────────────────────────────────────────────────────

elapsed=$((SECONDS - started))
{
  echo
  echo "Готово за $((elapsed / 60)) мин $((elapsed % 60)) с: $DB_NAME пересобрана из снимка прода."
  echo "  снимок:  $SNAPSHOT ($SIZE_MB МБ)"
  echo "  sha256:  $LOCAL_SHA"
  echo "  откат:   $PREV_DB (см. docs/eduson/refresh-from-prod.md)"
  if [ -n "$SHEETS_DIR" ]; then echo "  таблицы: $SHEETS_DIR"; else echo "  таблицы: не использованы (см. лог)"; fi
  # Таблица сверки и отчёт импорта — без ПДн (id Eduson, коды групп, числа).
  awk '/^Сверка Eduson/ { on = 1 } on && /^$/ { exit } on' "$IMPORT_OUT"
  grep -E "курс 'groups'|package_size|из таблиц" "$IMPORT_OUT" || true
  echo "  ⚠ runserver/Celery перезапустить: старые соединения разорваны."
} | tee -a "$LOG_FILE"
rm -f "$IMPORT_OUT"; IMPORT_OUT=""
log "=== обновление завершено за ${elapsed} с ==="
