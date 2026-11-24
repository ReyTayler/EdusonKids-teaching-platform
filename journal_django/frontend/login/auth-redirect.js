// Уже авторизован? Страница /login ему не нужна — сразу уводим в его раздел.
//   role === 'teacher' → /teacher, иначе (manager|admin) → /admin
//   (зеркалит серверный apps/auth_app/services.py: redirect_for). 401 → показываем форму.
//
// Access-cookie живёт 60 минут, refresh — 30 дней (SIMPLE_JWT в settings/base.py).
// Если с прошлого визита прошло больше часа, access уже протух и /me ответит 401,
// хотя refresh ещё жив — без попытки обновить токен пользователя молча кидало бы
// на форму входа. Поэтому при 401 один раз пробуем POST /api/auth/refresh и
// повторяем /me — тот же паттерн, что и в admin SPA (frontend/admin-src/src/lib/api.ts:
// api(), refreshAccessToken()). RefreshView идёт без CSRF (authentication_classes=[]),
// так что дёрнуть его с этой страницы можно напрямую.
//
// Без вспышки формы: проверка асинхронная (1–3 запроса), а браузер успевает
// нарисовать страницу раньше ответа — авторизованный видел карточки выбора роли,
// и только потом его уводило. Поэтому до ответа страница скрыта классом
// `auth-checking` на <html> (styles.css), форма показывается, только когда
// стало ясно, что вход нужен. Скрипт подключён в <head> синхронно — класс
// ставится до первой отрисовки. Без JS класса нет → форма видна как обычно.
// Страховка: если сервер не ответил за REVEAL_TIMEOUT_MS, форму показываем
// (редирект всё равно сработает, если ответ придёт позже).
//
// Вынесено из inline <script> ради CSP `script-src 'self'` (без 'unsafe-inline').
(function () {
  var REVEAL_TIMEOUT_MS = 3000;
  var root = document.documentElement;
  root.classList.add('auth-checking');

  function reveal() { root.classList.remove('auth-checking'); }
  var timer = setTimeout(reveal, REVEAL_TIMEOUT_MS);

  function fetchMe() {
    return fetch('/api/auth/me', { credentials: 'include', headers: { Accept: 'application/json' } });
  }

  fetchMe()
    .then(function (r) {
      if (r.ok) return r.json();
      if (r.status !== 401) return null;
      return fetch('/api/auth/refresh', { method: 'POST', credentials: 'include' })
        .then(function (rr) { return rr.ok ? fetchMe() : null; })
        .then(function (r2) { return r2 && r2.ok ? r2.json() : null; });
    })
    .then(function (me) {
      clearTimeout(timer);
      if (me && me.role) {
        // Остаёмся скрытыми до ухода со страницы.
        window.location.replace(me.role === 'teacher' ? '/teacher' : '/admin');
        return;
      }
      reveal();
    })
    .catch(function () {
      // сеть/ошибка — показываем форму входа
      clearTimeout(timer);
      reveal();
    });
})();
