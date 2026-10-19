import { Node, mergeAttributes } from '@tiptap/core';
import { NodeViewWrapper, ReactNodeViewRenderer } from '@tiptap/react';
import type { NodeViewProps } from '@tiptap/react';
import { FileCard } from './FileCard';

/**
 * Узел прикреплённого файла.
 *
 * Атомарный и блочный, как картинка: щелчок выделяет его целиком, следующий
 * Delete удаляет именно файл, а ручка блока умеет его тащить.
 *
 * В документе хранится fileId плюс имя, размер и тип. Последние три
 * продублированы ради читалки: без них карточку нельзя было бы нарисовать, не
 * сходив на сервер за каждым файлом статьи. При СКАЧИВАНИИ они не участвуют —
 * имя отдаёт сервер из своей записи, поэтому правка JSON меняет только надпись.
 */

function numberOrNull(raw: string | null): number | null {
  const value = Number(raw);
  return raw && Number.isInteger(value) && value > 0 ? value : null;
}

function FileNodeView({ node, selected }: NodeViewProps) {
  const attrs = node.attrs as { fileId: number | null; name: string; size: number; mime: string };

  // Узел появляется в документе только после того, как файл принят сервером:
  // место на время загрузки держит декорация (uploadPlaceholder.ts), а не он.
  // Поэтому промежуточного состояния здесь нет.
  return (
    <NodeViewWrapper
      className={`kb-file-block${selected ? ' is-selected' : ''}`}
      data-drag-handle
    >
      <FileCard
        fileId={attrs.fileId ?? 0}
        name={attrs.name || 'Файл'}
        size={attrs.size || 0}
        mime={attrs.mime || ''}
        // В режиме правки карточка не ссылка: щелчок обязан выделять блок,
        // иначе файл нельзя ни удалить, ни перетащить.
        downloadable={false}
      />
    </NodeViewWrapper>
  );
}

export const KnowledgeFileExtension = Node.create({
  name: 'knowledgeFile',
  group: 'block',
  atom: true,
  draggable: true,

  addAttributes() {
    return {
      fileId: {
        default: null,
        parseHTML: (element) => numberOrNull(element.getAttribute('data-file-id')),
        renderHTML: (attributes) => ({ 'data-file-id': attributes.fileId }),
      },
      name: {
        default: '',
        parseHTML: (element) => element.getAttribute('data-name') ?? '',
        renderHTML: (attributes) => ({ 'data-name': attributes.name }),
      },
      size: {
        default: 0,
        parseHTML: (element) => Number(element.getAttribute('data-size')) || 0,
        renderHTML: (attributes) => ({ 'data-size': attributes.size }),
      },
      mime: {
        default: '',
        parseHTML: (element) => element.getAttribute('data-mime') ?? '',
        renderHTML: (attributes) => ({ 'data-mime': attributes.mime }),
      },
    };
  },

  parseHTML() {
    // Только наша собственная разметка. Вставка из Word или Google Docs не
    // может подсунуть сюда чужой файл: у неё нет data-file-id.
    return [{ tag: 'div[data-file-id]' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes)];
  },

  addNodeView() {
    return ReactNodeViewRenderer(FileNodeView);
  },
});
