import { useMemo, useState } from 'react';
import { Modal } from '../ui/Modal';
import { Field } from '@shared/components/form/Field';
import { DateInput } from '@shared/components/form/DateInput';
import { Checkbox } from '@shared/components/form/Checkbox';
import { TextInput } from '@shared/components/form/TextInput';
import { ApiError, LESSON_ALREADY_RECORDED, REQUEST_TIMEOUT } from '@shared/lib/api';
import { useToast } from '@shared/components/ui/Toast';
import { useSubmitLesson } from '../../hooks/useSubmitLesson';
import { useGroupDirections } from '../../hooks/useGroupDirections';
import { isoDate, todayMsk } from '../../lib/dates';
import { calcPayment, fmtNum, getCourseLimit, isHalfLesson, lessonNumber, rub } from '../../lib/teacher-calc';
import type { GroupData, SubmitPayload, SubmitResult } from '../../lib/types';

/** Похожа ли строка на http(s)-ссылку — только мягкая подсказка, не блокирует сохранение. */
function looksLikeUrl(value: string): boolean {
  try {
    const u = new URL(value);
    return u.protocol === 'http:' || u.protocol === 'https:';
  } catch {
    return false;
  }
}

type BlockReason = 'skip' | 'unpaid' | 'locked';

/**
 * Причина, по которой ученика нельзя отметить НА ЭТОМ уроке: неоплачиваемый пропуск
 * (менеджер пометил, ученик этот урок не посещает), перевод-в-ожидании (группа ещё не
 * догнала его прогресс) либо нет оплаты.
 *
 * Две из трёх зависят от НОМЕРА урока, поэтому считаются здесь, а не приезжают
 * готовыми флагами с сервера: срез данных (/api/getData) знает только «следующий по
 * прогрессу» номер — max(lessonsDone)+шаг, — а урок записывается по номеру ИЗ ПЛАНА
 * занятия. Расходятся они штатно (переведённый ученик тянет max вверх, сожжённый
 * пропуск даёт +1 без занятия, перенос двигает план), и тогда флаг с сервера отвечал
 * бы про другой урок, чем заполняемый: помеченный ученик выглядел бы обычным, а
 * record_lesson всё равно исключил бы его — молча и с другой суммой в превью.
 */
function blockedReason(
  s: { id: number; remaining: number; lockedThrough: number | null },
  lessonNum: number,
  skipIds: ReadonlySet<number>,
): BlockReason | null {
  if (skipIds.has(s.id)) return 'skip';
  if (s.lockedThrough !== null && lessonNum <= s.lockedThrough) return 'locked';
  if (s.remaining <= 0) return 'unpaid';
  return null;
}

/**
 * Форма записи проведённого урока (submitLesson). Клиентские расчёты (выплата,
 * номер урока, лимит курса) — только превью; сервер авторитетен и может
 * посчитать иначе (штраф, округления). См. teacher-calc.ts.
 *
 * isSubstitution — ТОЛЬКО отображение (подзаголовок «Замена»): сервер выводит
 * замену сам из planned_lessons (назначение «Сменить преподавателя» в admin),
 * клиентские isSubstitution/originalTeacher в payload не отправляются (API — 400).
 * Тип урока (перенос) сервер тоже выводит сам — из moved_from_date плановой
 * строки, поэтому выбора «По расписанию / Перенос» в форме больше нет.
 */
export function LessonForm({
  group,
  groupData,
  initialDate,
  plannedLessonId,
  plannedLessonNumber,
  isSubstitution,
  onClose,
}: {
  group: string;
  groupData: GroupData;
  /** Предзаполнение даты (клик по занятию в календаре); по умолчанию — сегодня МСК. */
  initialDate?: string;
  /**
   * Позиция курса отмечаемого занятия (Occurrence.plannedLessonId) из календаря.
   * Сервер берёт из неё номер урока и закрепляет за ней факт, поэтому повторная
   * отправка упирается в занятую позицию (409) вместо создания второго урока.
   * Шлёт календарь — единственный вход в запись урока (с 2026-09-16 «Мои уроки»
   * стали историей и форму не открывают). Не задан только там, где позиции нет
   * вовсе (группа без плана занятий): тогда сервер резолвит её по дате.
   */
  plannedLessonId?: number | null;
  /** Номер урока по плану (Occurrence.lessonNumber) — показываем его вместо
   *  расчёта по прогрессу, чтобы превью совпадало с тем, что запишет сервер. */
  plannedLessonNumber?: number | null;
  isSubstitution?: boolean;
  onClose: () => void;
}) {
  const { toast } = useToast();
  const submitLesson = useSubmitLesson();

  const todayIso = useMemo(() => isoDate(todayMsk()), []);
  const [date, setDate] = useState(initialDate ?? todayIso);
  const [recordUrl, setRecordUrl] = useState('');
  // По умолчанию «пришли все»; заблокированных гасит presentOf (ниже), а не это
  // состояние: номер урока, от которого зависят блокировки, может приехать позже
  // самого первого рендера (карта направлений грузится асинхронно).
  const [present, setPresent] = useState<Record<number, boolean>>(() =>
    Object.fromEntries(groupData.students.map((s) => [s.id, true])),
  );
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Первичный источник half-lesson/лимита курса — карта /api/group-directions
  // (кэш общий с GroupsPage). Regex-эвристика — только фолбэк
  // на случай, если карта ещё не загрузилась или группы в ней нет.
  const { data: dirData } = useGroupDirections();
  const dir = dirData?.groups[group];
  const isHalf = dir ? dir.lessonDurationMinutes === 45 : isHalfLesson(group);
  const { done, step, next: nextByProgress } = lessonNumber(groupData.students, isHalf);
  // Номер урока: план авторитетен. Раньше превью считалось из прогресса учеников
  // (max(lessonsDone)+step), и это же значение писал сервер — из-за чего номер
  // «уезжал», стоило прогрессу разойтись с планом. Теперь сервер берёт номер из
  // позиции курса, поэтому и показывать надо его, иначе превью врёт.
  // Фолбэк на расчёт — группы без плана (там сервер тоже считает так).
  const next = plannedLessonNumber ?? nextByProgress;
  const limit = dir ? dir.totalLessons : getCourseLimit(group);

  // Маркеры «неоплачиваемый пропуск» — для ТОГО номера урока, который заполняется
  // (тот же, что показан преподавателю и под которым сервер запишет урок).
  // По id, а не по имени: тёзки в одной группе иначе блокировались бы вместе.
  // Запасной путь по именам — только если сервер не прислал skipIds вовсе
  // (старый ответ); для тёзок он неточен, но лучше, чем потерять пометку.
  const skipIds = useMemo(() => {
    if (groupData.skipIds) return new Set(groupData.skipIds[String(next)] ?? []);
    const names = new Set(groupData.skips?.[String(next)] ?? []);
    return new Set(groupData.students.filter((s) => names.has(s.name)).map((s) => s.id));
  }, [groupData.skipIds, groupData.skips, groupData.students, next]);
  const reasonOf = useMemo(() => {
    const m = new Map<number, BlockReason | null>();
    for (const s of groupData.students) m.set(s.id, blockedReason(s, next, skipIds));
    return m;
  }, [groupData.students, next, skipIds]);
  const isBlocked = (s: { id: number }) => reasonOf.get(s.id) != null;
  // Отметка «пришёл» у заблокированного ученика силы не имеет — гасим её при чтении,
  // а не правкой состояния: блокировка может появиться уже после первого рендера.
  const presentOf = (id: number) => reasonOf.get(id) == null && !!present[id];

  const blockedStudents = groupData.students.filter((s) => isBlocked(s));
  // Разделяем по причине: неоплата (→ к менеджеру) vs перевод-в-ожидании vs
  // неоплачиваемый пропуск (менеджер пометил — ученик этот урок не посещает).
  const unpaidStudents = blockedStudents.filter((s) => reasonOf.get(s.id) === 'unpaid');
  const lockedStudents = blockedStudents.filter((s) => reasonOf.get(s.id) === 'locked');
  const skipStudents = blockedStudents.filter((s) => reasonOf.get(s.id) === 'skip');

  // Переведённого ученика, ждущего, пока группа догонит его прогресс, на этом уроке
  // как будто нет: record_lesson выкидывает его из attendance ДО подсчёта
  // total_students/present_count/payroll. Значит и превью выплаты обязано считать по
  // ТОМУ ЖЕ составу, иначе преподаватель видит не ту сумму, что реально получит:
  // ростер 3 чел., один переведён → сервер платит как за малую группу (2 из 2 = 500₽),
  // а превью по ростеру из 3 показывало бы 200×2 = 400₽.
  // Неоплаченные (reason 'unpaid') из total НЕ вычитаются: сервер оставляет их в
  // total_students (фильтруется только перевод), они лишь не могут быть «пришёл».
  // Неоплачиваемый пропуск (skip) сервер исключает из зарплаты — вычитаем из total.
  const total = groupData.students.length - lockedStudents.length - skipStudents.length;
  const presentCount = groupData.students.reduce((n, s) => n + (presentOf(s.id) ? 1 : 0), 0);
  const absentCount = total - presentCount;
  const payment = calcPayment(total, presentCount, isHalf);
  const eligibleStudents = groupData.students.filter((s) => !isBlocked(s));
  const allPresent = eligibleStudents.length > 0 && eligibleStudents.every((s) => present[s.id]);

  const limitExceeded = limit !== null && Math.ceil(next) > limit;
  const limitMessage = useMemo(() => {
    if (limit === null) return null;
    const prefix = `По данному курсу максимум ${limit} уроков. Пройдено: ${done}.`;
    const remainingSteps = limit - done;
    if (remainingSteps > 0 && remainingSteps < step) {
      return `${prefix} Недостаточно для ${isHalf ? 'полурока' : 'урока'} (нужно ${step}, доступно ${remainingSteps.toFixed(1)}).`;
    }
    return `${prefix} Заполнение заблокировано.`;
  }, [limit, done, step, isHalf]);

  const toggleAll = () => {
    const nextVal = !allPresent;
    setPresent(Object.fromEntries(
      groupData.students.map((s) => [s.id, isBlocked(s) ? false : nextVal]),
    ));
  };

  // Нельзя записать урок без единого присутствующего ученика (все «Не пришёл»):
  // нет посещаемости — нет урока. Бэк тоже гейтит (submit_lesson), это UX-слой.
  const noOnePresent = presentCount === 0;

  const handleSubmit = () => {
    if (limitExceeded || noOnePresent || submitLesson.isPending) return;
    setSubmitError(null);

    const payload: SubmitPayload = {
      group,
      date,
      students: groupData.students.map((s) => ({ id: s.id, name: s.name, present: presentOf(s.id) })),
      ...(recordUrl.trim() ? { recordUrl: recordUrl.trim() } : {}),
      ...(plannedLessonId != null ? { plannedLessonId } : {}),
    };

    submitLesson.mutate(payload, {
      // result.success === true (не просто `if (result.success)`): при
      // strictNullChecks:false (см. tsconfig.json — общая настройка проекта)
      // TS не narrow-ит union по boolean-дискриминанту через truthy-проверку.
      onSuccess: (result: SubmitResult) => {
        if (result.success === true) {
          toast(`Урок записан · ${rub(result.payment)}`, 'ok');
          onClose();
        } else {
          // Устаревший состав распознаём по коду, не по тексту; данные группы уже
          // обновляются (см. useSubmitLesson). Прочие отказы — текст сервера как есть.
          setSubmitError(result.code === 'roster_stale'
            ? 'Состав группы изменился — список обновлён, проверьте отметки и сохраните ещё раз.'
            : result.error);
        }
      },
      onError: (err) => {
        // Урок за это занятие уже записан — типично после потерянного ответа:
        // предыдущая отправка дошла, подтверждение не вернулось. Работа сделана,
        // поэтому закрываем форму и говорим спокойно, а не красной ошибкой:
        // иначе учитель решит, что не сохранилось, и нажмёт ещё раз.
        if (err instanceof ApiError && err.code === LESSON_ALREADY_RECORDED) {
          toast(err.message, 'ok');
          onClose();
          return;
        }
        // Ответ не пришёл вовремя. Это НЕ значит, что урок не записался: сервер
        // мог закоммитить и не успеть ответить — так и вышел инцидент ПГ215.
        // Говорить «не удалось, попробуйте ещё раз» здесь нельзя, это прямое
        // приглашение создать дубль; отправляем проверить историю.
        if (err instanceof ApiError && err.code === REQUEST_TIMEOUT) {
          setSubmitError(
            'Сервер не ответил вовремя. Урок мог записаться — откройте «Мои уроки» '
            + 'и проверьте, прежде чем отправлять ещё раз.',
          );
          return;
        }
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          setSubmitError('Сессия истекла. Обновите страницу и войдите заново.');
        } else if (err instanceof ApiError) {
          setSubmitError(err.message);
        } else {
          // Сетевой сбой без ответа сервера — та же неизвестность, что и таймаут.
          setSubmitError(
            'Не удалось связаться с сервером. Урок мог записаться — откройте '
            + '«Мои уроки» и проверьте, прежде чем отправлять ещё раз.',
          );
        }
      },
    });
  };

  return (
    <Modal
      title={group}
      subtitle={isSubstitution ? 'Запись урока · замена' : 'Запись урока'}
      onClose={onClose}
      busy={submitLesson.isPending}
    >
      {/* Дата урока = дата занятия по расписанию, менять руками нельзя (иначе штраф за просрочку можно обойти). */}
      <Field label="Дата урока">
        <DateInput value={date} onChange={(e) => setDate(e.target.value)} disabled />
      </Field>

      <div>
        <div className="lf-students-hdr">
          <span className="t-sec-label">Посещаемость · {total}</span>
          <button
            type="button"
            className="btn ghost sm"
            onClick={toggleAll}
            disabled={eligibleStudents.length === 0}
          >
            {allPresent ? 'Снять всех' : 'Отметить всех'}
          </button>
        </div>
        <div className="lf-students">
          {groupData.students.map((s) => {
            const reason = reasonOf.get(s.id) ?? null;
            const blocked = reason !== null;
            return (
              <div
                key={s.id}
                className={`lf-student${presentOf(s.id) ? ' is-present' : ''}${blocked ? ' is-blocked' : ''}`}
                title={
                  reason === 'skip'
                    ? 'Неоплачиваемый пропуск — ученик этот урок не посещает (отметил менеджер)'
                    : reason === 'locked'
                      ? `Переведён — включится с урока №${(s.lockedThrough ?? 0) + 1}`
                      : reason === 'unpaid'
                        ? 'Нет оплаченных уроков — отметить нельзя'
                        : undefined
                }
              >
                <Checkbox
                  checked={presentOf(s.id)}
                  disabled={blocked}
                  onChange={() => {
                    if (blocked) return;
                    setPresent((p) => ({ ...p, [s.id]: !p[s.id] }));
                  }}
                  label={<span className="lf-student-name">{s.name}</span>}
                />
                <span className="lf-student-state">
                  {reason === 'skip' ? 'Не участвует' : reason === 'locked' ? 'Ожидает перевода' : reason === 'unpaid' ? 'Нет оплаты' : presentOf(s.id) ? 'Пришёл' : 'Не пришёл'}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Баннеры причин блокировки разделены: у переведённого ученика оплата ЕСТЬ
          (он просто ждёт, пока группа догонит его прогресс) — «сообщите менеджеру»
          для него неверно и гоняло бы преподавателя к менеджеру без повода. */}
      {unpaidStudents.length > 0 && (
        <div className="lf-warn">
          Нет оплаченных уроков: {unpaidStudents.map((s) => s.name).join(', ')}. Отметить их нельзя
          {groupData.pm ? ` — сообщите менеджеру ${groupData.pm}.` : ' — сообщите менеджеру.'}
        </div>
      )}

      {skipStudents.length > 0 && (
        <div className="lf-warn">
          Неоплачиваемый пропуск на этом уроке (отметил менеджер):{' '}
          {skipStudents.map((s) => s.name).join(', ')}. Этот урок они не посещают —
          отмечать их не нужно, на выплату они не влияют.
        </div>
      )}

      {lockedStudents.length > 0 && (
        <div className="lf-warn">
          Переведены из другой группы и пока ждут, когда группа догонит их прогресс:{' '}
          {lockedStudents.map((s) => s.name).join(', ')}. Отмечать их не нужно — включатся автоматически.
        </div>
      )}

      {limitExceeded && limitMessage && (
        <div className="lf-error">
          <strong>Лимит курса исчерпан.</strong> {limitMessage}
        </div>
      )}

      {!limitExceeded && noOnePresent && (
        <div className="lf-warn">
          Отметьте хотя бы одного пришедшего ученика — урок без присутствующих сохранить нельзя.
        </div>
      )}

      <div className="lf-record">
        <div className="lf-record-head">
          <span className="t-sec-label">Запись урока</span>
          <span className="lf-record-optional">необязательно</span>
        </div>
        <div className={`lf-record-box${recordUrl.trim() ? (looksLikeUrl(recordUrl.trim()) ? ' is-valid' : ' is-suspect') : ''}`}>
          <svg className="lf-record-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
          </svg>
          <TextInput
            className="lf-record-input"
            type="url"
            inputMode="url"
            value={recordUrl}
            onChange={(e) => setRecordUrl(e.target.value)}
            placeholder="Вставьте ссылку на запись занятия…"
            aria-label="Ссылка на запись урока"
          />
          {recordUrl && (
            <button
              type="button"
              className="lf-record-clear"
              onClick={() => setRecordUrl('')}
              aria-label="Очистить ссылку"
              title="Очистить"
            >
              ×
            </button>
          )}
        </div>
        {recordUrl.trim() !== '' && !looksLikeUrl(recordUrl.trim()) && (
          <span className="lf-record-hint">Похоже, это не ссылка — проверьте, что скопировали адрес целиком.</span>
        )}
      </div>

      <div className="lf-preview">
        <div className="lf-preview-row">
          <span>Пришли {presentCount} / Не пришли {absentCount}</span>
          <span className="lf-preview-money">Выплата {rub(payment)}</span>
        </div>
        <div className="lf-preview-num">
          №{fmtNum(next)}{isHalf ? ' (45 минут)' : ''}
        </div>
      </div>

      {submitError && <div className="lf-error">{submitError}</div>}

      <div className="lf-actions">
        {/* Пока запрос в полёте, уйти из формы нельзя: ответ пришёл бы в
            размонтированный компонент и человек не узнал бы, записался урок или нет. */}
        <button
          type="button"
          className="btn-cancel"
          onClick={onClose}
          disabled={submitLesson.isPending}
        >
          Отмена
        </button>
        <button
          type="button"
          className="btn-save"
          disabled={limitExceeded || noOnePresent || submitLesson.isPending}
          onClick={handleSubmit}
        >
          {submitLesson.isPending ? 'Сохранение…' : 'Сохранить урок'}
        </button>
      </div>
    </Modal>
  );
}
