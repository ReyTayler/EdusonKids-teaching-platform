import { useToast } from '../../components/ui/Toast';
import type { Teacher } from '../../lib/types';

/**
 * Блок «Звонок» в шапке карточки преподавателя: постоянная ссылка
 * (Телемост, ВК Звонок…), по которой он ведёт уроки.
 *
 * Главное действие — скопировать ссылку, чтобы кинуть родителю или ученику.
 * Копирование — тем же приёмом, что invite-ссылка в «Учётках»
 * (AccountsPage): clipboard + подтверждение тостом. Правка ссылки — в общей
 * форме «Редактировать», отдельного инпута здесь нет.
 */
export function TeacherCallBlock({ teacher }: { teacher: Teacher }) {
  const { toast } = useToast();
  const url = teacher.call_url;

  const copy = () => {
    if (!url) return;
    navigator.clipboard.writeText(url).then(
      () => toast('Ссылка скопирована', 'ok'),
      () => toast('Не удалось скопировать', 'error'),
    );
  };

  return (
    <div className="call-card">
      <div className="call-card__head">
        <span className="call-card__title">
          <VideoGlyph />
          Звонок
        </span>
        {!url && <span className="call-card__status">не указана</span>}
      </div>

      {url && (
        <div className="call-card__row">
          {/* noopener/noreferrer: чужой сайт не получает window.opener и адрес карточки. */}
          <a className="call-card__link" href={url} target="_blank" rel="noopener noreferrer" title={url}>
            {url.replace(/^https?:\/\//, '')}
          </a>
          <button
            type="button"
            className="call-card__copy"
            onClick={copy}
            title="Скопировать ссылку на звонок"
            aria-label="Скопировать ссылку на звонок"
          >
            <CopyGlyph />
          </button>
        </div>
      )}
    </div>
  );
}

function VideoGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2.5" y="6" width="13" height="12" rx="2" />
      <path d="m15.5 10.5 6-3.5v10l-6-3.5" />
    </svg>
  );
}

function CopyGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="8.5" y="8.5" width="12" height="12" rx="2" />
      <path d="M15.5 8.5V5.5a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3" />
    </svg>
  );
}
