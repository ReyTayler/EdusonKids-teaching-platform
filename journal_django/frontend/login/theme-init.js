// Ранняя установка темы ДО отрисовки (подключается в <head> как render-blocking) —
// чтобы не было вспышки светлой/тёмной темы. По умолчанию светлая.
// Вынесено из inline <script> ради CSP `script-src 'self'` (без 'unsafe-inline').
(function () {
  try {
    var t = localStorage.getItem('eduson-theme');
    document.documentElement.setAttribute('data-theme', t === 'dark' ? 'dark' : 'light');
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'light');
  }
})();
