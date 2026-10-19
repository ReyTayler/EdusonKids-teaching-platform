const $ = (id) => document.getElementById(id);
document.getElementById('yr').textContent = new Date().getFullYear();

const screens = { role: $('screen-role'), login: $('screen-login'), twofa: $('screen-2fa') };
function show(name) { for (const k in screens) screens[k].classList.toggle('hidden', k !== name); }

let role = null;
let challenge = null;     // login_challenge: verify (вход) ИЛИ enroll (первичная настройка)
let enableToken = null;   // токен для 2fa/enable при enrollment: enroll (totp) | email2fa (email)
let enroll = false;

async function post(path, body) {
  const r = await fetch(path, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  // Не падать молча на не-JSON ответе (HTML-страница 500 и т.п.): вернуть j=null,
  // чтобы вызывающий показал ошибку, а не «ничего не происходит».
  const t = await r.text();
  let j = null;
  try { j = t ? JSON.parse(t) : null; } catch (_) { j = null; }
  return { ok: r.ok, status: r.status, j };
}
function err(id, msg) { const e = $(id); e.textContent = msg; e.classList.remove('hidden'); }
function clr(id) { const e = $(id); e.textContent = ''; e.classList.add('hidden'); }

document.querySelectorAll('.card').forEach((c) => c.addEventListener('click', () => {
  role = c.dataset.role;
  $('login-kicker').textContent = role === 'teacher' ? 'КАБИНЕТ ПРЕПОДАВАТЕЛЯ' : 'КАБИНЕТ УПРАВЛЕНИЯ';
  $('login-h').textContent = role === 'teacher' ? 'Войти как преподаватель' : 'Войти как админ/менеджер';
  clr('login-err'); $('login-form').reset(); show('login');
}));
document.querySelectorAll('[data-back]').forEach((b) => b.addEventListener('click', () => show('role')));

$('login-form').addEventListener('submit', async (ev) => {
  ev.preventDefault(); clr('login-err');
  const email = $('f-email').value.trim();
  const password = $('f-pass').value;
  if (!email || !password) return err('login-err', 'Заполните email и пароль');
  const { ok, status, j } = await post('/api/auth/login', { email, password, role });
  if (ok && j.redirect) return (window.location = j.redirect);
  if (status === 429) return err('login-err', (j && j.error) || 'Слишком много попыток, попробуйте позже');
  if (j && j.twofa_required) { challenge = j.challenge_token; enroll = false; openTwofa(j.method); return; }
  if (j && j.twofa_enrollment_required) { challenge = j.challenge_token; enroll = true; openEnroll(); return; }
  err('login-err', (j && j.error) || 'Ошибка входа');
});

// --- Вход: ввод кода уже настроенным методом (без выбора) ---
function openTwofa(method) {
  clr('twofa-err'); $('twofa-form').reset(); $('twofa-qr-wrap').classList.add('hidden');
  $('twofa-choose').classList.add('hidden');
  $('twofa-enter').classList.remove('hidden');
  $('twofa-back').classList.add('hidden');
  // Резервный код доступен только при ВХОДЕ: при первичной настройке (enrollment)
  // кодов ещё нет — их выдаёт сам факт включения 2FA.
  setRecoveryMode(false);
  $('use-recovery').classList.remove('hidden');
  $('twofa-h').textContent = 'Введите код';
  if (method === 'email') {
    $('twofa-hint').textContent = 'Мы отправили код на вашу почту.';
    $('email-resend').classList.remove('hidden');
  } else {
    $('twofa-hint').textContent = 'Код из приложения-аутентификатора.';
    $('email-resend').classList.add('hidden');
  }
  show('twofa');
}

// --- Резервный код: отдельное поле вместо сегментированного ---
// otp.js превращает #f-code в 6 ячеек и вырезает всё, кроме цифр, поэтому
// резервный код (10 символов с буквами) туда физически не ввести. Переключатель
// прячет сегментированное поле и показывает обычное текстовое; запрос уходит тот
// же — сервер сам сверяет сначала TOTP, потом резервные коды.
let recoveryMode = false;

function setRecoveryMode(on) {
  recoveryMode = on;
  const code = $('f-code');
  const rec = $('f-recovery');
  const cells = code.closest('.otp');   // обёртка, созданная otp.js (может отсутствовать)
  (cells || code).classList.toggle('hidden', on);
  rec.classList.toggle('hidden', !on);
  $('use-recovery').textContent = on ? '← Ввести код подтверждения' : 'Ввести резервный код';
  clr('twofa-err');
  if (on) { rec.value = ''; rec.focus(); } else { code.value = ''; }
}

$('use-recovery').addEventListener('click', () => setRecoveryMode(!recoveryMode));

// --- Enrollment: явный выбор метода 2FA (без метода по умолчанию) ---
function openEnroll() {
  clr('twofa-err'); $('twofa-form').reset();
  $('twofa-h').textContent = 'Настройте 2FA';
  setRecoveryMode(false);
  $('use-recovery').classList.add('hidden');
  showChoose();
  show('twofa');
}
function showChoose() {
  clr('choose-err');
  $('twofa-choose').classList.remove('hidden');
  $('twofa-enter').classList.add('hidden');
}

document.querySelectorAll('#twofa-choose [data-method]').forEach((b) =>
  b.addEventListener('click', () => chooseMethod(b.dataset.method)));

// Индикатор ожидания: email-метод синхронно шлёт письмо по SMTP (~1-3 с).
function setChooseBusy(busy, msg) {
  document.querySelectorAll('#twofa-choose [data-method]').forEach((b) => { b.disabled = busy; });
  const p = $('choose-pending');
  if (busy) { p.textContent = msg || ''; p.classList.remove('hidden'); }
  else { p.classList.add('hidden'); }
}

async function chooseMethod(method) {
  clr('choose-err');
  setChooseBusy(true, method === 'email' ? 'Отправляем код на почту…' : 'Готовим настройку…');
  const { ok, j } = await post('/api/auth/2fa/setup', { challenge_token: challenge, method });
  setChooseBusy(false);
  if (!ok || !j) { return err('choose-err', (j && j.error) || 'Ошибка настройки 2FA'); }
  $('twofa-choose').classList.add('hidden');
  $('twofa-enter').classList.remove('hidden');
  $('twofa-back').classList.remove('hidden');
  $('f-code').value = ''; clr('twofa-err');
  if (method === 'totp') {
    $('twofa-hint').textContent = 'Отсканируйте QR в приложении (Google Authenticator / Яндекс.Ключ) и введите код.';
    $('twofa-qr').src = j.qr; $('twofa-qr-wrap').classList.remove('hidden');
    $('email-resend').classList.add('hidden');
    enableToken = challenge;            // totp: enable enroll-токеном
  } else {
    $('twofa-hint').textContent = 'Мы отправили 6-значный код на вашу почту. Введите его ниже.';
    $('twofa-qr-wrap').classList.add('hidden');
    $('email-resend').classList.remove('hidden');
    enableToken = j.challenge_token;    // email: enable email2fa-токеном из setup
  }
}

$('twofa-back').addEventListener('click', showChoose);

$('twofa-form').addEventListener('submit', async (ev) => {
  ev.preventDefault(); clr('twofa-err');
  // Дефисы в резервном коде — оформление показа (recovery.js), сервер их не знает.
  const code = recoveryMode
    ? $('f-recovery').value.trim().replace(/[\s-]/g, '')
    : $('f-code').value.trim();
  if (!code) return err('twofa-err', recoveryMode ? 'Введите резервный код' : 'Введите код');
  const path = enroll ? '/api/auth/2fa/enable' : '/api/auth/login/2fa';
  const token = enroll ? enableToken : challenge;
  const { ok, j } = await post(path, { challenge_token: token, code });
  if (!ok) return err('twofa-err', (j && j.error) || 'Неверный код');
  if (enroll && j.recovery_codes) {
    window.RecoveryCodes.show(j.recovery_codes, j.redirect);
    return;
  }
  if (j.redirect) window.location = j.redirect;
});

$('email-resend').addEventListener('click', async () => {
  // Enrollment: resend через enroll login_challenge (challenge) → новый email2fa-токен.
  // Вход: resend через текущий challenge (поведение без изменений).
  const { ok, j } = await post('/api/auth/2fa/email/send', { challenge_token: challenge });
  if (ok && j.challenge_token) {
    if (enroll) enableToken = j.challenge_token; else challenge = j.challenge_token;
    err('twofa-err', 'Код отправлен повторно');
  }
});
