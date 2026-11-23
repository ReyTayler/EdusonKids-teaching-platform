import { ActionMenu } from '../ui/ActionMenu';
import { SelectInput } from '../form/SelectInput';
import { FolderAllIcon, FolderIcon } from './knowledgeIcons';
import type { KnowledgeSection, LibraryScope } from '../../lib/knowledge';

export interface LibrarySelection { scope: LibraryScope; sectionId: number | null }

/** Folder navigation occupies one horizontal band instead of a second sidebar. */
export function LibraryNav({ selection, onSelect, sections, totalCount, canWrite,
  onRenameSection, onDeleteSection,
}: {
  selection: LibrarySelection;
  onSelect: (next: LibrarySelection) => void;
  sections: KnowledgeSection[];
  totalCount: number;
  canWrite: boolean;
  onRenameSection?: (section: KnowledgeSection) => void;
  onDeleteSection?: (section: KnowledgeSection) => void;
}) {
  const selected = sections.find((s) => s.id === selection.sectionId);
  return <div className="kb-folders">
    <div className="kb-folders__mobile">
      <FolderIcon size={18} />
      <SelectInput aria-label="Папка Wiki" value={selection.sectionId === null ? '' : String(selection.sectionId)}
        options={[
          { value: '', label: `Все документы · ${totalCount}` },
          ...sections.map((section) => ({ value: String(section.id), label: `${section.title} · ${section.document_count}` })),
        ]}
        onChange={(e) => onSelect({ scope: 'all', sectionId: e.target.value ? Number(e.target.value) : null })} />
    </div>
    <nav className="kb-folders__nav" aria-label="Папки Wiki">
      <button type="button" className={`kb-folder${selection.sectionId === null ? ' is-active' : ''}`}
        aria-current={selection.sectionId === null ? 'page' : undefined}
        onClick={() => onSelect({ scope: 'all', sectionId: null })}>
        <FolderAllIcon size={18} /><span>Все документы</span><span className="kb-folder__count">{totalCount}</span>
      </button>
      {sections.map((section) => <button type="button" key={section.id}
        className={`kb-folder${selection.sectionId === section.id ? ' is-active' : ''}`}
        aria-current={selection.sectionId === section.id ? 'page' : undefined}
        onClick={() => onSelect({ scope: 'all', sectionId: section.id })}>
        <FolderIcon size={18} /><span>{section.title}</span><span className="kb-folder__count">{section.document_count}</span>
      </button>)}
    </nav>
    {canWrite && selected && <ActionMenu label={`Действия с папкой «${selected.title}»`} items={[
      ...(onRenameSection ? [{ label: 'Переименовать папку', onSelect: () => onRenameSection(selected) }] : []),
      ...(onDeleteSection ? [{ label: 'Удалить папку', danger: true, onSelect: () => onDeleteSection(selected) }] : []),
    ]} />}
  </div>;
}
