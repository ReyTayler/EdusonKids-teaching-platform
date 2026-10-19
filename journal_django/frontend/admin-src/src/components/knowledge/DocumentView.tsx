import { renderJSONContentToReactElement } from '@tiptap/static-renderer/json/react';
import type { TipTapDoc, TipTapNode } from '../../lib/knowledge';
import { markMapping, nodeMapping, unhandledMark, unhandledNode } from './documentRenderMap';
import { HeadingAnchorProvider } from './headingAnchors';
import { ImageLightboxProvider } from './ImageLightbox';

/**
 * Рендер документа для чтения.
 *
 * Ключевой момент: JSON превращается в React-элементы, а не в HTML-строку.
 * Поэтому здесь нет dangerouslySetInnerHTML и не нужен санитайзер — вставить
 * исполняемую разметку через контент документа физически нечем.
 *
 * Импортируется JSON-рендерер (`@tiptap/static-renderer/json/react`), а не
 * `renderToReactElement`/`StarterKit`/`@tiptap/extension-table` из основного
 * пакета: те тянут `@tiptap/core` и ядро ProseMirror в основной бандл, хотя
 * DocumentView подключён статически и грузится на КАЖДОЙ странице админки.
 * Таблица соответствий узлов/марок — documentRenderMap.tsx, синхронизирована
 * с белым списком бэкенда (apps/knowledge/content.py). Полноценные
 * расширения (StarterKit, Table, KnowledgeImageExtension) остаются только в
 * DocumentEditor.tsx — он и должен тянуть ProseMirror, но в своём ленивом
 * чанке, а не здесь.
 */
export function DocumentView({
  content,
  anchors,
}: {
  content: TipTapDoc;
  /** Якоря заголовков для оглавления; без них статья рендерится без id. */
  anchors?: WeakMap<TipTapNode, string>;
}) {
  // Рендерер намеренно собирается на каждый рендер и не запоминается ни
  // модульной константой, ни useMemo. Внутри renderJSONContentToReactElement
  // живёт счётчик, из которого берутся React-ключи; он не сбрасывается между
  // вызовами. Переживи рендерер перерисовку — второй проход выдал бы всем
  // узлам новые ключи, React снёс бы и пересоздал всё дерево статьи: картинки
  // моргают, позиция прокрутки внутри таблиц теряется. Свежий рендерер
  // означает одни и те же ключи при одном и том же документе.
  const renderDocument = renderJSONContentToReactElement({
    nodeMapping,
    markMapping,
    unhandledNode,
    unhandledMark,
  });

  return (
    <HeadingAnchorProvider value={anchors ?? null}>
      <ImageLightboxProvider>
        <article className="kb-doc">{renderDocument({ content })}</article>
      </ImageLightboxProvider>
    </HeadingAnchorProvider>
  );
}
