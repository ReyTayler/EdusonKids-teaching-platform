/** Типы раздела «Wiki». Форма ответов — из apps/knowledge/views.py. */

export type KnowledgeRole = 'teacher' | 'manager' | 'admin' | 'superadmin';

export type DocumentStatus = 'draft' | 'published';

export interface KnowledgeSection {
  id: number;
  title: string;
  position: number;
  active: boolean;
  /** Сколько документов в папке видит текущая роль. Считает сервер. */
  document_count: number;
}

/** Ответ /sections: папки со счётчиками и общее число видимых документов. */
export interface KnowledgeSections {
  sections: KnowledgeSection[];
  total: number;
}

/** Строка списка документов — без content. */
export interface KnowledgeDocumentRow {
  id: number;
  section_id: number;
  title: string;
  status: DocumentStatus;
  reader_roles: KnowledgeRole[];
  position: number;
  published_at: string | null;
  updated_at: string;
  /** Первые ~160 символов текста — подпись под названием в карточке. Обрезает SQL. */
  excerpt?: string;
  /** Личная закладка текущего сотрудника. */
  is_favorite?: boolean;
  /** Кто завёл документ; пусто, если учётку удалили. */
  author_name?: string;
}

/** Документ целиком, с содержимым. */
export interface KnowledgeDocument extends KnowledgeDocumentRow {
  content: TipTapDoc;
}

/** Раздел списка слева. Живёт в адресе (?scope=), чтобы на него можно было дать ссылку. */
export type LibraryScope = 'all' | 'favorites' | 'archive';

/** Корень TipTap-документа. Структура узлов проверяется на сервере. */
export interface TipTapDoc {
  type: 'doc';
  content?: TipTapNode[];
}

export interface TipTapNode {
  type: string;
  attrs?: Record<string, unknown>;
  content?: TipTapNode[];
  marks?: { type: string; attrs?: Record<string, unknown> }[];
  text?: string;
}

export interface KnowledgeImage {
  id: number;
  mime: string;
  byte_size: number;
  width: number;
  height: number;
  optimize_state: 'pending' | 'ready' | 'failed';
}

export const EMPTY_DOC: TipTapDoc = { type: 'doc', content: [] };

/**
 * URL картинки для рендера. Собирается из id — в JSON хранится только он.
 *
 * optimized — сжатый WebP (ширина до 1600), им статья и иллюстрируется;
 * original — исходный файл, он открывается по клику в просмотрщике.
 */
export type ImageVariant = 'optimized' | 'thumb' | 'original';

export function imageUrl(imageId: number, variant: ImageVariant = 'optimized'): string {
  return `/api/admin/knowledge/images/${imageId}?variant=${variant}`;
}

/** Прикреплённый файл — ответ POST /files. */
export interface KnowledgeFileMeta {
  id: number;
  name: string;
  mime: string;
  byte_size: number;
}

/**
 * Адрес скачивания файла. Как и у картинок, в документе хранится только id —
 * схему раздачи можно поменять, не переписывая содержимое документов.
 */
export function fileUrl(fileId: number): string {
  return `/api/admin/knowledge/files/${fileId}`;
}

/**
 * Размер файла человеку. Единицы двоичные (КБ = 1024), как их считает
 * проводник и как ожидает увидеть тот, кто смотрит на свойства файла.
 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '';
  if (bytes < 1024) return `${bytes} Б`;
  const units = ['КБ', 'МБ', 'ГБ'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  // Один знак после запятой до 10 единиц, дальше он уже не несёт смысла.
  const shown = value < 10 ? value.toFixed(1).replace('.', ',') : Math.round(value);
  return `${shown} ${units[unit]}`;
}
