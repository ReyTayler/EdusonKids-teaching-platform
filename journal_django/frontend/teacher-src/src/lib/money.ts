/**
 * Форматирование денег для кабинета преподавателя.
 *
 * Сервер отдаёт суммы строками с масштабом ('800.00', '24800.00') — так же, как
 * во всём teacher-контракте. Разбирать их по месту через Number().toFixed()
 * нельзя: получится «24800 ₽» без разрядов и разное оформление на разных
 * экранах. Единственная точка форматирования — здесь.
 */

/** Неразрывный пробел: число не должно разрываться посреди себя при переносе. */
const NBSP = ' ';

/** '24800.00' → '24 800 ₽'; '777.50' → '777,50 ₽'. */
export function formatMoney(value: string | number): string {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return '—';

  const negative = amount < 0;
  const abs = Math.abs(amount);
  const whole = Math.trunc(abs);
  const kopecks = Math.round((abs - whole) * 100);

  const grouped = String(whole).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
  const body = kopecks
    ? `${grouped},${String(kopecks).padStart(2, '0')}`
    : grouped;

  return `${negative ? '−' : ''}${body} ₽`;
}

/** Удержание со знаком минус: '160.00' → '−160 ₽'. */
export function formatDeduction(value: string | number): string {
  return formatMoney(-Math.abs(Number(value)));
}

/** true, если сумма строго больше нуля (штраф есть / выплата есть). */
export function isPositive(value: string | number): boolean {
  return Number(value) > 0;
}
