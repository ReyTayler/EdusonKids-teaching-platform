import { useEffect, useState, type FormEvent, type KeyboardEvent } from 'react';
import { useBoardMutations } from '../../hooks/useTaskStructure';
import { useApiError } from '../../hooks/useApiError';
import { useAuth } from '../../hooks/useAuth';
import { useToast } from '../../components/ui/Toast';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { TextInput } from '../../components/form/TextInput';
import { Button } from '../../components/ui/Button';
import { ActionMenu, type ActionMenuItem } from '../../components/ui/ActionMenu';
import { ConfirmModal } from '../../components/ui/ConfirmModal';
import { initialsOf } from '../../components/Avatar';
import { plural } from '../../lib/labels';
import { canWriteTaskStages, type Role } from '../../lib/permissions';
import { conflictError } from './TaskBoard';
import type { TaskBoard } from '../../lib/tasks';

interface Props {
  boards: TaskBoard[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}

// Свёрнута полоса или нет — настройка рабочего места, а не состояние экрана:
// человек подгоняет её под ширину своего монитора один раз. Держим в
// localStorage тем же приёмом, что ширину выезжающих панелей
// (hooks/useDrawerResize.ts), включая try/catch: в приватном режиме браузера
// обращение к хранилищу бросает исключение, и без него полоса не отрисуется.
const RAIL_COLLAPSED_KEY = 'taskboard.railCollapsed';

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(RAIL_COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
}

function writeCollapsed(value: boolean): void {
  try {
    window.localStorage.setItem(RAIL_COLLAPSED_KEY, value ? '1' : '0');
  } catch {
    // приватный режим / отключённые данные сайта — просто не запомнится
  }
}

const CREATE_FORM_ID = 'task-board-create-form';

/**
 * Полоса воронок слева от рабочей области (по образцу Weeek). Раньше воронку
 * выбирали табами в шапке страницы: ряд табов рос вширь, седьмая воронка
 * уезжала в меню «Ещё», а счётчикам стадий и задач места не было вовсе.
 * Свёрнутая полоса оставляет от списка только аббревиатуры — на узком экране
 * доска важнее навигации.
 */
export function TaskBoardRail({ boards, selectedId, onSelect }: Props) {
  const { me } = useAuth();
  const { update, remove } = useBoardMutations();
  const showError = useApiError();
  const { toast } = useToast();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [createOpen, setCreateOpen] = useState(false);
  // Правят по одной воронке за раз — храним id, а не флаг на каждую карточку.
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [nameDraft, setNameDraft] = useState('');
  const [confirmBoard, setConfirmBoard] = useState<TaskBoard | null>(null);

  // Меню и правку видит только тот, кому бэкенд разрешит запись
  // (ReadStaffWriteSuperAdmin) — иначе нажатие кончалось бы 403.
  const canManage = canWriteTaskStages(me?.role as Role);

  // Свёрнутая полоса показывает одни аббревиатуры, поля для правки там нет.
  // Без сброса воронка осталась бы «в правке» и после разворачивания.
  useEffect(() => {
    if (collapsed) setRenamingId(null);
  }, [collapsed]);

  const startRename = (board: TaskBoard) => {
    setNameDraft(board.name);
    // Поле подменяет карточку не сразу, а следующим кадром: закрывая меню,
    // Radix возвращает фокус на свой триггер. Смени мы карточку синхронно,
    // триггера в DOM уже не было бы — фокус ушёл бы на body, и поле получило
    // бы blur сразу после autoFocus, то есть правка закрылась бы в тот же миг,
    // как открылась (тот же приём, что у переименования колонки в TaskColumn).
    requestAnimationFrame(() => setRenamingId(board.id));
  };

  const finishRename = (board: TaskBoard) => {
    setRenamingId(null);
    const trimmed = nameDraft.trim();
    // Пустое название бэкенд не примет, а молча отправлять его — значит ловить
    // 400 на действии, которого человек не совершал: возвращаем прежнее.
    if (!trimmed || trimmed === board.name) return;
    update.mutate(
      { id: board.id, name: trimmed },
      {
        onSuccess: () => toast('Воронка переименована', 'ok'),
        // Занятое имя бэкенд отдаёт кодом 409 duplicate_name — общий
        // conflictError превращает код в человеческий текст (lib/labels.ts).
        onError: (err) => showError(conflictError(err), 'Не удалось переименовать воронку'),
      },
    );
  };

  const handleRenameKey = (e: KeyboardEvent<HTMLInputElement>, board: TaskBoard) => {
    if (e.key === 'Enter') {
      (e.target as HTMLInputElement).blur(); // сохранение — в onBlur, как в TaskColumn
      return;
    }
    if (e.key === 'Escape') {
      // Возвращаем буфер ДО закрытия поля: onBlur (если браузер его пришлёт)
      // увидит прежнее название и ничего не отправит.
      setNameDraft(board.name);
      setRenamingId(null);
    }
  };

  const handleRemove = (board: TaskBoard) => {
    remove.mutate(board.id, {
      onSuccess: () => {
        setConfirmBoard(null);
        toast('Воронка удалена', 'ok');
        // Переключать раздел вручную не нужно: выбранная воронка живёт в
        // ?board=, и TasksPage сам подставляет первую доступную, как только id
        // из адреса перестаёт находиться в свежем списке.
      },
      onError: (err) => {
        setConfirmBoard(null);
        // has_tasks — 409 «в воронке есть задачи» (lib/labels.ts).
        showError(conflictError(err), 'Не удалось удалить воронку');
      },
    });
  };

  const menuItems = (board: TaskBoard): ActionMenuItem[] => [
    { label: 'Переименовать', onSelect: () => startRename(board) },
    { label: 'Удалить', danger: true, onSelect: () => setConfirmBoard(board) },
  ];

  const toggle = () => {
    setCollapsed((prev) => {
      writeCollapsed(!prev);
      return !prev;
    });
  };

  return (
    <aside className={`task-rail${collapsed ? ' task-rail--collapsed' : ''}`} aria-label="Воронки задач">
      <div className="task-rail__head">
        <button
          type="button"
          className="task-rail__toggle"
          aria-expanded={!collapsed}
          aria-label={collapsed ? 'Развернуть полосу воронок' : 'Свернуть полосу воронок'}
          title={collapsed ? 'Развернуть' : 'Свернуть'}
          onClick={toggle}
        >
          <ChevronGlyph dir={collapsed ? 'right' : 'left'} />
        </button>
        {!collapsed && canManage && (
          <Button variant="primary" size="sm" onClick={() => setCreateOpen(true)}>
            + Добавить доску
          </Button>
        )}
      </div>

      {collapsed ? (
        <div className="task-rail__tiles">
          {boards.map((b) => (
            <button
              key={b.id}
              type="button"
              className={`task-rail__tile${b.id === selectedId ? ' is-active' : ''}`}
              aria-current={b.id === selectedId ? 'true' : undefined}
              title={b.name}
              aria-label={b.name}
              onClick={() => onSelect(b.id)}
            >
              <span className="task-rail__abbr">{initialsOf(b.name)}</span>
              {b.open_tasks_count > 0 && (
                <span className="task-rail__tile-badge" aria-hidden="true">{b.open_tasks_count}</span>
              )}
            </button>
          ))}
        </div>
      ) : (
        <ul className="task-rail__list">
          {boards.map((b) => (
            <li key={b.id} className="task-rail__row">
              {/* Поле переименования ЗАМЕЩАЕТ карточку целиком, а не живёт
                  внутри неё: карточка сама по себе кнопка, инпут внутри кнопки
                  — и невалидная разметка, и клик по полю переключал бы воронку
                  (тот же приём, что у шапки колонки в TaskColumn). */}
              {renamingId === b.id ? (
                <TextInput
                  className="task-rail__rename-input"
                  value={nameDraft}
                  autoFocus
                  onChange={(e) => setNameDraft(e.target.value)}
                  onBlur={() => finishRename(b)}
                  onKeyDown={(e) => handleRenameKey(e, b)}
                  aria-label={`Название воронки «${b.name}»`}
                />
              ) : (
                <>
                  <button
                    type="button"
                    className={`task-rail__card${b.id === selectedId ? ' is-active' : ''}`}
                    aria-current={b.id === selectedId ? 'true' : undefined}
                    onClick={() => onSelect(b.id)}
                  >
                    <span className="task-rail__name">{b.name}</span>
                    <span className="task-rail__meta">
                      <span>{b.stages_count} {plural(b.stages_count, 'стадия', 'стадии', 'стадий')}</span>
                      <span className="task-rail__meta-sep" aria-hidden="true">·</span>
                      <span>{b.open_tasks_count} {plural(b.open_tasks_count, 'задача', 'задачи', 'задач')}</span>
                    </span>
                  </button>
                  {canManage && (
                    <span className="task-rail__row-menu">
                      <ActionMenu items={menuItems(b)} label={`Действия с воронкой «${b.name}»`} />
                    </span>
                  )}
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      {createOpen && (
        <BoardCreateModal
          onClose={() => setCreateOpen(false)}
          onCreated={onSelect}
        />
      )}

      {confirmBoard && (
        <ConfirmModal
          title="Удалить воронку?"
          message={`Воронка «${confirmBoard.name}» будет удалена вместе со своими стадиями. Если в ней есть задачи, бэкенд откажет — сначала перенесите или закройте их.`}
          confirmLabel="Удалить"
          danger
          isPending={remove.isPending}
          onConfirm={() => handleRemove(confirmBoard)}
          onClose={() => setConfirmBoard(null)}
        />
      )}
    </aside>
  );
}

/**
 * Создание воронки. Монтируется только пока открыта: размонтирование сбрасывает
 * поле, иначе следующее открытие начиналось бы с прошлого ввода.
 *
 * Экспортируется, потому что заводить воронку приходится и там, где полосы на
 * экране нет вовсе — из пустого состояния раздела (TasksPage).
 */
export function BoardCreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: (id: number) => void }) {
  const { create } = useBoardMutations();
  const showError = useApiError();
  const { toast } = useToast();
  const [name, setName] = useState('');

  const canSubmit = !!name.trim() && !create.isPending;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    create.mutate({ name: name.trim() }, {
      onSuccess: (board) => {
        toast('Воронка создана', 'ok');
        onClose();
        // Переключаемся сразу: воронку заводят, чтобы в ней работать.
        onCreated(board.id);
      },
      // Повторяющееся имя бэкенд отдаёт кодом 409 duplicate_name — общий
      // conflictError превращает код в человеческий текст (lib/labels.ts).
      onError: (err) => showError(conflictError(err), 'Не удалось создать воронку'),
    });
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => { if (!open) onClose(); }}
      title="Новая воронка"
      footer={(
        <button type="submit" form={CREATE_FORM_ID} className="btn-save" disabled={!canSubmit}>
          Создать
        </button>
      )}
    >
      <form id={CREATE_FORM_ID} className="modal-form" onSubmit={onSubmit}>
        <Field label="Название" required full>
          <TextInput
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Например: Продажи"
            autoFocus
          />
        </Field>
      </form>
    </Dialog>
  );
}

function ChevronGlyph({ dir }: { dir: 'left' | 'right' }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points={dir === 'right' ? '9 18 15 12 9 6' : '15 18 9 12 15 6'} />
    </svg>
  );
}
