import { useToast } from '../../components/ui/Toast';
import type { Teacher } from '../../lib/types';

/**
 * Строка «Звонок» в шапке карточки преподавателя: постоянная ссылка
 * (Телемост, ВК Звонок…), по которой он ведёт уроки.
 *
 * Стоит под чипами, а не карточкой в правой колонке: это одна строка, и
 * отдельная рамка рядом с блоком Telegram растягивалась в пустую коробку.
 *
 * Главное действие — скопировать ссылку, чтобы кинуть родителю или ученику.
 * Копирование — тем же приёмом, что invite-ссылка в «Учётках»
 * (AccountsPage): clipboard + подтверждение тостом. Правка ссылки — в общей
 * форме «Редактировать».
 */
export function TeacherCallLink({ teacher }: { teacher: Teacher }) {
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
    <div className="call-link">
      <VideoGlyph />
      {url ? (
        <>
          {/* noopener/noreferrer: чужой сайт не получает window.opener и адрес карточки. */}
          <a className="call-link__url" href={url} target="_blank" rel="noopener noreferrer" title={url}>
            {url.replace(/^https?:\/\//, '')}
          </a>
          <button
            type="button"
            className="call-link__copy"
            onClick={copy}
            title="Скопировать ссылку на звонок"
            aria-label="Скопировать ссылку на звонок"
          >
            <CopyGlyph />
          </button>
        </>
      ) : (
        <span className="call-link__empty">Ссылка на звонок не указана</span>
      )}
    </div>
  );
}

function VideoGlyph() {
  return (
    <svg className="call-link__icon" width="14" height="14" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2.5" y="6" width="13" height="12" rx="2" />
      <path d="m15.5 10.5 6-3.5v10l-6-3.5" />
    </svg>
  );
}

function CopyGlyph() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="8.5" y="8.5" width="12" height="12" rx="2" />
      <path d="M15.5 8.5V5.5a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3" />
    </svg>
  );
}
