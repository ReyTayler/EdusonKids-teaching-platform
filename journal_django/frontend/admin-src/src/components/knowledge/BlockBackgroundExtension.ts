import { Extension } from '@tiptap/core';
import { KNOWN_HIGHLIGHTS } from './editorColors';

/**
 * Фон блока — заливка целого абзаца или заголовка.
 *
 * Отличается от выделения текста охватом: выделение красит слова внутри строки,
 * фон блока — весь блок целиком, вместе с полями. Оттенки те же самые
 * (editorColors.ts), потому что и требование к ним одно: подложка должна
 * оставаться читаемой под любым цветом текста и в любой теме.
 *
 * Это НЕ замена выноске. У выноски есть подпись («Важно», «Совет») и значок,
 * то есть она сообщает читателю смысл словами. Фон блока — тихое выделение без
 * подписи: для абзаца, который надо просто отделить от соседних.
 *
 * Список типов совпадает с TextAlign (абзац и заголовок) намеренно: это те же
 * блоки, к которым применяется оформление целиком. Списку или таблице заливка
 * досталась бы неравномерно — по пунктам или по ячейкам.
 */

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    blockBackground: {
      setBlockBackground: (value: string) => ReturnType;
      unsetBlockBackground: () => ReturnType;
    };
  }
}

export const BlockBackgroundExtension = Extension.create({
  name: 'blockBackground',

  addOptions() {
    return { types: ['paragraph', 'heading'] };
  },

  addGlobalAttributes() {
    return [
      {
        types: this.options.types,
        attributes: {
          blockBackground: {
            default: null,
            // Из вставленного HTML принимаем только своё значение из палитры.
            // Чужой `background: yellow` иначе дожил бы до сохранения и сделал
            // документ несохраняемым — та же ловушка, что со шрифтами.
            parseHTML: (element: HTMLElement) => {
              const value = element.getAttribute('data-block-bg');
              return value && KNOWN_HIGHLIGHTS.has(value) ? value : null;
            },
            renderHTML: (attributes: Record<string, unknown>) => {
              const value = attributes.blockBackground;
              if (typeof value !== 'string' || !value) return {};
              // Значение дублируется в data-атрибут: из него оно и читается
              // обратно при разборе разметки, а из inline-стиля пришлось бы
              // выковыривать его разбором CSS.
              return { 'data-block-bg': value, style: `background-color: ${value}` };
            },
          },
        },
      },
    ];
  },

  addCommands() {
    return {
      setBlockBackground: (value: string) => ({ commands }) =>
        this.options.types.every(
          (type: string) => commands.updateAttributes(type, { blockBackground: value }),
        ),
      unsetBlockBackground: () => ({ commands }) =>
        this.options.types.every(
          (type: string) => commands.resetAttributes(type, 'blockBackground'),
        ),
    };
  },
});
