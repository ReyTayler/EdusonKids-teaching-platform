# DB Schema — миграции

| # | Файл | Что добавляет |
|---|------|--------------|
| 001 | `initial_schema.sql` | 11 таблиц |
| 002 | `backfill_keys.sql` | UNIQUE для idempotent backfill |
| 003 | `admin_soft_delete.sql` | `active` колонки |
| 004 | `directions_total_lessons.sql` | |
| 005 | `directions_color.sql` | `#RRGGBB` |
| 006 | `admin_user_settings.sql` | jsonb prefs per username (в Eduson не используется: раздел «Настройки» и приложение settings_app удалены 2026-10-19) |
| 007 | `directions_subscription_price.sql` | `numeric(10,2)` NULL = не настроен |
| 008 | `payments.sql` | immutable финансовые записи |
| 009 | `payments_legacy.sql` | direction/count nullable для backfill |
| 010 | `pagination_indexes.sql` | lessons_date_desc, payroll_lesson_id, payments_paid_at_desc |
| 011 | `discounts.sql` | name + amount(0..1) + active |
| 012 | `lesson_attendance_student_idx.sql` | индекс по student_id |
| 013 | `accounts.sql` | accounts + account_recovery_codes; email UNIQUE, password_hash, role, teacher_id, 2FA, lockout |
| 014 | `security_audit_log.sql` | журнал событий безопасности |
| ~~015~~ | ~~`students_consent.sql`~~ | ~~consent_* колонки (согласие на ПДн)~~ — **фича удалена 2026-07** (колонки сброшены Django-миграцией `students/0002`) |

## Важные особенности схемы

- **PK токенов = текст** (не serial)
- **`lesson_number` = numeric(5,1)** для полусчёта (1.5 на 45-минутках)
- **`group_schedule_slots`** — отдельная таблица, UNIQUE(group_id, day_of_week, start_time)
- **`enrollment_status` + `frozen_until_month` CHECK**: `((status='frozen') = (frozen_until_month IS NOT NULL))`
- **`submitted_by_token`** — text, не FK
- **Soft-delete**: `active=false` (teachers/groups/directions/tokens/discounts). Для students soft-delete удалён: `enrollment_status ∈ ('enrolled','frozen','declined')`, уход = `declined`
- **ON DELETE RESTRICT** на FK payments→students/directions
- **DATE type-parser** в `services/db.js`: `setTypeParser(1082, v => v)` — даты приходят строкой YYYY-MM-DD
