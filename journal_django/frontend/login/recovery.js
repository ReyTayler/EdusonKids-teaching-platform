// Экран «Сохраните резервные коды» — общий для входа (login.js) и первичной
// настройки по инвайту (set-password.js).
//
// Коды показываются ЕДИНСТВЕННЫЙ раз: в БД лежат только хеши (account_recovery_codes),
// достать их повторно нельзя. Поэтому здесь нет автоматического редиректа — уйти
// со страницы можно только сознательно, отметив «я сохранил». Прежнее поведение
// (показать строкой и через 6 секунд уехать в кабинет) означало, что резервных
// кодов у пользователя фактически нет.
//
// CSP script-src 'self': файл внешний, inline-обработчиков нет. Скачивание — через
// Blob (не data:-URI): top-level переход на data: браузеры блокируют.
(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }

  // 05d744acfc → 05d7-44ac-fc. Код переписывают руками с экрана или из файла,
  // сплошные 10 символов для этого непригодны. Дефисы — только оформление:
  // при вводе они срезаются (см. login.js), сервер получает исходный код.
  function format(code) {
    return String(code).replace(/(.{4})(?=.)/g, '$1-');
  }

  /**
   * Показать экран сохранения кодов вместо формы ввода.
   * codes — массив кодов в открытом виде (ответ /2fa/enable), redirect — куда
   * уйти по кнопке «Продолжить».
   */
  function show(codes, redirect) {
    var list = $('recovery-list');
    var ack = $('recovery-ack');
    var go = $('recovery-continue');
    var save = $('recovery-save');
    // Разметки нет (старая страница из кеша) — не запираем пользователя на
    // странице без выхода: уходим, как раньше.
    if (!list || !ack || !go || !save) { window.location = redirect; return; }

    list.textContent = '';
    var pretty = [];
    codes.forEach(function (code) {
      var text = format(code);
      pretty.push(text);
      var li = document.createElement('li');
      li.className = 'recovery-code';
      li.textContent = text;
      list.appendChild(li);
    });

    var fileText = 'Резервные коды Eduson Kids\n'
      + 'Каждый код работает один раз и заменяет код подтверждения входа.\n'
      + 'Храните их отдельно от телефона и пароля.\n\n'
      + pretty.join('\n') + '\n';

    var copyBtn = $('recovery-copy');
    if (copyBtn) {
      copyBtn.addEventListener('click', function () {
        // clipboard недоступен по http и при отказе в доступе — коды остаются
        // на экране и в файле, поэтому молча ничего не делаем.
        if (!navigator.clipboard) return;
        navigator.clipboard.writeText(pretty.join('\n')).then(function () {
          copyBtn.textContent = 'Скопировано';
          setTimeout(function () { copyBtn.textContent = 'Скопировать'; }, 2000);
        }, function () {});
      });
    }

    var dl = $('recovery-download');
    if (dl) {
      dl.href = URL.createObjectURL(new Blob([fileText], { type: 'text/plain;charset=utf-8' }));
      dl.download = 'eduson-recovery-codes.txt';
    }

    ack.addEventListener('change', function () { go.disabled = !ack.checked; });
    go.addEventListener('click', function () { window.location = redirect; });

    var enter = $('twofa-enter');
    if (enter) enter.classList.add('hidden');
    var choose = $('twofa-choose');
    if (choose) choose.classList.add('hidden');
    var head = $('twofa-h');
    if (head) head.textContent = 'Сохраните резервные коды';
    save.classList.remove('hidden');
  }

  window.RecoveryCodes = { show: show, format: format };
})();
