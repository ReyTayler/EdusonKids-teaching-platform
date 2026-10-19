# Backfill runbook

Одноразовый импорт данных из Google Sheets в PostgreSQL перед cutover'ом (Phase 3 миграции).

## Полный прогон

```powershell
npm run backfill:all              # реальный прогон всех 7 сущностей
node scripts/backfill-all.js --dry-run   # без записи в PG
```

Печатает JSON-сводку в stdout, прогресс — в stderr.

## По отдельности

Порядок важен (FK-зависимости):

```powershell
npm run backfill:directions
npm run backfill:teachers
npm run backfill:tokens
npm run backfill:groups
npm run backfill:students         # включает group_memberships
npm run backfill:lessons          # включает lesson_attendance
npm run backfill:payroll
```

Все скрипты:
- Идемпотентны (повторный прогон → 0 inserts).
- Логируют прогресс в stderr, итоговый JSON — в stdout.
- Возвращают exit code 1 при любой ошибке.

## Что значит «skipped» в JSON-выводе

- `inserted` — новая строка вставлена.
- `updated` — существующая обновлена (изменились ключевые поля).
- `skipped` — строка уже соответствует БД, ничего не сделано.
- Для `lessons` и `payroll` также есть тех. категория `no_lesson` — payroll-строка, для которой нет соответствующего lesson в PG (например, lesson был пропущен из-за отсутствия группы).

## Известная потеря данных

- 21 группа из журналов отсутствует в листе «Список всех детей» → 12% уроков и 13% attendance скипаются. Решение принято: эти группы закрыты, история не критична. Если когда-то понадобятся — Sheets не удаляется до Phase 5, можно добавить ghost-groups вручную.

## Если что-то не сходится

1. Прогнать `--dry-run` для конкретной сущности и сверить количество.
2. Проверить колонку источника в Sheets.
3. Если Sheets правился после первого прогона — запустить ещё раз, увидеть `updated=N`.

## Откат

```powershell
PGPASSWORD=journal_dev_password psql -U journal -h localhost -d journal -c "
TRUNCATE payroll, lesson_attendance, lessons, group_memberships,
         group_schedule_slots, students, groups, tokens, teachers,
         directions RESTART IDENTITY CASCADE;
"
```

Полный re-import: `npm run backfill:all`.
