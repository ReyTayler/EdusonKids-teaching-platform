import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import type { Editor } from '@tiptap/react';
import type { EditorView } from '@tiptap/pm/view';
import { runOnCells, selectCells, type CellScope, type TableChain } from './tableCommands';

/**
 * Управление таблицей ручками по её краям — как в table-node у TipTap и в
 * таблицах Notion/Plate.
 *
 * Почему не всплывающее меню (BubbleMenu), которое стояло здесь раньше:
 *   1. Оно висело над таблицей и закрывало текст ровно там, куда смотришь.
 *   2. Оно переезжало на каждое перемещение курсора между ячейками, то есть
 *      мельтешило при обычном наборе.
 *   3. Выпадающий список внутри всплывающего слоя терял привязку к кнопке и
 *      открывался в углу экрана.
 * Ручки лишены всех трёх бед: они лежат на полях ТАБЛИЦЫ (вне текста), стоят
 * на месте и являются обычными кнопками — Radix привязывает меню к ним штатно.
 *
 * Устройство. Внутри ячеек не рисуется ничего: слой абсолютно позиционирован
 * поверх листа, а координаты берутся измерением DOM таблицы (rect строк и
 * ячеек первой строки). Взамен нужен пересчёт — на транзакциях редактора, на
 * изменении размеров и во время перетаскивания границы столбца (оно меняет
 * ширины в обход транзакций, поэтому там свой кадровый цикл).
 *
 * Действия выполняются через tableCommands.ts: ячейка из DOM → CellSelection →
 * обычная команда @tiptap/extension-table.
 */

interface Band {
  /** Смещение полосы (строки или столбца) от начала таблицы, px. */
  start: number;
  size: number;
  /** Любая ячейка полосы — точка входа в документ для команд. */
  cell: HTMLElement;
}

interface Geometry {
  left: number;
  top: number;
  width: number;
  height: number;
  rows: Band[];
  cols: Band[];
}

/** Насколько положения считаются одинаковыми: доли пикселя не двигают ручку. */
const EPSILON = 0.5;

/**
 * Отступ полосы-ручки от границ своей строки/столбца.
 *
 * Зазор между соседними ручками: без него они сливаются в одну сплошную
 * полосу, и непонятно, где кончается ручка одной строки и начинается ручка
 * следующей.
 */
const BAR_INSET = 2;

/**
 * Пауза перед тем, как убрать ручки после ухода курсора с таблицы.
 *
 * Ручки стоят ЗА пределами таблицы, и путь мыши к ним лежит по чужому тексту.
 * Без паузы они исчезали бы ровно в тот момент, когда до них тянешься.
 */
const HIDE_DELAY_MS = 200;

export function TableControls({ editor }: { editor: Editor }) {
  const layerRef = useRef<HTMLDivElement>(null);
  const [table, setTable] = useState<HTMLTableElement | null>(null);
  const [geometry, setGeometry] = useState<Geometry | null>(null);

  // Представление ProseMirror появляется позже нас (см. viewOf). Держим его в
  // состоянии, чтобы эффекты, которым нужен DOM редактора, выполнились ЗАНОВО,
  // когда оно создастся, а не молча пропустили свою единственную попытку.
  const [view, setView] = useState<EditorView | null>(() => viewOf(editor));

  useEffect(() => {
    const sync = () => setView(viewOf(editor));
    // Первый вызов — на случай, когда представление уже создано: событие о
    // его появлении к этому моменту давно прошло.
    sync();
    // mount — сразу после создания представления, create — тиком позже.
    // Слушаем оба: между ними есть промежуток, и в него попадает компонент,
    // смонтированный ровно в это время. unmount снимает ручки вместе с
    // представлением, чтобы они не остались висеть над мёртвым DOM.
    editor.on('mount', sync);
    editor.on('create', sync);
    editor.on('unmount', sync);
    return () => {
      editor.off('mount', sync);
      editor.off('create', sync);
      editor.off('unmount', sync);
    };
  }, [editor]);

  // Таблица под курсором или под выделением. В ref — чтобы обработчики
  // событий не пересоздавались на каждое наведение.
  const tableRef = useRef<HTMLTableElement | null>(null);
  tableRef.current = table;

  /**
   * Сколько меню сейчас открыто.
   *
   * Пока меню открыто, ручки убирать нельзя ни при каких условиях: выпадающий
   * список Radix живёт внутри ручки, и снятие ручки закрыло бы его. А курсор к
   * открытому меню как раз уходит с листа — то есть без этого счётчика меню
   * закрывалось бы при попытке в него попасть.
   */
  const openMenus = useRef(0);
  const noteMenu = useCallback((open: boolean) => {
    openMenus.current = Math.max(0, openMenus.current + (open ? 1 : -1));
  }, []);

  const measure = useCallback(() => {
    const layer = layerRef.current;
    const target = tableRef.current;
    if (!layer || !target) return;
    // Таблицу могли удалить (командой или Ctrl+Z) — тогда ручкам не за что
    // держаться, и слой обязан исчезнуть вместе с ней.
    if (!target.isConnected) {
      setTable(null);
      setGeometry(null);
      return;
    }
    const next = measureTable(target, layer);
    setGeometry((prev) => (sameGeometry(prev, next) ? prev : next));
  }, []);

  // Кадр пересчёта. Измерять синхронно на каждое событие незачем: за один кадр
  // приходят и транзакция, и ResizeObserver, и событие мыши.
  const frame = useRef(0);
  const schedule = useCallback(() => {
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(measure);
  }, [measure]);

  // Какая таблица «активна»: та, на которую навели мышь, а когда мыши на
  // таблице нет — та, внутри которой стоит курсор.
  useEffect(() => {
    if (!view) return;
    const dom = view.dom as HTMLElement;
    const sheet = (dom.closest('.kb-editor-sheet') ?? dom) as HTMLElement;
    let hideTimer = 0;

    const keep = () => window.clearTimeout(hideTimer);
    const hideSoon = () => {
      window.clearTimeout(hideTimer);
      hideTimer = window.setTimeout(() => {
        if (openMenus.current > 0) return;
        setTable(tableForSelection(editor));
      }, HIDE_DELAY_MS);
    };

    // Слушаем лист целиком, а не полотно редактора: ручки лежат на его полях,
    // и наведение на них обязано считаться «мы всё ещё у таблицы».
    const onOver = (event: Event) => {
      const target = event.target as HTMLElement | null;
      const hovered = target?.closest?.('table') as HTMLTableElement | null;
      if (hovered) {
        keep();
        if (hovered !== tableRef.current) setTable(hovered);
        return;
      }
      if (target?.closest?.('.kb-tablectl-layer')) {
        keep();
        return;
      }
      hideSoon();
    };

    sheet.addEventListener('mouseover', onOver);
    sheet.addEventListener('mouseleave', hideSoon);
    return () => {
      window.clearTimeout(hideTimer);
      sheet.removeEventListener('mouseover', onOver);
      sheet.removeEventListener('mouseleave', hideSoon);
    };
  }, [editor, view]);

  useEffect(() => {
    const onTransaction = () => {
      const selected = tableForSelection(editor);
      if (selected && selected !== tableRef.current) setTable(selected);
      schedule();
    };
    editor.on('transaction', onTransaction);
    return () => {
      editor.off('transaction', onTransaction);
    };
  }, [editor, schedule]);

  // Измеряем до отрисовки: иначе ручки появляются на кадр раньше, чем встают
  // на свои места, и это видно как рывок.
  useLayoutEffect(() => {
    if (!table || !view) {
      setGeometry(null);
      return;
    }
    measure();

    const observer = new ResizeObserver(schedule);
    observer.observe(table);
    if (layerRef.current) observer.observe(layerRef.current);
    window.addEventListener('resize', schedule);

    /**
     * Перетаскивание границы столбца (штатное resizable у extension-table)
     * меняет ширины прямо в DOM, без транзакции и без изменения размеров самой
     * таблицы, — ни ResizeObserver, ни подписка на транзакции его не замечают.
     * Поэтому на время зажатой кнопки мыши идёт свой кадровый цикл.
     */
    const dom = view.dom as HTMLElement;
    let dragging = false;
    const tick = () => {
      if (!dragging) return;
      measure();
      requestAnimationFrame(tick);
    };
    const onDown = () => {
      dragging = true;
      requestAnimationFrame(tick);
    };
    const onUp = () => {
      dragging = false;
      schedule();
    };
    dom.addEventListener('mousedown', onDown);
    window.addEventListener('mouseup', onUp);

    return () => {
      observer.disconnect();
      window.removeEventListener('resize', schedule);
      dom.removeEventListener('mousedown', onDown);
      window.removeEventListener('mouseup', onUp);
      cancelAnimationFrame(frame.current);
    };
  }, [table, view, measure, schedule]);

  return (
    <div ref={layerRef} className="kb-tablectl-layer">
      {geometry && table && (
        <TableFrame
          editor={editor}
          table={table}
          geometry={geometry}
          onChanged={schedule}
          onMenuToggle={noteMenu}
        />
      )}
    </div>
  );
}

/** Рамка ручек вокруг одной таблицы. */
function TableFrame({
  editor,
  table,
  geometry,
  onChanged,
  onMenuToggle,
}: {
  editor: Editor;
  table: HTMLTableElement;
  geometry: Geometry;
  onChanged: () => void;
  onMenuToggle: (open: boolean) => void;
}) {
  const { rows, cols } = geometry;
  const firstCell = rows[0]?.cell;

  const act: ActOnCell = (cell, scope, run) => {
    if (!cell) return;
    runOnCells(editor, cell, scope, run);
    onChanged();
  };

  /**
   * Действие над ТЕКУЩИМ выделением — для меню всей таблицы.
   *
   * Выделение там намеренно не переставляется: «объединить ячейки» обязано
   * работать над теми ячейками, которые человек выделил протяжкой. Переставь
   * мы выделение на всю таблицу — та же команда слила бы её в одну ячейку.
   */
  const actCurrent = (run: (chain: TableChain) => void) => {
    run(editor.chain().focus());
    onChanged();
  };

  return (
    <div
      className="kb-tablectl"
      style={{
        left: geometry.left,
        top: geometry.top,
        width: geometry.width,
        height: geometry.height,
      }}
    >
      {/* Угол — вход в действия над всей таблицей: шапки, объединение, удаление. */}
      <HandleMenu
        className="kb-tablectl__corner"
        label="Действия с таблицей"
        icon={<GridGlyph />}
        onToggle={onMenuToggle}
        onOpen={() => {
          // Курсор мог остаться в другом месте документа — тогда команды меню
          // не нашли бы таблицу. Если же он уже здесь, выделение не трогаем:
          // им может быть выделенная протяжкой область для объединения.
          if (tableForSelection(editor) !== table && firstCell) {
            selectCells(editor, firstCell, 'cell');
          }
        }}
        items={[
          { label: 'Строка-шапка', onSelect: () => actCurrent((c) => c.toggleHeaderRow().run()) },
          { label: 'Столбец-шапка', onSelect: () => actCurrent((c) => c.toggleHeaderColumn().run()) },
          'sep',
          {
            label: 'Объединить или разделить ячейки',
            onSelect: () => actCurrent((c) => c.mergeOrSplit().run()),
          },
          'sep',
          { label: 'Удалить таблицу', danger: true, onSelect: () => actCurrent((c) => c.deleteTable().run()) },
        ]}
      />

      {cols.map((col, index) => (
        <HandleMenu
          key={`col-${index}`}
          className="kb-tablectl__bar kb-tablectl__bar--col"
          style={{ left: col.start + BAR_INSET, width: Math.max(8, col.size - BAR_INSET * 2) }}
          label={`Действия со столбцом ${index + 1}`}
          icon={<DotsGlyph />}
          onToggle={onMenuToggle}
          onOpen={() => selectCells(editor, col.cell, 'column')}
          items={columnItems(index === 0, (scope, run) => act(col.cell, scope, run))}
        />
      ))}

      {rows.map((row, index) => (
        <HandleMenu
          key={`row-${index}`}
          className="kb-tablectl__bar kb-tablectl__bar--row"
          style={{ top: row.start + BAR_INSET, height: Math.max(8, row.size - BAR_INSET * 2) }}
          label={`Действия со строкой ${index + 1}`}
          icon={<DotsGlyph vertical />}
          onToggle={onMenuToggle}
          onOpen={() => selectCells(editor, row.cell, 'row')}
          items={rowItems(index === 0, (scope, run) => act(row.cell, scope, run))}
        />
      ))}

      {/* Длинные кнопки по правому и нижнему краю — дописать столбец и строку
          в конец. Самое частое действие при наборе таблицы, поэтому у него
          отдельная крупная цель, а не точка на границе. */}
      <button
        type="button"
        className="kb-tablectl__add kb-tablectl__add--col"
        aria-label="Добавить столбец в конец"
        onClick={() => act(cols[cols.length - 1]?.cell, 'column', (c) => c.addColumnAfter().run())}
      >
        <PlusGlyph />
      </button>
      <button
        type="button"
        className="kb-tablectl__add kb-tablectl__add--row"
        aria-label="Добавить строку в конец"
        onClick={() => act(rows[rows.length - 1]?.cell, 'row', (c) => c.addRowAfter().run())}
      >
        <PlusGlyph />
      </button>
    </div>
  );
}

type MenuItem = { label: string; onSelect: () => void; danger?: boolean } | 'sep';

type Act = (scope: CellScope, run: (chain: TableChain) => void) => void;

type ActOnCell = (
  cell: HTMLElement | undefined,
  scope: CellScope,
  run: (chain: TableChain) => void,
) => void;

function rowItems(isFirst: boolean, act: Act): MenuItem[] {
  return [
    { label: 'Строку выше', onSelect: () => act('row', (c) => c.addRowBefore().run()) },
    { label: 'Строку ниже', onSelect: () => act('row', (c) => c.addRowAfter().run()) },
    'sep',
    ...alignItems(act, 'row'),
    ...(isFirst
      ? ([
          'sep',
          { label: 'Строка-шапка', onSelect: () => act('row', (c) => c.toggleHeaderRow().run()) },
        ] as MenuItem[])
      : []),
    'sep',
    { label: 'Удалить строку', danger: true, onSelect: () => act('row', (c) => c.deleteRow().run()) },
  ];
}

function columnItems(isFirst: boolean, act: Act): MenuItem[] {
  return [
    { label: 'Столбец слева', onSelect: () => act('column', (c) => c.addColumnBefore().run()) },
    { label: 'Столбец справа', onSelect: () => act('column', (c) => c.addColumnAfter().run()) },
    'sep',
    ...alignItems(act, 'column'),
    ...(isFirst
      ? ([
          'sep',
          { label: 'Столбец-шапка', onSelect: () => act('column', (c) => c.toggleHeaderColumn().run()) },
        ] as MenuItem[])
      : []),
    'sep',
    { label: 'Удалить столбец', danger: true, onSelect: () => act('column', (c) => c.deleteColumn().run()) },
  ];
}

/**
 * Выравнивание текста в полосе.
 *
 * setCellAttribute применяется ко всем ячейкам выделения, а выделена у нас
 * целая строка или целый столбец — поэтому пункт задаёт выравнивание сразу
 * всей полосе, а не одной ячейке. Сам атрибут ячейке добавляет
 * editorExtensions.ts (withCellAlign): в @tiptap/extension-table его нет.
 */
function alignItems(act: Act, scope: CellScope): MenuItem[] {
  return [
    { label: 'Текст по левому краю', onSelect: () => act(scope, (c) => c.setCellAttribute('align', 'left').run()) },
    { label: 'Текст по центру', onSelect: () => act(scope, (c) => c.setCellAttribute('align', 'center').run()) },
    { label: 'Текст по правому краю', onSelect: () => act(scope, (c) => c.setCellAttribute('align', 'right').run()) },
  ];
}

/** Ручка с выпадающим меню. */
function HandleMenu({
  className,
  style,
  label,
  icon,
  items,
  onOpen,
  onToggle,
}: {
  className: string;
  style?: CSSProperties;
  label: string;
  icon: ReactNode;
  items: MenuItem[];
  onOpen: () => void;
  onToggle: (open: boolean) => void;
}) {
  return (
    <DropdownMenu.Root
      // modal={false} — редактор под меню остаётся живым: Radix в модальном
      // режиме гасит указатель на всей странице и блокирует прокрутку.
      modal={false}
      onOpenChange={(open) => {
        onToggle(open);
        // Выделяем полосу в момент открытия: видно, над чем меню.
        if (open) onOpen();
      }}
    >
      <DropdownMenu.Trigger className={className} style={style} aria-label={label}>
        {icon}
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          className="action-menu__list"
          align="start"
          sideOffset={4}
          // Фокус после закрытия возвращать на ручку не нужно: работа
          // продолжается в тексте, а не на полях таблицы.
          onCloseAutoFocus={(event) => event.preventDefault()}
        >
          {items.map((item, index) =>
            item === 'sep' ? (
              <DropdownMenu.Separator key={`sep-${index}`} className="action-menu__sep" />
            ) : (
              <DropdownMenu.Item
                key={item.label}
                className={`action-menu__item${item.danger ? ' is-danger' : ''}`}
                onSelect={item.onSelect}
              >
                {item.label}
              </DropdownMenu.Item>
            ),
          )}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/* --- Измерение ------------------------------------------------------------ */

function measureTable(table: HTMLTableElement, layer: HTMLElement): Geometry {
  const origin = layer.getBoundingClientRect();
  const box = table.getBoundingClientRect();
  const rows = Array.from(table.rows);

  const rowBands: Band[] = [];
  for (const row of rows) {
    const cell = row.cells[0];
    if (!cell) continue;
    const rect = row.getBoundingClientRect();
    rowBands.push({ start: rect.top - box.top, size: rect.height, cell });
  }

  // Столбцы считаем по ячейкам ПЕРВОЙ строки: только у неё гарантированно есть
  // ячейка в каждом столбце (ниже могут быть объединения).
  const colBands: Band[] = [];
  for (const cell of Array.from(rows[0]?.cells ?? [])) {
    const rect = cell.getBoundingClientRect();
    colBands.push({ start: rect.left - box.left, size: rect.width, cell });
  }

  return {
    left: box.left - origin.left,
    top: box.top - origin.top,
    width: box.width,
    height: box.height,
    rows: rowBands,
    cols: colBands,
  };
}

function sameGeometry(a: Geometry | null, b: Geometry): boolean {
  if (!a) return false;
  if (
    Math.abs(a.left - b.left) > EPSILON ||
    Math.abs(a.top - b.top) > EPSILON ||
    Math.abs(a.width - b.width) > EPSILON ||
    Math.abs(a.height - b.height) > EPSILON
  ) {
    return false;
  }
  return sameBands(a.rows, b.rows) && sameBands(a.cols, b.cols);
}

function sameBands(a: Band[], b: Band[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((band, index) => {
    const other = b[index];
    return (
      band.cell === other.cell &&
      Math.abs(band.start - other.start) <= EPSILON &&
      Math.abs(band.size - other.size) <= EPSILON
    );
  });
}

/**
 * Представление редактора — или null, пока его нет.
 *
 * Проверить нужно именно так, а не `if (editor.view)`: до создания
 * представления @tiptap/core отдаёт на этом месте прокси, который истинен, но
 * бросает исключение при обращении к любому полю, кроме state и dispatch.
 * Компонент монтируется РАНЬШЕ, чем EditorContent создаёт представление
 * (эффекты выполняются в порядке дерева, а EditorContent идёт следом), — то
 * есть первый заход в эффект приходится ровно на этот промежуток.
 */
function viewOf(editor: Editor): EditorView | null {
  if (editor.isDestroyed) return null;
  try {
    const view = editor.view;
    return view.dom ? view : null;
  } catch {
    return null;
  }
}

/** Таблица, внутри которой стоит курсор, — или null. */
function tableForSelection(editor: Editor): HTMLTableElement | null {
  const view = viewOf(editor);
  if (!view || !editor.isActive('table')) return null;
  try {
    const { node } = view.domAtPos(editor.state.selection.from);
    const element = node.nodeType === 1 ? (node as HTMLElement) : node.parentElement;
    return element?.closest('table') ?? null;
  } catch {
    return null;
  }
}

/* --- Значки --------------------------------------------------------------- */

function DotsGlyph({ vertical = false }: { vertical?: boolean }) {
  const points: [number, number][] = vertical
    ? [[8, 3.5], [8, 8], [8, 12.5]]
    : [[3.5, 8], [8, 8], [12.5, 8]];
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      {points.map(([cx, cy]) => (
        <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="1.35" />
      ))}
    </svg>
  );
}

function PlusGlyph() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function GridGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <line x1="3" y1="9" x2="21" y2="9" />
      <line x1="9" y1="9" x2="9" y2="21" />
    </svg>
  );
}
