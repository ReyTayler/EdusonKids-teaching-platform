import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { DocumentView } from '@shared/components/knowledge/DocumentView';
import { TableOfContents } from '@shared/components/knowledge/TableOfContents';
import { collectHeadings } from '@shared/components/knowledge/headingAnchors';
import { DocumentSide } from '@shared/components/knowledge/DocumentSide';
import { StarIcon } from '@shared/components/knowledge/knowledgeIcons';
import { IconButton } from '@shared/components/ui/IconButton';
import {
  useKnowledgeDocument,
  useKnowledgeMutations,
  useKnowledgeSections,
} from '@shared/hooks/useKnowledge';
import { fmtDateTimeShort } from '@shared/lib/format';
import { KB_BASE } from './KnowledgeLibraryPage';
import { PageHead } from '../../components/ui/PageHead';

/**
 * Документ Wiki глазами преподавателя — чтение и только чтение.
 *
 * Редактор (DocumentEditor) сюда не подключён вовсе, и это не ограничение
 * интерфейса, а экономия: он тянет ProseMirror и весь TipTap, а
 * преподавателю нужен рендер готового JSON в React — им занимается общий
 * DocumentView, читателю ядро редактора не приезжает.
 *
 * Недоступный документ сервер отдаёт как 404 (а не 403): по ответу нельзя
 * узнать, существует ли документ с таким номером. Экран показывает то же
 * самое — «нет доступа или удалён», без подробностей.
 */
export default function KnowledgeDocumentPage() {
  const { id } = useParams();
  const documentId = Number(id);
  const { data, isLoading, isError } = useKnowledgeDocument(
    Number.isInteger(documentId) && documentId > 0 ? documentId : undefined,
  );
  const sections = useKnowledgeSections();
  const { setFavorite } = useKnowledgeMutations();

  const headings = useMemo(() => collectHeadings(data?.content), [data?.content]);

  if (isLoading) return <div className="cal-skel" />;
  if (isError || !data) {
    return (
      <div className="kb-teacher">
        <PageHead title="Документ не найден" />
        <div className="cal-empty">
          Документ удалён или у вас нет к нему доступа.{' '}
          <Link to={KB_BASE}>Вернуться к списку</Link>
        </div>
      </div>
    );
  }

  const sectionTitle = sections.data?.sections.find((s) => s.id === data.section_id)?.title ?? '';

  return (
    <div className="kb-teacher">
      <PageHead
        lead={
          <div className="kb-teacher__crumbs">
            <Link to={KB_BASE}>Wiki</Link>
            {sectionTitle && (
              <>
                <span aria-hidden="true">›</span>
                <Link to={`${KB_BASE}?section=${data.section_id}`}>{sectionTitle}</Link>
              </>
            )}
          </div>
        }
        title={data.title}
      >
        <IconButton
          label={data.is_favorite ? 'Убрать из избранного' : 'В избранное'}
          className={`kb-star${data.is_favorite ? ' is-on' : ''}`}
          active={data.is_favorite}
          icon={<StarIcon size={18} filled={data.is_favorite} />}
          onClick={() => setFavorite.mutate({ id: documentId, value: !data.is_favorite })}
        />
      </PageHead>

      <div className="kb-doc-meta">
        {data.author_name && <span className="kb-doc-meta__item">{data.author_name}</span>}
        <span className="kb-doc-meta__item">Обновлён {fmtDateTimeShort(data.updated_at)}</span>
      </div>

      <div className="kb-reader">
        <div className="kb-doc-paper">
          <DocumentView content={data.content} anchors={headings.anchors} />
        </div>
        <DocumentSide>
          <TableOfContents entries={headings.entries} />
        </DocumentSide>
      </div>
    </div>
  );
}
