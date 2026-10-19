import { fileUrl, formatBytes } from '../../lib/knowledge';

/**
 * Карточка прикреплённого файла — одна на редактор и на читалку.
 *
 * Общая намеренно: что видно при наборе, то и увидит читатель. Разъедься эти
 * два представления — автор оформлял бы документ вслепую.
 *
 * Слева плашка с типом. Она же цветовая метка, но цвет здесь не единственный
 * носитель смысла: тип написан буквами, поэтому карточка читается и в
 * чёрно-белой печати, и при нарушении цветовосприятия.
 */

/** Расширение файла заглавными — то, что показывает плашка. */
function badgeOf(name: string, mime: string): string {
  const dot = name.lastIndexOf('.');
  if (dot > 0 && dot < name.length - 1) return name.slice(dot + 1).toUpperCase().slice(0, 4);
  // Имя без расширения — редкость, но пустая плашка выглядит как поломка.
  return (mime.split('/')[1] ?? 'файл').toUpperCase().slice(0, 4);
}

/** Семейство для окраски плашки. Список закрытый — значение уходит в класс. */
function toneOf(badge: string): string {
  const b = badge.toLowerCase();
  if (b === 'pdf') return 'pdf';
  if (['doc', 'docx', 'odt', 'rtf'].includes(b)) return 'doc';
  if (['xls', 'xlsx', 'ods', 'csv'].includes(b)) return 'sheet';
  if (['ppt', 'pptx', 'odp'].includes(b)) return 'slides';
  if (b === 'zip') return 'archive';
  return 'plain';
}

export interface FileCardProps {
  fileId: number;
  name: string;
  size: number;
  mime: string;
  /** В редакторе скачивание не нужно: клик там выделяет блок. */
  downloadable?: boolean;
}

export function FileCard({ fileId, name, size, mime, downloadable = true }: FileCardProps) {
  const badge = badgeOf(name, mime);
  const body = (
    <>
      <span className={`kb-file__badge kb-file__badge--${toneOf(badge)}`} aria-hidden="true">
        {badge}
      </span>
      <span className="kb-file__text">
        <span className="kb-file__name">{name}</span>
        <span className="kb-file__meta">{formatBytes(size)}</span>
      </span>
      {downloadable && (
        <span className="kb-file__action" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3v12" />
            <polyline points="7 11 12 16 17 11" />
            <path d="M4 20h16" />
          </svg>
        </span>
      )}
    </>
  );

  if (!downloadable) return <span className="kb-file">{body}</span>;

  return (
    // Ссылка, а не кнопка: это переход к ресурсу, и браузер должен уметь
    // «сохранить как» через контекстное меню. download подсказывает имя, но
    // решает всё равно сервер — он присылает его в заголовке.
    <a className="kb-file" href={fileUrl(fileId)} download={name}>
      {body}
    </a>
  );
}
