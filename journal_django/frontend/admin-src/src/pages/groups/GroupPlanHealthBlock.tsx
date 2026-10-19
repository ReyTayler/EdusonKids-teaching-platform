import { useState } from 'react';
import {
  useGroupPlanHealth, useResyncPlan,
  type PlanHealthFinding, type ResyncExpectedTriple,
} from '../../hooks/useGroupPlanHealth';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { useAuth } from '../../hooks/useAuth';
import { canFixPlan, type Role } from '../../lib/permissions';
import { Dialog } from '../../components/ui/Dialog';
import { Button } from '../../components/ui/Button';
import { BlockLoading } from '../../components/ui/Skeleton';
import { fmtDate } from '../../lib/format';
import { PLAN_HEALTH_CHECK_LABELS, PLAN_HEALTH_ORPHAN_REASON_LABELS } from '../../lib/labels';

interface Props {
  groupId: number;
}

// У этих двух проверок id в findings — id ЗАНЯТИЯ (у него нет своей позиции
// в плане), у остальных пяти — id плановой строки. Различаем по ключу
// проверки, а не по содержимому строки (см. health.check_group на бэке) —
// подписи должны получиться разные: «занятие от 14.05» vs «позиция №12 на 15.05».
const FACT_KEYED_CHECKS = new Set(['fact_without_position', 'duplicate_dates']);

function findingLabel(checkKey: string, row: PlanHealthFinding): string {
  const num = row.lesson_number != null ? ` №${row.lesson_number}` : '';
  if (FACT_KEYED_CHECKS.has(checkKey)) {
    return `Занятие${num} от ${fmtDate(row.fact_date)}`;
  }
  const date = row.scheduled_date ? ` на ${fmtDate(row.scheduled_date)}` : '';
  const fact = row.fact_date ? ` — факт ${fmtDate(row.fact_date)}` : '';
  return `Позиция${num}${date}${fact}`;
}

/**
 * «Состояние плана» — вкладка «Расписание» карточки группы. Предпросмотр и
 * починка planned_lessons↔lessons (docs/superpowers/specs/2026-08-05-plan-health-design.md).
 *
 * Только суперадмин (canFixPlan) — бэк держит то же самое на /plan/health и
 * /plan/resync (IsSuperAdmin), но 403 на КАЖДОЕ открытие вкладки для
 * менеджера/админа — плохой UX, поэтому блок вообще не рендерится, если роль
 * не подходит (см. enabled в useGroupPlanHealth).
 *
 * Три состояния, разные по смыслу:
 *  - чисто (findings и changes пусты) — одна строка, без кнопки;
 *  - можно починить (blocked_by пуст, changes непуст) — список найденного +
 *    кнопка, открывающая модалку предпросмотра (позиция → было → станет);
 *  - чинить нельзя (сервер вернул blocked_by и/или orphan_facts) — кнопки НЕТ
 *    вообще, только список проблем и предупреждение про деньги. Реальный
 *    случай, из-за которого блокировка вообще появилась: группа ПИ263, 22
 *    дубля занятий, 11 задвоенных занятий у ученика, 5500 ₽ лишней зарплаты —
 *    автопочинка в такой группе закрепила бы ошибку, а не исправила её.
 */
export default function GroupPlanHealthBlock({ groupId }: Props) {
  const { me } = useAuth();
  const allowed = canFixPlan(me?.role as Role);
  const { data, isLoading, isError } = useGroupPlanHealth(groupId, allowed);
  const resync = useResyncPlan(groupId);
  const showError = useApiError();
  const { toast } = useToast();
  const [confirmOpen, setConfirmOpen] = useState(false);

  if (!allowed) return null;
  if (isLoading) return <BlockLoading rows={2} label="Проверяем состояние плана…" />;
  // Молча исчезнуть нельзя: пустое место неотличимо от «всё хорошо», а это
  // ровно тот экран, единственная задача которого — сказать правду о плане.
  if (isError) {
    return (
      <div className="plan-health plan-health--warn">
        <span className="plan-health__icon" aria-hidden>!</span>
        Не удалось проверить состояние плана — обновите страницу
      </div>
    );
  }
  if (!data) return null;

  const { findings, resync: r } = data;
  const findingEntries = Object.entries(findings);
  // Граница — ИМЕННО поле blocked_by с сервера (плюс orphan_facts: сервер
  // отдаёт их непустыми только когда changes уже null, см. plan_resync_diff),
  // а не собственный пересчёт findings на клиенте — иначе правило «когда
  // чинить нельзя» оказалось бы записано дважды и разошлось бы, стоит бэку
  // завести восьмую проверку.
  const isBlocked = r.blocked_by.length > 0 || r.orphan_facts.length > 0;
  const changeCount = r.changes?.length ?? 0;
  // isBlocked ПЕРВЫМ слагаемым: причина блокировки может не дать ни одной
  // строки в findings (например duplicate_position_numbers считает сам resync,
  // в health.CHECKS её нет), и без этого условия экран показал бы
  // «План согласован» на группе, которую сервер чинить отказывается.
  const isClean = !isBlocked && findingEntries.length === 0 && changeCount === 0;

  const closeConfirm = () => setConfirmOpen(false);

  const submitResync = async () => {
    if (!r.changes) return;
    const expected: ResyncExpectedTriple[] = r.changes.map(
      (c) => [c.position_id, c.to.fact_lesson_id, c.to.scheduled_date as string],
    );
    try {
      const result = await resync.mutateAsync(expected);
      toast(
        result.applied > 0
          ? `План приведён к занятиям — изменено позиций: ${result.applied}`
          : 'Чинить было нечего, план уже согласован',
        'ok',
      );
      closeConfirm();
    } catch (err) {
      // 409: состояние изменилось между предпросмотром и подтверждением —
      // показываем текст сервера как есть (useApiError берёт err.message,
      // а api() кладёт туда json.error), а не generic «что-то пошло не так».
      // Хук useResyncPlan сам инвалидирует health на onError — свежий
      // предпросмотр подтянется следом за тостом.
      closeConfirm();
      showError(err);
    }
  };

  if (isClean) {
    return (
      <div className="plan-health plan-health--ok">
        <span className="plan-health__icon" aria-hidden>✓</span>
        План согласован с занятиями
      </div>
    );
  }

  return (
    <div className={`plan-health ${isBlocked ? 'plan-health--blocked' : 'plan-health--warn'}`}>
      <div className="plan-health__head">
        <span className="plan-health__title">
          {isBlocked
            ? 'План разошёлся с занятиями — автопочинка недоступна'
            : changeCount > 0
              ? 'План разошёлся с занятиями'
              : 'В плане есть замечания — автопочинка их не лечит'}
        </span>
        {/* Кнопка только когда есть ЧТО чинить: при пустом changes она открыла бы
            модалку с пустой таблицей и отправила бы expected: [] ради 200 applied=0. */}
        {!isBlocked && changeCount > 0 && (
          <Button variant="primary" size="sm" onClick={() => setConfirmOpen(true)} disabled={resync.isPending}>
            Привести план к занятиям
          </Button>
        )}
      </div>

      {/* Что именно блокирует. Без этого списка пользователь видит красную
          плашку и findings, среди которых блокирующей причины может не быть
          вовсе — понять, почему кнопки нет, невозможно. */}
      {isBlocked && r.blocked_by.length > 0 && (
        <div className="plan-health__blockers">
          <span className="plan-health__blockers-title">Почему чинить нельзя:</span>
          <ul className="plan-health__findings">
            {r.blocked_by.map((key) => (
              <li key={key} className="plan-health__check">
                {PLAN_HEALTH_CHECK_LABELS[key] ?? key}
              </li>
            ))}
          </ul>
        </div>
      )}

      {findingEntries.length > 0 && (
        <ul className="plan-health__findings">
          {findingEntries.map(([key, rows]) => (
            <li key={key} className="plan-health__check">
              <span className="plan-health__check-label">{PLAN_HEALTH_CHECK_LABELS[key] ?? key}</span>
              <ul className="plan-health__rows">
                {rows.map((row) => (
                  <li key={row.id}>{findingLabel(key, row)}</li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}

      {r.orphan_facts.length > 0 && (
        <div className="plan-health__check">
          <span className="plan-health__check-label">Занятия без пары в плане</span>
          <ul className="plan-health__rows">
            {r.orphan_facts.map((f) => (
              <li key={f.lesson_id}>
                Занятие{f.lesson_number != null ? ` №${f.lesson_number}` : ''} от {fmtDate(f.lesson_date)}
                {' — '}{PLAN_HEALTH_ORPHAN_REASON_LABELS[f.reason] ?? f.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {isBlocked && (
        <div className="plan-health__warning">
          Это затрагивает баланс ученика и уже выплаченную зарплату преподавателю.
          Автоматическая починка здесь отключена намеренно — разберитесь с занятиями
          и позициями курса вручную.
        </div>
      )}

      <Dialog
        open={confirmOpen}
        onOpenChange={(o) => !o && closeConfirm()}
        title="Привести план к занятиям?"
        footer={
          <>
            <Button onClick={closeConfirm} disabled={resync.isPending}>Отмена</Button>
            <Button variant="primary" onClick={() => { void submitResync(); }} disabled={resync.isPending}>
              Применить
            </Button>
          </>
        }
      >
        <div className="schedule-form__hint">
          Привязки план↔занятие пересоберутся по правилу «номер факта = номер
          позиции», плановые даты подтянутся к фактическим датам занятий.
        </div>
        <table className="plan-health__preview-table">
          <thead>
            <tr>
              <th>Позиция</th>
              <th>Было</th>
              <th>Станет</th>
            </tr>
          </thead>
          <tbody>
            {(r.changes ?? []).map((c) => (
              <tr key={c.position_id}>
                <td>{c.lesson_number != null ? `№${c.lesson_number}` : `#${c.position_id}`}</td>
                <td>{c.from.fact_lesson_id ? fmtDate(c.from.scheduled_date) : '— (нет занятия)'}</td>
                <td>{c.to.fact_lesson_id ? fmtDate(c.to.scheduled_date) : 'освобождается'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {r.freed.length > 0 && (
          <div className="plan-health__freed">
            <div className="schedule-form__hint">
              Позиции без занятия после починки (уйдут ближайшими по расписанию):
            </div>
            <ul className="plan-health__rows">
              {r.freed.map((f) => (
                <li key={f.position_id}>
                  {f.lesson_number != null ? `№${f.lesson_number}` : `Позиция #${f.position_id}`}
                  {f.scheduled_date ? ` — ${fmtDate(f.scheduled_date)}` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Dialog>
    </div>
  );
}
