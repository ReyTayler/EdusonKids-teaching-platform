# Аутентификация и роли (RBAC)

Спека: `docs/superpowers/specs/2026-06-06-rbac-unified-auth-design.md`

## Как работает

Единая страница `/login` для всех ролей: выбор роли → email+пароль → 2FA (TOTP или email-OTP).

**Сессия** — HMAC-cookie `session`, payload `{account_id, role}`, `Path=/`, HttpOnly, SameSite=Strict (+Secure в prod). Подпись через `ADMIN_COOKIE_SECRET`.

**Роли**: `teacher`, `manager`, `admin`.

**2FA**: обязательна для admin/manager (enrollment при первом входе), опциональна для teacher. TOTP через `otplib` v13, email-OTP через SMTP.

**Lockout + rate-limit**: счётчик неудачных попыток в `accounts`; `express-rate-limit` на `/login`, `/login/2fa`, `/2fa/email/send`.

**Audit-log**: события входа/2FA/блокировок/действий → `security_audit_log` (миграция 014).

**Consent** (миграция 015): `students.consent_*` — фиксация согласия на обработку ПДн.

## Гейтинг по ролям (services/auth.js)

- `/api/*` (teacher) → `requireRole('teacher')`
- `/api/admin/*` → `requireRole('manager','admin')`
- `/api/admin/accounts`, `/api/admin/audit-log` → дополнительно `requireRole('admin')`

**⚠️ Порядок mount критичен**: `/api/admin` должен монтироваться ДО `/api`, иначе teacher-guard (`requireRole('teacher')`) перехватит admin-запросы и вернёт 403.

## Таблица accounts (миграция 013)

`email` UNIQUE, `password_hash` (bcrypt), `role`, `teacher_id`, 2FA-поля, lockout-поля.  
`account_recovery_codes` — одноразовые коды восстановления.

## Зависимости

`otplib`, `qrcode`, `nodemailer`, `express-rate-limit`

## Legacy-раздел (до RBAC, не актуален)

Cookie называлась `admin_session`, payload `{user, iat, exp}`, Path=/api/admin. Теперь всё через `session` с `account_id`.
