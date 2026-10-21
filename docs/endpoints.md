# API Endpoints

## Auth (`/api/auth/*`) — публично, кроме `/me` и `/2fa/disable`

| Метод | Путь | Назначение |
|-------|------|------------|
| POST | `/api/auth/login` | email+пароль → сессия или 2FA-challenge. Rate-limited |
| POST | `/api/auth/login/2fa` | Подтверждение 2FA → Set-Cookie `session`. Rate-limited |
| POST | `/api/auth/2fa/email/send` | Отправить email-OTP. Rate-limited |
| POST | `/api/auth/2fa/setup` | Сгенерировать TOTP-secret + QR |
| POST | `/api/auth/2fa/enable` | Включить TOTP → recovery-codes |
| POST | `/api/auth/2fa/disable` | Отключить 2FA (за сессией) |
| POST | `/api/auth/logout` | Очистить cookie |
| GET  | `/api/auth/me` | `{ me: {account_id, role, ...} }` |

## Teacher SPA (`/api/*`) — requireRole('teacher')

| Метод | Путь | Назначение |
|-------|------|------------|
| POST | `/api/getData` | Группы и ученики текущего препода |
| POST | `/api/submitLesson` | Отправка урока (lesson + attendance + payroll) |
| POST | `/api/getAllData` | Все группы всех преподов (для замен) |
| POST | `/api/refreshData` | Сброс кеша |
| GET  | `/api/report` | Сводный отчёт по текущей неделе |
| GET  | `/api/schedule` | Расписание всех групп |

## Admin SPA (`/api/admin/*`) — requireRole('manager','admin')

### Accounts и Audit — дополнительно requireRole('admin')

| Метод | Путь | Назначение |
|-------|------|------------|
| GET/POST | `/api/admin/accounts` | Список / создать |
| GET | `/api/admin/accounts/:id` | Один аккаунт |
| POST | `/api/admin/accounts/:id/reset-password` | Сброс пароля |
| POST | `/api/admin/accounts/:id/reset-2fa` | Сброс 2FA |
| GET | `/api/admin/audit-log` | security_audit_log |

### Changelog — журнал изменений данных (только admin)

Захват — триггеры django-pghistory (спека `docs/superpowers/specs/2026-07-06-changelog-design.md`).

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/admin/changelog?page=&page_size=&filter[k]=v` | Лента операций `{rows,total,page,page_size}`; фильтры: `actor`, `operation`, `entity`, `entity_id`, `date_from`, `date_to` |
| GET | `/api/admin/changelog/:context_id` | Детали операции: события с diff «было/стало» |
| POST | `/api/admin/changelog/:context_id/revert` | Откат операции целиком; 409 — конфликт (данные менялись позже), 400 — неоткатываемая (accounts/пустая); пишет `changelog_revert` в security_audit_log |

### Students (paginated)

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/admin/students?page=&sort_by=&filter[k]=v` | `{rows,total,page,page_size}` |
| GET | `/api/admin/students/:id` | Один ученик |
| GET | `/api/admin/students/:id/stats` | Статистика посещаемости |
| GET | `/api/admin/students/:id/balance` | Баланс (payments - attended) |
| POST | `/api/admin/students` | Создать |
| PATCH | `/api/admin/students/:id` | Обновить |

Статуса у ученика нет: `enrollment_status` и даты заморозки удалены (миграция
students/0016). «Статус» — стадия последней сделки продления, она приезжает в
каждой строке ученика (`stage`, `stage_is_open`, `stage_frozen_until_month`) и
фильтруется через `filter[stage_id]`. Заморозка и уход оформляются переходом
сделки в разделе «Продления» (см. ниже), без каскадов по членствам и расписанию.

### Renewals (сделки продления)

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/admin/renewals/:id/move` | Перевод стадии. Для стадии `frozen` обязателен `frozen_until_month` (`YYYY-MM-01`), при уходе с неё месяц обнуляется |
| POST | `/api/admin/renewals/:id/unfreeze` | «Вернуть в работу»: со стадии `frozen` на расчётную авто-стадию. 409, если сделка не заморожена |
| POST | `/api/admin/renewals/:id/reopen` | Переоткрыть закрытую сделку |

### Groups (paginated, join direction/teacher/slots)

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/admin/groups?page=&...` | Paginated + direction_name, direction_color, teacher_name, slots |
| GET | `/api/admin/groups/:id` | Один + slots |
| POST | `/api/admin/groups` | Создать (slots в одной tx) |
| PATCH | `/api/admin/groups/:id` | Update (slots replace в tx) |
| DELETE | `/api/admin/groups/:id` | Soft (active=false) |

### Lessons (paginated)

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/admin/lessons?filter[group_id]=&filter[date_from]=` | Paginated |
| GET | `/api/admin/lessons/:id` | Полный урок + attendance + payroll |
| POST | `/api/admin/lessons` | Создать (attendance; зарплату считает движок; без присутствующих → 400) |
| PATCH | `/api/admin/lessons/:id` | Дата/тип/url |
| DELETE | `/api/admin/lessons/:id` | Hard (cascade attendance + payroll) |
| PATCH | `/api/admin/lessons/:lessonId/attendance/:studentId` | Toggle present |

### Payroll (paginated)

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/admin/payroll?filter[date_from]=&filter[date_to]=` | Paginated |
| GET | `/api/admin/payroll/summary?teacher_id=&date_from=&date_to=` | Агрегация за период |
| GET | `/api/admin/payroll?filter[kind]=lesson\|cancellation` | Строки уроков и отмен (`kind` + `id`) |
| PATCH | `/api/admin/payroll/:id` | Ручная правка payment/penalty урока → `is_manual` |
| POST | `/api/admin/payroll/:id/reset` | Сбросить ручную правку урока, пересчитать |
| PATCH | `/api/admin/payroll/cancellations/:id` | Ручная правка суммы за отмену → `is_manual` |
| POST | `/api/admin/payroll/cancellations/:id/reset` | Сбросить ручную правку отмены |
| GET/POST | `/api/admin/payroll/rate-versions` | Версии ставок школы (+ ставки направлений), `no_rate` |
| PATCH/DELETE | `/api/admin/payroll/rate-versions/:id` | Правка/удаление версии → пересчёт периода (`recalculated`) |
| GET/POST | `/api/admin/payroll/personal-rates` | Персональные ставки (paginated, `?teacher=&group=`) |
| PATCH/DELETE | `/api/admin/payroll/personal-rates/:id` | Правка/удаление → пересчёт группы (`recalculated`) |

Всё под `/api/admin/payroll` — только superadmin. Правила расчёта — `CLAUDE.md`, раздел «Зарплата преподавателей».

### Teachers / Directions / Tokens / Memberships

```
GET/POST/PATCH/DELETE /api/admin/teachers[/:id]         include_inactive=1
GET /api/admin/teachers/:id/stats?month=YYYY-MM         # нагрузка, направления, посещаемость,
                                                        # дни недели, пропуски, незаполненные,
                                                        # ряд за календарный год, прогресс групп.
                                                        # payroll в ответе — только суперадмину
POST/DELETE /api/admin/teachers/:id/telegram            # привязка Telegram-аккаунта
GET/POST/PATCH/DELETE /api/admin/directions[/:id]
GET /api/admin/tokens?include_inactive=1
POST /api/admin/tokens/generate                         # случайный XXX-XXX-XXX
POST/PATCH/DELETE /api/admin/tokens[/:token]            # PK = текст
GET/POST/PATCH/DELETE /api/admin/group-memberships[/:id]
```

### Payments (immutable: POST/DELETE только)

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/admin/payments?student_id=&direction_id=&from=&to=` | Список |
| GET | `/api/admin/payments/:id` | Одна |
| POST | `/api/admin/payments` | Создать с cap-валидацией в tx |
| DELETE | `/api/admin/payments/:id` | Hard delete → `{deleted, new_balance, warning?}` |

### Discounts

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/admin/discounts?include_inactive=1` | Список |
| POST | `/api/admin/discounts` | name + amount(0..1) |
| PATCH | `/api/admin/discounts/:id` | name/amount/active |
| DELETE | `/api/admin/discounts/:id` | Soft (active=false) |

### Settings, Dashboard

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/admin/dashboard?from=&to=` | FIFO KPIs + долги (period = текущий месяц если пусто) |
| GET | `/api/admin/dashboard/monthly?years=2025,2026` | Помесячные ряды для графиков |

## Static / SPA fallback

```
GET /              → public/login/index.html
GET /login         → public/login/index.html
GET /teacher[/*]   → public/teacher/index.html
GET /admin[/*]     → public/admin-dist/index.html
GET /admin/assets/* → Vite assets
```
