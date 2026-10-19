import { Link } from 'react-router-dom';
import { fmtDate } from '../../lib/format';
import { plural } from '../../lib/labels';
import type { TeacherStats } from '../../hooks/useTeacherStats';

interface Props {
  stats: TeacherStats | undefined;
}

/**
 * Полоса «требует действия»: незаполненные занятия и пропуски, ждущие решения.
 *
 * Отдельно от плиток, а не шестой и седьмой плиткой в ряду, по двум причинам.
 * Во-первых, это единственные числа на карточке, которые зовут что-то сделать,
 * а среди нейтральных «занятий» и «часов» они бы потерялись. Во-вторых, они
 * НЕ за выбранный месяц: просрочка мая не перестаёт быть просрочкой оттого,
 * что открыт июль, — а стоя вплотную к переключателю месяца, они читались бы
 * как часть периода.
 *
 * Когда делать нечего — полосы нет вовсе. Пустой блок «0 просрочек» шумит и
 * приучает не смотреть в это место.
 */
export default function TeacherAttentionBar({ stats }: Props) {
  const unfilled = stats?.unfilled;
  const pending = stats?.absences.pending_now ?? 0;

  const hasUnfilled = (unfilled?.count ?? 0) > 0;
  if (!hasUnfilled && pending === 0) return null;

  return (
    <div className="tattention" role="status">
      {hasUnfilled && (
        <Link to="/admin/dashboard?tab=fill" className="tattention__item">
          <span className="tattention__count">{unfilled!.count}</span>
          <span className="tattention__text">
            {plural(unfilled!.count, 'занятие не заполнено', 'занятия не заполнены', 'занятий не заполнено')}
            {unfilled!.oldest_date && (
              <span className="tattention__since">
                {' '}с {fmtDate(unfilled!.oldest_date)}
              </span>
            )}
          </span>
        </Link>
      )}
      {pending > 0 && (
        <Link to="/admin/extra-lessons" className="tattention__item">
          <span className="tattention__count">{pending}</span>
          <span className="tattention__text">
            {plural(pending, 'пропуск ждёт', 'пропуска ждут', 'пропусков ждут')} решения
          </span>
        </Link>
      )}
    </div>
  );
}
