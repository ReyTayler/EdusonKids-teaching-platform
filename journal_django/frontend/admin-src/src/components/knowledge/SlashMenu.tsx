import { useEffect, useImperativeHandle, useState, forwardRef } from 'react';
import { Extension } from '@tiptap/core';
import Suggestion from '@tiptap/suggestion';
import { ReactRenderer } from '@tiptap/react';
import type { Editor, Range } from '@tiptap/core';
import { CALLOUT_LABELS, CALLOUT_TONES } from './calloutMeta';

/**
 * Меню по «/» — быстрый способ вставить блок, не уходя к панели инструментов.
 *
 * Построено на @tiptap/suggestion: это тот же механизм, что у упоминаний, и он
 * сам следит за состоянием набора («/» в середине слова меню не открывает,
 * Esc закрывает, стрелки водят по списку).
 *
 * Всплывающее окно позиционируется по координатам курсора вручную, без
 * floating-ui: точка привязки — не элемент, а место в тексте, и библиотеке
 * пришлось бы подсовывать виртуальный элемент. Тридцать строк против ещё одной
 * зависимости в ленивом чанке.
 */

interface SlashItem {
  title: string;
  hint: string;
  keywords: string;
  run: (editor: Editor, range: Range) => void;
}

const ITEMS: SlashItem[] = [
  {
    title: 'Текст',
    hint: 'Обычный абзац',
    keywords: 'текст абзац parag',
    run: (editor, range) => editor.chain().focus().deleteRange(range).setParagraph().run(),
  },
  {
    title: 'Крупный заголовок',
    hint: 'Верхний уровень',
    keywords: 'заголовок h1 крупный раздел',
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).setHeading({ level: 1 }).run(),
  },
  {
    title: 'Заголовок',
    hint: 'Раздел статьи',
    keywords: 'заголовок h2 раздел',
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).setHeading({ level: 2 }).run(),
  },
  {
    title: 'Подзаголовок',
    hint: 'Уровень ниже',
    keywords: 'подзаголовок h3',
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).setHeading({ level: 3 }).run(),
  },
  {
    title: 'Список',
    hint: 'Маркированный',
    keywords: 'список маркер ul',
    run: (editor, range) => editor.chain().focus().deleteRange(range).toggleBulletList().run(),
  },
  {
    title: 'Нумерованный список',
    hint: 'По порядку',
    keywords: 'нумерованный список ol',
    run: (editor, range) => editor.chain().focus().deleteRange(range).toggleOrderedList().run(),
  },
  {
    title: 'Чеклист',
    hint: 'Пункты с галочками',
    keywords: 'чеклист задачи todo галочки',
    run: (editor, range) => editor.chain().focus().deleteRange(range).toggleTaskList().run(),
  },
  {
    title: 'Цитата',
    hint: 'Выделенный фрагмент',
    keywords: 'цитата quote',
    run: (editor, range) => editor.chain().focus().deleteRange(range).toggleBlockquote().run(),
  },
  ...CALLOUT_TONES.map((tone) => ({
    title: `Выноска: ${CALLOUT_LABELS[tone].toLowerCase()}`,
    hint: 'Блок «обратите внимание»',
    keywords: `выноска callout ${CALLOUT_LABELS[tone].toLowerCase()}`,
    run: (editor: Editor, range: Range) =>
      editor.chain().focus().deleteRange(range).setCallout(tone).run(),
  })),
  {
    title: 'Таблица',
    hint: '3 × 3 с шапкой',
    keywords: 'таблица table',
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range)
        .insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(),
  },
  {
    title: 'Блок кода',
    hint: 'Моноширинный, с подсветкой',
    keywords: 'код code блок',
    run: (editor, range) => editor.chain().focus().deleteRange(range).toggleCodeBlock().run(),
  },
  {
    title: 'Разделитель',
    hint: 'Горизонтальная линия',
    keywords: 'разделитель линия hr',
    run: (editor, range) => editor.chain().focus().deleteRange(range).setHorizontalRule().run(),
  },
];

/** Картинку и файл вставляет не команда, а загрузка — колбэки даёт страница. */
export interface SlashMenuOptions {
  onPickImage: () => void;
  onPickFile: () => void;
}

interface MenuProps {
  items: SlashItem[];
  command: (item: SlashItem) => void;
}

export interface MenuHandle {
  onKeyDown: (event: KeyboardEvent) => boolean;
}

const SlashMenuList = forwardRef<MenuHandle, MenuProps>(function SlashMenuList(
  { items, command },
  ref,
) {
  const [selected, setSelected] = useState(0);

  useEffect(() => setSelected(0), [items]);

  useImperativeHandle(ref, () => ({
    onKeyDown: (event: KeyboardEvent) => {
      if (event.key === 'ArrowDown') {
        setSelected((current) => (current + 1) % items.length);
        return true;
      }
      if (event.key === 'ArrowUp') {
        setSelected((current) => (current - 1 + items.length) % items.length);
        return true;
      }
      if (event.key === 'Enter') {
        const item = items[selected];
        if (item) command(item);
        return true;
      }
      return false;
    },
  }), [items, selected, command]);

  if (items.length === 0) {
    return <div className="kb-slash"><p className="kb-slash__empty">Ничего не нашлось</p></div>;
  }

  return (
    <div className="kb-slash" role="listbox" aria-label="Вставить блок">
      {items.map((item, index) => (
        <button
          key={item.title}
          type="button"
          role="option"
          aria-selected={index === selected}
          className={`kb-slash__item${index === selected ? ' is-active' : ''}`}
          // mousedown, а не click: click срабатывает после потери фокуса
          // редактором, и диапазон вставки к тому моменту уже сбит.
          onMouseDown={(event) => { event.preventDefault(); command(item); }}
          onMouseEnter={() => setSelected(index)}
        >
          <span className="kb-slash__title">{item.title}</span>
          <span className="kb-slash__hint">{item.hint}</span>
        </button>
      ))}
    </div>
  );
});

export function createSlashMenu(options: SlashMenuOptions) {
  const items: SlashItem[] = [
    ...ITEMS,
    {
      title: 'Изображение',
      hint: 'Загрузить файл',
      keywords: 'изображение картинка фото image',
      run: (editor, range) => {
        editor.chain().focus().deleteRange(range).run();
        options.onPickImage();
      },
    },
    {
      title: 'Файл',
      hint: 'Документ, таблица, архив',
      keywords: 'файл вложение документ pdf архив скачать',
      run: (editor, range) => {
        // «/» убираем сразу, до открытия диалога: пока пользователь выбирает
        // файл, символ иначе висел бы в тексте, а диапазон вставки успел бы
        // сбиться.
        editor.chain().focus().deleteRange(range).run();
        options.onPickFile();
      },
    },
  ];

  return Extension.create({
    name: 'slashMenu',

    addProseMirrorPlugins() {
      return [
        Suggestion<SlashItem>({
          editor: this.editor,
          char: '/',
          // Меню открывается только в начале блока: «/» внутри предложения —
          // это дробь в тексте, а не команда.
          startOfLine: true,
          items: ({ query }) => {
            const needle = query.trim().toLowerCase();
            if (!needle) return items;
            return items.filter(
              (item) =>
                item.title.toLowerCase().includes(needle) ||
                item.keywords.includes(needle),
            );
          },
          command: ({ editor, range, props }) => props.run(editor, range),
          render: () => {
            let renderer: ReactRenderer<MenuHandle, MenuProps> | null = null;
            let element: HTMLElement | null = null;

            const place = (rect: DOMRect | null) => {
              if (!element || !rect) return;
              // Меню под курсором, но не за нижним краем экрана: у длинного
              // списка иначе не видно последних пунктов.
              const height = element.offsetHeight;
              const below = window.innerHeight - rect.bottom;
              const top = below < height + 16 ? rect.top - height - 8 : rect.bottom + 8;
              element.style.top = `${Math.max(8, top)}px`;
              element.style.left = `${rect.left}px`;
            };

            return {
              onStart: (props) => {
                renderer = new ReactRenderer(SlashMenuList, {
                  props: { items: props.items, command: (item) => props.command(item) },
                  editor: props.editor,
                });
                element = renderer.element as HTMLElement;
                // Позиция задаётся из JS (точка привязки — курсор в тексте, а
                // не элемент), слой — из CSS вместе с остальным оформлением.
                element.style.position = 'fixed';
                document.body.appendChild(element);
                place(props.clientRect?.() ?? null);
              },
              onUpdate: (props) => {
                renderer?.updateProps({
                  items: props.items,
                  command: (item) => props.command(item),
                });
                place(props.clientRect?.() ?? null);
              },
              onKeyDown: (props) => {
                if (props.event.key === 'Escape') return false;
                return renderer?.ref?.onKeyDown(props.event) ?? false;
              },
              onExit: () => {
                element?.remove();
                renderer?.destroy();
                renderer = null;
                element = null;
              },
            };
          },
        }),
      ];
    },
  });
}
