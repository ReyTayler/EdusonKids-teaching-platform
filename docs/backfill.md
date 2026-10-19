# Backfill: Sheets → PG

Все скрипты в `scripts/`. Запускать строго по порядку из-за FK.

## Порядок

| # | Скрипт | Источник Sheets | → PG |
|---|--------|----------------|------|
| 1 | `backfill-directions.js` | `Список всех детей` (S col) | `directions` |
| 2 | `backfill-teachers.js` | `Список всех детей` (L col) | `teachers` |
| 3 | `backfill-tokens.js` | Лист «Токены» (E/F col) | `tokens` |
| 4 | `backfill-groups.js` | `Список всех детей` (M col) | `groups` + `group_schedule_slots` |
| 5 | `backfill-students.js` | `Список всех детей` (A:T) | `students` + `group_memberships` |
| 6 | `backfill-lessons.js` | `Журнал группы/индивы` | `lessons` + `lesson_attendance` |
| 7 | `backfill-payroll.js` | `Зарплата` | `payroll` |
| 8 | `backfill-payments.js` | `Свод оплат` (отдельно!) | `payments` |

```bash
npm run backfill:all              # шаги 1-7
npm run backfill:all -- --dry-run

node scripts/backfill-payments.js --dry-run
node scripts/backfill-payments.js --yes
node scripts/backfill-payments.js --reset --yes   # перезалить
```

Payments **не входит** в `backfill-all`.

## Общие правила

- **Идемпотентны** через `ON CONFLICT` UPSERT.
- `--dry-run` — превью без записи; `--yes` — обязателен для боевого запуска.
- Чтение через `services/sheets.js` (service-account-key.json).
- JSON-отчёт в stdout: `{ inserted, updated, skipped, duration_ms }`.
- stderr для прогресса.

## Особенности скриптов

**`backfill-students.js`**: читает 11 полей (A,C,D,E,F,G,H,I,J,T) + статус по словарю. Ученики без teacher/group импортируются без membership, статус default `not_enrolled`.

**`backfill-groups.js`**: парсит день недели + время регуляркой (`scripts/lib/parse-time.js`). Несколько слотов → все пишутся в `group_schedule_slots`.

**`backfill-lessons.js`**: 21 группа из журналов отсутствует в листе учеников → ~12% уроков и 13% attendance скипаются (предупреждение в stderr). Принято.

**`backfill-payments.js`**:
- Источник: лист «Свод оплат» (A=имя, B=номер→note, C=сумма, D=дата DD.MM.YYYY, E=направление)
- Направление «Архив» → `direction_id=NULL`, `subscriptions_count=NULL` (миграция 009)
- Нормализация имени: lowercase + trim + ё→е + пробелы→один
- Нестандартные суммы: если делится нацело на `subscription_price` → `count=сумма/price`, иначе `count=1, unit_price=сумма`
- Защита от двойного запуска: `COUNT(*) WHERE created_by='backfill-script'` > 0 и нет `--reset` → отказ

## Прочие скрипты

| Скрипт | Назначение |
|--------|-----------|
| `db-truncate.js` | TRUNCATE всех таблиц кроме `directions` + `schema_migrations`. `--yes`. `payments` первым (FK RESTRICT) |
| `rebuild-payroll.js` | Пересчёт payroll из lessons + lesson_attendance |
| `rebuild-counters.js` | Пересчёт `lessons_done`/`remaining` в memberships (legacy) |
| `admin-set-password.js` | CLI: генерирует `ADMIN_PASSWORD_HASH` + `ADMIN_COOKIE_SECRET` для .env |
| `create-account.js` | Создать первый admin-аккаунт |
