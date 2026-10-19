import StarterKit from '@tiptap/starter-kit';
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table';
import { TaskItem, TaskList } from '@tiptap/extension-list';
import TextAlign from '@tiptap/extension-text-align';
import { BackgroundColor, Color, FontFamily, TextStyle } from '@tiptap/extension-text-style';
import { UniqueID } from '@tiptap/extension-unique-id';
import { Placeholder } from '@tiptap/extensions';
import { CodeBlockLowlight } from '@tiptap/extension-code-block-lowlight';
import { KnowledgeImageExtension } from './KnowledgeImageExtension';
import { KnowledgeFileExtension } from './KnowledgeFileExtension';
import { UploadPlaceholderExtension } from './uploadPlaceholder';
import { CalloutExtension } from './CalloutExtension';
import { BlockBackgroundExtension } from './BlockBackgroundExtension';
import { FONT_CHOICES } from './editorFonts';
import { KNOWN_HIGHLIGHTS, KNOWN_TEXT_COLORS, displayTextColor, storedTextColor } from './editorColors';
import type { Lowlight } from './codeLanguages';

/**
 * Набор расширений редактора — единственный источник.
 *
 * Любое изменение здесь обязано быть согласовано с белым списком сервера
 * (apps/knowledge/content.py) И с documentRenderMap.tsx. Узел, который можно
 * создать, но нельзя сохранить, — потерянная работа пользователя; узел, который
 * сохраняется, но не рисуется, — сломанный документ.
 */

/**
 * Ссылки, которые сервер согласен принять (content.py: ALLOWED_LINK_PREFIXES).
 * TipTap по умолчанию пропускает ещё ftp, tel, sms, xmpp и другие схемы — текст
 * с телефоном, скопированный из другого документа, стал бы несохраняемым.
 */
function isAllowedHref(href: string): boolean {
  if (href.startsWith('//') || href.startsWith('/\\')) return false;
  return /^(https?:\/\/|mailto:|\/)/.test(href);
}

/**
 * Шрифт, вставленный вместе с внешним HTML (Word, Google Docs), приводим к
 * известному или отбрасываем.
 *
 * Это ключевая защита от тупика: сервер принимает только шесть гарнитур, и
 * «Calibri» из вставленного регламента делал документ невозможным для
 * сохранения.
 */
const KNOWN_FONTS = new Set(FONT_CHOICES.map((f) => f.value));

/**
 * Разрешить расширению только значения из закрытого списка.
 *
 * Применяется ко всему, что приезжает вместе с внешним HTML и попадает в
 * инлайновый стиль: гарнитура, цвет букв, цвет выделения. Из Word и Google Docs
 * их прилетает целая россыпь — `Calibri`, `rgb(0, 0, 0)`, `background: yellow`,
 * — и ни одно сервер не примет. Чужое значение выбрасывается ЗДЕСЬ, при
 * вставке: иначе оно доживало бы до сохранения и делало документ
 * несохраняемым, а человек узнавал бы об этом через час работы.
 */
type AnyExtension = { extend: (config: object) => unknown };

/**
 * Разные значения в документе и на экране (см. displayTextColor). Показ —
 * через renderHTML, а parseHTML переводит показанное обратно, иначе
 * копирование внутри самого редактора теряло бы цвет: нарисованное значение
 * не входит в закрытый список.
 */
interface DisplayAlias {
  toDisplay: (value: string) => string;
  toStored: (value: string) => string;
}

function restrictToKnown<T extends AnyExtension>(
  extension: T,
  attribute: string,
  known: Set<string>,
  alias?: DisplayAlias,
): T {
  return (extension as AnyExtension).extend({
    addGlobalAttributes(this: { parent?: () => { attributes?: Record<string, unknown> }[] }) {
      const parent = this.parent?.() ?? [];
      return parent.map((group) => ({
        ...group,
        attributes: Object.fromEntries(
          Object.entries(group.attributes ?? {}).map(([name, attr]) => {
            if (name !== attribute) return [name, attr];
            const original = attr as {
              parseHTML?: (el: HTMLElement) => unknown;
              renderHTML?: (attributes: Record<string, unknown>) => unknown;
            };
            return [
              name,
              {
                ...(attr as object),
                parseHTML: (element: HTMLElement) => {
                  const raw = original.parseHTML?.(element);
                  const value = typeof raw === 'string' && alias ? alias.toStored(raw) : raw;
                  return typeof value === 'string' && known.has(value) ? value : null;
                },
                ...(alias && original.renderHTML
                  ? {
                      renderHTML: (attributes: Record<string, unknown>) => {
                        const value = attributes[attribute];
                        return original.renderHTML!(
                          typeof value === 'string' && value
                            ? { ...attributes, [attribute]: alias.toDisplay(value) }
                            : attributes,
                        );
                      },
                    }
                  : {}),
              },
            ];
          }),
        ),
      }));
    },
  }) as T;
}

/**
 * Выравнивание текста в ячейке таблицы.
 *
 * Своё расширение атрибута, потому что @tiptap/extension-table его не заводит:
 * у ячейки штатно есть только colspan/rowspan/colwidth. Сервер значение уже
 * принимал (content.py: _check_align с тем же закрытым списком), читалка уже
 * рисовала (documentRenderMap: attrs.align → text-align), а задать его было
 * нечем — пункт меню «текст по центру» вызывал setCellAttribute('align'), и
 * ProseMirror молча выбрасывал неизвестный схеме атрибут. Здесь эта половина
 * и дописывается.
 *
 * text-align, а не устаревший HTML-атрибут align: последний игнорируется в
 * standards mode, и выравнивание пропало бы при вставке в другой редактор.
 */
const CELL_ALIGNMENTS = new Set(['left', 'center', 'right', 'justify']);

function withCellAlign<T extends AnyExtension>(cell: T): T {
  return (cell as AnyExtension).extend({
    addAttributes(this: { parent?: () => Record<string, unknown> }) {
      return {
        ...this.parent?.(),
        align: {
          default: null,
          parseHTML: (element: HTMLElement) => {
            const value = element.style.textAlign || element.getAttribute('align') || '';
            return CELL_ALIGNMENTS.has(value) ? value : null;
          },
          renderHTML: (attributes: { align?: string | null }) =>
            attributes.align ? { style: `text-align: ${attributes.align}` } : {},
        },
      };
    },
  }) as T;
}

const AlignedTableCell = withCellAlign(TableCell);
const AlignedTableHeader = withCellAlign(TableHeader);

const SafeFontFamily = restrictToKnown(FontFamily, 'fontFamily', KNOWN_FONTS);
const SafeColor = restrictToKnown(Color, 'color', KNOWN_TEXT_COLORS, {
  toDisplay: displayTextColor,
  toStored: storedTextColor,
});
const SafeBackgroundColor = restrictToKnown(BackgroundColor, 'backgroundColor', KNOWN_HIGHLIGHTS);

/**
 * Блоки, которым нужен устойчивый идентификатор: по нему строятся якоря
 * оглавления и работает перетаскивание. Списком, а не «всем подряд»: id на
 * каждом абзаце раздувает JSON документа без пользы.
 */
const ID_TYPES = ['heading', 'callout', 'table', 'codeBlock', 'knowledgeImage', 'knowledgeFile'];

/**
 * Набор расширений.
 *
 * Функция, а не константа: блоку кода нужен экземпляр lowlight, а он создаётся
 * один на редактор. Грамматики в него догружаются позже (codeLanguages.ts) —
 * пересобирать из-за них редактор нельзя, это сбросило бы курсор и историю
 * правок прямо во время набора.
 */
export function buildExtensions(lowlight: Lowlight) {
  return [
    StarterKit.configure({
      // Подчёркивание включено: сервер принимает марку underline.
      // Уровни заголовков ограничены тремя — рендер на чтении знает только
      // h1–h3, а Ctrl+Alt+4 иначе создавал бы заголовок, который показывается
      // как h3.
      heading: { levels: [1, 2, 3] },
      link: { openOnClick: false, isAllowedUri: isAllowedHref },
      // Свой блок кода с подсветкой ставится ниже; штатный пришлось бы
      // выключать после подключения — TipTap не терпит двух узлов с одним
      // именем.
      codeBlock: false,
    }),
    CodeBlockLowlight.configure({ lowlight, defaultLanguage: null }),
    // renderWrapper даёт .tableWrapper — отдельный слой между абзацем и
    // таблицей, на котором держится её поведение по ширине (knowledge.css:
    // в редакторе таблица выходит за поля листа, при чтении — растянута до
    // краёв листа и прокручивается внутри себя).
    // resizable — штатное изменение ширины столбцов из @tiptap/extension-table.
    // Раньше оно было выключено, и атрибут colwidth оказывался мёртвым грузом:
    // вставка из Google Docs его приносила, поменять было нечем, а читалка его
    // не применяла. Включение оживляет обе половины разом.
    Table.configure({
      renderWrapper: true,
      resizable: true,
      // Ширина области захвата у границы столбца. По умолчанию 5px — попасть
      // мышью в такую полоску тяжело, особенно на плотной таблице.
      handleWidth: 8,
      // Ниже этого столбец схлопывается в нечитаемую полосу.
      cellMinWidth: 48,
    }),
    TableRow,
    AlignedTableHeader,
    AlignedTableCell,
    TaskList,
    // nested: пункт чеклиста может содержать вложенный чеклист — привычно по
    // Notion и не требует ничего на сервере: узлы те же.
    TaskItem.configure({ nested: true }),
    TextStyle,
    SafeFontFamily,
    SafeColor,
    SafeBackgroundColor,
    TextAlign.configure({
      types: ['heading', 'paragraph'],
      alignments: ['left', 'center', 'right', 'justify'],
    }),
    UniqueID.configure({ types: ID_TYPES, attributeName: 'blockId' }),
    // Официальное расширение из @tiptap/extensions — оно приходит вместе со
    // starter-kit, отдельной зависимости не требуется. Здесь раньше стоял свой
    // компонент поверх полотна: имя пакета искали по второй версии TipTap
    // (@tiptap/extension-placeholder), не нашли и решили, что расширения нет.
    // В третьей версии оно переехало сюда.
    Placeholder.configure({
      placeholder: 'Начните писать документ или введите «/» — появится список блоков',
      // Подсказка нужна в пустом документе, а не в каждом пустом абзаце:
      // иначе она мигает под курсором на каждой новой строке.
      showOnlyWhenEditable: true,
    }),
    CalloutExtension,
    BlockBackgroundExtension,
    KnowledgeImageExtension,
    KnowledgeFileExtension,
    UploadPlaceholderExtension,
  ];
}
