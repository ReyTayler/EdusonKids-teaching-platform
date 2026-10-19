// Сегментированный ввод одноразового кода.
//
// Прогрессивное улучшение: находит каждый <input class="code"> и оборачивает его
// в строку из отдельных ячеек. Реальный input остаётся в DOM прозрачным слоем
// поверх ячеек — поэтому нативные фокус, вставка (paste), автозаполнение
// autocomplete="one-time-code" и чтение/запись .value продолжают работать.
// Логику входа (login.js / set-password.js) не трогаем: они по-прежнему читают
// и обнуляют $('f-code').value, а ячейки перерисовываются автоматически.
(function () {
  'use strict';
  var LEN = 6; // TOTP и email-код — 6 цифр

  function enhance(input) {
    if (input.dataset.otp) return;
    input.dataset.otp = '1';
    input.maxLength = LEN;
    input.setAttribute('inputmode', 'numeric');
    input.setAttribute('pattern', '[0-9]*');
    if (!input.getAttribute('autocomplete')) input.setAttribute('autocomplete', 'one-time-code');

    var wrap = document.createElement('div');
    wrap.className = 'otp';
    var cells = document.createElement('div');
    cells.className = 'otp-cells';
    for (var i = 0; i < LEN; i++) {
      var cell = document.createElement('div');
      cell.className = 'otp-cell';
      cell.setAttribute('aria-hidden', 'true');
      cells.appendChild(cell);
    }

    // wrap встаёт на место input, затем input переезжает внутрь wrap поверх ячеек.
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(cells);
    wrap.appendChild(input);

    function render() {
      var v = input.value || '';
      var focused = document.activeElement === input;
      var nodes = cells.children;
      for (var i = 0; i < LEN; i++) {
        var c = nodes[i];
        c.textContent = v.charAt(i);
        c.classList.toggle('is-filled', i < v.length);
        // активная ячейка = первая пустая, либо последняя при полном вводе
        var active = focused && (i === Math.min(v.length, LEN - 1));
        c.classList.toggle('is-active', active);
      }
    }

    input.addEventListener('input', function () {
      var clean = (input.value || '').replace(/\D/g, '').slice(0, LEN);
      if (clean !== input.value) input.value = clean; // отфильтровать нецифры
      render();
    });
    input.addEventListener('focus', render);
    input.addEventListener('blur', render);

    // Внешний код делает $('f-code').value = '' напрямую — это не порождает
    // событие input. Перехватываем сеттер value на самом элементе, чтобы
    // программная запись тоже перерисовывала ячейки.
    var desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    if (desc && desc.configurable) {
      Object.defineProperty(input, 'value', {
        configurable: true,
        enumerable: desc.enumerable,
        get: function () { return desc.get.call(this); },
        set: function (val) { desc.set.call(this, val); render(); },
      });
    }
    // form.reset() обнуляет значение на нативном уровне в обход сеттера — ловим отдельно.
    if (input.form) input.form.addEventListener('reset', function () { setTimeout(render, 0); });

    render();
  }

  function init() {
    var inputs = document.querySelectorAll('input.code');
    for (var i = 0; i < inputs.length; i++) enhance(inputs[i]);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
