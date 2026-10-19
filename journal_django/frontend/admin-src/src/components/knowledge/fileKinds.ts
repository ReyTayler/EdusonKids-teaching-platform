/**
 * Какие файлы редактор соглашается прикрепить.
 *
 * Список обязан совпадать с белым списком сервера (apps/knowledge/file_types.py:
 * ALLOWED). Разойдутся — пользователь получит отказ уже после того, как файл
 * улетел на сервер: 25 МБ впустую и непонятная ошибка вместо подсказки заранее.
 *
 * Здесь проверка по расширению и только она. Сигнатуру содержимого проверяет
 * сервер — браузеру верить в этом нельзя, и дублировать проверку на клиенте
 * значило бы делать вид, что она что-то защищает.
 */
export const ALLOWED_FILE_EXTENSIONS = [
  'pdf',
  'docx', 'xlsx', 'pptx',
  'doc', 'xls', 'ppt',
  'odt', 'ods', 'odp',
  'rtf', 'zip', 'txt', 'csv',
] as const;

/** Значение для атрибута accept у поля выбора файла. */
export const FILE_ACCEPT = ALLOWED_FILE_EXTENSIONS.map((ext) => `.${ext}`).join(',');

/**
 * Предел размера файла. Обязан совпадать с KNOWLEDGE_MAX_FILE_BYTES на сервере
 * (config/settings/base.py) и укладываться в client_max_body_size на локации
 * загрузки в nginx.
 *
 * Проверка здесь не заменяет серверную — она её опережает. Без неё человек
 * ждал бы, пока сорокамегабайтный файл целиком уедет по сети, и только потом
 * получал отказ; а если запрос отсечёт nginx, то отказ придёт не от нашего
 * приложения и без внятного текста.
 */
export const MAX_FILE_BYTES = 25 * 1024 * 1024;

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : '';
}

export function isAttachable(file: File): boolean {
  return (ALLOWED_FILE_EXTENSIONS as readonly string[]).includes(extensionOf(file.name));
}

/**
 * Почему файл нельзя прикрепить, или null, если можно.
 *
 * Текст сразу называет и предел, и фактический размер: «слишком большой» без
 * чисел не даёт понять, нужно сжать файл вдвое или в двадцать раз.
 */
export function rejectionReason(file: File): string | null {
  if (!isAttachable(file)) {
    const ext = extensionOf(file.name);
    return ext
      ? `«${file.name}»: файлы .${ext} прикреплять нельзя.`
      : `«${file.name}»: у файла нет расширения, определить формат нечем.`;
  }
  if (file.size > MAX_FILE_BYTES) {
    return (
      `«${file.name}» весит ${formatSize(file.size)} — это больше ` +
      `допустимых ${formatSize(MAX_FILE_BYTES)}.`
    );
  }
  if (file.size === 0) return `«${file.name}» пустой.`;
  return null;
}

/** Мегабайты для сообщения об ошибке. Отдельно от formatBytes: там КБ и ГБ. */
function formatSize(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  return mb < 10 ? `${mb.toFixed(1).replace('.', ',')} МБ` : `${Math.round(mb)} МБ`;
}

export function isImage(file: File): boolean {
  return file.type.startsWith('image/');
}

/**
 * Разложить брошенное или вставленное на картинки, вложения и отказы.
 *
 * Картинка остаётся картинкой: у неё свой узел, просмотрщик и сжатые варианты.
 * Всё прочее проверяется на пригодность.
 *
 * Отказы возвращаются наружу, а не проглатываются. Человек, бросивший файл на
 * лист, ждёт результата: если не появилось ни карточки, ни объяснения, экран
 * выглядит сломанным, и следующее действие — бросить файл ещё раз.
 */
export function splitFiles(list: FileList | null | undefined): {
  images: File[];
  attachments: File[];
  rejected: string[];
} {
  const images: File[] = [];
  const attachments: File[] = [];
  const rejected: string[] = [];

  for (const file of Array.from(list ?? [])) {
    if (isImage(file)) {
      images.push(file);
      continue;
    }
    const reason = rejectionReason(file);
    if (reason) rejected.push(reason);
    else attachments.push(file);
  }

  return { images, attachments, rejected };
}
