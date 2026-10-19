export function calcPayment(total: number, present: number, isHalf = false): number {
  if (!Number.isFinite(present) || present === 0) return 0;
  if (isHalf) return 250 * present;
  if (total <= 2) return present === total ? 500 : 300;
  return 200 * present;
}
