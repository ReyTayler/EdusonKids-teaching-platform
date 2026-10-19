/**
 * Стартовый экран (#splash из index.html) — снятие.
 *
 * Разметка живёт в index.html, потому что должна быть на экране до загрузки
 * бандла; снимаем её отсюда, когда приложение готово рисовать (AuthGate знает
 * этот момент: роль получена либо стало ясно, что пользователь не авторизован).
 */

/** Минимальная «жизнь» экрана: при быстром ответе API он иначе мигнёт. */
const MIN_VISIBLE_MS = 600;
/** Должно совпадать с transition в #splash (index.html). */
const FADE_MS = 300;

let done = false;

/**
 * Убрать стартовый экран: плавно погасить и удалить узел.
 * Идемпотентна — повторные вызовы (ре-рендеры, StrictMode) ничего не делают.
 */
export function hideSplash(): void {
  if (done) return;
  done = true;

  const el = document.getElementById('splash');
  if (!el) return;

  // performance.now() отсчитывается от начала загрузки документа, поэтому это
  // ровно «сколько экран уже провисел», без собственной точки отсчёта.
  const wait = Math.max(0, MIN_VISIBLE_MS - performance.now());
  window.setTimeout(() => {
    el.classList.add('is-hidden');
    window.setTimeout(() => el.remove(), FADE_MS);
  }, wait);
}
