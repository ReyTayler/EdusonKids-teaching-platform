import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import type { Editor } from '@tiptap/react';

/**
 * Место под загружаемый файл — декорацией, а не узлом документа.
 *
 * Это канонический приём ProseMirror для асинхронной вставки, и выбран он не
 * ради чистоты. Декорация НЕ является частью документа: её нельзя случайно
 * сохранить, она не проходит проверку содержимого и не переживает
 * перезагрузку страницы — то есть все три вопроса «а что, если сохранение
 * уйдёт прямо сейчас» отпадают сами.
 *
 * Раньше здесь стоял настоящий узел `knowledgeFile` с `fileId: null`. Из этого
 * росли два костыля: вырезание таких узлов перед отправкой (иначе сервер
 * отвергал документ целиком, потому что у файла нет числового id) и поиск
 * блока обходом дерева при замене. Оба исчезли вместе с узлом.
 *
 * Позиция переезжает сама: `set.map(tr.mapping, tr.doc)` сдвигает декорацию при
 * любой правке документа, пока файл летит на сервер. Обходить дерево и искать
 * блок по идентификатору больше не нужно — `find` возвращает актуальную
 * позицию.
 */

const key = new PluginKey<DecorationSet>('knowledgeUploadPlaceholder');

interface AddAction {
  add: { id: string; pos: number; name: string };
  remove?: never;
}
interface RemoveAction {
  remove: { id: string };
  add?: never;
}
type PlaceholderAction = AddAction | RemoveAction;

/** Элементы полос прогресса по идентификатору загрузки. */
const widgets = new Map<string, HTMLElement>();

/**
 * Разметка карточки повторяет FileCard: те же классы, те же стили. Собирается
 * обычным DOM, а не React — декорация живёт вне дерева React, и тащить сюда
 * отдельный корень рендеринга ради трёх элементов незачем.
 */
function buildWidget(id: string, name: string): HTMLElement {
  const root = document.createElement('div');
  root.className = 'kb-file kb-file--uploading';

  const badge = document.createElement('span');
  badge.className = 'kb-file__badge kb-file__badge--plain';
  badge.setAttribute('aria-hidden', 'true');
  badge.textContent = '…';

  const text = document.createElement('span');
  text.className = 'kb-file__text';
  const title = document.createElement('span');
  title.className = 'kb-file__name';
  title.textContent = name;
  const meta = document.createElement('span');
  meta.className = 'kb-file__meta';
  meta.textContent = 'Загрузка 0%';
  text.append(title, meta);

  const progress = document.createElement('span');
  progress.className = 'kb-file__progress';
  progress.setAttribute('role', 'progressbar');
  progress.setAttribute('aria-valuemin', '0');
  progress.setAttribute('aria-valuemax', '100');
  progress.setAttribute('aria-valuenow', '0');
  progress.setAttribute('aria-label', `Загрузка ${name}`);
  const bar = document.createElement('span');
  bar.className = 'kb-file__progress-bar';
  bar.style.width = '0%';
  progress.append(bar);

  root.append(badge, text, progress);
  widgets.set(id, root);
  return root;
}

export const UploadPlaceholderExtension = Extension.create({
  name: 'uploadPlaceholder',

  addProseMirrorPlugins() {
    return [
      new Plugin<DecorationSet>({
        key,
        state: {
          init: () => DecorationSet.empty,
          apply(tr, set) {
            // Сдвигаем существующие декорации вслед за правками документа —
            // ради этой строки всё и затевалось.
            let next = set.map(tr.mapping, tr.doc);
            const action = tr.getMeta(key) as PlaceholderAction | undefined;

            if (action?.add) {
              const { id, pos, name } = action.add;
              next = next.add(tr.doc, [
                Decoration.widget(pos, buildWidget(id, name), { id }),
              ]);
            } else if (action?.remove) {
              const { id } = action.remove;
              next = next.remove(
                next.find(undefined, undefined, (spec) => spec.id === id),
              );
              widgets.delete(id);
            }
            return next;
          },
        },
        props: {
          decorations: (state) => key.getState(state),
        },
      }),
    ];
  },
});

/** Занять место под файл в текущей позиции курсора. */
export function addUploadPlaceholder(editor: Editor, id: string, name: string): void {
  const { tr, selection } = editor.state;
  editor.view.dispatch(tr.setMeta(key, { add: { id, pos: selection.from, name } }));
}

/** Сообщить о ходе загрузки. Правит DOM напрямую: документ здесь ни при чём. */
export function updateUploadProgress(id: string, percent: number): void {
  const widget = widgets.get(id);
  if (!widget) return;
  const value = Math.max(0, Math.min(100, Math.round(percent)));
  const bar = widget.querySelector<HTMLElement>('.kb-file__progress-bar');
  const meta = widget.querySelector<HTMLElement>('.kb-file__meta');
  const progress = widget.querySelector<HTMLElement>('.kb-file__progress');
  if (bar) bar.style.width = `${value}%`;
  if (meta) meta.textContent = `Загрузка ${value}%`;
  if (progress) progress.setAttribute('aria-valuenow', String(value));
}

/**
 * Актуальная позиция места под файл или null, если его уже нет: документ могли
 * править, пока файл летел, и место могли просто удалить.
 */
export function findUploadPlaceholder(editor: Editor, id: string): number | null {
  const set = key.getState(editor.state);
  const found = set?.find(undefined, undefined, (spec) => spec.id === id);
  return found && found.length > 0 ? found[0].from : null;
}

export function removeUploadPlaceholder(editor: Editor, id: string): void {
  editor.view.dispatch(editor.state.tr.setMeta(key, { remove: { id } }));
}
