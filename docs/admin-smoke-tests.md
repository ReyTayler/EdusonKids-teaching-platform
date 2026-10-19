# Admin endpoints — curl smoke

После реализации Phase 4.2 пройти руками. Запускать `npm start` (порт по умолчанию 3000).
Cookie сохраняем в `cookies.txt` (флаг `-c`), передаём дальше через `-b cookies.txt`.

> **Подготовка:** в `.env` должны лежать `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`, `ADMIN_COOKIE_SECRET`.
> Сгенерировать: `node scripts/admin-set-password.js <пароль>`.

## Логин

```bash
curl -i -c cookies.txt -X POST -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<your-password>"}' \
  http://localhost:3000/api/admin/login
# expect: HTTP 200, Set-Cookie: admin_session=...; HttpOnly; SameSite=Strict; Path=/api/admin; Max-Age=86400
```

## Без cookie → 401

```bash
curl -i http://localhost:3000/api/admin/students
# expect: HTTP 401 {"error":"Unauthorized"}
```

## С cookie → 200

```bash
curl -i -b cookies.txt http://localhost:3000/api/admin/students
# expect: HTTP 200, JSON-массив активных учеников
```

## Teacher SPA не сломан

```bash
curl -i -X POST -H "Content-Type: application/json" \
  -d '{"token":"<существующий токен>"}' \
  http://localhost:3000/api/validateToken
# expect: HTTP 200 { valid: true, teacher: "..." }  (без cookie!)
```

## CRUD teachers (smoke)

```bash
# Create
curl -b cookies.txt -X POST -H "Content-Type: application/json" \
  -d '{"name":"TEST_TEACHER","phone":"+79991234567"}' \
  http://localhost:3000/api/admin/teachers
# expect 201, тело с id

# List + найти запись
curl -b cookies.txt http://localhost:3000/api/admin/teachers | jq '.[] | select(.name=="TEST_TEACHER")'

# Patch
curl -b cookies.txt -X PATCH -H "Content-Type: application/json" \
  -d '{"phone":"+70000000000"}' \
  http://localhost:3000/api/admin/teachers/<id>
# expect 200, phone обновлён

# Soft-delete
curl -i -b cookies.txt -X DELETE http://localhost:3000/api/admin/teachers/<id>
# expect 204

# Уже не в списке active
curl -b cookies.txt http://localhost:3000/api/admin/teachers | jq '.[] | select(.name=="TEST_TEACHER")'
# expect: пусто

# С флагом include_inactive=1 — виден, active:false
curl -b cookies.txt 'http://localhost:3000/api/admin/teachers?include_inactive=1' | jq '.[] | select(.name=="TEST_TEACHER")'
# expect: active: false
```

## Token generation

```bash
curl -b cookies.txt -X POST http://localhost:3000/api/admin/tokens/generate
# expect: { "token": "XXX-XXX-XXX" }  (без 0/O/1/I/L)
```

## Negative cases

```bash
# 400 missing required field
curl -i -b cookies.txt -X POST -H "Content-Type: application/json" \
  -d '{}' http://localhost:3000/api/admin/teachers
# expect 400 {"error":"name required"}

# 409 duplicate
curl -b cookies.txt -X POST -H "Content-Type: application/json" \
  -d '{"name":"TEST_DUP"}' http://localhost:3000/api/admin/teachers
curl -i -b cookies.txt -X POST -H "Content-Type: application/json" \
  -d '{"name":"TEST_DUP"}' http://localhost:3000/api/admin/teachers
# expect 409 {"error":"Already exists"}

# 404 unknown id
curl -i -b cookies.txt http://localhost:3000/api/admin/teachers/9999999
# expect 404
```

## Logout

```bash
curl -i -b cookies.txt -X POST http://localhost:3000/api/admin/logout
# expect: HTTP 200, Set-Cookie: admin_session=; Max-Age=0

# Cookie очищена → дальше snippet падает
curl -i -b cookies.txt http://localhost:3000/api/admin/students
# expect 401 (cookie из файла больше не валидна — браузер бы её стёр, в curl нужно перечитать -c)
```

## Чистка после smoke

```sql
PGPASSWORD=journal_dev_password psql -U journal -h localhost -d journal -c "
DELETE FROM teachers WHERE name LIKE 'TEST_%';
"
```

---

## UI smoke (Phase 4.3)

`npm start` → открыть http://localhost:3000/admin

### Login

- [ ] Без cookie показывается login-карточка.
- [ ] Неверный пароль → красный тост «Invalid credentials».
- [ ] Правильный логин/пароль → перерисовка в shell с sidebar (5 пунктов).
- [ ] Logout → перезагрузка → снова login.

### Каждый раздел (Students, Groups, Teachers, Tokens, Directions)

- [ ] Клик в sidebar → таблица грузится (первый клик — спиннер «Загружаем», второй — мгновенно из кеша).
- [ ] Поиск в шапке фильтрует таблицу.
- [ ] «+ Новый» → модалка с пустой формой; обязательные поля валидируются сервером (404/400 → тост).
- [ ] Клик по строке → модалка с прелейфилом.
- [ ] Сохранить → строка обновилась/добавилась, тост «Сохранено»/«Создано».
- [ ] Удалить → двухшаговая кнопка («Удалить» → «Точно удалить?»). Строка ушла, тост «Архивировано»/«Деактивировано»/«Отозвано».

### Tokens

- [ ] В модалке нового токена кнопка «Сгенерировать» подставляет XXX-XXX-XXX.

### Groups

- [ ] В модалке slot-редактор: «+ Добавить слот» появляется новая строка (день+время).
- [ ] Удаление слота через ×.
- [ ] После сохранения и повторного открытия слоты те же.

### Students

- [ ] Блок «Группы ученика» внутри Edit-модалки.
- [ ] Добавить в группу — строка появилась без закрытия модалки.
- [ ] Изменить lessons_done — blur сохраняет.
- [ ] × удаляет из группы.

### Сессия

- [ ] Если cookie протухла (можно дождаться 24ч или удалить в DevTools → Application → Cookies) → следующий fetch даёт тост «Сессия истекла» и через 1.5с перезагрузка.

### Уборка

```sql
DELETE FROM group_memberships WHERE student_id IN (SELECT id FROM students WHERE full_name LIKE 'TEST_%');
DELETE FROM students   WHERE full_name LIKE 'TEST_%';
DELETE FROM tokens     WHERE token LIKE 'TEST-%';
DELETE FROM teachers   WHERE name  LIKE 'TEST_%';
DELETE FROM groups     WHERE name  LIKE 'TEST_%';
DELETE FROM directions WHERE name  LIKE 'TEST_%';
```

---

## Phase 3b — Уроки и Зарплата

### Sidebar навигация

- [ ] В sidebar появились пункты «Уроки» и «Зарплата» между «Направления» и «Архив»
- [ ] Клик «Уроки» → таблица с уроками (ID/Дата/Группа/Преподаватель/Урок #/Тип/Был-Всего/Оплата/Штраф)
- [ ] Клик «Зарплата» → переключатель «Список / Сводка» вверху секции

### Lesson detail

- [ ] Клик по строке в Уроках → переход на detail-страницу
- [ ] Видны: данные урока (свёртка), посещаемость, зарплата
- [ ] Toggle «был/не был» сохраняет PATCH мгновенно (тост «Сохранено»)
- [ ] Edit полей зарплаты на blur сохраняет
- [ ] Кнопка «✎ Редактировать» открывает модалку с базовыми полями урока
- [ ] Кнопка «🗑 Удалить урок» (двухшаговая) удаляет — урок исчезает из списка; payroll и attendance тоже удаляются

### Create lesson

- [ ] Клик «+ Новый» в Уроках → модалка «Новый урок»
- [ ] Выбор группы → подгружаются ученики с галочками «был/не был»
- [ ] Снятие/установка галочки → автообновление поля «Оплата ₽»
- [ ] Submit → урок создан, переходим на его detail-страницу

### Group detail — «Уроки группы»

- [ ] На странице группы внизу есть секция «Уроки группы»
- [ ] Видна таблица всех уроков этой группы
- [ ] Кнопка «+ Новый урок» открывает модалку с preset'нутой группой
- [ ] Клик по строке урока → переход в lesson detail

### Payroll: Список / Сводка

- [ ] «Список» — таблица payroll (Дата, Преподаватель, Группа, Урок #, Было/Всего, Оплата, Штраф)
- [ ] Клик по строке → переход в lesson detail
- [ ] «Сводка» — таблица агрегата (Преподаватель, Уроков, Сумма оплат, Сумма штрафов)
- [ ] Переключение между «Список» и «Сводка» работает без сброса в дефолт

### Чистка тестовых данных

```sql
DELETE FROM payroll WHERE lesson_id IN (SELECT id FROM lessons WHERE submitted_by_token = 'admin-imported' AND lesson_date >= '2025-01-01');
DELETE FROM lesson_attendance WHERE lesson_id IN (SELECT id FROM lessons WHERE submitted_by_token = 'admin-imported' AND lesson_date >= '2025-01-01');
DELETE FROM lessons WHERE submitted_by_token = 'admin-imported' AND lesson_date >= '2025-01-01';
```
