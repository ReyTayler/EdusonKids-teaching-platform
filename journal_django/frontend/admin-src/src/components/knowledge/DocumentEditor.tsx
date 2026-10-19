import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { EditorContent, useEditor, useEditorState } from '@tiptap/react';
import type { Editor } from '@tiptap/react';
import { DragHandle } from '@tiptap/extension-drag-handle-react';
import { createLowlight } from 'lowlight';
import { buildExtensions } from './editorExtensions';
import { createSlashMenu } from './SlashMenu';
import { registerLanguages } from './codeLanguages';
import { askLink } from './linkPrompt';
import { EditorToolbar } from './EditorToolbar';
import { TableControls } from './TableControls';
import { BlockHandleIcon, PlusIcon } from './editorIcons';
import { useKnowledgeMutations } from '../../hooks/useKnowledge';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../ui/Toast';
import { FILE_ACCEPT, splitFiles } from './fileKinds';
import {
  addUploadPlaceholder,
  findUploadPlaceholder,
  removeUploadPlaceholder,
  updateUploadProgress,
} from './uploadPlaceholder';
import { extractPastedImages, resolvePastedImages } from './pastedImages';
import type { ExtractedPaste, UploadedImage } from './pastedImages';
import type { TipTapDoc } from '../../lib/knowledge';

/**
 * Редактор документа. Грузится через React.lazy — читателям этот чанк не нужен.
 *
 * Раскладка повторяет привычный текстовый процессор: панель инструментов во всю
 * ширину сверху, под ней «лист» по центру на приглушённом фоне.
 *
 * Набор расширений — в editorExtensions.ts, общий источник для редактора и для
 * сверки с белым списком сервера (apps/knowledge/content.py).
 */
export default function DocumentEditor({
  content,
  onChange,
}: {
  content: TipTapDoc;
  onChange: (doc: TipTapDoc) => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const attachInput = useRef<HTMLInputElement>(null);
  const { uploadImage, uploadFile } = useKnowledgeMutations();
  const showError = useApiError();
  const { toast } = useToast();

  /**
   * Вставка картинок и открытие диалога файла — через ref, а не напрямую в
   * конфигурации редактора: расширения задаются один раз при создании и
   * замыкают значения того момента, когда самого редактора ещё нет.
   */
  const insertImagesRef = useRef<(files: File[], at?: number) => void>(() => {});
  const insertFilesRef = useRef<(files: File[], at?: number) => void>(() => {});
  const rejectFilesRef = useRef<(reasons: string[]) => void>(() => {});
  const pasteWithImagesRef = useRef<(paste: ExtractedPaste) => void>(() => {});
  const pickImageRef = useRef<() => void>(() => {});
  const pickFileRef = useRef<() => void>(() => {});
  const askLinkRef = useRef<() => void>(() => {});
  const insertSlashRef = useRef<(atBlockStart: boolean) => void>(() => {});

  // Ядро подсветки создаётся сразу и живёт всё время жизни редактора,
  // грамматики догружаются в него отдельным чанком (см. useCodeLanguages).
  const lowlight = useMemo(() => createLowlight(), []);
  useCodeLanguages(lowlight);

  const extensions = useMemo(
    () => [
      ...buildExtensions(lowlight),
      createSlashMenu({
        onPickImage: () => pickImageRef.current(),
        onPickFile: () => pickFileRef.current(),
      }),
    ],
    [lowlight],
  );

  const editor = useEditor({
    extensions,
    content,
    // Без этого useEditor отдаёт null на первом рендере (режим, рассчитанный на
    // серверный рендеринг, которого у нас нет), и всё, что подписывается на
    // состояние редактора, получает пустое значение.
    immediatelyRender: true,
    onUpdate: ({ editor: e }) => onChange(e.getJSON() as TipTapDoc),
    editorProps: {
      // Сочетания, которых нет в StarterKit. Остальные (Ctrl+B/I/U, Ctrl+Z,
      // Ctrl+Shift+Z, Ctrl+Shift+7 для нумерованного списка) даёт он сам.
      handleKeyDown: (view, event) => {
        if (!(event.ctrlKey || event.metaKey)) return false;
        if (event.key === 'k' || event.key === 'K') {
          event.preventDefault();
          askLinkRef.current();
          return true;
        }
        if (event.key === '/') {
          event.preventDefault();
          // Меню блоков открывается набором «/» в начале строки — здесь мы
          // просто набираем этот символ за пользователя, при необходимости
          // начав новый абзац. Так у сочетания и у ручного ввода один путь.
          const { $from, empty } = view.state.selection;
          const atBlockStart = empty && $from.parentOffset === 0;
          insertSlashRef.current(atBlockStart);
          return true;
        }
        return false;
      },
      // Вставка из буфера и перетаскивание — самый частый способ добавить
      // скриншот, кнопкой пользуются реже.
      //
      // Обработчики отдаём самому редактору, а НЕ вешаем на editor.view.dom из
      // эффекта: в @tiptap/react v3 представление создаётся позже, чем успевает
      // отработать эффект родительского компонента, и обращение к view роняло
      // страницу с «The editor view is not available».
      handlePaste: (_view, event) => {
        const { images, attachments, rejected } = splitFiles(event.clipboardData?.files);
        if (images.length > 0 || attachments.length > 0 || rejected.length > 0) {
          if (images.length > 0) insertImagesRef.current(images);
          if (attachments.length > 0) insertFilesRef.current(attachments);
          rejectFilesRef.current(rejected);
          return true; // событие обработано — обычную вставку не выполняем
        }
        // Текст с картинками (Google Docs и т. п.): файлов в буфере нет,
        // картинки лежат внутри HTML. Без этой ветки их выбрасывал парсер.
        const html = event.clipboardData?.getData('text/html');
        const extracted = html ? extractPastedImages(html) : null;
        if (!extracted) return false;
        pasteWithImagesRef.current(extracted);
        return true;
      },
      handleDrop: (view, event, _slice, moved) => {
        // moved — тащат существующий узел внутри документа. Это работа
        // ProseMirror, файлов там нет.
        if (moved) return false;
        const drag = event as DragEvent;
        const { images, attachments, rejected } = splitFiles(drag.dataTransfer?.files);
        if (images.length === 0 && attachments.length === 0 && rejected.length === 0) return false;
        rejectFilesRef.current(rejected);
        // Куда бросили, туда и класть. Без этого вложение уезжало в место,
        // где стоял курсор до перетаскивания.
        const at = view.posAtCoords({ left: drag.clientX, top: drag.clientY });
        if (images.length > 0) insertImagesRef.current(images, at?.pos);
        if (attachments.length > 0) insertFilesRef.current(attachments, at?.pos);
        return true;
      },
    },
  }, [extensions]);

  const insertImages = useCallback(
    async (files: File[], at?: number) => {
      // Точку вставки фиксируем СРАЗУ, до загрузки: пока файл летит на сервер,
      // пользователь успевает щёлкнуть в другом месте. ProseMirror сам сдвинет
      // выделение, если документ за это время изменится.
      if (at !== undefined && editor && !editor.isDestroyed) editor.commands.focus(at);
      // Последовательно, а не Promise.all: несколько скриншотов должны лечь в
      // том же порядке, в каком их бросили.
      for (const file of files) {
        try {
          const image = await uploadImage.mutateAsync(file);
          // Пока файл летел на сервер, редактор могли закрыть.
          if (!editor || editor.isDestroyed) return;
          // В документ кладём размер ПОКАЗА, а не размеры файла. Постер
          // 960×1359 иначе просил бы 960 пикселей ширины, упирался в колонку и
          // в потолок высоты одновременно, и результат зависел бы от того, как
          // браузер разрулит два ограничения сразу. Считаем сами: ширина не
          // больше колонки, не больше оригинала и такая, чтобы высота
          // укладывалась в потолок.
          editor.chain().focus().insertContent({
            type: 'knowledgeImage',
            attrs: {
              imageId: image.id,
              alt: file.name.replace(/\.[^.]+$/, ''),
              ...displaySize(editor, image.width, image.height),
            },
          }).run();
        } catch (err) {
          showError(err);
        }
      }
    },
    [editor, uploadImage, showError],
  );

  /**
   * Вставка вложений.
   *
   * Отличается от картинок двумя вещами. Во-первых, место под файл занимается
   * СРАЗУ — временным блоком с полосой прогресса: загрузка 25 МБ длится
   * десятки секунд, и без него интерфейс выглядит зависшим. Во-вторых, при
   * отказе временный блок обязательно убирается, иначе в документе останется
   * карточка файла, которого нет.
   */
  const insertFiles = useCallback(
    async (files: File[], at?: number) => {
      if (at !== undefined && editor && !editor.isDestroyed) editor.commands.focus(at);
      for (const file of files) {
        if (!editor || editor.isDestroyed) return;
        const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        // Место под файл — декорация, а не узел документа: сохраниться она не
        // может по построению, поэтому автосохранение во время загрузки
        // работает как обычно (см. uploadPlaceholder.ts).
        addUploadPlaceholder(editor, id, file.name);

        try {
          const meta = await uploadFile.mutateAsync({
            file,
            onProgress: (percent) => updateUploadProgress(id, percent),
          });
          if (!editor || editor.isDestroyed) return;
          // Позиция переехала вместе с правками, которые автор успел внести:
          // за этим следит сама декорация. Если места уже нет — его удалили
          // вместе с куском текста, и вставлять файл некуда.
          const pos = findUploadPlaceholder(editor, id);
          if (pos !== null) {
            editor.chain().insertContentAt(pos, {
              type: 'knowledgeFile',
              attrs: {
                // Имя берём своё, а не из ответа: сервер хранит один файл на
                // содержимое и вернул бы имя ПЕРВОЙ загрузки. Тот же документ,
                // прикреплённый под другим названием, показывал бы чужое.
                fileId: meta.id, name: file.name, size: meta.byte_size, mime: meta.mime,
              },
            }).run();
          }
        } catch (err) {
          showError(err);
        } finally {
          if (editor && !editor.isDestroyed) removeUploadPlaceholder(editor, id);
        }
      }
    },
    [editor, uploadFile, showError],
  );

  /**
   * Показать, почему файлы не приняты.
   *
   * По одному сообщению на файл, но не больше трёх: бросили папку с двадцатью
   * файлами — двадцать всплывающих сообщений сами становятся проблемой.
   */
  const showRejected = useCallback((reasons: string[]) => {
    if (reasons.length === 0) return;
    for (const reason of reasons.slice(0, 3)) toast(reason, 'error');
    if (reasons.length > 3) {
      toast(`И ещё ${reasons.length - 3} файлов прикрепить нельзя.`, 'error');
    }
  }, [toast]);

  /**
   * Вставка разметки с картинками внутри (Google Docs и т. п.).
   *
   * Сначала картинки грузятся на сервер, потом разметка вставляется целиком —
   * уже с нашими картинками — штатной вставкой ProseMirror, со всеми её
   * правилами разбора. Пока идёт загрузка, место вставки держит временный
   * блок (та же декорация, что у файлов): он сдвигается вместе с правками, и
   * текст ляжет туда, где его вставили, даже если автор успел печатать дальше.
   */
  const pasteWithImages = useCallback(
    async ({ html, images, lost }: ExtractedPaste) => {
      if (!editor || editor.isDestroyed) return;
      if (images.length === 0) {
        editor.view.pasteHTML(html);
        reportLostImages(toast, lost);
        return;
      }

      // Вставка заменяет выделенное — убираем его сразу, чтобы место под
      // загрузку встало туда же, куда потом ляжет текст.
      editor.commands.deleteSelection();
      const id = `paste-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      addUploadPlaceholder(
        editor, id,
        images.length === 1 ? 'Картинка из вставки' : `Картинки из вставки: ${images.length}`,
      );

      const uploaded = new Map<string, UploadedImage>();
      let failed = 0;
      let placed = false;
      try {
        // Последовательно: порядок и так задан разметкой, а десяток
        // параллельных загрузок по мегабайту забил бы канал и сервер.
        for (const [index, item] of images.entries()) {
          try {
            const image = await uploadImage.mutateAsync(item.file);
            if (editor.isDestroyed) return;
            uploaded.set(item.token, {
              id: image.id,
              alt: item.alt,
              ...pastedSize(editor, image.width, image.height, item.sourceWidth),
            });
          } catch (err) {
            failed += 1;
            // Причину показываем один раз; сколько всего не перенесено —
            // скажет итоговое сообщение.
            if (failed === 1) showError(err);
          }
          updateUploadProgress(id, ((index + 1) / images.length) * 100);
        }
        if (editor.isDestroyed) return;

        const pos = findUploadPlaceholder(editor, id);
        removeUploadPlaceholder(editor, id);
        placed = true;
        // Место удалили вместе с куском текста, пока шла загрузка, —
        // вставлять некуда, и это решение автора.
        if (pos === null) return;
        editor.chain().focus().setTextSelection(pos).run();
        editor.view.pasteHTML(resolvePastedImages(html, uploaded));
        reportLostImages(toast, lost + failed);
      } finally {
        if (!placed && !editor.isDestroyed) removeUploadPlaceholder(editor, id);
      }
    },
    [editor, uploadImage, showError, toast],
  );

  insertImagesRef.current = (files: File[], at?: number) => { void insertImages(files, at); };
  pasteWithImagesRef.current = (paste: ExtractedPaste) => { void pasteWithImages(paste); };
  insertFilesRef.current = (files: File[], at?: number) => { void insertFiles(files, at); };
  rejectFilesRef.current = showRejected;
  pickImageRef.current = () => fileInput.current?.click();
  pickFileRef.current = () => attachInput.current?.click();
  askLinkRef.current = () => { if (editor) askLink(editor); };
  insertSlashRef.current = (atBlockStart: boolean) => {
    if (!editor || editor.isDestroyed) return;
    const chain = editor.chain().focus();
    if (!atBlockStart) chain.splitBlock();
    chain.insertContent('/').run();
  };

  if (!editor) return null;

  return (
    <div className="kb-editor">
      <EditorToolbar
        editor={editor}
        uploading={uploadImage.isPending}
        onPickImage={() => fileInput.current?.click()}
        onPickFile={() => attachInput.current?.click()}
      />
      <input
        ref={fileInput}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        hidden
        multiple
        onChange={(e) => {
          const { images } = splitFiles(e.target.files);
          if (images.length > 0) void insertImages(images);
          e.target.value = '';
        }}
      />
      <input
        ref={attachInput}
        type="file"
        accept={FILE_ACCEPT}
        hidden
        multiple
        onChange={(e) => {
          const { attachments, rejected } = splitFiles(e.target.files);
          if (attachments.length > 0) void insertFiles(attachments);
          showRejected(rejected);
          e.target.value = '';
        }}
      />
      {/* Приглушённое поле вокруг «листа» — как в текстовом процессоре: белая
          страница на сером фоне читается как физический лист бумаги. */}
      <div className="kb-editor-field">
        <div className="kb-editor-sheet kb-editor-canvas">
          <BlockHandles editor={editor} />
          <TableControls editor={editor} />
          <EditorContent editor={editor} className="kb-doc" />
        </div>
      </div>
    </div>
  );
}

/**
 * Ручка блока: «+» добавляет абзац следом, «⋮⋮» перетаскивает.
 *
 * Компонент из @tiptap/extension-drag-handle-react сам следит, над каким блоком
 * курсор, и ставит ручку на поля листа. Своя реализация означала бы пересчёт
 * координат на каждое движение мыши.
 */
function BlockHandles({ editor }: { editor: Editor }) {
  // Над таблицей ручка блока не нужна: там своё управление (TableControls), и
  // оно занимает то же самое левое поле листа.
  const [overTable, setOverTable] = useState(false);

  return (
    <DragHandle
      editor={editor}
      className={`kb-handle${overTable ? ' is-over-table' : ''}`}
      onNodeChange={({ node }) => setOverTable(node?.type.name === 'table')}
    >
      <button
        type="button"
        className="kb-handle__btn"
        aria-label="Добавить блок ниже"
        onClick={() => {
          // Курсор уже стоит в блоке, над которым висит ручка: команда
          // добавляет абзац сразу за ним и уводит туда фокус.
          editor.chain().focus().insertContentAt(
            editor.state.selection.$to.after(), { type: 'paragraph' },
          ).run();
        }}
      >
        <PlusIcon size={14} />
      </button>
      <span className="kb-handle__grip" aria-label="Перетащить блок" role="img">
        <BlockHandleIcon size={14} />
      </span>
    </DragHandle>
  );
}

/**
 * Размер, с которым картинка встаёт в документ.
 *
 * Пропорции сохраняются всегда: высота считается из ширины, а не берётся из
 * файла. Потолок высоты — тот же, что у показа (--kb-image-max-h): без него
 * вертикальный снимок занимал бы экран целиком и разрывал чтение пополам.
 */
/**
 * Размер вставленной картинки: как у загруженной вручную, но не шире, чем она
 * стояла в исходном документе. Уменьшенная автором картинка иначе
 * раздувалась бы до ширины колонки.
 */
function pastedSize(editor: Editor, width: number, height: number, sourceWidth: number | null) {
  const size = displaySize(editor, width, height);
  if (!sourceWidth || !size.width || sourceWidth >= size.width) return size;
  return {
    width: sourceWidth,
    height: Math.max(1, Math.round((sourceWidth * size.height) / size.width)),
  };
}

/**
 * Сказать, сколько картинок из вставки не перенесено. Без сообщения потеря
 * незаметна: текст на месте, и отсутствие картинки видно, только если помнить
 * оригинал.
 */
function reportLostImages(toast: (message: string, kind?: 'ok' | 'error' | 'info') => void, lost: number) {
  if (lost <= 0) return;
  toast(
    `Не удалось перенести картинок: ${lost}. Скопируйте их по одной (правой кнопкой → «Копировать изображение») и вставьте.`,
    'error',
  );
}

function displaySize(editor: Editor, width: number, height: number): {
  width: number; height: number;
} {
  if (!width || !height) return { width, height };
  const ratio = height / width;

  const column = editor.view.dom.clientWidth || width;
  const capRaw = getComputedStyle(editor.view.dom).getPropertyValue('--kb-image-max-h');
  const cap = Number.parseFloat(capRaw);

  let shown = Math.min(width, column);
  if (Number.isFinite(cap) && cap > 0) shown = Math.min(shown, Math.round(cap / ratio));

  return { width: shown, height: Math.max(1, Math.round(shown * ratio)) };
}

/**
 * Догрузить грамматики подсветки в уже созданный экземпляр.
 *
 * Ничего не возвращает и не вызывает перерисовку: расширения остаются теми же,
 * а подсветка появляется на следующей транзакции редактора. Без подсветки
 * редактор полностью работоспособен, поэтому ошибку загрузки проглатываем.
 */
function useCodeLanguages(lowlight: ReturnType<typeof createLowlight>) {
  useEffect(() => {
    void registerLanguages(lowlight).catch(() => {});
  }, [lowlight]);
}

