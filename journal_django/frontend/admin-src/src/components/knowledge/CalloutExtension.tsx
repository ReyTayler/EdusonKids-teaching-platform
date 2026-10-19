import { Node, mergeAttributes } from '@tiptap/core';
import { NodeViewContent, NodeViewWrapper, ReactNodeViewRenderer } from '@tiptap/react';
import type { NodeViewProps } from '@tiptap/react';
import {
  CALLOUT_LABELS,
  CALLOUT_TONES,
  DEFAULT_CALLOUT_TONE,
  isCalloutTone,
} from './calloutMeta';

/**
 * Выноска — блок «обратите внимание» с видом (совет, важно, ошибка…).
 *
 * Своя, а не @tiptap/extension-details: та про сворачиваемый блок, здесь нужен
 * всегда раскрытый абзац с тоном. Кода на сорок строк, зависимость экономим.
 *
 * NodeView на React нужен ради подписи вида: она обязана выглядеть в редакторе
 * ровно так же, как при чтении, и брать текст из общего словаря
 * (calloutMeta.ts). Нарисуй её CSS-ом через ::before — подпись пришлось бы
 * держать в двух местах, а переключатель вида делать было бы негде.
 */
declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    callout: {
      setCallout: (tone?: string) => ReturnType;
      toggleCallout: (tone?: string) => ReturnType;
    };
  }
}

export const CalloutExtension = Node.create({
  name: 'callout',
  group: 'block',
  // Внутри — обычные блоки: в выноску кладут абзац, список, иногда картинку.
  content: 'block+',
  defining: true,

  addAttributes() {
    return {
      tone: {
        default: DEFAULT_CALLOUT_TONE,
        parseHTML: (element) => {
          const raw = element.getAttribute('data-tone');
          return isCalloutTone(raw) ? raw : DEFAULT_CALLOUT_TONE;
        },
        renderHTML: (attributes) => ({ 'data-tone': attributes.tone }),
      },
    };
  },

  parseHTML() {
    return [{ tag: 'aside[data-tone]' }];
  },

  renderHTML({ HTMLAttributes, node }) {
    const tone = isCalloutTone(node.attrs.tone) ? node.attrs.tone : DEFAULT_CALLOUT_TONE;
    return ['aside', mergeAttributes(HTMLAttributes, {
      class: `kb-callout kb-callout--${tone}`,
    }), 0];
  },

  addNodeView() {
    return ReactNodeViewRenderer(CalloutView);
  },

  addCommands() {
    return {
      setCallout: (tone = DEFAULT_CALLOUT_TONE) => ({ commands }) =>
        commands.wrapIn(this.name, { tone }),
      toggleCallout: (tone = DEFAULT_CALLOUT_TONE) => ({ commands }) =>
        commands.toggleWrap(this.name, { tone }),
    };
  },
});

function CalloutView({ node, updateAttributes, editor }: NodeViewProps) {
  const tone = isCalloutTone(node.attrs.tone) ? node.attrs.tone : DEFAULT_CALLOUT_TONE;

  return (
    <NodeViewWrapper className={`kb-callout kb-callout--${tone}`} data-tone={tone}>
      {/* contentEditable={false} обязателен: иначе курсор заходит в подпись и
          её начинают править как текст документа, а в JSON она не попадает. */}
      <div className="kb-callout__head" contentEditable={false}>
        <span className="kb-callout__label">{CALLOUT_LABELS[tone]}</span>
        {editor.isEditable && (
          <span className="kb-callout__tones">
            {CALLOUT_TONES.map((option) => (
              <button
                key={option}
                type="button"
                className={`kb-callout__tone kb-callout__tone--${option}${
                  option === tone ? ' is-active' : ''
                }`}
                aria-label={CALLOUT_LABELS[option]}
                aria-pressed={option === tone}
                onClick={() => updateAttributes({ tone: option })}
              />
            ))}
          </span>
        )}
      </div>
      <NodeViewContent className="kb-callout__body" />
    </NodeViewWrapper>
  );
}
