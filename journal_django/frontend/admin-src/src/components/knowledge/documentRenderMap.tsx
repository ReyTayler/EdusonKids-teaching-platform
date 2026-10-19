import type { ReactNode } from 'react';
import type { JSONMarkType, JSONNodeType, MarkProps, NodeProps } from '@tiptap/static-renderer/json/react';
import { imageUrl } from '../../lib/knowledge';
import type { TipTapNode } from '../../lib/knowledge';
import { useImageLightbox } from './ImageLightbox';
import { useHeadingAnchor } from './headingAnchors';
import { CodeBlockView } from './CodeBlockView';
import { FileCard } from './FileCard';
import { CALLOUT_LABELS, DEFAULT_CALLOUT_TONE, isCalloutTone } from './calloutMeta';
import { displayTextColor } from './editorColors';

/**
 * Таблица соответствий «тип узла/марки TipTap → React-разметка» для чтения
 * документов базы знаний.
 *
 * Работает поверх сырого TipTap-JSON (`@tiptap/static-renderer/json/react`),
 * а не поверх расширений (`StarterKit`, `@tiptap/extension-table`) — тот путь
 * тянет `@tiptap/core` и ядро ProseMirror в основной бандл, хотя для простого
 * рендера в React-элементы они не нужны. DocumentEditor.tsx по-прежнему на
 * полноценных расширениях — это правильно, редактор должен уметь мутировать
 * документ, а читалка — только показывать.
 *
 * Набор узлов и марок жёстко привязан к белому списку бэкенда
 * (apps/knowledge/content.py: ALLOWED_NODES/ALLOWED_MARKS). Разметка
 * подобрана так, чтобы совпадать с тем, что рендерит редактор (см. renderHTML
 * в @tiptap/starter-kit и @tiptap/extension-table) — от этого зависят стили
 * в styles/knowledge.css.
 */

type NProps = NodeProps<JSONNodeType, ReactNode | ReactNode[]>;
type MProps = MarkProps<JSONMarkType, ReactNode | ReactNode[], JSONNodeType>;

const HEADING_TAGS = { 1: 'h1', 2: 'h2', 3: 'h3' } as const;

/**
 * Выравнивание абзаца/заголовка. @tiptap/extension-text-align хранит его в
 * атрибуте textAlign и на чтении обязано отработать так же, иначе выровненный
 * по центру заголовок при просмотре съедет влево. Значение сервер принимает
 * только из закрытого списка (content.py: ALLOWED_TEXT_ALIGN).
 */
function alignStyle(node: JSONNodeType) {
  const align = node.attrs?.textAlign;
  const background = node.attrs?.blockBackground;

  const style: {
    textAlign?: 'center' | 'right' | 'justify';
    backgroundColor?: string;
  } = {};
  if (typeof align === 'string' && align && align !== 'left') {
    style.textAlign = align as 'center' | 'right' | 'justify';
  }
  // Заливка блока. Сервер пропускает только значения из закрытого списка
  // (content.py: ALLOWED_HIGHLIGHT_COLORS), поэтому подставляется как есть.
  if (typeof background === 'string' && background) style.backgroundColor = background;

  return Object.keys(style).length > 0 ? style : undefined;
}

/** Класс блока с заливкой — из него берутся поля и скругление (knowledge.css). */
function blockClass(node: JSONNodeType): string | undefined {
  return typeof node.attrs?.blockBackground === 'string' && node.attrs.blockBackground
    ? 'kb-block-bg'
    : undefined;
}

/**
 * Нумерованный список. `start` обязателен: список, разорванный вставкой кода
 * или картинки, продолжается со своего номера — так делает и редактор
 * (@tiptap/starter-kit), и Google Docs при вставке. Без этого атрибута статья
 * при чтении нумеровалась заново с единицы, хотя в редакторе автор видел
 * «4, 5, 6», — то есть читатель получал не тот документ, который написали.
 */
function OrderedList({ node, children }: NProps) {
  const start = Number(node.attrs?.start);
  return <ol start={Number.isInteger(start) && start !== 1 ? start : undefined}>{children}</ol>;
}

function Heading({ node, children }: NProps) {
  const level = Number(node.attrs?.level) as 1 | 2 | 3;
  const Tag = HEADING_TAGS[level] ?? 'h3';
  // id ставится ради оглавления справа. Значение берётся из общей таблицы
  // якорей (headingAnchors.ts), а не считается здесь заново: разойдись они —
  // ссылка в оглавлении вела бы в никуда.
  const anchor = useHeadingAnchor(node as TipTapNode);
  return (
    <Tag id={anchor} className={blockClass(node)} style={alignStyle(node)}>{children}</Tag>
  );
}

/**
 * Ячейка таблицы. `colspan`/`rowspan`/`align` — те же атрибуты, что заводит
 * @tiptap/extension-table при объединении ячеек; без них склеенная в
 * редакторе ячейка при чтении распадётся обратно на несколько.
 */
function cellProps(node: JSONNodeType) {
  const attrs = node.attrs ?? {};
  const props: Record<string, unknown> = {};
  if (attrs.colspan && attrs.colspan !== 1) props.colSpan = attrs.colspan;
  if (attrs.rowspan && attrs.rowspan !== 1) props.rowSpan = attrs.rowspan;
  if (attrs.align) props.style = { textAlign: attrs.align };
  return props;
}

function TableCell({ node, children }: NProps) {
  return <td {...cellProps(node)}>{children}</td>;
}

function TableHeaderCell({ node, children }: NProps) {
  return <th {...cellProps(node)}>{children}</th>;
}

/**
 * Картинка статьи: в тексте — сжатый вариант по центру, по клику — оригинал
 * поверх страницы (ImageLightbox).
 *
 * Обёртка — кнопка, а не картинка с onClick: клавиатурой до неё тоже надо
 * добираться, а нативная кнопка даёт и фокус, и Enter/Пробел бесплатно.
 * width/height проставляются атрибутами — браузер резервирует место, и текст
 * под картинкой не прыгает, когда файл догрузился.
 */
function KnowledgeImage({ node }: NProps) {
  const openLightbox = useImageLightbox();
  const attrs = node.attrs ?? {};
  const imageId = attrs.imageId;
  if (typeof imageId !== 'number') return null;

  const alt = typeof attrs.alt === 'string' ? attrs.alt : '';
  const width = typeof attrs.width === 'number' ? attrs.width : undefined;
  const height = typeof attrs.height === 'number' ? attrs.height : undefined;
  const picture = (
    <img
      src={imageUrl(imageId)}
      // Подпись несёт кнопка: иначе экранный диктор прочитает её дважды.
      alt={openLightbox ? '' : alt}
      width={width}
      height={height}
      loading="lazy"
    />
  );

  if (!openLightbox) return picture;

  return (
    <button
      type="button"
      className="kb-zoom"
      aria-label={alt ? `${alt} — открыть в полном размере` : 'Открыть изображение в полном размере'}
      onClick={() => openLightbox({ id: imageId, alt })}
    >
      {picture}
    </button>
  );
}

/**
 * Оформление куска текста: гарнитура, цвет букв, цвет выделения.
 *
 * Сервер пропускает только значения из своих закрытых списков (content.py:
 * ALLOWED_FONTS, ALLOWED_TEXT_COLORS, ALLOWED_HIGHLIGHT_COLORS), поэтому здесь
 * достаточно подставить их в стиль — произвольной строки в документе оказаться
 * не может. Цвета хранятся ссылками на токены и потому сами подстраиваются под
 * тему читателя.
 */
function TextStyle({ mark, children }: MProps) {
  const attrs = mark.attrs ?? {};
  const style: { fontFamily?: string; color?: string; backgroundColor?: string } = {};
  if (typeof attrs.fontFamily === 'string' && attrs.fontFamily) style.fontFamily = attrs.fontFamily;
  if (typeof attrs.color === 'string' && attrs.color) style.color = displayTextColor(attrs.color);
  if (typeof attrs.backgroundColor === 'string' && attrs.backgroundColor) {
    style.backgroundColor = attrs.backgroundColor;
  }
  // Ни одного оформления — не плодим лишний span вокруг каждого куска текста.
  if (Object.keys(style).length === 0) return <>{children}</>;
  return <span style={style}>{children}</span>;
}

function Link({ mark, children }: MProps) {
  const href = mark.attrs?.href;
  if (typeof href !== 'string' || !href) return <>{children}</>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

/**
 * Выноска. Тон приходит закрытым списком с сервера (content.py:
 * ALLOWED_CALLOUT_TONES), поэтому его можно подставлять в имя класса.
 * Подписи — из общего словаря с редактором (calloutMeta.ts).
 */
function Callout({ node, children }: NProps) {
  const raw = node.attrs?.tone;
  const tone = isCalloutTone(raw) ? raw : DEFAULT_CALLOUT_TONE;
  return (
    <aside className={`kb-callout kb-callout--${tone}`} data-tone={tone}>
      {/* Подпись вида выноски — не украшение: без неё цвет остаётся
          единственным носителем смысла, а он не читается ни в чёрно-белой
          печати, ни при нарушении цветовосприятия. */}
      <div className="kb-callout__head">
        <span className="kb-callout__label">{CALLOUT_LABELS[tone]}</span>
      </div>
      <div className="kb-callout__body">{children}</div>
    </aside>
  );
}

/** Пункт чеклиста: отметка только для чтения — статью не редактируют. */
function TaskItem({ node, children }: NProps) {
  const checked = node.attrs?.checked === true;
  return (
    <li className={`kb-task${checked ? ' is-done' : ''}`}>
      <input type="checkbox" checked={checked} readOnly tabIndex={-1} aria-hidden="true" />
      <div>{children}</div>
    </li>
  );
}

function CodeBlock({ node, children }: NProps) {
  const language = typeof node.attrs?.language === 'string' ? node.attrs.language : '';
  // Исходный текст берём из JSON, а не из отрисованных детей: подсветке нужна
  // строка, а children — уже React-элементы.
  const source = (node.content ?? [])
    .map((child) => (typeof child.text === 'string' ? child.text : ''))
    .join('');
  return (
    <CodeBlockView language={language} source={source}>
      {children}
    </CodeBlockView>
  );
}

/**
 * Прикреплённый файл при чтении: карточка со ссылкой на скачивание.
 *
 * Имя, размер и тип берутся из самого узла — иначе на каждый файл статьи
 * пришлось бы сходить на сервер. На безопасность это не влияет: скачивание
 * отдаёт имя из базы, а не отсюда.
 */
function KnowledgeFile({ node }: NProps) {
  const attrs = node.attrs ?? {};
  const fileId = attrs.fileId;
  if (typeof fileId !== 'number') return null;
  return (
    <FileCard
      fileId={fileId}
      name={typeof attrs.name === 'string' && attrs.name ? attrs.name : 'Файл'}
      size={typeof attrs.size === 'number' ? attrs.size : 0}
      mime={typeof attrs.mime === 'string' ? attrs.mime : ''}
    />
  );
}

/**
 * Ширины столбцов таблицы.
 *
 * Хранятся в атрибуте colwidth КАЖДОЙ ячейки первой строки — так их пишет
 * @tiptap/extension-table при изменении ширины мышью. Массив, а не число:
 * у объединённой ячейки (colspan) ширин столько же, сколько столбцов она
 * накрывает.
 *
 * Без colgroup ширины при чтении просто игнорировались бы, и статья выглядела
 * бы иначе, чем в редакторе, — та же поломка, что была с нумерацией списка.
 */
function colgroupOf(node: JSONNodeType): ReactNode | null {
  const firstRow = node.content?.[0];
  if (!firstRow?.content) return null;

  const widths: (number | null)[] = [];
  let anyWidth = false;

  for (const cell of firstRow.content) {
    const span = Number(cell.attrs?.colspan) || 1;
    const raw = cell.attrs?.colwidth;
    const list = Array.isArray(raw) ? raw : null;
    for (let i = 0; i < span; i += 1) {
      const value = list?.[i];
      const width = typeof value === 'number' && value > 0 ? value : null;
      if (width !== null) anyWidth = true;
      widths.push(width);
    }
  }

  // Ни одной заданной ширины — colgroup не нужен вовсе: пусть браузер считает
  // столбцы по содержимому, как делал раньше.
  if (!anyWidth) return null;

  return (
    <colgroup>
      {widths.map((width, index) => (
        <col key={index} style={width ? { width: `${width}px` } : undefined} />
      ))}
    </colgroup>
  );
}

/**
 * Таблица статьи. Обёртка .tableWrapper повторяет разметку редактора
 * (Table.configure({ renderWrapper: true })) — на ней висит горизонтальная
 * прокрутка, иначе широкая таблица растягивает страницу.
 */
function KnowledgeTable({ node, children }: NProps) {
  const colgroup = colgroupOf(node);
  return (
    <div className="tableWrapper">
      <table className={colgroup ? 'kb-table--sized' : undefined}>
        {colgroup}
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export const nodeMapping: Record<string, (props: NProps) => ReactNode> = {
  doc: ({ children }) => <>{children}</>,
  paragraph: ({ node, children }) => (
    <p className={blockClass(node)} style={alignStyle(node)}>{children}</p>
  ),
  text: ({ node }) => node.text ?? '',
  heading: Heading,
  bulletList: ({ children }) => <ul>{children}</ul>,
  orderedList: OrderedList,
  listItem: ({ children }) => <li>{children}</li>,
  taskList: ({ children }) => <ul className="kb-tasks">{children}</ul>,
  taskItem: TaskItem,
  blockquote: ({ children }) => <blockquote>{children}</blockquote>,
  callout: Callout,
  codeBlock: CodeBlock,
  horizontalRule: () => <hr />,
  hardBreak: () => <br />,
  // renderWrapper: true в редакторе (см. DocumentEditor.tsx) оборачивает
  // таблицу в div.tableWrapper — styles/knowledge.css вешает на него
  // горизонтальный скролл.
  table: KnowledgeTable,
  tableRow: ({ children }) => <tr>{children}</tr>,
  tableHeader: TableHeaderCell,
  tableCell: TableCell,
  knowledgeImage: KnowledgeImage,
  knowledgeFile: KnowledgeFile,
};

export const markMapping: Record<string, (props: MProps) => ReactNode> = {
  bold: ({ children }) => <strong>{children}</strong>,
  italic: ({ children }) => <em>{children}</em>,
  underline: ({ children }) => <u>{children}</u>,
  strike: ({ children }) => <s>{children}</s>,
  code: ({ children }) => <code>{children}</code>,
  link: Link,
  textStyle: TextStyle,
};

/**
 * Документ мог быть сохранён до расширения белого списка бэкенда — не роняем
 * страницу на незнакомом узле/марке, просто показываем содержимое как есть.
 */
export function unhandledNode({ children }: NProps): ReactNode {
  return children ?? null;
}

export function unhandledMark({ children }: MProps): ReactNode {
  return children ?? null;
}
