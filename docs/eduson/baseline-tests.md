# Базовая линия тестов форка Eduson

Дата: 2026-10-16 (этап 1, задача 4: «схема с нуля из миграций + базовый прогон»).

## Схема из миграций

- `manage.py migrate --noinput` на пустой БД `eduson` (DROP/CREATE перед прогоном):
  все 147 миграций применились с первого раза, код возврата 0. **Правок миграций не понадобилось.**
  Ранние миграции KOTOKOD создают таблицы сами (`RunSQL` с DDL), на таблицы эпохи Express
  не опираются.
- Сиды, которые приезжают миграциями (в пустой БД появляются сами):
  `renewal_pipeline` (1) + `renewal_stage` (11) — `renewals.0002/0005/...`;
  `task_board` (1) + `task_stage` (3) — `taskboard.0003_seed_default_board`.
- `manage.py makemigrations --check --dry-run` → `No changes detected`
  (модели и миграции согласованы; предупреждения `fields.W342` про `ForeignKey(unique=True)`
  унаследованы от KOTOKOD и на схему не влияют).
- `bash scripts/recreate_test_db.sh` → `Done: eduson_test migrated.`

## Полный прогон

Команда (из `platform/journal_django`):

```
../.venv/Scripts/python -m pytest -q -p no:cacheprovider -rs
```

Результат после ревью (финальный):

| passed | failed | skipped | errors | всего собрано |
|-------:|-------:|--------:|-------:|--------------:|
| 2876   | 4      | 1       | 0      | 2881          |

Время — около 7,5 мин. До ревью два прогона подряд без пересоздания БД дали одинаково
2875 / 4 / 1 из 2880 — повторный прогон стабилен.

До закрытия пропусков (первый прогон задачи) было 2805 passed / 4 failed / 67 skipped из 2876.

Ожидавшиеся заранее падения `apps/finances/tests/test_fifo_inputs.py` (устаревший `_add_payment`)
**не воспроизводятся**: хелпер в снимке уже пишет `lessons_count`, тесты зелёные.

## После задачи 6 (удаление инструментов переноса данных KOTOKOD)

| passed | failed | skipped | errors | всего собрано |
|-------:|-------:|--------:|-------:|--------------:|
| 2772   | 4      | 1       | 0      | 2777          |

Падения и пропуск — те же (см. ниже). Разница с 2881:

- −110 — удалены файлы тестов целиком: загрузчики из Google-таблиц KOTOKOD
  (`apps/sync/tests/test_backfill_{teachers,groups,students,lessons,payments,payroll}.py`,
  `test_run_all.py`, `test_sheets_client.py`, `test_rows.py`, `test_dates.py`,
  `test_parse_time.py`) и импорт истории направлений из .xlsx
  (`apps/groups/tests/test_direction_history_importer.py`);
- −1 — в `apps/sync/tests/test_tasks.py` два теста удалённых задач заменены одним на
  `rebuild_counters_task`;
- +7 — в `apps/sync/tests/test_views.py` параметризованный тест: URL удалённых действий
  (`teachers`, `groups`, `students`, `lessons`, `payments`, `payroll`, `run-all`) отвечают 404.
  Остальные тесты вьюх перенацелены с `teachers` на `rebuild-counters`.

Почему удалено (спека §3.4): инструменты искали ученика по `full_name`
(`ON CONFLICT (full_name)`, `filter(full_name=...)`), а у Eduson уникальность имени снимается
(тёзки); к тому же они грузили данные из таблиц KOTOKOD. Служебные пересборки по данным БД
(`rebuild_*`, `check_plan_health`) оставлены. Из `requirements.txt` убраны
`google-api-python-client` и `google-auth` (других потребителей нет).

## После локального nginx Eduson (2026-10-18)

| passed | failed | skipped | errors | всего собрано |
|-------:|-------:|--------:|-------:|--------------:|
| 3068   | 0      | 3       | 0      | 3071          |

Время — около 8 мин. Появился `deploy/nginx/` форка: локальный конфиг
(`deploy/nginx/local/nginx.conf`, :8180 → runserver :8100), скрипт запуска и общий
сниппет статики. `test_upload_limits_in_sync.py` для локального конфига теперь
проходит по-настоящему (2 параметризации). Две параметризации прод-конфига
(`journal-eduson.conf`) пропускаются с причиной «прод-шаблон nginx Eduson — этап 4»
— только пока файла нет; положат шаблон — проверка включится без правки теста.
Падений больше нет; пропусков 3 = этот 2 + POSIX-права (ниже).

Решение координатора 2026-10-18 заменяет прежнее «пропуском не маскируем»: маскировать
нечего — локальный конфиг сверяется, а прод-файла физически нет до этапа 4
(`stage4-deploy-backlog.md`).

## Допустимые падения (4) — ИСТОРИЯ, закрыты 2026-10-18


`apps/knowledge/tests/test_upload_limits_in_sync.py::test_nginx_limit_covers_application_limit`,
все 4 параметризации (`files`/`images` × `прод`/`локальный`).

Причина: тест сверяет лимит загрузки Django с `client_max_body_size` в
`deploy/nginx/journal-kotokod.conf` и `deploy/nginx/local/nginx.conf`. Каталог `deploy/`
в форк намеренно не перенесён (спека `docs/specs/2026-10-15-eduson-fork-design.md`:
это прод-инфраструктура KOTOKOD), nginx в этапе 1 не используется. С миграциями,
сидами и изоляцией не связано.

Решение координатора: допустимы до этапа 4 (развёртывание) — тогда тест перенацелить на
конфиги nginx Eduson. Пропуском (`skip`) не маскируем: тест защищает от реального прод-инцидента.

## Допустимый пропуск (1)

`apps/knowledge/tests/test_images.py`: POSIX-права (`os.chmod`) на Windows не проверяются;
скип заложен в KOTOKOD, на Linux тест выполняется.

## Закрытые пропуски (66) — выдуманная «минимальная школа»

Тесты групп, членств, оплат и учеников брали «любую» строку из общей тестовой БД
(направление, преподавателя, активную группу, ученика, оплату с направлением, посещение)
и без неё делали `pytest.skip`. В KOTOKOD в `journal_test` лежали данные разработки;
`eduson_test` строится миграциями пустой.

Почему не сид в самой БД: тесты с `django_db(transaction=True)` (`directions`, `teachers`,
`teacher_spa/test_concurrent_submit`) после себя делают `flush` всех таблиц, и сид исчезал
до того, как до него дойдут (проверено опытом). KOTOKOD к тому же прямо требует не сеять
тестовую БД (шапка `test_concurrent_submit.py`). Поэтому (решение координатора, вариант 1):

- `ensure_minimal_school()` — `apps/core/tests/minimal_school.py`, рядом со своим тестом.
  Создаёт через ORM явно выдуманные данные: «Тест Направление 1», «Тест Преподаватель 1»,
  активную «Тест Группа 1» со слотом; «Тест Ученик 1» — активное членство в группе, урок
  90 мин с посещением (`lessons_done` = 1), оплата purchase (1 абонемент × 4000.00 = 4000.00,
  `lessons_count` = 4); «Тест Ученик 2» — без членства и оплат, для тестов, которые сами
  создают пару «группа + ученик» и ждут 201. Телефоны `7000000000X`, почта `@example.com`.
  Идемпотентен (`get_or_create` по фиксированным ключам).
- Хелперы бывших пропусков берут id детерминированно из генератора (`ensure_minimal_school()[...]`),
  без «LIMIT 1 из чужих данных». Строки живут в транзакции теста и откатываются вместе с ней:
  `eduson_test` после прогона остаётся пустой.
- Тесты, которые на пустом результате проходили впустую, усилены `assert result` перед
  проверками: фильтры членств по группе/ученику (репозиторий и API), формы
  `paid_by_direction`/`attended_by_direction` в `students`.
- Тест генератора: `apps/core/tests/test_minimal_school.py` (создаётся ровно то, что ищут
  бывшие пропуски, включая членство; второй ученик без членства и оплат; инварианты оплаты;
  идемпотентность; фейковость значений).

Вскрывшиеся ошибки в самих тестах (не в коде приложения), исправлены минимально:

- `groups/tests/test_groups_api.py`, `groups/tests/test_groups_repository.py` — `_cleanup_group`
  делал сырой `DELETE FROM groups`, оставляя `group_schedule_slots`: FK слотов в БД без
  `ON DELETE CASCADE` (каскад у модели — только ORM), и проверка ограничений в конце теста
  падала (9 ошибок). Теперь сначала удаляются слоты группы.
- `payments/tests/test_payments_constraints.py::test_backfill_does_not_touch_properly_tagged_payments`
  — сырой INSERT оплаты без `lessons_count` отстал от схемы (CHECK `payments_purchase_signs`).
  Добавлен `lessons_count = 12` (3 абонемента × 4 урока).

Багов в коде приложения бывшие пропуски не вскрыли.

## Изменённые миграции и сиды

Миграции и сиды БД — без изменений.
