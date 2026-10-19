// Переключатель светлой/тёмной темы для страниц входа.
// По умолчанию — светлая. Выбор запоминается в localStorage('eduson-theme').
// Мгновенное применение темы (без вспышки) делает inline-скрипт в <head>;
// здесь — только подключение кнопки-тумблера.
(function () {
  'use strict';
  var KEY = 'eduson-theme';

  function current() {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }
  function apply(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem(KEY, theme); } catch (e) { /* private mode */ }
  }

  function init() {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    btn.setAttribute('aria-pressed', String(current() === 'dark'));
    btn.addEventListener('click', function () {
      var next = current() === 'dark' ? 'light' : 'dark';
      apply(next);
      btn.setAttribute('aria-pressed', String(next === 'dark'));
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
