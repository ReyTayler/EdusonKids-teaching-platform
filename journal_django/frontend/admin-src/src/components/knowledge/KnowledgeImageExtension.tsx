import { Node, mergeAttributes } from '@tiptap/core';
import { ReactNodeViewRenderer } from '@tiptap/react';
import { imageUrl } from '../../lib/knowledge';
import { ImageNodeView } from './ImageNodeView';

/**
 * Узел картинки базы знаний.
 *
 * В документе хранится только imageId — не URL. Благодаря этому смена схемы
 * раздачи файлов не потребует переписывать содержимое документов: src
 * собирается при рендере.
 */
function numberOrNull(raw: string | null): number | null {
  const value = Number(raw);
  return raw && Number.isInteger(value) && value > 0 ? value : null;
}

export const KnowledgeImageExtension = Node.create({
  name: 'knowledgeImage',
  group: 'block',
  atom: true,
  draggable: true,

  addAttributes() {
    return {
      imageId: {
        default: null,
        parseHTML: (element) => {
          const raw = element.getAttribute('data-image-id');
          return raw ? Number(raw) : null;
        },
        renderHTML: (attributes) => ({ 'data-image-id': attributes.imageId }),
      },
      alt: { default: '' },
      // Размеры картинки в документе. Изначально это размеры оригинала (их
      // отдаёт сервер при загрузке), дальше их меняет автор, потянув за край
      // (ImageNodeView). Пара обязана оставаться согласованной по соотношению
      // сторон: из неё браузер узнаёт размер ДО загрузки файла и резервирует
      // место, иначе текст под картинкой прыгает.
      width: {
        default: null,
        parseHTML: (element) => numberOrNull(element.getAttribute('width')),
        renderHTML: (attributes) => (attributes.width ? { width: attributes.width } : {}),
      },
      height: {
        default: null,
        parseHTML: (element) => numberOrNull(element.getAttribute('height')),
        renderHTML: (attributes) => (attributes.height ? { height: attributes.height } : {}),
      },
    };
  },

  parseHTML() {
    return [{ tag: 'img[data-image-id]' }];
  },

  addNodeView() {
    // Ручки изменения размера рисует React-представление. Схема узла при этом
    // не меняется: и читалка, и вставка из разметки работают через renderHTML
    // и parseHTML ниже, как раньше.
    return ReactNodeViewRenderer(ImageNodeView);
  },

  renderHTML({ HTMLAttributes, node }) {
    const id = node.attrs.imageId as number | null;
    return [
      'img',
      mergeAttributes(HTMLAttributes, {
        src: id ? imageUrl(id) : '',
        loading: 'lazy',
      }),
    ];
  },
});
