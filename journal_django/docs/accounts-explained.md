# Как работает app `accounts`: аутентификация, permissions и «мгновенный разлогин»

> Документ-объяснялка для понимания, а не справочник API. Читается сверху вниз.
> Парная к нему — `docs/auth-explained.md` (там про cookie/JWT/CSRF с нуля).
> Здесь — про то, **кто** пользователь, **что** ему можно, и **почему его
> выкидывает** при сбросе пароля/2FA, хотя строчки «выкинуть» в коде нет.

---

## Часть 0. Два разных вопроса, которые легко спутать

Когда приходит запрос, сервер последовательно отвечает на **два** вопроса. Это
разные вещи, и за них отвечают разные механизмы:

| Вопрос | Англ. термин | «Кто спрашивает?» | Кто отвечает в коде |
|---|---|---|---|
| **Ты вообще кто?** | **Authentication** (аутентификация) | Проверка личности | `authentication_classes` → `CookieJWTAuthentication` |
| **Тебе сюда можно?** | **Authorization / Permissions** (авторизация) | Проверка прав | `permission_classes` → `IsAdmin` и т.п. |

Аналогия: вход в офисное здание.

- **Authentication** — охранник на входе смотрит твой **пропуск** и убеждается,
  что это действительно ты (пропуск настоящий, не просрочен, не поддельный).
  Если пропуска нет или он липовый — ты вообще «никто», `user = None`.
- **Authorization** — на 5-м этаже на двери серверной стоит **замок по роли**:
  даже с настоящим пропуском внутрь пускают только админов. Ты — настоящий
  сотрудник (аутентифицирован), но прав на эту дверь нет (не авторизован).

Это объясняет два разных кода ошибки:

- **401 Unauthorized** — «не понял, кто ты» (провалилась аутентификация).
- **403 Forbidden** — «понял кто ты, но тебе сюда нельзя» (провалились permissions).

Запомни связку: **`authentication_classes` определяют `request.user`.
`permission_classes` решают, пускать ли этого `request.user`.**

---

## Часть 1. Что такое app `accounts`

`accounts` — это раздел про **людей и их доступы**. Одна таблица `accounts`
(модель `Account`) хранит всех: учителей, менеджеров, админов. Плюс две
вспомогательные таблицы:

- `account_invites` — одноразовые ссылки-приглашения для установки пароля.
- `account_recovery_codes` — резервные коды для входа, если 2FA-устройство потеряно.

Ключевой момент структуры: **`Account` — это не самописная сущность, это
`AbstractUser` из Django** (`apps/accounts/models.py:44`).

```python
class Account(AbstractUser):
    username = None                  # отказались от username
    email = models.EmailField(unique=True)
    USERNAME_FIELD = 'email'         # логинимся по email
    role = models.CharField(...)     # teacher | manager | admin
    token_version = models.IntegerField(default=0)  # ← ключ к «разлогину», см. Часть 5
```

Почему это важно: взяв `AbstractUser`, мы **бесплатно** получили от Django:

- хранение пароля как безопасного хэша (поле `password`, метод `set_password`);
- `check_password()` для проверки;
- флаг `is_active`;
- свойство `is_authenticated` (всегда `True` у настоящего объекта пользователя);
- поля `last_login`, `date_joined`.

Это и есть принцип проекта «не изобретать велосипед»: систему пользователей и
паролей за нас уже написал Django. Мы лишь **добавили** свои поля: `role`,
`token_version`, 2FA-поля.

### Слои внутри app (важно для чтения кода)

Данные текут строго по слоям, каждый слой не лезет через голову соседа:

```
HTTP-запрос
   │
   ▼
views.py        ← тонкий: распарсить вход, проверить permission, отдать ответ
   │
   ▼
services.py     ← бизнес-логика: «создать учётку + выписать invite + записать в аудит»
   │
   ▼
repository.py   ← ЕДИНСТВЕННОЕ место, где трогаем БД (ORM-запросы)
   │
   ▼
PostgreSQL
```

Когда ищешь «где это происходит» — определись, на каком слое. «Кто может
дёрнуть эндпоинт» → `views.py`. «Что именно при этом происходит» → `services.py`.
«Какой SQL» → `repository.py`.

---

## Часть 2. `permission_classes` — что это и как работает под капотом

Открой `apps/accounts/views.py:71`:

```python
class AccountListCreateView(APIView):
    permission_classes = [IsAdmin]
    ...
```

`permission_classes` — это **список «вахтёров»**, которых DRF обязан опросить
перед тем, как пустить запрос в твой метод (`get`/`post`/...). Каждый вахтёр —
класс с методом `has_permission(request, view)`, который возвращает `True`
(пропустить) или `False` (отказать → **403**).

Наши вахтёры лежат в `apps/core/permissions.py`. Вот весь `IsAdmin` целиком:

```python
class IsAdmin(BasePermission):
    message = 'Admin role required.'
    def has_permission(self, request, view):
        return _authenticated_with_role(request, 'admin')
```

А вот сам помощник — здесь видно ОБА вопроса из Части 0 сразу:

```python
def _authenticated_with_role(request, *roles):
    user = request.user
    if not (user and user.is_authenticated):   # ← вопрос 1: ты вообще кто?
        return False
    return user.role in roles                  # ← вопрос 2: твоя роль подходит?
```

То есть `IsAdmin` сначала проверяет, что пользователь вообще опознан, **потом**
что у него `role == 'admin'`. Если оба `True` — `has_permission` вернёт `True`,
и DRF вызовет твой `post()`.

### Откуда берётся `request.user`?

Вот ключевой вопрос, который путает новичков. В permission-классе ты пишешь
`request.user.role` — но **кто положил туда пользователя?** Ты нигде в `views.py`
не писал `request.user = ...`. Ответ: это сделала **аутентификация ДО того**, как
дело дошло до permissions. Об этом — Часть 3.

### Глобальный дефолт

В `config/settings/base.py:155` задан дефолт на весь проект:

```python
'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.AllowAny'],
```

Это значит: **по умолчанию любой view открыт всем**. Поэтому каждый защищённый
view **обязан** сам объявить `permission_classes = [IsAdmin]` (или другой).
Забыл объявить — эндпоинт открыт настежь. Все 8 view в `accounts/views.py`
аккуратно ставят `[IsAdmin]` — управление учётками только для админа.

---

## Часть 3. `authentication_classes` — что это и как работает

Открой `config/settings/base.py:148`:

```python
'DEFAULT_AUTHENTICATION_CLASSES': [
    'apps.core.authentication.CookieJWTAuthentication',
],
```

`authentication_classes` — это **список способов опознать пользователя**. На
каждый запрос DRF берёт каждый класс из списка и вызывает его `.authenticate(request)`.
Класс возвращает одно из трёх:

- `(user, token)` — «опознал, вот пользователь» → DRF кладёт его в `request.user`;
- `None` — «этим способом не получилось, попробуй следующий»;
- бросает исключение `AuthenticationFailed` → **401** сразу.

У нас способ ровно один: `CookieJWTAuthentication` (`apps/core/authentication.py:29`).
Это **тонкая надстройка** над `JWTAuthentication` из библиотеки
`djangorestframework-simplejwt` — опять же, не велосипед, а 40 строк поверх
готового. Разберём её `authenticate()` по шагам:

```python
def authenticate(self, request):
    raw_token = request.COOKIES.get('access')   # 1. достаём JWT из cookie
    if raw_token is None:
        return None                              # 2. cookie нет → «не опознал», None
    validated_token = self.get_validated_token(raw_token)  # 3. подпись/срок (иначе 401)
    if request.method in _UNSAFE_METHODS:        # 4. на POST/PUT/PATCH/DELETE —
        self._enforce_csrf(request)              #    ещё и CSRF (см. auth-explained.md)
    return self.get_user(validated_token), validated_token  # 5. достаём пользователя
```

Шаг 5 — `get_user()` — здесь и живёт вся наша кастомная логика. К нему вернёмся
в Части 5, потому что **именно он отвечает за «выкидывание»**.

### `UNAUTHENTICATED_USER = None` — почему это важно

В `base.py:166`:

```python
'UNAUTHENTICATED_USER': None,
```

Обычно DRF при провале аутентификации кладёт в `request.user` объект
`AnonymousUser`. Мы это отключили: если опознать не вышло, `request.user` будет
**`None`**, а не «аноним». Поэтому в `permissions.py` стоит защита
`if not (user and user.is_authenticated)` — сначала проверяем, что `user` вообще
не `None`, и только потом дёргаем `.is_authenticated`. Без этой проверки на
`None.is_authenticated` был бы краш 500 вместо честного 401/403. (Это один из
багов, который ранее ловили — см. историю в memory.)

---

## Часть 4. Полный путь запроса: где и в каком порядке всё вызывается

Соберём Части 2 и 3 в одну картину. Админ из SPA жмёт «Сбросить 2FA» →
браузер шлёт `POST /api/admin/accounts/42/reset-2fa`. Что происходит на сервере:

```
1. Запрос доходит до AccountReset2faView (по url-роутингу).

2. DRF: фаза АУТЕНТИФИКАЦИИ (до твоего кода!)
   → перебирает authentication_classes → CookieJWTAuthentication.authenticate()
   → берёт access-cookie, проверяет подпись и срок,
   → на POST проверяет CSRF,
   → get_user(): достаёт Account из БД + сверяет token_version (Часть 5)
   → успех: request.user = <Account админа>

3. DRF: фаза PERMISSIONS
   → перебирает permission_classes = [IsAdmin]
   → IsAdmin.has_permission(): request.user.is_authenticated? да. role == 'admin'? да.
   → True → пропускаем

4. Только ТЕПЕРЬ вызывается твой метод AccountReset2faView.post()
   → services.reset_twofa(42, ...) делает работу

5. Response → DRF навешивает заголовки → браузер
```

Главное, что нужно усвоить: **шаги 2 и 3 происходят ДО твоего `post()`, и ты их
нигде явно не вызываешь.** Они объявлены декларативно — двумя списками
(`authentication_classes`, `permission_classes`), а движок DRF сам прогоняет их
в нужном порядке. Поэтому в `views.py` и нет строчки «проверь права» — права
проверяет фреймворк по списку, который ты ему дал.

---

## Часть 5. Главный вопрос: почему юзера «выкидывает» при сбросе 2FA/пароля?

Твоя формулировка была точной: *«по коду я не могу понять как это происходит,
нет конкретной строчки, которая указывает выкинуть пользователя»*. Правильно —
**такой строчки и нет**. Выкидывание — это не действие, а **следствие**.
Разберём механизм.

### 5.1. Что такое `token_version`

У каждой учётки есть число `token_version` (`models.py:92`), по умолчанию `0`.
Это **счётчик-печать «поколения» доступа**. Думай о нём как о номере на
ключ-карте от отеля: при выезде ресепшн меняет номер в системе, и старая карта,
физически оставшаяся у тебя в кармане, просто перестаёт открывать дверь — её
никто не «отбирал», она устарела.

### 5.2. Этот номер вшивается в токен в момент входа

Когда пользователь логинится, мы кладём текущий `token_version` **внутрь JWT**
как claim (`apps/core/authentication.py:101`):

```python
def issue_tokens_for(user):
    refresh = RefreshToken.for_user(user)
    refresh['token_version'] = user.token_version   # ← вшиваем «поколение» в токен
    return refresh
```

Теперь в access-токене, который лежит у юзера в cookie, навсегда записано,
например, `token_version: 0`. JWT подписан — поменять это число в куке
незаметно нельзя (см. `auth-explained.md` про подпись).

### 5.3. На КАЖДОМ запросе сверяем токен с БД

Вот та самая `get_user()` из `authenticate()` (`authentication.py:71`):

```python
def get_user(self, validated_token):
    user = super().get_user(validated_token)        # достаём Account из БД (+ is_active)

    token_version_claim = validated_token['token_version']  # «поколение» из куки

    auth_state = get_auth_state(user.id)            # «поколение» сейчас в БД
    if auth_state is None or auth_state['token_version'] != token_version_claim:
        raise AuthenticationFailed('Токен устарел. Выполните вход заново.')

    return user
```

`get_auth_state` (`repository.py:210`) — это просто свежее чтение из БД:

```python
def get_auth_state(account_id):
    return dictrow(Account.objects.filter(id=account_id).values('token_version', 'is_active'))
```

То есть на каждый запрос сравниваются два числа:

- **что вшито в куке** (поколение на момент входа),
- **что лежит в БД прямо сейчас**.

Совпадают → пускаем. Не совпадают → `AuthenticationFailed` → **401**.

### 5.4. Сброс 2FA/пароля увеличивает номер в БД

Теперь смотри `services.reset_twofa()` (`services.py:132`):

```python
def reset_twofa(account_id, actor_account_id, request):
    acc = repository.reset_twofa(account_id)        # стираем 2FA-секрет
    if acc is None:
        return False
    repository.bump_token_version(account_id)        # ← ВОТ ОНА, «та самая строчка»
    log_event(event='2fa_reset', ...)
    return True
```

А `bump_token_version` (`repository.py:206`) — одна короткая команда БД:

```python
def bump_token_version(account_id):
    Account.objects.filter(id=account_id).update(token_version=F('token_version') + 1)
```

Было `0` → стало `1`. Всё. **Это единственное действие.** Никто никого не
«выкидывал», никакую сессию не «убивал».

### 5.5. Собираем причинно-следственную цепочку

Вот почему другой админ вылетает при **любом** следующем запросе или F5:

```
1. Админ Б вошёл  → в его access-cookie вшито token_version = 0
2. Админ А жмёт «Сбросить 2FA админу Б»
       → bump_token_version(Б): в БД у Б теперь token_version = 1
3. Админ Б обновляет страницу (любой запрос к API)
       → CookieJWTAuthentication.get_user():
            кука говорит  token_version = 0
            БД говорит     token_version = 1
            0 ≠ 1          → AuthenticationFailed → 401
4. Фронт ловит 401 → редиректит на /login
       → для пользователя это выглядит как «меня выкинуло»
```

«Выкидывание» — это **эмерджентный эффект** трёх независимых фактов:

1. в куке вшито старое поколение (его нельзя подделать — токен подписан);
2. в БД лежит новое поколение (его увеличил `bump_token_version`);
3. на каждом запросе они сверяются, и расхождение = 401.

Нет ни одной строки «logout пользователя Б». Есть лишь «увеличь число в БД», а
всё остальное — автоматическое следствие проверки на каждом запросе.

### 5.6. Где ещё дёргается `bump_token_version`

Тот же приём используется везде, где нужно «обнулить все активные входы»:

| Действие | Файл | Эффект |
|---|---|---|
| Сброс 2FA | `services.py:137` | все входы юзера протухают |
| Сброс пароля | `services.py:125` | старый пароль зануляется + входы протухают |
| Смена email | `services.py:102` | входы протухают |
| Soft-delete учётки | `services.py:223` | входы протухают (+ `is_active=False`) |
| Logout (выход самого себя) | `auth_app` | свой же токен протухает — иначе refresh жил бы 7 дней |
| Принятие invite (новый пароль) | `repository.py:313` | старые входы протухают |

Один механизм закрывает все сценарии отзыва. Это и есть «отзыв без blacklist»:
не храним список «запрещённых токенов», а просто двигаем число-поколение.

### 5.7. Почему «сразу», а не через 15 минут

Можно спросить: access-токен живёт 15 минут — почему расхождение ловится
мгновенно, а не ждёт истечения? Потому что `token_version` проверяется на
**каждом** запросе свежим чтением из БД (`get_auth_state`), независимо от срока
жизни токена. Срок (`exp`) — это «когда токен сам по себе протухнет». А
`token_version` — это «принудительно протух прямо сейчас, не дожидаясь срока».
Две независимые защиты.

---

## Часть 6. Краткая шпаргалка

- **Authentication** = «кто ты». Делают `authentication_classes`
  (`CookieJWTAuthentication`). Результат → `request.user`. Провал → **401**.
- **Permissions** = «тебе можно». Делают `permission_classes` (`IsAdmin` и др.).
  Читают `request.user.role`. Провал → **403**.
- Оба списка вызывает **сам DRF до твоего метода** — поэтому в `views.py` нет
  явных вызовов «проверь вход/права».
- `Account` = Django `AbstractUser` + наши поля (`role`, `token_version`, 2FA).
  Пароли, `is_active`, `check_password` — готовые от Django.
- **«Выкидывание» = расхождение `token_version`** между токеном (в куке) и БД.
  `bump_token_version` увеличивает число в БД → на следующем запросе сверка
  падает → 401 → фронт уводит на логин. Отдельной команды «logout» нет.

---

## Где это в коде

| Что | Файл |
|---|---|
| Модель `Account` (+ `token_version`, роли) | `apps/accounts/models.py` |
| Permission-классы (`IsAdmin`, `IsManagerOrAdmin`, …) | `apps/core/permissions.py` |
| Аутентификация + `get_user`/проверка `token_version` | `apps/core/authentication.py` |
| Views управления учётками (`permission_classes = [IsAdmin]`) | `apps/accounts/views.py` |
| Бизнес-логика сброса (где зовётся `bump_token_version`) | `apps/accounts/services.py` |
| Доступ к БД (`bump_token_version`, `get_auth_state`) | `apps/accounts/repository.py` |
| Глобальные DRF-дефолты (auth/permission) | `config/settings/base.py` (REST_FRAMEWORK) |
| Параметры JWT/cookie | `config/settings/base.py` (SIMPLE_JWT) |

Парный документ про cookie/JWT/CSRF с самых азов — `docs/auth-explained.md`.
