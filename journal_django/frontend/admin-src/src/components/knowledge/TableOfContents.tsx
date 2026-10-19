import { useEffect, useMemo, useState } from 'react';
import type { TocEntry } from './headingAnchors';

/**
 * Оглавление статьи.
 *
 * Строится из того же JSON, что и статья (см. headingAnchors.ts), а не из
 * готового DOM: пакет @tiptap/extension-table-of-contents остался платным, а
 * обход дерева заголовков — тридцать строк и ноль зависимостей. Заодно
 * читателю не приезжает ProseMirror.
 */
export function TableOfContents({ entries }: { entries: TocEntry[] }) {
  const active = useActiveHeading(entries);

  // На одном заголовке оглавление не помогает, а места занимает столько же.
  if (entries.length < 2) return null;

  return (
    <nav className="kb-toc" aria-label="На этой странице">
      <p className="kb-side__title">На этой странице</p>
      <ul className="kb-toc__list">
        {entries.map((entry) => (
          <li key={entry.id}>
            <a
              href={`#${entry.id}`}
              className={`kb-toc__link kb-toc__link--h${entry.level}${
                active === entry.id ? ' is-active' : ''
              }`}
              aria-current={active === entry.id ? 'location' : undefined}
              onClick={(event) => {
                const target = document.getElementById(entry.id);
                if (!target) return;
                // Своя прокрутка вместо перехода по якорю: адрес не должен
                // обрастать решётками — по такой ссылке документ потом
                // открывался бы прокрученным в середину.
                event.preventDefault();
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
              }}
            >
              {entry.text}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/**
 * Какой заголовок сейчас на экране.
 *
 * IntersectionObserver, а не пересчёт координат на каждый пиксель прокрутки:
 * браузер сам сообщает о пересечении, и на длинной статье обработчик не
 * выполняется сорок раз в секунду.
 */
function useActiveHeading(entries: TocEntry[]): string | null {
  const ids = useMemo(() => entries.map((e) => e.id).join('|'), [entries]);
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    const nodes = ids
      .split('|')
      .filter(Boolean)
      .map((id) => document.getElementById(id))
      .filter((node): node is HTMLElement => node !== null);
    if (nodes.length === 0) return;

    const observer = new IntersectionObserver(
      (records) => {
        const visible = records
          .filter((r) => r.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (visible?.target.id) setActive(visible.target.id);
      },
      // Рабочая зона — верхняя треть экрана: заголовок считается текущим, пока
      // не ушёл выше неё.
      { rootMargin: '-80px 0px -66% 0px', threshold: 0 },
    );
    nodes.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [ids]);

  return active;
}
