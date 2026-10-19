import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api, apiUpload, apiUploadWithProgress } from '../lib/api';
import type {
  DocumentStatus,
  KnowledgeDocument,
  KnowledgeDocumentRow,
  KnowledgeFileMeta,
  KnowledgeImage,
  KnowledgeRole,
  KnowledgeSection,
  KnowledgeSections,
  LibraryScope,
  TipTapDoc,
} from '../lib/knowledge';
import type { Paginated } from '../lib/types';

const BASE = '/api/admin/knowledge';
const KEY = ['knowledge'] as const;

/**
 * Разделы со счётчиками документов.
 *
 * Счётчики приходят отсюда, а не считаются на клиенте по выгрузке всех
 * документов: та выгрузка росла вместе с базой знаний (500 документов — 293 КБ
 * на каждое открытие экрана), тогда как здесь ответ остаётся размером со список
 * папок независимо от объёма.
 */
export function useKnowledgeSections() {
  return useQuery({
    queryKey: [...KEY, 'sections'],
    queryFn: () => api<KnowledgeSections>('GET', `${BASE}/sections`),
  });
}

export interface LibraryQuery {
  sectionId: number | null;
  q: string;
  scope: LibraryScope;
  status: DocumentStatus | '';
  page: number;
  pageSize: number;
}

/** Размер страницы по умолчанию. */
export const DEFAULT_PAGE_SIZE = 50;

/**
 * Список документов: поиск, папка, подборка, статус — всё считает сервер.
 *
 * Выборка ВСЕГДА постраничная. Прежняя схема грузила разом до 500 документов и
 * фильтровала их в браузере; это работало ровно до тех пор, пока база знаний
 * оставалась маленькой, а дальше каждое открытие экрана стоило сотен килобайт.
 */
export function useKnowledgeLibrary(params: LibraryQuery) {
  const search = new URLSearchParams();
  if (params.sectionId !== null) search.set('section_id', String(params.sectionId));
  if (params.q) search.set('q', params.q);
  if (params.scope !== 'all') search.set('scope', params.scope);
  if (params.status) search.set('status', params.status);
  search.set('page', String(params.page));
  search.set('page_size', String(params.pageSize));

  return useQuery({
    queryKey: [...KEY, 'documents', 'library', params],
    queryFn: () => api<Paginated<KnowledgeDocumentRow>>('GET', `${BASE}/documents?${search}`),
    // Обязательно во всех server-paginated хуках проекта: без этого при смене
    // страницы список схлопывается в пустой и экран «прыгает».
    placeholderData: keepPreviousData,
  });
}

/**
 * Сколько документов раздела показывает дерево слева.
 *
 * Дерево — навигация, а не список: сотня строк в панели шириной 240px уже
 * бесполезна, а вот запрос за ними стоит столько же, сколько за всей базой.
 * Что не поместилось, открывается в основной части экрана с пагинацией.
 */
export const TREE_BRANCH_SIZE = 50;

/**
 * Документы одного раздела для ветки дерева.
 *
 * Отдельный хук, а не useKnowledgeLibrary с другими параметрами: у ветки свои
 * правила — фиксированный размер выборки, отсутствие фильтров и запрос ТОЛЬКО
 * когда ветка раскрыта (enabled). Смешай их — и любой фильтр основного списка
 * начал бы менять содержимое дерева.
 */
export function useSectionDocuments(sectionId: number, enabled: boolean) {
  return useQuery({
    queryKey: [...KEY, 'documents', 'branch', sectionId],
    queryFn: () =>
      api<Paginated<KnowledgeDocumentRow>>(
        'GET',
        `${BASE}/documents?section_id=${sectionId}&page=1&page_size=${TREE_BRANCH_SIZE}`,
      ),
    enabled,
    placeholderData: keepPreviousData,
  });
}

export function useKnowledgeDocument(id: number | undefined) {
  return useQuery({
    queryKey: [...KEY, 'document', id ?? 0],
    queryFn: () => api<KnowledgeDocument>('GET', `${BASE}/documents/${id}`),
    enabled: id !== undefined,
  });
}

/** Мутации разделов и создание/удаление документов — без привязки к id конкретного документа. */
export function useKnowledgeMutations() {
  const qc = useQueryClient();
  const invalidateSections = () => qc.invalidateQueries({ queryKey: [...KEY, 'sections'] });
  // Префиксом накрывает все варианты списка документов (любой раздел, страница,
  // подборка). Разделы освежаем заодно: в их ответе лежат счётчики документов,
  // и без этого цифра рядом с папкой врала бы до перезагрузки страницы.
  const invalidateDocuments = () => {
    qc.invalidateQueries({ queryKey: [...KEY, 'documents'] });
    invalidateSections();
  };
  return {
    createSection: useMutation({
      mutationFn: (title: string) => api<KnowledgeSection>('POST', `${BASE}/sections`, { title }),
      onSuccess: invalidateSections,
    }),
    renameSection: useMutation({
      mutationFn: ({ id, title }: { id: number; title: string }) =>
        api<KnowledgeSection>('PATCH', `${BASE}/sections/${id}`, { title }),
      onSuccess: invalidateSections,
    }),
    deleteSection: useMutation({
      mutationFn: (id: number) => api<void>('DELETE', `${BASE}/sections/${id}`),
      onSuccess: invalidateSections,
    }),
    createDocument: useMutation({
      mutationFn: ({ sectionId, title }: { sectionId: number; title: string }) =>
        api<KnowledgeDocument>('POST', `${BASE}/documents`, { section_id: sectionId, title }),
      onSuccess: invalidateDocuments,
    }),
    deleteDocument: useMutation({
      mutationFn: (id: number) => api<void>('DELETE', `${BASE}/documents/${id}`),
      onSuccess: invalidateDocuments,
    }),
    // Публикация с id в аргументе — для списков. Хук useDocumentMutations(id)
    // привязан к одному документу и на экране со списком породил бы столько же
    // наборов мутаций, сколько карточек.
    setDocumentPublished: useMutation({
      mutationFn: ({ id, published }: { id: number; published: boolean }) =>
        api<KnowledgeDocument>(
          'POST',
          `${BASE}/documents/${id}/${published ? 'publish' : 'unpublish'}`,
          {},
        ),
      onSuccess: (doc) => {
        mergeDocument(qc, doc.id, doc);
        invalidateDocuments();
      },
    }),
    restoreDocument: useMutation({
      mutationFn: (id: number) =>
        api<KnowledgeDocument>('POST', `${BASE}/documents/${id}/restore`, {}),
      onSuccess: invalidateDocuments,
    }),
    duplicateDocument: useMutation({
      mutationFn: (id: number) =>
        api<KnowledgeDocument>('POST', `${BASE}/documents/${id}/duplicate`, {}),
      onSuccess: invalidateDocuments,
    }),
    // Закладка личная и меняется одним щелчком по звёздочке, поэтому список
    // обновляем целиком: пересчитывать её вручную в кэше — лишний источник
    // расхождения между «звёздочка горит» и «документ в избранном».
    setFavorite: useMutation({
      mutationFn: async ({ id, value }: { id: number; value: boolean }) => {
        if (value) await api<{ is_favorite: boolean }>('POST', `${BASE}/documents/${id}/favorite`, {});
        else await api<void>('DELETE', `${BASE}/documents/${id}/favorite`);
      },
      onSuccess: (_data, { id }) => {
        invalidateDocuments();
        qc.invalidateQueries({ queryKey: [...KEY, 'document', id] });
      },
    }),
    uploadImage: useMutation({
      mutationFn: (file: File) => apiUpload<KnowledgeImage>(`${BASE}/images`, file),
    }),
    // Файл грузится с сообщением о ходе отправки: 25 МБ по слабому каналу —
    // это десятки секунд, и молчащий интерфейс читается как поломка.
    uploadFile: useMutation({
      mutationFn: ({ file, onProgress }: { file: File; onProgress?: (p: number) => void }) =>
        apiUploadWithProgress<KnowledgeFileMeta>(`${BASE}/files`, file, onProgress),
    }),
  };
}

/**
 * Влить ответ мутации в кэш документа, а не заменить его целиком.
 *
 * Ответы на сохранение и публикацию собирает services._serialize, и полей в
 * них меньше, чем отдаёт GET: там нет ни автора, ни признака избранного —
 * первый требует join, второй зависит от того, кто спрашивает. Прямая замена
 * стирала бы их со страницы после каждого автосохранения.
 */
function mergeDocument(
  qc: ReturnType<typeof useQueryClient>,
  id: number,
  doc: KnowledgeDocument,
) {
  qc.setQueryData<KnowledgeDocument>(
    [...KEY, 'document', id],
    (old) => (old ? { ...old, ...doc } : doc),
  );
}

export interface DocumentPatch {
  title?: string;
  section_id?: number;
  content?: TipTapDoc;
  reader_roles?: KnowledgeRole[];
  /**
   * Отметка времени версии, которую правим. Сервер сравнивает её со своей и
   * отвечает 409, если документ успели изменить в другом месте. Без этого
   * автосохранение из двух вкладок молча затирает чужую работу.
   */
  base_updated_at?: string;
}

/**
 * Мутации, привязанные к конкретному документу (правка содержимого,
 * публикация) — параметризован id, как useStudentCommentMutations/
 * useTeacherTelegramMutations, а не впихнут в общий useKnowledgeMutations,
 * где id пришлось бы передавать в каждый вызов отдельно.
 */
export function useDocumentMutations(id: number) {
  const qc = useQueryClient();
  const onDocSuccess = (doc: KnowledgeDocument) => {
    mergeDocument(qc, id, doc);
    qc.invalidateQueries({ queryKey: [...KEY, 'documents', doc.section_id] });
  };
  return {
    update: useMutation({
      mutationFn: (patch: DocumentPatch) =>
        api<KnowledgeDocument>('PATCH', `${BASE}/documents/${id}`, patch),
      onSuccess: onDocSuccess,
    }),
    setPublished: useMutation({
      mutationFn: (published: boolean) =>
        api<KnowledgeDocument>(
          'POST',
          `${BASE}/documents/${id}/${published ? 'publish' : 'unpublish'}`,
          {},
        ),
      onSuccess: onDocSuccess,
    }),
  };
}
