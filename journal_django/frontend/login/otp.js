// Сегментированный ввод одноразового кода.
//
// Прогрессивное улучшение: находит каждый <input class="code"> и оборачивает его
// в строку из отдельных ячеек. Реальный input остаётся в DOM прозрачным слоем
// поверх ячеек — поэтому нативные фокус, вставка (paste), автозаполнение
// autocomplete="one-time-code" и чтение/запись .value продолжают работать.
// Логику входа (login.js / set-password.js) не трогаем: они по-прежнему читают
// и обнуляют $('f-code').value, а ячейки перерисовываются автоматически.
//
// window.Otp.setState(input, state) — состояние отправки кода:
//   idle    обычный ввод;
//   loading волна по ячейкам, форма заблокирована (повторно код не отправить);
//   done    код принят, идёт переход в кабинет;
//   fail    ячейки вздрагивают и краснеют; снимается, как только код начали править.
(function () {
  'use strict';
  var LEN = 6; // TOTP и email-код — 6 цифр
  var LABELS = { loading: 'Проверяем…', done: '✓ Вход выполнен' };

  function setState(input, state) {
    var wrap = input.closest('.otp');
    if (!wrap) return;
    var locked = state === 'loading' || state === 'done';
    if (state === 'idle') delete wrap.dataset.state;
    else wrap.dataset.state = state;
    input.readOnly = locked;
    if (input.form) input.form.setAttribute('aria-busy', String(locked));

    // «Назад» лежит вне формы, поэтому блокируем кнопки всего экрана.
    var scope = input.closest('.panel') || input.form;
    if (scope) {
      scope.querySelectorAll('button').forEach(function (b) { b.disabled = locked; });
    }

    var submit = input.form && input.form.querySelector('[type="submit"]');
    if (submit) {
      if (!submit.dataset.label) submit.dataset.label = submit.textContent;
      submit.textContent = LABELS[state] || submit.dataset.label;
      submit.classList.toggle('is-busy', locked);
    }

    if (state === 'fail') {
      input.focus();
      input.select(); // следующая цифра заменит неверный код целиком
    }
  }

  function isBusy(input) {
    var wrap = input.closest('.otp');
    return !!wrap && (wrap.dataset.state === 'loading' || wrap.dataset.state === 'done');
  }

  window.Otp = { setState: setState, isBusy: isBusy };

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
      if (wrap.dataset.state === 'fail') setState(input, 'idle');
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
        set: function (val) {
          desc.set.call(this, val);
          if (!val && wrap.dataset.state === 'fail') setState(input, 'idle');
          render();
        },
      });
    }
    // form.reset() обнуляет значение на нативном уровне в обход сеттера — ловим отдельно.
    if (input.form) {
      input.form.addEventListener('reset', function () {
        setState(input, 'idle');
        setTimeout(render, 0);
      });
    }
    // Возврат кнопкой «Назад» из кабинета поднимает страницу из кеша браузера как есть,
    // и без этого форма осталась бы заблокированной.
    window.addEventListener('pageshow', function (e) {
      if (e.persisted) setState(input, 'idle');
    });

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
