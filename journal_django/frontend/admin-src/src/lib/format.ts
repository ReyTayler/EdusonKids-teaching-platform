/**
 * Сегодняшняя дата по МСК в формате 'YYYY-MM-DD'.
 * Intl.DateTimeFormat с явным timeZone игнорирует часовой пояс ОС/браузера —
 * в отличие от ручной арифметики с getTimezoneOffset(), которая давала
 * двойной сдвиг на машинах с локальным поясом Europe/Moscow.
 */
export function todayMSK(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Moscow',
    year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
}

/**
 * ISO-дата ('YYYY-MM-DD') по МСК из ISO-даты-времени с бэкенда.
 * Нужна там, где datetime показывается календарём: сам инпут работает только с
 * 'YYYY-MM-DD', а браузер администратора может стоять в любой зоне — без явной
 * Europe/Moscow дата уезжала бы на сутки у всех восточнее МСК.
 */
export function isoDateMSK(s: string | null | undefined): string {
  if (!s) return '';
  const d = new Date(s);
  if (isNaN(d.getTime())) return '';
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Moscow',
    year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(d);
}

/**
 * Текущее время по МСК в минутах от полуночи (09:30 → 570).
 * Часовой пояс задан явно: у администратора на машине может стоять любой, а
 * расписание школы живёт по Москве. hourCycle 'h23' — чтобы полночь пришла
 * как '00', а не '24' (hour12: false этого не гарантирует).
 */
export function minutesNowMSK(): number {
  const hm = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(new Date());
  const [h, m] = hm.split(':').map(Number);
  if (!Number.isFinite(h) || !Number.isFinite(m)) return 0;
  return (h % 24) * 60 + m;
}

/** 'HH:MM' → минуты от полуночи; null, если формат не тот. */
export function parseHHMM(time: string | null | undefined): number | null {
  const m = /^(\d{1,2}):(\d{2})/.exec(String(time ?? '').trim());
  if (!m) return null;
  const h = +m[1], min = +m[2];
  if (h > 23 || min > 59) return null;
  return h * 60 + min;
}

export function fmtDate(s: string | Date | null | undefined): string {
  if (!s) return '—';
  const str = String(s);
  if (/^\d{4}-\d{2}-\d{2}$/.test(str)) {
    const [y, m, d] = str.split('-');
    return `${d}.${m}.${y}`;
  }
  const d = new Date(str);
  if (!isNaN(d.getTime())) {
    return d.toLocaleDateString('ru-RU', {
      timeZone: 'Europe/Moscow',
      day: '2-digit', month: '2-digit', year: 'numeric',
    });
  }
  return str;
}

/** Дата-время по МСК с секундами (журналы ИБ/изменений). */
export function fmtDateTime(s: string | null | undefined): string {
  if (!s) return '—';
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString('ru-RU', {
    timeZone:  'Europe/Moscow',
    day:       '2-digit',
    month:     '2-digit',
    year:      'numeric',
    hour:      '2-digit',
    minute:    '2-digit',
    second:    '2-digit',
  });
}

/** Компактное дата-время по МСК для лент: '06.07 11:11'. */
export function fmtDateTimeShort(s: string | null | undefined): string {
  if (!s) return '—';
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  const dm = d.toLocaleDateString('ru-RU', {
    timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit',
  });
  const hm = d.toLocaleTimeString('ru-RU', {
    timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit',
  });
  return `${dm} ${hm}`;
}

export function escapeHtml(s: unknown): string {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string));
}

export function fmtRub(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  // 7 250 ₽ / 7 250,50 ₽
  const rounded = Math.round(n * 100) / 100;
  const intPart = Math.floor(Math.abs(rounded)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const fracCents = Math.round((Math.abs(rounded) - Math.floor(Math.abs(rounded))) * 100);
  const sign = rounded < 0 ? '−' : '';
  const fracPart = fracCents ? `,${String(fracCents).padStart(2, '0')}` : '';
  return `${sign}${intPart}${fracPart} ₽`;
}

export function fmtLessons(value: number): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return '0';
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(1).replace('.', ',');
}

/**
 * Полных лет по дате рождения ('YYYY-MM-DD'), по МСК. null — если даты нет или
 * она не разобралась.
 *
 * Поле `age` удалено из модели ученика: возраст — производное от даты рождения,
 * а хранить снимок значит держать его вечно устаревающим. Считаем на клиенте,
 * чтобы не гонять вычисление на сервере (по просьбе).
 */
export function ageFromBirthDate(birthDate: string | null | undefined): number | null {
  if (!birthDate) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(birthDate).trim());
  if (!m) return null;
  const by = +m[1], bm = +m[2], bd = +m[3];
  // «Сегодня» по МСК, чтобы возраст не прыгал в день рождения из-за пояса машины.
  const [ty, tm, td] = todayMSK().split('-').map(Number);
  let age = ty - by;
  if (tm < bm || (tm === bm && td < bd)) age -= 1;
  return age >= 0 && age < 150 ? age : null;
}

/** «14 лет» / «21 год» / «2 года» — возраст с правильным словом, или '—'. */
export function fmtAge(birthDate: string | null | undefined): string {
  const a = ageFromBirthDate(birthDate);
  if (a == null) return '—';
  const mod10 = a % 10, mod100 = a % 100;
  const word = (mod10 === 1 && mod100 !== 11) ? 'год'
    : ([2, 3, 4].includes(mod10) && ![12, 13, 14].includes(mod100)) ? 'года'
    : 'лет';
  return `${a} ${word}`;
}

const MONTHS_GENITIVE = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

/**
 * Месяц без дня: '2026-09-01' → «сентября 2026». Нужен там, где дата означает
 * именно месяц (frozen_until_month у сделки — всегда 1-е число), и fmtDate с
 * его «01.09.2026» вводил бы в заблуждение точностью до дня.
 * Пустое/некорректное значение → '—' (как fmtDate).
 */
export function fmtMonth(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})/.exec(String(iso ?? '').trim());
  if (!m) return '—';
  const month = MONTHS_GENITIVE[+m[2] - 1];
  return month ? `${month} ${m[1]}` : '—';
}

const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн',
  'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

/**
 * Вчерашняя дата по МСК в формате 'YYYY-MM-DD'.
 * Считаем от todayMSK(), а не от локальной полуночи браузера: у сотрудника в
 * другом поясе «вчера» иначе разъезжается с тем, что показывает бэкенд.
 */
function yesterdayMSK(): string {
  const [y, m, d] = todayMSK().split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() - 1);
  return dt.toISOString().slice(0, 10);
}

/** Только время по МСК: '09:38'. Время в пузыре переписки — дата уже в разделителе дня. */
export function fmtTimeMSK(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('ru-RU', {
    timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit',
  });
}

/**
 * Дата-время для ленты истории: «сегодня, 13:19», «вчера, 09:36», «26 авг, 13:19».
 * Год добавляем, только если он не текущий: в ленте почти всегда свежие записи,
 * и «2026» в каждой строке — шум.
 */
export function fmtRelativeDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const day = isoDateMSK(iso);
  if (!day) return String(iso);
  const time = fmtTimeMSK(iso);
  if (day === todayMSK()) return `сегодня, ${time}`;
  if (day === yesterdayMSK()) return `вчера, ${time}`;
  const [y, m, d] = day.split('-');
  const month = MONTHS_SHORT[+m - 1] ?? m;
  const year = y === todayMSK().slice(0, 4) ? '' : ` ${y}`;
  return `${+d} ${month}${year}, ${time}`;
}

/**
 * Разделитель дня в переписке: «26 августа 2026», «Сегодня», «Вчера».
 * Год здесь оставляем всегда: разделитель один на всю пачку сообщений, лишним
 * шумом не становится, а без года старая переписка читается неоднозначно.
 */
export function fmtDayDivider(iso: string | null | undefined): string {
  if (!iso) return '—';
  const day = isoDateMSK(iso);
  if (!day) return '—';
  if (day === todayMSK()) return 'Сегодня';
  if (day === yesterdayMSK()) return 'Вчера';
  const [y, m, d] = day.split('-');
  const month = MONTHS_GENITIVE[+m - 1];
  return month ? `${+d} ${month} ${y}` : fmtDate(day);
}

/**
 * Подпись ученика в выпадающих списках. Имена не уникальны (у Eduson записано
 * только имя ребёнка, тёзки обычны), поэтому рядом с именем — родитель и номер
 * карточки: по ним тёзок можно отличить, а значение списка всё равно id.
 */
export function studentOptionLabel(
  s: { id: number; full_name: string; parent1_name?: string | null },
): string {
  const parent = s.parent1_name ? `, родитель: ${s.parent1_name}` : '';
  return `${s.full_name} (№${s.id}${parent})`;
}
