import { Link } from 'react-router-dom';
import { ActionMenu } from '../ui/ActionMenu';
import { DocumentStatusBadge } from '../ui/StatusBadge';
import { DocIcon } from './knowledgeIcons';
import { fmtDateTimeShort } from '../../lib/format';
import type { KnowledgeDocumentRow, KnowledgeSection } from '../../lib/knowledge';

/** One document list, shared by administrators and readers. */
export interface DocumentActions {
  canWrite: boolean;
  /**
   * Действия правки — необязательные: у читателя (teacher SPA) их нет вовсе,
   * и требовать от него пустые заглушки означало бы делать вид, будто правка
   * там возможна. Меню с ними всё равно строится только при canWrite.
   */
  onEdit?: (id: number) => void;
  onTogglePublish?: (doc: KnowledgeDocumentRow) => void;
  onDuplicate?: (doc: KnowledgeDocumentRow) => void;
  onDelete?: (doc: KnowledgeDocumentRow) => void;
}

/**
 * Корень адресов документа. У админки это /admin/knowledge, у преподавателя —
 * /knowledge: два SPA, два роутера, одни и те же карточки.
 */
const DEFAULT_BASE_PATH = '/admin/knowledge';

function menuItems(doc: KnowledgeDocumentRow, actions: DocumentActions) {
  if (!actions.canWrite) return [];
  return [
    { label: 'Редактировать', onSelect: () => actions.onEdit?.(doc.id) },
    {
      label: doc.status === 'draft' ? 'Опубликовать' : 'Снять с публикации',
      onSelect: () => actions.onTogglePublish?.(doc),
    },
    { label: 'Дублировать', onSelect: () => actions.onDuplicate?.(doc) },
    { label: 'Удалить', danger: true, onSelect: () => actions.onDelete?.(doc) },
  ];
}

export function DocumentTable({
  rows,
  actions,
  showSection,
  sections,
  basePath = DEFAULT_BASE_PATH,
}: {
  rows: KnowledgeDocumentRow[];
  actions: DocumentActions;
  showSection: boolean;
  sections: KnowledgeSection[];
  basePath?: string;
}) {
  const sectionTitle = (id: number) => sections.find((s) => s.id === id)?.title ?? '—';

  return (
    <div
      className={`kb-rows${showSection ? ' kb-rows--with-section' : ''}`}
      role="table"
      aria-label="Документы"
    >
      <div className="kb-rows__head" role="row">
        <span role="columnheader">Название</span>
        {showSection && <span role="columnheader" className="kb-rows__optional">Папка</span>}
        <span role="columnheader">Изменён</span>
        <span role="columnheader" className="kb-rows__spacer" aria-label="Действия" />
      </div>
      {rows.map((doc) => (
        <DocumentRow
          key={doc.id}
          doc={doc}
          actions={actions}
          basePath={basePath}
          sectionName={showSection ? sectionTitle(doc.section_id) : null}
        />
      ))}
    </div>
  );
}

function DocumentRow({
  doc,
  actions,
  basePath,
  sectionName,
}: {
  doc: KnowledgeDocumentRow;
  actions: DocumentActions;
  basePath: string;
  sectionName: string | null;
}) {
  const items = menuItems(doc, actions);

  return (
    <div className="kb-rows__row" role="row">
      <span className="kb-rows__name" role="cell">
        <span className="kb-rows__icon" aria-hidden="true"><DocIcon size={20} /></span>
        <span className="kb-rows__text">
          <Link to={`${basePath}/${doc.id}`} className="kb-rows__link">{doc.title}</Link>
          {/* Фрагмент текста — вторая строка: по названию «Регламент №4»
              непонятно, о чём документ, а по первой фразе обычно понятно. */}
          <span className="kb-rows__excerpt">{doc.excerpt?.trim() || 'Пустой документ'}</span>
        </span>
        {doc.status === 'draft' && <DocumentStatusBadge status="draft" />}
      </span>
      {sectionName !== null && (
        <span className="kb-rows__muted kb-rows__optional" role="cell">{sectionName}</span>
      )}
      <span className="kb-rows__muted" role="cell">{fmtDateTimeShort(doc.updated_at)}</span>
      <span className="kb-rows__actions" role="cell">
        {items.length > 0 && <ActionMenu items={items} label={`Действия: ${doc.title}`} />}
      </span>
    </div>
  );
}
