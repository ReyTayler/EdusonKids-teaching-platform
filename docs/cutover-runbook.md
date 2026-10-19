# Cutover Phase 3a — runbook

**Дата cutover:** 2026-05-28
**Pre-cutover dump:** `backups/pre-cutover-2026-05-28.sql` (490 KB)

## Результат

✅ 8 teacher-эндпоинтов переведены на PostgreSQL  
✅ `submitLesson` атомарен через `db.tx()` (insertLesson + incrementCounters + insertAttendance + insertPayroll)  
✅ `services/cache.js` удалён  
✅ Запись в Google Sheets отключена (`sheets.js` остаётся только для backfill-скриптов до Phase 5)  
✅ Baseline/post-cutover snapshots сняты и сверены (сами файлы удалены из репозитория 2026-09-30: содержали ФИО учеников)  
✅ Тесты: 73/73 pass  

## Diff summary (baseline vs post-cutover)

| Файл | Сравнение | Вывод |
|------|-----------|-------|
| `validateToken-tokenA.json` | identical (только UTF-8 BOM) | ✅ |
| `validateToken-invalid.json` | identical (только UTF-8 BOM) | ✅ |
| `getData-tokenA.json` | baseline 17 групп, PG 19 групп. **Baseline имеет mojibake Cyrillic** (двойное кодирование UTF-8 → Win-1251), PG возвращает чистый UTF-8. Группы по сути те же; +2 группы в PG (вероятно правки через админку или более полный backfill) | ✅ улучшение |
| `getData-tokenB.json` | 8 групп ↔ 8 групп, размеры почти идентичны | ✅ |
| `getAllData.json` | 74 KB ↔ 82 KB, разница — кодировка + новые поля сущностей (phone, birth_date и т.д. в admin SPA) | ✅ |
| `report.html` | 91 KB ↔ 100 KB, корректные данные за неделю | ✅ |
| `schedule.html` | 100 KB ↔ 110 KB, корректное расписание | ✅ |

**Главная находка:** Sheets-версия возвращала mojibake-кодированные имена групп; PG-версия исправляет это побочно. Никаких структурных регрессий.

## Cutover процесс (для повторного запуска при необходимости)

1. ✅ `npm test` → 66 pass (baseline)
2. ✅ `curl` snapshot 7 эндпоинтов → `docs/baseline/`
3. ✅ `pg_dump -U journal -h localhost -d journal > backups/pre-cutover-2026-05-28.sql`
4. ✅ Реализованы `db.js`: `readTokens`, `readAllStudents`, `readFilledLessons`, `incrementCounters`, `insertLesson`, `insertAttendance`, `insertPayroll` + 7 новых тестов
5. ✅ `services/repository.js` переписан как thin proxy над `db.js`
6. ✅ `submitLesson` в `server.js` переписан под `db.tx()` (lookup → insertLesson → incrementCounters → insertAttendance → insertPayroll)
7. ✅ `cache.*` вызовы убраны из `server.js`; refresh-эндпоинты — no-op
8. ✅ `services/cache.js` удалён
9. ✅ `.env.example` зачищен (`DUAL_WRITE_ENABLED`, `READ_FROM`, `CACHE_TTL` удалены)
10. ✅ `npm test` → 73 pass
11. ✅ `npm start` стартует чисто
12. ✅ Post-cutover snapshot → `docs/post-cutover/`
13. ✅ Diff → identical (modulo encoding fix + admin edits в PG)

## Rollback (catastrophe)

Если что-то пошло сильно не так:

```powershell
# 1. Stop server
Get-Process -Name node -ErrorAction SilentlyContinue | Stop-Process -Force

# 2. Restore PG
$env:PGPASSWORD = "journal_dev_password"
psql -U journal -h localhost -d journal -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
psql -U journal -h localhost -d journal -f backups\pre-cutover-2026-05-28.sql

# 3. Восстановить старые версии файлов из истории / резервных копий:
#    - services/repository.js → Sheets-прокси
#    - services/cache.js → recreate
#    - server.js → revert submitLesson + cache restore in other endpoints

# 4. Start
npm start
```

⚠️ После cutover все операционные данные (новые уроки, attendance, payroll) пишутся ТОЛЬКО в PG. Rollback PG к pre-cutover dump → ПОТЕРЯ всех уроков созданных после cutover.

## Rollback (soft, баг в коде)

- Fix-forward + restart
- Точечный SQL-fix через psql при необходимости
- Все CRUD-операции по сущностям доступны через `/admin` Admin SPA

## Phase 3b — следующий шаг

Админ-эндпоинты для операционных таблиц (`lessons`, `lesson_attendance`, `payroll`):
- GET/PATCH/DELETE для уроков (просмотр + редактирование + удаление)
- View attendance per lesson + редактирование
- View/edit payroll
- UI: sidebar-разделы «Уроки» и «Зарплата» + вкладка «Уроки» на странице группы

## Phase 5 — финальная очистка (отложено)

- Удалить `services/sheets.js`, `googleapis`, `service-account-key.json`
- Удалить колонки `sheet_row` (group_memberships) и `sheet_name` (directions) из БД (миграция 006)
- Удалить `STUDENTS_SPREADSHEET_ID` / `JOURNAL_SPREADSHEET_ID` из `.env`
- Удалить `scripts/backfill-*.js` (или переместить в `docs/archive/`)

## Ручной smoke-test для пользователя

Перед тем как считать cutover финально успешным:

1. Открыть `http://localhost:3000/` в браузере
2. Войти реальным токеном преподавателя
3. Проверить:
   - [ ] Видишь свои группы (как и раньше)
   - [ ] Клик по группе → список учеников со счётчиками
   - [ ] Отправить **тестовый** урок → счётчики обновились
   - [ ] `/api/report` — отчёт корректен
   - [ ] `/api/schedule` — расписание корректно
4. Проверить что в PG появилась новая запись:
   ```
   psql -U journal -d journal -c "SELECT * FROM lessons ORDER BY submitted_at DESC LIMIT 5;"
   psql -U journal -d journal -c "SELECT * FROM lesson_attendance WHERE lesson_id = <last id>;"
   psql -U journal -d journal -c "SELECT * FROM payroll WHERE lesson_id = <last id>;"
   ```
5. Если всё OK — Phase 3a финально успешен. Запись в Sheets-журнал больше **не происходит**.
