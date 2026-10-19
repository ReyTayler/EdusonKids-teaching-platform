import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { CODE_LANGUAGE_VALUES, loadHighlighter } from './codeLanguages';

/**
 * Блок кода при чтении: подсветка, подпись языка и кнопка копирования.
 *
 * Подсветка грузится по требованию и только если у блока задан язык. Пакет
 * highlight.js весит больше всей читалки, а блок кода встречается в одной
 * статье из десяти — тянуть его в основной бандл ради этого нельзя.
 *
 * Пока грамматика едет, показывается неподсвеченный текст: содержимое видно
 * сразу, подсветка добавляется следом.
 */
export function CodeBlockView({
  language,
  source,
  children,
}: {
  language: string;
  source: string;
  children: ReactNode;
}) {
  const highlighted = useHighlighted(language, source);

  return (
    <div className="kb-code-block">
      <div className="kb-code-block__bar" aria-hidden="true">
        <span className="kb-code-block__lang">{language || 'код'}</span>
        <CopyButton source={source} />
      </div>
      <pre className="kb-code">
        <code className={language ? `language-${language}` : undefined}>
          {highlighted ?? children}
        </code>
      </pre>
    </div>
  );
}

/** hast-узел lowlight — ровно та его часть, что нужна для отрисовки. */
interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: { className?: string[] };
  children?: HastNode[];
}

function useHighlighted(language: string, source: string): ReactNode | null {
  const [tree, setTree] = useState<ReactNode | null>(null);
  // Гонка: пока грузилась грамматика, компонент мог показать другой блок.
  const token = useRef(0);

  useEffect(() => {
    setTree(null);
    if (!language || !CODE_LANGUAGE_VALUES.includes(language)) return;
    const current = ++token.current;

    void loadHighlighter()
      .then((lowlight) => {
        if (current !== token.current) return;
        const result = lowlight.highlight(language, source) as unknown as HastNode;
        setTree(renderHast(result.children ?? [], 'c'));
      })
      .catch(() => {
        // Не смогли подсветить — текст всё равно виден, и это не повод
        // показывать читателю ошибку.
      });
  }, [language, source]);

  return tree;
}

function renderHast(nodes: HastNode[], keyPrefix: string): ReactNode {
  return nodes.map((node, index) => {
    const key = `${keyPrefix}-${index}`;
    if (node.type === 'text') return <span key={key}>{node.value}</span>;
    const className = node.properties?.className?.join(' ');
    return (
      <span key={key} className={className}>
        {renderHast(node.children ?? [], key)}
      </span>
    );
  });
}

function CopyButton({ source }: { source: string }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(timer);
  }, [copied]);

  return (
    <button
      type="button"
      className="kb-code-block__copy"
      // aria-hidden на всей панели: кнопка остаётся доступной, поэтому
      // возвращаем ей видимость для ассистивных технологий явно.
      aria-hidden={false}
      onClick={() => {
        void navigator.clipboard?.writeText(source).then(() => setCopied(true));
      }}
    >
      {copied ? 'Скопировано' : 'Копировать'}
    </button>
  );
}
