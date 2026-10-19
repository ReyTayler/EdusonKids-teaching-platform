import { useCallback, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { Editor } from '@tiptap/react';
import { useEditorState } from '@tiptap/react';
import * as Tooltip from '@radix-ui/react-tooltip';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { FONT_CHOICES, DEFAULT_FONT_LABEL } from './editorFonts';
import { CODE_LANGUAGES } from './codeLanguages';
import { HIGHLIGHT_COLORS, TEXT_COLORS, displayTextColor } from './editorColors';
import { askLink } from './linkPrompt';
import {
  AlignCenterIcon,
  AlignJustifyIcon,
  AlignLeftIcon,
  AlignRightIcon,
  BoldIcon,
  BulletListIcon,
  CodeBlockIcon,
  CodeIcon,
  Heading1Icon,
  Heading2Icon,
  Heading3Icon,
  HorizontalRuleIcon,
  ImageIcon,
  AttachIcon,
  ItalicIcon,
  OrderedListIcon,
  QuoteIcon,
  SpinnerIcon,
  StrikeIcon,
  TableIcon,
  UnderlineIcon,
  LinkIcon,
  TaskListIcon,
  CalloutIcon,
  UndoIcon,
  RedoIcon,
  type IconProps,
} from './editorIcons';

/**
 * Панель форматирования — иконочные кнопки, сгруппированные по смыслу.
 *
 * Не использует компонент Button проекта: тот рассчитан на самостоятельные
 * кнопки действия с горизонтальными полями, а не на плотную квадратную сетку
 * инструментов — здесь собственный класс .kb-tool (см. knowledge.css).
 *
 * Активное и доступное состояние читается через useEditorState, а не через
 * editor.isActive()/editor.can() напрямую в JSX. Прямое чтение не обновляло бы
 * панель: useEditor в @tiptap/react v3 по умолчанию НЕ ререндерит компонент на
 * каждую транзакцию — это сознательная оптимизация против ререндера тяжёлого
 * редактора на каждое движение курсора. Без точечной подписки подсветка
 * «полужирный» не двигалась бы вместе с курсором.
 */

interface Tool {
  key: string;
  icon: (props: IconProps) => ReactNode;
  label: string;
  shortcut?: string;
  /** undefined — команда без состояния (линия/таблица/картинка): aria-pressed не рисуется. */
  active?: boolean;
  disabled?: boolean;
  loading?: boolean;
  run: () => void;
}

export function EditorToolbar({
  editor,
  onPickImage,
  onPickFile,
  uploading,
}: {
  editor: Editor;
  onPickImage: () => void;
  onPickFile: () => void;
  uploading: boolean;
}) {
  const state = useEditorState({
    editor,
    selector: ({ editor: e }) => {
      // isDestroyed — обязательная часть проверки, а не перестраховка.
      // При уничтожении редактора его внутренний менеджер команд обнуляется,
      // а сам объект остаётся живым: подписка успевает вызвать селектор ещё
      // раз, и editor.can() падает с «Cannot read properties of null».
      // Одной проверки на null здесь мало.
      if (!e || e.isDestroyed) return null;
      const can = () => e.can().chain().focus();
      return {
        h1: e.isActive('heading', { level: 1 }),
        h2: e.isActive('heading', { level: 2 }),
        h3: e.isActive('heading', { level: 3 }),
        bold: e.isActive('bold'),
        italic: e.isActive('italic'),
        underline: e.isActive('underline'),
        strike: e.isActive('strike'),
        code: e.isActive('code'),
        alignLeft: e.isActive({ textAlign: 'left' }),
        alignCenter: e.isActive({ textAlign: 'center' }),
        alignRight: e.isActive({ textAlign: 'right' }),
        alignJustify: e.isActive({ textAlign: 'justify' }),
        fontFamily: (e.getAttributes('textStyle').fontFamily as string | undefined) ?? '',
        color: (e.getAttributes('textStyle').color as string | undefined) ?? '',
        highlight: (e.getAttributes('textStyle').backgroundColor as string | undefined) ?? '',
        // Фон блока живёт на самом абзаце/заголовке, а не на марке текста,
        // поэтому и читается из атрибутов узла.
        blockBg: (e.getAttributes('paragraph').blockBackground
          ?? e.getAttributes('heading').blockBackground
          ?? '') as string,
        bulletList: e.isActive('bulletList'),
        orderedList: e.isActive('orderedList'),
        taskList: e.isActive('taskList'),
        blockquote: e.isActive('blockquote'),
        callout: e.isActive('callout'),
        codeBlock: e.isActive('codeBlock'),
        link: e.isActive('link'),
        codeLanguage: (e.getAttributes('codeBlock').language as string | undefined) ?? '',
        canH1: can().toggleHeading({ level: 1 }).run(),
        canH2: can().toggleHeading({ level: 2 }).run(),
        canH3: can().toggleHeading({ level: 3 }).run(),
        canBold: can().toggleBold().run(),
        canItalic: can().toggleItalic().run(),
        canUnderline: can().toggleUnderline().run(),
        canStrike: can().toggleStrike().run(),
        canCode: can().toggleCode().run(),
        canBulletList: can().toggleBulletList().run(),
        canOrderedList: can().toggleOrderedList().run(),
        canBlockquote: can().toggleBlockquote().run(),
        canCodeBlock: can().toggleCodeBlock().run(),
        canTaskList: can().toggleTaskList().run(),
        canCallout: can().toggleCallout().run(),
        canHr: can().setHorizontalRule().run(),
        canTable: can().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(),
        canUndo: e.can().undo(),
        canRedo: e.can().redo(),
      };
    },
  });

  if (!state) return null;

  const chain = () => editor.chain().focus();

  const groups: Tool[][] = [
    [
      // Три уровня, а не два: h1 принимает сервер и рисует читалка, а
      // документы, вставленные из Word и Google Docs, приходят именно с ним.
      // Пока кнопки не было, такой заголовок нельзя было ни поставить, ни
      // снять — только набрать вслепую сочетанием клавиш.
      {
        key: 'h1', icon: Heading1Icon, label: 'Крупный заголовок', shortcut: 'Ctrl+Alt+1',
        active: state.h1, disabled: !state.canH1,
        run: () => chain().toggleHeading({ level: 1 }).run(),
      },
      {
        key: 'h2', icon: Heading2Icon, label: 'Заголовок', shortcut: 'Ctrl+Alt+2',
        active: state.h2, disabled: !state.canH2,
        run: () => chain().toggleHeading({ level: 2 }).run(),
      },
      {
        key: 'h3', icon: Heading3Icon, label: 'Подзаголовок', shortcut: 'Ctrl+Alt+3',
        active: state.h3, disabled: !state.canH3,
        run: () => chain().toggleHeading({ level: 3 }).run(),
      },
    ],
    [
      {
        key: 'bold', icon: BoldIcon, label: 'Полужирный', shortcut: 'Ctrl+B',
        active: state.bold, disabled: !state.canBold,
        run: () => chain().toggleBold().run(),
      },
      {
        key: 'italic', icon: ItalicIcon, label: 'Курсив', shortcut: 'Ctrl+I',
        active: state.italic, disabled: !state.canItalic,
        run: () => chain().toggleItalic().run(),
      },
      {
        key: 'underline', icon: UnderlineIcon, label: 'Подчёркнутый', shortcut: 'Ctrl+U',
        active: state.underline, disabled: !state.canUnderline,
        run: () => chain().toggleUnderline().run(),
      },
      {
        key: 'strike', icon: StrikeIcon, label: 'Зачёркнутый', shortcut: 'Ctrl+Shift+S',
        active: state.strike, disabled: !state.canStrike,
        run: () => chain().toggleStrike().run(),
      },
      {
        key: 'code', icon: CodeIcon, label: 'Моноширинный код', shortcut: 'Ctrl+E',
        active: state.code, disabled: !state.canCode,
        run: () => chain().toggleCode().run(),
      },
    ],
    [
      {
        key: 'ul', icon: BulletListIcon, label: 'Маркированный список',
        active: state.bulletList, disabled: !state.canBulletList,
        run: () => chain().toggleBulletList().run(),
      },
      {
        key: 'ol', icon: OrderedListIcon, label: 'Нумерованный список',
        active: state.orderedList, disabled: !state.canOrderedList,
        run: () => chain().toggleOrderedList().run(),
      },
      {
        key: 'tasks', icon: TaskListIcon, label: 'Чеклист',
        active: state.taskList, disabled: !state.canTaskList,
        run: () => chain().toggleTaskList().run(),
      },
      {
        key: 'quote', icon: QuoteIcon, label: 'Цитата',
        active: state.blockquote, disabled: !state.canBlockquote,
        run: () => chain().toggleBlockquote().run(),
      },
    ],
    [
      {
        key: 'align-left', icon: AlignLeftIcon, label: 'По левому краю',
        active: state.alignLeft,
        run: () => chain().setTextAlign('left').run(),
      },
      {
        key: 'align-center', icon: AlignCenterIcon, label: 'По центру',
        active: state.alignCenter,
        run: () => chain().setTextAlign('center').run(),
      },
      {
        key: 'align-right', icon: AlignRightIcon, label: 'По правому краю',
        active: state.alignRight,
        run: () => chain().setTextAlign('right').run(),
      },
      {
        key: 'align-justify', icon: AlignJustifyIcon, label: 'По ширине',
        active: state.alignJustify,
        run: () => chain().setTextAlign('justify').run(),
      },
    ],
    [
      {
        key: 'callout', icon: CalloutIcon, label: 'Выноска',
        active: state.callout, disabled: !state.canCallout,
        run: () => chain().toggleCallout().run(),
      },
      {
        key: 'codeblock', icon: CodeBlockIcon, label: 'Блок кода',
        active: state.codeBlock, disabled: !state.canCodeBlock,
        run: () => chain().toggleCodeBlock().run(),
      },
      {
        key: 'hr', icon: HorizontalRuleIcon, label: 'Горизонтальная линия',
        disabled: !state.canHr,
        run: () => chain().setHorizontalRule().run(),
      },
      {
        key: 'table', icon: TableIcon, label: 'Таблица',
        disabled: !state.canTable,
        run: () => chain().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(),
      },
    ],
    [
      {
        key: 'link', icon: LinkIcon, label: 'Ссылка', shortcut: 'Ctrl+K',
        active: state.link,
        run: () => askLink(editor),
      },
      {
        key: 'image', icon: ImageIcon, label: 'Картинка',
        loading: uploading, disabled: uploading,
        run: onPickImage,
      },
      {
        key: 'file', icon: AttachIcon, label: 'Прикрепить файл',
        run: onPickFile,
      },
    ],
    [
      {
        key: 'undo', icon: UndoIcon, label: 'Отменить', shortcut: 'Ctrl+Z',
        disabled: !state.canUndo,
        run: () => chain().undo().run(),
      },
      {
        key: 'redo', icon: RedoIcon, label: 'Вернуть', shortcut: 'Ctrl+Shift+Z',
        disabled: !state.canRedo,
        run: () => chain().redo().run(),
      },
    ],
  ];

  return (
    <Tooltip.Provider delayDuration={350} skipDelayDuration={200}>
      <RovingToolbar
        groups={groups}
        before={
          <>
            <FontPicker
              value={state.fontFamily}
              onChange={(font) => {
                if (font) chain().setFontFamily(font).run();
                else chain().unsetFontFamily().run();
              }}
            />
            <ColorPicker
              color={state.color}
              highlight={state.highlight}
              blockBg={state.blockBg}
              onColor={(value) => {
                if (value) chain().setColor(value).run();
                else chain().unsetColor().run();
              }}
              onHighlight={(value) => {
                if (value) chain().setBackgroundColor(value).run();
                else chain().unsetBackgroundColor().run();
              }}
              onBlockBg={(value) => {
                if (value) chain().setBlockBackground(value).run();
                else chain().unsetBlockBackground().run();
              }}
            />
          </>
        }
        after={
          // Язык показываем только внутри блока кода: в обычном абзаце этот
          // список ни на что не влияет и только занимает место в панели.
          // Управление таблицей живёт на её собственных полях
          // (TableControls) — ручки строк и столбцов там, где сама таблица,
          // а не в общей панели наверху страницы.
          state.codeBlock ? (
            <LanguagePicker
              value={state.codeLanguage}
              onChange={(language) => chain().updateAttributes('codeBlock', { language }).run()}
            />
          ) : null
        }
      />
    </Tooltip.Provider>
  );
}

/** Язык блока кода. Список закрытый — сервер принимает только его (content.py). */
function LanguagePicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (language: string) => void;
}) {
  const current = CODE_LANGUAGES.find((item) => item.value === value) ?? CODE_LANGUAGES[0];

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="kb-toolselect kb-toolselect--narrow" aria-label="Язык блока кода">
        <span className="kb-toolselect__label">{current.label}</span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="action-menu__list" align="start" sideOffset={6}>
          {CODE_LANGUAGES.map((item) => (
            <DropdownMenu.Item
              key={item.value || 'none'}
              className="action-menu__item"
              onSelect={() => onChange(item.value)}
            >
              {item.label}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/**
 * Цвет букв и цвет выделения — в одном меню двумя рядами образцов.
 *
 * Не свободная палитра: список закрытый и состоит из ссылок на токены
 * (editorColors.ts). Цвет в проекте закреплён за смыслом, а произвольная
 * раскраска ещё и ломалась бы при смене темы — сохранённый код цвета не знает,
 * на каком фоне его потом покажут.
 *
 * Образцы — квадраты с подписью в title, а не только цветные кнопки: по одному
 * цвету не догадаться, что «оранжевый» здесь означает «внимание», да и выбирать
 * цвет вслепую при нарушении цветовосприятия невозможно.
 */
function ColorPicker({
  color,
  highlight,
  blockBg,
  onColor,
  onHighlight,
  onBlockBg,
}: {
  color: string;
  highlight: string;
  blockBg: string;
  onColor: (value: string) => void;
  onHighlight: (value: string) => void;
  onBlockBg: (value: string) => void;
}) {
  const active = color || highlight;

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="kb-tool kb-colorpick" aria-label="Цвет текста и выделение">
        <span className="kb-colorpick__glyph" aria-hidden="true">A</span>
        <span
          className="kb-colorpick__bar"
          aria-hidden="true"
          style={active ? { background: color ? displayTextColor(color) : highlight } : undefined}
        />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="action-menu__list kb-colors" align="start" sideOffset={6}>
          <span className="kb-colors__title">Цвет текста</span>
          <div className="kb-colors__row">
            {TEXT_COLORS.map((item) => (
              <DropdownMenu.Item
                key={item.label}
                className={`kb-colors__swatch${color === item.value ? ' is-active' : ''}`}
                title={item.label}
                onSelect={() => onColor(item.value)}
              >
                <span
                  className="kb-colors__chip kb-colors__chip--text"
                  style={item.value ? { color: displayTextColor(item.value) } : undefined}
                >
                  A
                </span>
              </DropdownMenu.Item>
            ))}
          </div>

          <span className="kb-colors__title">Выделение</span>
          <div className="kb-colors__row">
            {HIGHLIGHT_COLORS.map((item) => (
              <DropdownMenu.Item
                key={item.label}
                className={`kb-colors__swatch${highlight === item.value ? ' is-active' : ''}`}
                title={item.label}
                onSelect={() => onHighlight(item.value)}
              >
                <span
                  className="kb-colors__chip"
                  style={item.value ? { background: item.value } : undefined}
                />
              </DropdownMenu.Item>
            ))}
          </div>

          {/* Третий ряд — заливка целого абзаца. Оттенки те же: требование к
              ним одно — читаемость под любым цветом текста. */}
          <span className="kb-colors__title">Фон блока</span>
          <div className="kb-colors__row">
            {HIGHLIGHT_COLORS.map((item) => (
              <DropdownMenu.Item
                key={item.label}
                className={`kb-colors__swatch${blockBg === item.value ? ' is-active' : ''}`}
                title={item.label}
                onSelect={() => onBlockBg(item.value)}
              >
                <span
                  className="kb-colors__chip"
                  style={item.value ? { background: item.value } : undefined}
                />
              </DropdownMenu.Item>
            ))}
          </div>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/**
 * Выбор шрифта. Список закрытый (editorFonts.ts) и совпадает с тем, что
 * принимает сервер: свободный ввод означал бы произвольный CSS в документе.
 *
 * Radix-меню, а не <select>: нативные списки в админке запрещены и не
 * поддаются оформлению, а тут ещё и нужно показывать каждый пункт его же
 * шрифтом — иначе выбирать приходится вслепую.
 */
function FontPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (font: string) => void;
}) {
  const current = FONT_CHOICES.find((f) => f.value === value);

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="kb-toolselect" aria-label="Шрифт">
        <span className="kb-toolselect__label" style={current ? { fontFamily: current.value } : undefined}>
          {current?.label ?? DEFAULT_FONT_LABEL}
        </span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="action-menu__list" align="start" sideOffset={6}>
          <DropdownMenu.Item className="action-menu__item" onSelect={() => onChange('')}>
            {DEFAULT_FONT_LABEL}
          </DropdownMenu.Item>
          {FONT_CHOICES.map((font) => (
            <DropdownMenu.Item
              key={font.value}
              className="action-menu__item"
              style={{ fontFamily: font.value }}
              onSelect={() => onChange(font.value)}
            >
              {font.label}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/**
 * Панель с «блуждающим» фокусом (roving tabindex, паттерн ARIA toolbar).
 *
 * В обходе клавиатурой панель занимает ОДНУ позицию, а не тринадцать: внутри
 * перемещение стрелками, Home/End — к краям. Без этого путь от заголовка
 * страницы до текста статьи занимал бы 13 нажатий Tab, и клавиатурный
 * пользователь просто не смог бы быстро добраться до полотна.
 */
function RovingToolbar({
  groups,
  before,
  after,
}: {
  groups: Tool[][];
  before?: ReactNode;
  after?: ReactNode;
}) {
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Кнопки ищем в DOM, а не держим массив ref'ов: у кнопок уже есть ref от
  // Tooltip.Trigger (asChild), и второй ref поверх него — лишний источник
  // расхождений. Плюс позиция фокуса хранится ключом инструмента, а не
  // индексом: индексы «съезжают», когда набор кнопок меняется.
  const buttons = useCallback(
    () => Array.from(containerRef.current?.querySelectorAll<HTMLButtonElement>(
      'button[data-tool]:not([disabled])',
    ) ?? []),
    [],
  );

  const move = useCallback(
    (step: number) => {
      const list = buttons();
      if (list.length === 0) return;
      const current = list.findIndex((b) => b === document.activeElement);
      const next = list[(current + step + list.length) % list.length] ?? list[0];
      next.focus();
      setFocusedKey(next.dataset.tool ?? null);
    },
    [buttons],
  );

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const list = buttons();
    if (list.length === 0) return;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      event.preventDefault();
      move(1);
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      event.preventDefault();
      move(-1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      list[0].focus();
      setFocusedKey(list[0].dataset.tool ?? null);
    } else if (event.key === 'End') {
      event.preventDefault();
      const last = list[list.length - 1];
      last.focus();
      setFocusedKey(last.dataset.tool ?? null);
    }
  };

  // Первая доступная кнопка — точка входа в панель по Tab, если фокус ещё
  // никуда не ставили или инструмент стал недоступен.
  const flat = groups.flat();
  const entryKey = flat.find((t) => t.key === focusedKey && !t.disabled)?.key
    ?? flat.find((t) => !t.disabled)?.key
    ?? null;

  return (
    <div
      ref={containerRef}
      className="kb-editor__toolbar"
      role="toolbar"
      aria-label="Форматирование текста"
      aria-orientation="horizontal"
      onKeyDown={onKeyDown}
    >
      {before && (
        <>
          {before}
          <Separator />
        </>
      )}
      {groups.map((group, groupIndex) => (
        <div className="kb-tool-group" key={group[0].key}>
          {groupIndex > 0 && <Separator />}
          {group.map((tool) => (
            <ToolButton
              key={tool.key}
              tool={tool}
              tabIndex={tool.key === entryKey ? 0 : -1}
              onFocus={() => setFocusedKey(tool.key)}
            />
          ))}
        </div>
      ))}
      {after && (
        <>
          <Separator />
          {after}
        </>
      )}
    </div>
  );
}

function Separator() {
  return <div className="kb-tool-sep" role="separator" aria-orientation="vertical" />;
}

function ToolButton({
  tool,
  tabIndex,
  onFocus,
}: {
  tool: Tool;
  tabIndex: number;
  onFocus: () => void;
}) {
  const { icon: IconComp, label, shortcut, active, disabled = false, loading = false, run } = tool;

  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <button
          data-tool={tool.key}
          type="button"
          className={`kb-tool${active ? ' is-active' : ''}`}
          aria-label={label}
          aria-pressed={active === undefined ? undefined : active}
          aria-keyshortcuts={shortcut}
          tabIndex={tabIndex}
          disabled={disabled}
          onFocus={onFocus}
          onClick={run}
        >
          {loading ? <SpinnerIcon size={18} /> : <IconComp size={18} />}
        </button>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content className="kb-tool-tip" sideOffset={6}>
          {label}
          {shortcut && <span className="kb-tool-tip__shortcut">{shortcut}</span>}
          <Tooltip.Arrow className="kb-tool-tip__arrow" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}
