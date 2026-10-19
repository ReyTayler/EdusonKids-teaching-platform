import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { PageHeader } from '../../components/shell/PageHeader';
import { PageLoading } from '../../components/ui/Skeleton';
import { Button } from '../../components/ui/Button';
import { IconButton } from '../../components/ui/IconButton';
import { ActionMenu } from '../../components/ui/ActionMenu';
import { DocumentStatusBadge } from '../../components/ui/StatusBadge';
import { DocumentView } from '../../components/knowledge/DocumentView';
import { TableOfContents } from '../../components/knowledge/TableOfContents';
import { collectHeadings } from '../../components/knowledge/headingAnchors';
import { DocumentSide } from '../../components/knowledge/DocumentSide';
import { PencilIcon, StarIcon } from '../../components/knowledge/knowledgeIcons';
import { ReaderRolesField } from '../../components/knowledge/ReaderRolesField';
import { ConfirmDialog } from './KnowledgeDialogs';
import { AccessDialog } from './AccessDialog';
import { DocumentPropertiesDialog } from './DocumentPropertiesDialog';
import { useAuth } from '../../hooks/useAuth';
import { useApiError } from '../../hooks/useApiError';
import { useAutosave, type SaveState } from '../../hooks/useAutosave';
import {
  useDocumentMutations,
  useKnowledgeDocument,
  useKnowledgeMutations,
  useKnowledgeSections,
} from '../../hooks/useKnowledge';
import { EMPTY_DOC } from '../../lib/knowledge';
import { fmtDateTimeShort } from '../../lib/format';
import type { KnowledgeRole, TipTapDoc } from '../../lib/knowledge';
import type { Role } from '../../lib/permissions';

// TipTap — тяжёлая зависимость, держим её вне основного бандла: читателям
// документов редактор не нужен.
const DocumentEditor = lazy(() => import('../../components/knowledge/DocumentEditor'));

const WRITE_ROLES: Role[] = ['admin', 'superadmin'];

/**
 * Страница документа: чтение и правка на ОДНОМ адресе.
 *
 * Правка — режим страницы, а не отдельный маршрут: адрес не меняется, «Назад»
 * выходит из режима. Сохранение автоматическое, поэтому сторожа несохранённых
 * правок больше нет — сторожить нечего, кроме секунды между последней буквой и
 * запросом; её закрывает beforeunload.
 */
export default function KnowledgeDocumentPage() {
  const { id } = useParams();
  const documentId = Number(id);
  const navigate = useNavigate();
  const location = useLocation();
  const showError = useApiError();
  const { me } = useAuth();
  const canWrite = !!me && WRITE_ROLES.includes(me.role as Role);

  const { data, isLoading, isError } = useKnowledgeDocument(documentId);
  const { update, setPublished } = useDocumentMutations(documentId);
  const { deleteDocument, duplicateDocument, setFavorite } = useKnowledgeMutations();
  const sections = useKnowledgeSections();

  const wantsEdit = Boolean((location.state as { edit?: boolean } | null)?.edit);
  const [editing, setEditing] = useState(wantsEdit);
  const [content, setContent] = useState<TipTapDoc>(EMPTY_DOC);
  const [roles, setRoles] = useState<KnowledgeRole[]>([]);
  const [accessOpen, setAccessOpen] = useState(false);
  const [propsOpen, setPropsOpen] = useState(false);
  const [propsError, setPropsError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  /**
   * Отметка времени версии, поверх которой правим. Обновляется ответом сервера
   * на каждое сохранение и уходит в следующее — так сервер ловит правку из
   * второй вкладки и отвечает конфликтом вместо тихого затирания.
   */
  const baseUpdatedAt = useRef<string | null>(null);

  /**
   * Что уже лежит на сервере. Нужно, чтобы не гонять содержимое документа
   * туда, где менялись только права: смена галочки доступа иначе стоила бы
   * полной валидации текста и перезаписи всего документа в базе.
   */
  const savedContent = useRef<string>('');

  const save = useCallback(
    async (payload: { content: TipTapDoc; roles: KnowledgeRole[] }) => {
      const serialized = JSON.stringify(payload.content);
      const fresh = await update.mutateAsync({
        content: serialized === savedContent.current ? undefined : payload.content,
        reader_roles: payload.roles,
        base_updated_at: baseUpdatedAt.current ?? undefined,
      });
      savedContent.current = serialized;
      baseUpdatedAt.current = fresh.updated_at;
    },
    [update],
  );

  const autosave = useAutosave(save);

  // Данные с сервера — исходное состояние формы. Пока идёт правка, ответ
  // сервера в поля не переливаем: он придёт как раз в момент, когда человек
  // печатает, и откатит последние буквы.
  useEffect(() => {
    if (!data) return;
    baseUpdatedAt.current = data.updated_at;
    if (autosave.pending) return;
    setContent(data.content ?? EMPTY_DOC);
    setRoles(data.reader_roles);
    savedContent.current = JSON.stringify(data.content ?? EMPTY_DOC);
  }, [data, autosave.pending]);

  const enterEditing = () => {
    setEditing(true);
    window.history.pushState({ kbEditing: true }, '', window.location.href);
  };

  useEffect(() => {
    if (!wantsEdit) return;
    window.history.pushState({ kbEditing: true }, '', window.location.href);
    navigate(location.pathname, { replace: true, state: null });
    // Намеренно один раз на монтирование: повторные срабатывания добавили бы
    // лишние записи в историю.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const leaveEditing = useCallback(() => {
    autosave.flush();
    setEditing(false);
    window.history.back();
  }, [autosave]);

  // Браузерное «Назад» во время правки выходит из режима, а не со страницы.
  useEffect(() => {
    const onPopState = () => setEditing(false);
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  // Единственное, что осталось сторожить, — секунда между последней буквой и
  // запросом, плюс сам незавершённый запрос.
  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!autosave.pending && autosave.state !== 'error') return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [autosave.pending, autosave.state]);

  // Только для чтения: оглавление в режиме правки не показывается, и считать
  // его там незачем — раньше оно пересобиралось на каждое нажатие клавиши, а
  // результат выбрасывался. Поэтому и `content` в зависимостях больше нет.
  const headings = useMemo(
    () => collectHeadings(editing ? undefined : data?.content),
    [editing, data?.content],
  );

  if (isLoading) return <PageLoading />;
  if (isError || !data) {
    return (
      <div className="page">
        <PageHeader title="Документ не найден" />
        <p className="kb-dialog__text">Документ удалён или у вас нет к нему доступа.</p>
        <p><Link to="/admin/knowledge">Вернуться к списку</Link></p>
      </div>
    );
  }

  const sectionTitle = sections.data?.sections.find((s) => s.id === data.section_id)?.title ?? '';
  const edit = (next: { content?: TipTapDoc; roles?: KnowledgeRole[] }) => {
    const nextContent = next.content ?? content;
    const nextRoles = next.roles ?? roles;
    if (next.content) setContent(next.content);
    if (next.roles) setRoles(next.roles);
    autosave.schedule({ content: nextContent, roles: nextRoles });
  };

  return (
    <div className={editing ? 'page page--editing' : 'page'}>
      <PageHeader
        title={data.title}
        crumbs={[
          { label: 'Wiki', to: '/admin/knowledge' },
          ...(sectionTitle
            ? [{ label: sectionTitle, to: `/admin/knowledge?section=${data.section_id}` }]
            : []),
          { label: data.title },
        ]}
        actions={
          editing ? (
            <>
              <SaveIndicator state={autosave.state} reason={autosave.reason} />
              <Button variant="ghost" onClick={() => setAccessOpen(true)}>Доступ</Button>
              <Button variant="primary" onClick={leaveEditing}>Готово</Button>
            </>
          ) : (
            <>
              <IconButton
                label={data.is_favorite ? 'Убрать из избранного' : 'В избранное'}
                className={`kb-star${data.is_favorite ? ' is-on' : ''}`}
                active={data.is_favorite}
                icon={<StarIcon size={18} filled={data.is_favorite} />}
                onClick={() =>
                  setFavorite.mutate(
                    { id: documentId, value: !data.is_favorite },
                    { onError: (err) => showError(err) },
                  )
                }
              />
              {canWrite && (
                <>
                  <IconButton
                    label="Редактировать"
                    icon={<PencilIcon size={18} />}
                    onClick={enterEditing}
                  />
                  <ActionMenu
                    label="Ещё действия с документом"
                    items={[
                      {
                        label: data.status === 'draft' ? 'Опубликовать' : 'Снять с публикации',
                        onSelect: () => setPublished.mutate(data.status === 'draft'),
                      },
                      {
                        label: 'Дублировать',
                        onSelect: () =>
                          duplicateDocument.mutate(documentId, {
                            onSuccess: (copy) =>
                              navigate(`/admin/knowledge/${copy.id}`, { state: { edit: true } }),
                            onError: (err) => showError(err),
                          }),
                      },
                      { label: 'Название и раздел', onSelect: () => setPropsOpen(true) },
                      { label: 'Настройки доступа', onSelect: () => setAccessOpen(true) },
                      { label: 'Удалить', danger: true, onSelect: () => setConfirmDelete(true) },
                    ]}
                  />
                </>
              )}
            </>
          )
        }
      />

      {/* Мета-строка документа: кто написал, когда, сколько читать. Это
          справочные сведения, поэтому они идут одной строкой под заголовком, а
          не колонкой — колонка отделяла бы название от текста. */}
      <div className="kb-doc-meta">
        <DocumentStatusBadge status={data.status} />
        {data.author_name && <span className="kb-doc-meta__item">{data.author_name}</span>}
        <span className="kb-doc-meta__item">
          Обновлён {fmtDateTimeShort(data.updated_at)}
        </span>
        {data.status === 'draft' && (
          <span className="kb-doc-meta__hint">Читателям он пока не виден.</span>
        )}
      </div>

      {editing ? (
        <Suspense fallback={<PageLoading />}>
          <DocumentEditor content={content} onChange={(doc) => edit({ content: doc })} />
        </Suspense>
      ) : (
        <div className="kb-reader">
          <div className="kb-doc-paper">
            <DocumentView content={data.content} anchors={headings.anchors} />
          </div>
          <DocumentSide>
            <TableOfContents entries={headings.entries} />
          </DocumentSide>
        </div>
      )}

      <DocumentPropertiesDialog
        open={propsOpen}
        onClose={() => { setPropsOpen(false); setPropsError(null); }}
        title={data.title}
        sectionId={data.section_id}
        sections={sections.data?.sections ?? []}
        saving={update.isPending}
        error={propsError}
        onSave={({ title, sectionId }) => {
          setPropsError(null);
          update.mutate(
            // base_updated_at намеренно не шлём: это правка свойств, а не
            // перезапись текста, и терять её из-за чужого автосохранения
            // незачем — затирать здесь нечего.
            { title, section_id: sectionId },
            {
              onSuccess: () => setPropsOpen(false),
              onError: (err) => setPropsError(
                err instanceof Error ? err.message : 'Не удалось сохранить',
              ),
            },
          );
        }}
      />

      <AccessDialog
        open={accessOpen}
        onClose={() => setAccessOpen(false)}
        footer={
          <ReaderRolesField
            value={roles}
            onChange={(next) => edit({ roles: next })}
          />
        }
      />

      <ConfirmDialog
        open={confirmDelete}
        title="Удалить документ"
        message={`Документ «${data.title}» уедет в архив. Оттуда его можно вернуть.`}
        confirmLabel="Удалить"
        danger
        busy={deleteDocument.isPending}
        onConfirm={() =>
          deleteDocument.mutate(documentId, {
            onSuccess: () => navigate('/admin/knowledge'),
            onError: (err) => showError(err),
          })
        }
        onClose={() => setConfirmDelete(false)}
      />
    </div>
  );
}

/**
 * Состояние автосохранения.
 *
 * Показывать обязательно: без кнопки «Сохранить» единственный признак, что
 * работа не потеряна, — эта строка. Ошибку показываем ею же, иначе отказ
 * сервера (например, непринятый узел) превращается в молчаливую потерю текста.
 */
function SaveIndicator({ state, reason }: { state: SaveState; reason: string | null }) {
  if (state === 'idle') return null;
  const label = {
    saving: 'Сохраняем…',
    saved: 'Сохранено',
    error: 'Не сохранено',
    conflict: 'Документ изменён в другом месте',
  }[state];
  return (
    <span
      className={`kb-savestate kb-savestate--${state}`}
      role="status"
      aria-live="polite"
      // Полный текст причины — в подсказке: в строке он обрезается, а знать
      // его целиком иногда нужно (в отказе бывает имя узла или атрибута).
      title={reason ?? undefined}
    >
      {label}
      {/* Причина отказа рядом с самим отказом. Без неё автор видит, что текст
          не уходит, но не понимает, что именно сервер не принял, — и остаётся
          править вслепую. */}
      {reason && <span className="kb-savestate__reason">{reason}</span>}
    </span>
  );
}
