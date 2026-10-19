// Страница установки пароля по invite-ссылке.
// Два сценария после установки пароля (решает бэкенд invite_accept):
//   - НЕТ 2FA (новая учётка): ЯВНЫЙ выбор метода → 2fa/setup → 2fa/enable.
//   - ЕСТЬ 2FA (сброс пароля): сразу ввод кода уже настроенным методом → login/2fa.
const $ = (id) => document.getElementById(id);
$('yr').textContent = new Date().getFullYear();

const token = new URLSearchParams(location.search).get('token') || '';
let challenge = null;     // login_challenge: enroll (новая 2FA) ИЛИ verify/email2fa (есть 2FA)
let enableToken = null;   // токен для 2fa/enable при enrollment: enroll (totp) | email2fa (email)
let enroll = true;        // true = настройка новой 2FA; false = вход по уже настроенной

const screens = { invalid: $('screen-invalid'), set: $('screen-set'), '2fa': $('screen-2fa') };
function show(name) {
  for (const k in screens) screens[k].classList.toggle('hidden', k !== name);
}
function err(id, msg) { const e = $(id); e.textContent = msg; e.classList.remove('hidden'); }
function clr(id) { const e = $(id); e.textContent = ''; e.classList.add('hidden'); }

async function req(method, path, body) {
  const r = await fetch(path, {
    method,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  // Не падать молча на не-JSON ответе (например HTML-странице 500): вернуть j=null,
  // чтобы вызывающий показал ошибку, а не «ничего не происходит».
  const t = await r.text();
  let j = null;
  try { j = t ? JSON.parse(t) : null; } catch (_) { j = null; }
  return { ok: r.ok, status: r.status, j };
}

// 1. Проверяем валидность invite-ссылки.
(async function init() {
  if (!token) return show('invalid');
  const { j } = await req('GET', `/api/auth/invite?token=${encodeURIComponent(token)}`);
  if (!j || !j.valid) return show('invalid');
  $('set-email').textContent = j.email;
  show('set');
})();

// 2. Установка пароля → дальше зависит от того, есть ли у аккаунта 2FA.
$('set-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  clr('set-err');
  const password = $('f-pass').value;
  if (password.length < 8) return err('set-err', 'Пароль не короче 8 символов');
  const { ok, j } = await req('POST', '/api/auth/invite/accept', { token, password });
  if (!ok || !j || !j.challenge_token) return err('set-err', (j && j.error) || 'Ссылка недействительна');
  challenge = j.challenge_token;
  show('2fa');
  if (j.twofa_required) {        // у аккаунта 2FA уже есть → ввод кода настроенным методом
    enroll = false;
    openVerify(j.method);
  } else {                       // 2FA ещё нет → выбор и настройка метода
    enroll = true;
    showChoose();
  }
});

// 3a. Выбор метода 2FA (новая учётка, без метода по умолчанию).
function showChoose() {
  clr('choose-err');
  $('twofa-choose').classList.remove('hidden');
  $('twofa-enter').classList.add('hidden');
}

// 3b. Вход по УЖЕ настроенной 2FA (сброс пароля): сразу ввод кода, без выбора метода.
function openVerify(method) {
  $('twofa-choose').classList.add('hidden');
  $('twofa-enter').classList.remove('hidden');
  $('twofa-qr-wrap').classList.add('hidden');
  $('twofa-back').classList.add('hidden');   // назад к выбору метода тут нельзя
  $('f-code').value = '';
  clr('twofa-err');
  if (method === 'email') {
    $('twofa-hint').textContent = 'Мы отправили код на вашу почту. Введите его ниже.';
    $('email-resend').classList.remove('hidden');
  } else {
    $('twofa-hint').textContent = 'Введите код из приложения-аутентификатора.';
    $('email-resend').classList.add('hidden');
  }
}

document.querySelectorAll('#twofa-choose [data-method]').forEach((b) =>
  b.addEventListener('click', () => chooseMethod(b.dataset.method)));

// Индикатор ожидания: при email-методе /2fa/setup синхронно шлёт письмо по SMTP
// (~1-3 с) — без обратной связи кажется, что «зависло». Блокируем кнопки + статус.
function setChooseBusy(busy, msg) {
  document.querySelectorAll('#twofa-choose [data-method]').forEach((b) => { b.disabled = busy; });
  const p = $('choose-pending');
  if (busy) { p.textContent = msg || ''; p.classList.remove('hidden'); }
  else { p.classList.add('hidden'); }
}

async function chooseMethod(method) {
  clr('choose-err');
  setChooseBusy(true, method === 'email' ? 'Отправляем код на почту…' : 'Готовим настройку…');
  const { ok, j } = await req('POST', '/api/auth/2fa/setup', { challenge_token: challenge, method });
  setChooseBusy(false);
  if (!ok || !j) return err('choose-err', (j && j.error) || 'Ошибка настройки 2FA');

  $('twofa-choose').classList.add('hidden');
  $('twofa-enter').classList.remove('hidden');
  $('f-code').value = '';
  clr('twofa-err');

  if (method === 'totp') {
    $('twofa-hint').textContent = 'Отсканируйте QR в приложении (Google Authenticator / Яндекс.Ключ) и введите код.';
    $('twofa-qr').src = j.qr;
    $('twofa-qr-wrap').classList.remove('hidden');
    $('email-resend').classList.add('hidden');
    enableToken = challenge;            // totp: enable enroll-токеном
  } else {
    $('twofa-hint').textContent = 'Мы отправили 6-значный код на вашу почту. Введите его ниже.';
    $('twofa-qr-wrap').classList.add('hidden');
    $('email-resend').classList.remove('hidden');
    enableToken = j.challenge_token;    // email: enable email2fa-токеном из setup
  }
}

// Вернуться к выбору метода.
$('twofa-back').addEventListener('click', showChoose);

// Повторная отправка email-кода.
$('email-resend').addEventListener('click', async () => {
  const { ok, j } = await req('POST', '/api/auth/2fa/email/send', { challenge_token: challenge });
  if (ok && j && j.challenge_token) {
    if (enroll) enableToken = j.challenge_token; else challenge = j.challenge_token;
    err('twofa-err', 'Код отправлен повторно');
  } else {
    err('twofa-err', (j && j.error) || 'Не удалось отправить код');
  }
});

// 4. Подтверждение кода. enroll → 2fa/enable (включить новую 2FA + recovery);
//    verify → login/2fa (подтвердить уже настроенную 2FA). Оба выдают сессию + redirect.
$('twofa-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  clr('twofa-err');
  const code = $('f-code').value.trim();
  if (!code) return err('twofa-err', 'Введите код');
  const path = enroll ? '/api/auth/2fa/enable' : '/api/auth/login/2fa';
  const tok = enroll ? enableToken : challenge;
  const { ok, j } = await req('POST', path, { challenge_token: tok, code });
  if (!ok || !j) return err('twofa-err', (j && j.error) || 'Неверный код');
  if (enroll && j.recovery_codes) {
    window.RecoveryCodes.show(j.recovery_codes, j.redirect || '/');
    return;
  }
  window.location = j.redirect || '/';
});
