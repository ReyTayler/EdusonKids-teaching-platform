# План: Content-Security-Policy (CSP) для платформы

> **Статус (обновлено 2026-06-19): Report-Only РЕАЛИЗОВАН.** Сделаны этапы 1–3 + 6:
> рефактор teacher SPA (inline-JS → `frontend/teacher/app.js`, 27 `on*=` → addEventListener),
> заголовок `Content-Security-Policy-Report-Only` + сбор нарушений в `csp-violations.log`
> в обоих nginx-конфигах, документация-объяснялка `docs/csp-explained.md`.
> **Осталось:** этапы 4–5 — браузерный прогон 3 фронтов под Report-Only до нуля нарушений
> и флип на боевой `Content-Security-Policy` (чек-лист — в `csp-explained.md`, Часть 7).
> Оба этапа требуют живого браузера и выполняются отдельно.
>
> **Расхождения с этим планом, найденные при реализации (учтены):**
> - План считал, что «внешних CDN/шрифтов нет» — на деле teacher И admin грузили Google
>   Fonts. **Обновление (2026-06-23): шрифты self-hosted** (Manrope/Inter/JetBrains Mono —
>   woff2 латиница+кириллица в `/fonts/` и `/admin/fonts/`), `fonts.googleapis.com`/
>   `fonts.gstatic.com` убраны из политики → `style-src 'self' 'unsafe-inline'; font-src 'self'`.
>   Также добавлен `report-to` рядом с `report-uri` и `app.js` обёрнут в IIFE.
>   Детали и статус backlog — `docs/csp-explained.md` Часть 13.
> - Добавлена директива `upgrade-insecure-requests` (по итогам security-ревью).
> - Сбор отчётов реализован полностью в nginx (self-proxy на no-op сток `127.0.0.1:19876`),
>   БЕЗ эндпоинта на Django; `/csp-report` под rate-limit на проде.
> - 27-й inline-обработчик оказался в template-строке самого скрипта (popup-close) —
>   тоже устранён (делегирование на `#schedOverlay`).
>
> **Приоритет:** средний (defense-in-depth, не blocker). Объём — полноценная мини-фаза.
> Подробное объяснение реализованного — `docs/csp-explained.md`.

---

## TL;DR

CSP сейчас **намеренно выключен** (наследие `helmet({csp:false})` из Express —
см. `deploy/nginx/journal-kotokod.conf:59`). После перехода на JWT-cookie + double-submit
CSRF мы сознательно сделали CSRF-токен читаемым из JS (`CSRF_COOKIE_HTTPONLY=False`).
Это **сдвинуло модель угроз**: CSRF-защита больше не закрывает XSS, и единственная
реальная защита от связки «XSS → обход CSRF» — не допустить сам XSS. Главный механизм
defense-in-depth против XSS — CSP — отсутствует. Нужно его ввести, но мешают inline-скрипты
(в первую очередь teacher SPA).

---

## Зачем это нужно (модель угроз)

### Что изменилось
- **Было (Express / HMAC-cookie):** CSRF-токена в JS не было; сессия — HttpOnly-cookie.
- **Стало (architecture_v2, JWT-cookie):** для double-submit паттерна SPA **обязана читать**
  csrf-токен из `document.cookie` и слать его в заголовке `X-CSRFToken`. Поэтому
  `production.py: CSRF_COOKIE_HTTPONLY = False` — это **требование**, а не недосмотр.

### Следствие
- **CSRF-защита больше НЕ защищает от XSS.** Любой исполнившийся на странице чужой скрипт
  читает csrf-токен из cookie и подделывает любой мутирующий запрос от имени пользователя.
- Значит главный остаточный риск — **XSS**, и основная мера defense-in-depth против него —
  **CSP** (ограничивает, откуда грузятся/исполняются скрипты, стили, коннекты и т.д.).

### Почему это НЕ blocker (трезвая оценка)
- JWT `access`/`refresh`-cookie остаются **`HttpOnly=True`** (`base.py`) — украсть саму
  сессию через XSS нельзя, можно лишь «прокатиться» на ней, пока открыта вкладка.
- Это defense-in-depth, а не активная дыра. CSP отсутствовал и до Фазы 3b; изменения лишь
  **повысили цену** его отсутствия. Поэтому — backlog, а не hotfix.

---

## Текущее состояние (факты)

### Security-заголовки (есть всё, кроме CSP)
`deploy/nginx/journal-kotokod.conf` (прод) и `deploy/nginx/local/nginx.conf` (локально)
ставят: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`,
`Referrer-Policy: no-referrer`, `X-XSS-Protection: 0`, COOP/CORP `same-origin`,
`X-Permitted-Cross-Domain-Policies: none`, `Origin-Agent-Cluster`. **CSP — нет**
(прямой комментарий: «намеренно НЕ задаём — ломает inline-скрипты login/teacher SPA»).
Django CSP тоже не задаёт.

### Источник истины заголовков — nginx
Security-заголовки живут в nginx (helmet-эквивалент), не в Django. CSP добавлять туда же,
в оба конфига (прод + локальный сниппет/сервер).

### Инвентаризация inline-кода (2026-06-18)
| Фронт | inline `<script>` | внешний script | `<style>` блок | inline `on*=` | `style="..."` |
|---|---|---|---|---|---|
| `frontend/teacher/index.html` | **1 (большой)** | 0 | 1 | **27** | 27 |
| `frontend/login/index.html` | 0 | 1 (`login.js`) | 0 | 0 | 1 |
| `frontend/login/set-password.html` | 0 | 1 (`set-password.js`) | 0 | 0 | 1 |
| `frontend/admin-dist/index.html` | 0 | 1 (Vite-бандл) | 0 | 0 | 0 |

**Вывод:**
- **teacher SPA — главный и единственный серьёзный блокер.** Inline `<script>` + 27 inline-обработчиков
  (`onclick=` и т.п.) несовместимы с `script-src 'self'` без `'unsafe-inline'` (что обнуляет смысл CSP).
- **login-страницы — почти чисто:** скрипты уже внешние; по 1 inline `style=""` (мелочь, закрывается `style-src`).
- **admin SPA — чисто в HTML** (внешний бандл). Остаётся проверить в браузере runtime inline-стили React
  (`style={{...}}`) и Vite modulepreload — обычно совместимо, но требует проверки.

---

## Что нужно сделать (план)

### Шаг 1. Решить блокер teacher SPA
Для рабочего `script-src 'self'` (без `'unsafe-inline'`) надо убрать из teacher SPA **и** inline-скрипт,
**и** 27 inline-обработчиков:
- **Вынести inline-`<script>` в отдельный `frontend/teacher/app.js`** (`<script src>`).
- **Заменить 27 `on*=`-атрибутов на `addEventListener`** в этом app.js (делегирование событий или
  навешивание по `id`/`data-`-атрибутам).

Альтернативы и почему они хуже:
- *`'unsafe-inline'` в `script-src`* — проще всего, но **обнуляет защиту от XSS**. Не вариант.
- *hash-based (`'sha256-...'`)* — работает для статики, но хеш надо пересчитывать при каждой правке
  скрипта; для inline-обработчиков нужен `'unsafe-hashes'` (хрупко и слабее). Не рекомендуется.
- *nonce* — не подходит: nginx отдаёт это как статику, инжектить per-request nonce негде (нет рендера шаблона).

> Замечание: teacher SPA по плану (CLAUDE.md, Phase 7) когда-нибудь мигрирует на React — тогда
> CSP-совместимость получится «бесплатно». Можно либо подождать миграции, либо сделать минимальный
> вынос скрипта сейчас. Решение — за реализацией.

### Шаг 2. Стили
- `style-src 'self' 'unsafe-inline'` — прагматичный компромисс на старте (inline-стили — куда меньший
  риск, чем inline-скрипты). teacher имеет `<style>`-блок и 27 `style=""`; login — по 1 `style=""`.
- Позже, при желании, вынести стили в `.css` и убрать `'unsafe-inline'` из `style-src`.

### Шаг 3. Сформулировать политику
Черновик (уточнить после инвентаризации в браузере):
```
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline';
img-src 'self' data:;
font-src 'self';
connect-src 'self';
frame-ancestors 'none';
base-uri 'self';
form-action 'self';
object-src 'none'
```
- `connect-src 'self'` — все fetch идут на относительные `/api/*` (same-origin), внешних нет.
- `frame-ancestors 'none'` — дублирует/усиливает `X-Frame-Options`.
- Внешних CDN/шрифтов/аналитики нет → политика может быть строгой.

### Шаг 4. Выкатка через Report-Only (обязательно)
1. Сначала `Content-Security-Policy-Report-Only: <политика>` — браузер **логирует** нарушения
   (консоль / `report-uri`/`report-to`-endpoint), но **ничего не ломает**.
2. Прогнать **все три фронта** в браузере (login → 2FA → teacher SPA: журнал/submitLesson/refresh;
   admin SPA: все разделы, графики Recharts, мутации), собрать нарушения.
3. Добить политику до нуля нарушений.
4. Переключить `Report-Only` → боевой `Content-Security-Policy`.

### Шаг 5. Зафиксировать в обоих nginx-конфигах
- `deploy/nginx/journal-kotokod.conf` (прод) и `deploy/nginx/local/nginx.conf` (локально),
  чтобы dev/prod parity. Учесть правило наследования `add_header` в nginx: на server-уровне,
  НЕ внутри `location` (иначе серверные заголовки молча исчезнут в этом location —
  см. предупреждения в самих конфигах).

---

## Решения, которые НЕ меняем (контекст)
- **`CSRF_COOKIE_HTTPONLY=False` остаётся** — это требование double-submit, не баг.
- **JWT-cookie остаются `HttpOnly=True`** — не трогать.
- **`:5173` (Vite) в `CSRF_TRUSTED_ORIGINS` НЕ добавлять** — отдельный вопрос, к CSP не относится;
  авторизованные запросы с :5173 мертвы из-за `SameSite=Lax` (нужен same-origin nginx).

---

## Оценка объёма
- Рефактор teacher SPA (вынос скрипта + 27 обработчиков) — основная работа, риск регрессий
  (3411-строчный живой файл) → делать с проверкой в браузере.
- Правка 2 nginx-конфигов — мелочь.
- Браузерное тестирование Report-Only на 3 фронтах — ощутимо по времени.
- Итог: **мини-фаза**, не правка на 10 минут. Делать отдельной сессией.

## Верификация (после реализации)
- Браузер: 0 нарушений CSP в консоли на всех трёх фронтах во всех сценариях.
- `curl -I` прод/локального nginx → заголовок `Content-Security-Policy` присутствует на HTML-ответах.
- Регресс-проверка: login/2FA, teacher submitLesson/refresh, admin мутации + графики — всё работает.
- Бэкенд CSP не касается → pytest не затрагивается.
