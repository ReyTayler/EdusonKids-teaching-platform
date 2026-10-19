import { useEffect, useState, type KeyboardEvent } from 'react';
import { useDroppable } from '@dnd-kit/core';
import { TaskCard } from './TaskCard';
import { StageSettingsDialog } from './StageSettingsDialog';
import { conflictError } from './TaskBoard';
import { BoardColumnHead } from '../../components/board/BoardColumnHead';
import { TextInput } from '../../components/form/TextInput';
import { EmptyState } from '../../components/ui/EmptyState';
import { ActionMenu, type ActionMenuItem } from '../../components/ui/ActionMenu';
import { ConfirmModal } from '../../components/ui/ConfirmModal';
import { useToast } from '../../components/ui/Toast';
import { useApiError } from '../../hooks/useApiError';
import { useAuth } from '../../hooks/useAuth';
import { useTaskColumnCards, useTaskMutations } from '../../hooks/useTasks';
import { useStageMutations, useTaskStages } from '../../hooks/useTaskStructure';
import { canWriteTaskStages, type Role } from '../../lib/permissions';
import type { TaskColumnCount, TaskFilters, TaskRow } from '../../lib/tasks';

interface Props {
  col: TaskColumnCount;
  boardId: number;
  filters: TaskFilters;
  onOpen: (id: number) => void;
}

/**
 * Колонка доски задач. Карточки колонки — свой пагинированный запрос
 * (useTaskColumnCards), не кусок общего ответа доски: счётчики уже приехали
 * отдельно через useTaskColumns, самих карточек доска целиком не грузит.
 */
export function TaskColumn({ col, boardId, filters, onOpen }: Props) {
  const { setNodeRef, isOver } = useDroppable({ id: col.stage_id });
  const showError = useApiError();
  const { toast } = useToast();
  const { me } = useAuth();
  const { create } = useTaskMutations();
  // Порядок колонок берём из справочника стадий: reorder требует ПОЛНЫЙ набор
  // id воронки, а не пару переставленных соседей. Запрос общий на всю доску —
  // ключ у useTaskStages один, поэтому каждая колонка читает тот же кэш.
  const { data: stages } = useTaskStages(boardId);
  const stageM = useStageMutations(boardId);

  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<TaskRow[]>([]);
  const [quickTitle, setQuickTitle] = useState('');
  // Поле быстрого добавления по умолчанию свёрнуто в ссылку внизу колонки:
  // доска — про карточки, а не про постоянно торчащий инпут.
  const [adding, setAdding] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [label, setLabel] = useState(col.label);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);

  const canEditStages = canWriteTaskStages(me?.role as Role);

  // Фильтры завязаны в объект — сравниваем по сериализации, как в RenewalColumn.
  const filtersKey = JSON.stringify(filters);
  const { data, isLoading, isFetching } = useTaskColumnCards(col.stage_id, filters, page);

  // Стадия/фильтры сменились (карточку перенесли, поменяли фильтр), либо
  // счётчик колонки изменился (карточку перенесли В НЕЁ/ИЗ НЕЁ, создали новую) —
  // старая догрузка «Показать ещё» больше не актуальна, начинаем набор страниц
  // заново. Без col.count в зависимостях перенесённая карточка осталась бы
  // «фантомом» в исходной колонке — тот же приём, что и в RenewalColumn.
  useEffect(() => {
    setPage(1);
    setRows([]);
  }, [col.stage_id, filtersKey, col.count]);

  // Копим страницы в состоянии колонки: первая страница ЗАМЕНЯЕТ список (новый
  // запрос из-за смены фильтра/стадии тоже приходит с page=1), следующие —
  // доклеиваются кнопкой «Показать ещё». Дедуп по id — на случай, если карточка
  // успела попасть в обе выдачи между запросами.
  useEffect(() => {
    if (!data) return;
    setRows((prev) => {
      if (data.page <= 1) return data.rows;
      const known = new Set(prev.map((r) => r.id));
      return [...prev, ...data.rows.filter((r) => !known.has(r.id))];
    });
  }, [data]);

  // Буфер переименования синхронизируем с сервером только по смене самой стадии
  // или её названия — не на каждый рефетч: иначе набираемый текст затирался бы
  // фоновой инвалидацией (тот же повод, что у буфера полей в TaskDrawer).
  useEffect(() => {
    setLabel(col.label);
    setRenaming(false);
  }, [col.stage_id, col.label]);

  // total — из самого пагинированного ответа (учитывает filters); col.count
  // (из useTaskColumns) фильтры не знает и годится только как счётчик в шапке.
  const total = data?.total ?? col.count;
  const hasMore = rows.length < total;
  const loadingMore = isFetching && page > 1;

  const handleQuickAddKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      setQuickTitle('');
      setAdding(false);
      return;
    }
    if (e.key !== 'Enter') return;
    const title = quickTitle.trim();
    if (!title) return;
    create.mutate(
      { board_id: boardId, title, stage_id: col.stage_id },
      {
        // Поле остаётся открытым: задачи заводят пачками, и повторный клик по
        // ссылке после каждой был бы лишним шагом.
        onSuccess: () => setQuickTitle(''),
        onError: (err) => showError(err, 'Не удалось создать задачу'),
      },
    );
  };

  const handleRenameBlur = () => {
    setRenaming(false);
    const trimmed = label.trim();
    // Пустое название стадии бэкенд не примет, а молча отправлять его — значит
    // ловить 400 на действии, которого человек не совершал: возвращаем прежнее.
    if (!trimmed) { setLabel(col.label); return; }
    if (trimmed === col.label) return;
    stageM.update.mutate(
      { id: col.stage_id, label: trimmed },
      {
        onError: (err) => {
          setLabel(col.label); // откат к серверному названию
          showError(conflictError(err), 'Не удалось переименовать стадию');
        },
      },
    );
  };

  const handleRenameKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      (e.target as HTMLInputElement).blur(); // сохранение — в onBlur, как у заголовка задачи
      return;
    }
    if (e.key === 'Escape') {
      // Возвращаем буфер ДО закрытия поля: onBlur (если браузер его пришлёт)
      // увидит прежнее название и ничего не отправит.
      setLabel(col.label);
      setRenaming(false);
    }
  };

  const stageOrder = (stages || []).map((s) => s.id);
  const stageIndex = stageOrder.indexOf(col.stage_id);

  /** Обмен местами с соседом. Отправляем ВЕСЬ порядок воронки — неполный набор бэкенд отклоняет. */
  const shift = (delta: number) => {
    const target = stageIndex + delta;
    if (stageIndex < 0 || target < 0 || target >= stageOrder.length) return;
    const next = [...stageOrder];
    [next[stageIndex], next[target]] = [next[target], next[stageIndex]];
    stageM.reorder.mutate(
      { boardId, order: next },
      {
        onSuccess: () => toast(`Стадия «${col.label}» переставлена`, 'ok'),
        onError: (err) => showError(conflictError(err), 'Не удалось переставить стадию'),
      },
    );
  };

  const handleRemove = () => {
    stageM.remove.mutate(col.stage_id, {
      onSuccess: () => {
        setConfirmRemove(false);
        toast(`Стадия «${col.label}» удалена`, 'ok');
      },
      onError: (err) => {
        setConfirmRemove(false);
        showError(conflictError(err), 'Не удалось удалить стадию');
      },
    });
  };

  // Меню собирается здесь, а не в BoardColumnHead: шапка общая с «Продлениями»,
  // и специфика раздела приходит в неё слотом actions.
  const menuItems: ActionMenuItem[] = [
    {
      label: 'Переименовать',
      onSelect: () => {
        setLabel(col.label);
        // Поле подменяет плашку не сразу, а следующим кадром: закрывая меню,
        // Radix возвращает фокус на свой триггер. Смени мы плашку синхронно,
        // триггера в DOM уже не было бы — фокус ушёл бы на body, и поле
        // получило бы blur сразу после autoFocus, то есть правка закрылась бы
        // в тот же миг, как открылась.
        requestAnimationFrame(() => setRenaming(true));
      },
    },
    { label: 'Настройки', onSelect: () => setSettingsOpen(true) },
  ];
  // У крайней колонки соответствующего пункта нет — он бы ничего не делал.
  if (stageIndex > 0) menuItems.push({ label: 'Сдвинуть влево', onSelect: () => shift(-1) });
  if (stageIndex >= 0 && stageIndex < stageOrder.length - 1) {
    menuItems.push({ label: 'Сдвинуть вправо', onSelect: () => shift(1) });
  }
  menuItems.push({ label: 'Удалить стадию', danger: true, onSelect: () => setConfirmRemove(true) });

  return (
    <div
      ref={setNodeRef}
      className={`task-col${isOver ? ' task-col--over' : ''}`}
    >
      {/* Поле переименования ЗАМЕЩАЕТ плашку стадии, а не живёт внутри неё: на
          цветной заливке инпут читался бы хуже, чем на фоне колонки (тот же
          приём, что у свёрнутого поиска в RenewalColumn). */}
      {renaming ? (
        <div className="task-col__rename">
          <TextInput
            className="task-col__rename-input"
            value={label}
            autoFocus
            onChange={(e) => setLabel(e.target.value)}
            onBlur={handleRenameBlur}
            onKeyDown={handleRenameKey}
            aria-label={`Название стадии «${col.label}»`}
          />
        </div>
      ) : (
        <BoardColumnHead
          label={col.label}
          count={col.count}
          color={col.color}
          actions={canEditStages ? (
            <ActionMenu items={menuItems} label={`Действия со стадией «${col.label}»`} />
          ) : undefined}
        />
      )}

      {/* aria-live: колонка перерисовывается от смены фильтра/страницы, без
          объявления незрячий пользователь не узнаёт, что список сменился. */}
      <div className="task-col__body" aria-live="polite">
        {isLoading ? (
          <div className="task-col__note">Загружаем…</div>
        ) : rows.length === 0 ? (
          <EmptyState hint="Перетащите сюда карточку или добавьте задачу ниже">
            Здесь пока пусто
          </EmptyState>
        ) : (
          rows.map((row) => (
            <TaskCard key={row.id} task={row} stageId={col.stage_id} onOpen={onOpen} />
          ))
        )}
      </div>

      {hasMore && (
        <button
          type="button"
          className="btn ghost sm task-col__more"
          disabled={loadingMore}
          onClick={() => setPage((p) => p + 1)}
        >
          {loadingMore ? 'Загружаем…' : `Показать ещё (${total - rows.length})`}
        </button>
      )}

      {/* Задача не может родиться закрытой — бэкенд отклонит создание в стадии
          category='closed', поэтому в такой колонке добавлять нечего. */}
      {col.category !== 'closed' && (
        adding ? (
          <TextInput
            className="task-col__quick-add"
            value={quickTitle}
            autoFocus
            onChange={(e) => setQuickTitle(e.target.value)}
            onKeyDown={handleQuickAddKey}
            onBlur={() => { if (!quickTitle.trim()) setAdding(false); }}
            placeholder="Название задачи…"
            disabled={create.isPending}
            aria-label={`Добавить задачу в стадию «${col.label}»`}
          />
        ) : (
          <button
            type="button"
            className="task-col__add"
            onClick={() => setAdding(true)}
          >
            + Добавить задачу
          </button>
        )
      )}

      {settingsOpen && (
        <StageSettingsDialog
          boardId={boardId}
          stage={{
            id: col.stage_id, label: col.label, color: col.color, category: col.category,
          }}
          onClose={() => setSettingsOpen(false)}
        />
      )}

      {confirmRemove && (
        <ConfirmModal
          title="Удалить стадию?"
          message={`Стадия «${col.label}» будет удалена безвозвратно. Если на ней есть карточки или это последняя стадия своего вида, бэкенд откажет.`}
          confirmLabel="Удалить"
          danger
          isPending={stageM.remove.isPending}
          onConfirm={handleRemove}
          onClose={() => setConfirmRemove(false)}
        />
      )}
    </div>
  );
}
