import { CellSelection } from '@tiptap/pm/tables';
import type { ResolvedPos } from '@tiptap/pm/model';
import type { Editor } from '@tiptap/react';

/**
 * Мост между DOM таблицы и командами @tiptap/extension-table.
 *
 * Ручки строк и столбцов (TableControls.tsx) живут в DOM: они знают про
 * <tr> и <td>, но не про позиции в документе ProseMirror. А все табличные
 * команды работают от ТЕКУЩЕГО выделения — «добавить строку ниже» означает
 * «ниже той строки, где стоит курсор». Отсюда единственный приём этого
 * модуля: сначала перевести ячейку из DOM в выделение (CellSelection), потом
 * выполнить обычную команду.
 *
 * CellSelection, а не курсор в ячейке, — потому что выделение строки целиком
 * нужно и по делу (setCellAttribute применяется ко ВСЕМ ячейкам выделения,
 * то есть выравнивание задаётся сразу всей строке или всему столбцу), и
 * визуально: открывая меню строки, видно, какая именно строка будет удалена.
 */

/** Роли узлов-ячеек по схеме @tiptap/extension-table. */
const CELL_ROLES = new Set(['cell', 'header_cell']);

/**
 * Позиция ПЕРЕД ячейкой — та, из которой CellSelection умеет строить выделения.
 *
 * posAtDOM(cell, 0) отдаёт позицию ВНУТРИ ячейки, перед её первым блоком;
 * сама ячейка начинается на единицу раньше. Ошибиться здесь легко, а результат
 * тихий: prosemirror-tables просто не найдёт таблицу и команда молча ничего
 * не сделает.
 */
function resolveCell(editor: Editor, cell: HTMLElement): ResolvedPos | null {
  const { view } = editor;
  if (!cell.isConnected) return null;
  let inside: number;
  try {
    inside = view.posAtDOM(cell, 0);
  } catch {
    // Ячейку успели убрать из документа между отрисовкой ручки и щелчком.
    return null;
  }
  if (inside <= 0) return null;
  const $cell = view.state.doc.resolve(inside - 1);
  const role = $cell.nodeAfter?.type.spec.tableRole;
  return role && CELL_ROLES.has(role) ? $cell : null;
}

export type CellScope = 'row' | 'column' | 'cell';

/**
 * Выделить строку, столбец или одну ячейку по её элементу в DOM.
 *
 * Возвращает false, если ячейки в документе уже нет: в этом случае вызывающий
 * код обязан ничего не делать, а не выполнять команду над чужим выделением.
 */
export function selectCells(editor: Editor, cell: HTMLElement, scope: CellScope): boolean {
  const $cell = resolveCell(editor, cell);
  if (!$cell) return false;
  const selection =
    scope === 'row'
      ? CellSelection.rowSelection($cell)
      : scope === 'column'
        ? CellSelection.colSelection($cell)
        : new CellSelection($cell);
  const { state, dispatch } = editor.view;
  dispatch(state.tr.setSelection(selection));
  return true;
}

export type TableChain = ReturnType<Editor['chain']>;

/**
 * Выделить и выполнить.
 *
 * Два шага, а не одна цепочка: команды внутри chain() делят одну транзакцию,
 * и prosemirror-tables читал бы в них выделение ДО нашей установки. Отдельная
 * отправка гарантирует, что addRowAfter увидит именно ту строку, за ручку
 * которой взялись.
 *
 * focus() в цепочке безопасен: при не-текстовом выделении (а CellSelection
 * именно такое) команда только возвращает фокус в редактор и выделение не
 * трогает — см. @tiptap/core, commands/focus.ts.
 */
export function runOnCells(
  editor: Editor,
  cell: HTMLElement,
  scope: CellScope,
  run: (chain: TableChain) => void,
): void {
  if (!selectCells(editor, cell, scope)) return;
  run(editor.chain().focus());
}
