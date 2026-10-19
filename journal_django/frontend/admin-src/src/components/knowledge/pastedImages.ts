/**
 * Картинки внутри вставленного HTML.
 *
 * Google Docs, Word Online и почтовые клиенты кладут в буфер не файлы, а
 * разметку, где картинка — `<img src="data:image/png;base64,…">`. Файлов в
 * буфере при этом нет (`clipboardData.files` пуст), а схема редактора знает
 * только свои картинки — `img[data-image-id]`. Итог был такой: текст
 * вставлялся, картинки молча пропадали.
 *
 * Здесь вставка раскладывается на две части: разметка, где на месте каждой
 * картинки стоит метка, и сами картинки — готовыми File для обычной загрузки
 * (POST /images). После загрузки метки заменяются на наши картинки, и
 * разметка вставляется штатным путём ProseMirror.
 *
 * Картинки по внешней ссылке (`https://…`, `file:///…`) перенести отсюда
 * нельзя: браузер не прочитает чужой сервер (CORS, connect-src 'self') и тем
 * более диск. Их только считаем, чтобы сказать человеку, что они не
 * перенесены, — молчаливая потеря и была исходной проблемой.
 */

const TOKEN_ATTR = 'data-kb-paste-token';
const DATA_IMAGE = /^data:(image\/[a-z0-9.+-]+);base64,/i;

export interface PastedImage {
  token: string;
  file: File;
  /** Ширина, с которой картинка стояла в исходном документе, если указана. */
  sourceWidth: number | null;
  /** Подпись из исходного документа; у Google Docs обычно пустая. */
  alt: string;
}

export interface ExtractedPaste {
  /** Разметка с метками вместо встроенных картинок и без внешних картинок. */
  html: string;
  images: PastedImage[];
  /** Сколько картинок перенести нельзя: внешние ссылки, битые данные. */
  lost: number;
}

/**
 * Разобрать вставленный HTML. null — во вставке нет чужих картинок, и её
 * можно отдать ProseMirror как есть: в том числе копирование внутри самого
 * редактора, где картинки уже наши (`img[data-image-id]`).
 */
export function extractPastedImages(html: string): ExtractedPaste | null {
  // Дешёвая проверка до разбора: подавляющее большинство вставок — текст.
  if (!/<img\b/i.test(html)) return null;

  const doc = new DOMParser().parseFromString(html, 'text/html');
  const foreign = Array.from(doc.querySelectorAll('img')).filter(
    (img) => !img.hasAttribute('data-image-id'),
  );
  if (foreign.length === 0) return null;

  const images: PastedImage[] = [];
  let lost = 0;

  foreign.forEach((img, index) => {
    const file = dataUrlToFile(img.getAttribute('src') ?? '', index + 1);
    if (!file) {
      lost += 1;
      img.remove();
      return;
    }
    const token = String(index);
    const width = Number(img.getAttribute('width'));
    images.push({
      token,
      file,
      sourceWidth: Number.isFinite(width) && width > 0 ? Math.round(width) : null,
      alt: (img.getAttribute('alt') ?? '').trim(),
    });
    // Сами данные из разметки убираем сразу: мегабайты base64 незачем держать
    // в памяти до конца загрузки и тем более отдавать парсеру.
    img.removeAttribute('src');
    img.setAttribute(TOKEN_ATTR, token);
  });

  return { html: doc.body.innerHTML, images, lost };
}

export interface UploadedImage {
  id: number;
  width: number | null;
  height: number | null;
  alt: string;
}

/**
 * Подставить загруженные картинки на место меток. Метка без загруженной
 * картинки (загрузка не удалась) удаляется: иначе парсер выбросил бы её
 * молча, а так её уже посчитали в сообщении.
 */
export function resolvePastedImages(html: string, uploaded: Map<string, UploadedImage>): string {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  doc.querySelectorAll(`img[${TOKEN_ATTR}]`).forEach((img) => {
    const image = uploaded.get(img.getAttribute(TOKEN_ATTR) ?? '');
    if (!image) {
      img.remove();
      return;
    }
    // Узел картинки читает из разметки только data-image-id, alt, width и
    // height (KnowledgeImageExtension) — остальное собираем заново.
    const clean = doc.createElement('img');
    clean.setAttribute('data-image-id', String(image.id));
    clean.setAttribute('alt', image.alt);
    if (image.width && image.height) {
      clean.setAttribute('width', String(image.width));
      clean.setAttribute('height', String(image.height));
    }
    img.replaceWith(clean);
  });
  return doc.body.innerHTML;
}

function dataUrlToFile(src: string, index: number): File | null {
  const match = DATA_IMAGE.exec(src);
  if (!match) return null;
  const mime = match[1].toLowerCase();
  try {
    const binary = atob(src.slice(match[0].length));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    const ext = mime.split('/')[1].replace('jpeg', 'jpg').replace(/\+.*$/, '');
    return new File([bytes], `image-${index}.${ext}`, { type: mime });
  } catch {
    // Битый base64 — картинку не перенести, но и вставку это ронять не должно.
    return null;
  }
}
