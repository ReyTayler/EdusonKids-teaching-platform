// Уже авторизован? Страница /login ему не нужна — сразу уводим в его раздел.
//   role === 'teacher' → /teacher, иначе (manager|admin) → /admin
//   (зеркалит серверный apps/auth_app/services.py: redirect_for). 401 → показываем форму.
//
// Access-cookie живёт 60 минут, refresh — 7 дней (SIMPLE_JWT в settings/base.py).
// Если с прошлого визита прошло больше часа, access уже протух и /me ответит 401,
// хотя refresh ещё жив — без попытки обновить токен пользователя молча кидало бы
// на форму входа. Поэтому при 401 один раз пробуем POST /api/auth/refresh и
// повторяем /me — тот же паттерн, что и в admin SPA (frontend/admin-src/src/lib/api.ts:
// api(), refreshAccessToken()). RefreshView идёт без CSRF (authentication_classes=[]),
// так что дёрнуть его с этой страницы можно напрямую.
// Вынесено из inline <script> ради CSP `script-src 'self'` (без 'unsafe-inline').
(function () {
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
      if (me && me.role) {
        window.location.replace(me.role === 'teacher' ? '/teacher' : '/admin');
      }
    })
    .catch(function () { /* сеть/ошибка — оставляем форму входа */ });
})();
