import { createContext, useContext } from 'react';
import type { TipTapDoc, TipTapNode } from '../../lib/knowledge';

/**
 * Якоря заголовков статьи — общий источник для оглавления и для самой статьи.
 *
 * Оглавление и заголовок обязаны сойтись на одном и том же id, иначе ссылка
 * ведёт в никуда. Поэтому id не вычисляется дважды: он считается один раз при
 * обходе JSON и складывается в WeakMap по ссылке на узел. Рендер статьи
 * получает те же объекты узлов, что и обход, — совпадение гарантировано
 * тождеством ссылок, а не совпадением порядка вызовов.
 *
 * У блока может быть свой blockId (UniqueID редактора) — тогда берём его: он
 * переживает вставку заголовка в середину документа. Пока id нет (документы,
 * сохранённые раньше), падаем на порядковый номер.
 */
export interface TocEntry {
  id: string;
  level: number;
  text: string;
}

export interface HeadingAnchors {
  entries: TocEntry[];
  /** Якорь конкретного узла заголовка или undefined, если это не заголовок. */
  anchors: WeakMap<TipTapNode, string>;
}

export function collectHeadings(doc: TipTapDoc | undefined): HeadingAnchors {
  const entries: TocEntry[] = [];
  const anchors = new WeakMap<TipTapNode, string>();
  let index = 0;

  const walk = (node: TipTapNode) => {
    if (node.type === 'heading') {
      const blockId = node.attrs?.blockId;
      const id = typeof blockId === 'string' && blockId ? blockId : `h-${index}`;
      anchors.set(node, id);
      const text = inlineText(node);
      if (text) entries.push({ id, level: Number(node.attrs?.level) || 1, text });
      index += 1;
    }
    for (const child of node.content ?? []) walk(child);
  };

  for (const child of doc?.content ?? []) walk(child);
  return { entries, anchors };
}

function inlineText(node: TipTapNode): string {
  return (node.content ?? [])
    .map((child) => (child.type === 'text' ? child.text ?? '' : inlineText(child)))
    .join('')
    .trim();
}

const AnchorContext = createContext<WeakMap<TipTapNode, string> | null>(null);

export const HeadingAnchorProvider = AnchorContext.Provider;

/** Якорь заголовка при рендере статьи. null — статья без оглавления. */
export function useHeadingAnchor(node: TipTapNode): string | undefined {
  return useContext(AnchorContext)?.get(node);
}
