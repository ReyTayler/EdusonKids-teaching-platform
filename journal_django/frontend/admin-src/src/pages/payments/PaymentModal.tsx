import { useEffect, useMemo, useState } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { Combobox } from '../../components/form/Combobox';
import { NumberInput } from '../../components/form/NumberInput';
import { DateInput } from '../../components/form/DateInput';
import { Textarea } from '../../components/form/Textarea';
import { Checkbox } from '../../components/form/Checkbox';
import { useStudentsAll } from '../../hooks/useStudents';
import { useDirections } from '../../hooks/useDirections';
import { usePayments, usePaymentMutations } from '../../hooks/usePayments';
import { useDiscounts } from '../../hooks/useDiscounts';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { fmtRub, todayMSK, studentOptionLabel } from '../../lib/format';
import type { Discount } from '../../lib/types';
import { BlockSelector } from './BlockSelector';

interface Props {
  open: boolean;
  onClose: () => void;
  studentId?: number;
  directionId?: number;
}

interface FormErrors {
  student?: string;
  direction?: string;
  count?: string;
  price?: string;
  date?: string;
}

const FILL_FIELD = 'Заполните поле';

export function PaymentModal({ open, onClose, studentId, directionId }: Props) {
  const students = useStudentsAll();
  const directions = useDirections();
  const muts = usePaymentMutations();
  const showError = useApiError();
  const { toast } = useToast();

  const [stId, setStId] = useState<number | undefined>(studentId);
  const [dirId, setDirId] = useState<number | undefined>(directionId);
  const [count, setCount] = useState(0);
  const [customPriceOpen, setCustomPriceOpen] = useState(false);
  const [customPrice, setCustomPrice] = useState<number | ''>('');
  const [paidAt, setPaidAt] = useState(todayMSK());
  const [note, setNote] = useState('');
  const [errors, setErrors] = useState<FormErrors>({});
  const [discountIds, setDiscountIds] = useState<number[]>([]);
  // 'blocks' — целые абонементы по 4 урока; 'lessons' — поштучно, любое число уроков
  // в пределах свободного остатка курса (с 2026-10-14; раньше — только 1–3).
  const [mode, setMode] = useState<'blocks' | 'lessons'>('blocks');
  const [pieceLessons, setPieceLessons] = useState<number | ''>('');
  const [priceMode, setPriceMode] = useState<'per_block' | 'total'>('per_block');
  const [totalInput, setTotalInput] = useState<number | ''>('');
  const [pieceSum, setPieceSum] = useState<number | ''>(''); // сумма за уроки поштучно (ручная; дефолт — пропорционально)
  // Доплата сверх курса (kind='extra'): деньги в реальном направлении, но МИМО
  // лимита курса. Для случая, когда все абонементы оплачены, а нужен доп.урок.
  const [isExtra, setIsExtra] = useState(false);

  // Reset при открытии
  useEffect(() => {
    if (open) {
      setStId(studentId);
      setDirId(directionId);
      setCount(0);
      setCustomPriceOpen(false);
      setCustomPrice('');
      setPaidAt(todayMSK());
      setNote('');
      setErrors({});
      setDiscountIds([]);
      setMode('blocks');
      setPieceLessons('');
      setPriceMode('per_block');
      setTotalInput('');
      setPieceSum('');
      setIsExtra(false);
    }
  }, [open, studentId, directionId]);

  // Единая сумма применима только для покупки 2+ блоков.
  useEffect(() => {
    if (mode !== 'blocks' || count < 2) setPriceMode('per_block');
  }, [mode, count]);

  const existing = usePayments({ student_id: stId, direction_id: dirId });
  const alreadyPurchased = useMemo(() => {
    if (!existing.data) return 0;
    return existing.data.reduce((s, p) => s + Number(p.subscriptions_count), 0);
  }, [existing.data]);
  const alreadyPurchasedLessons = useMemo(() => {
    if (!existing.data) return 0;
    return existing.data.reduce((s, p) => s + Number(p.lessons_count ?? 0), 0);
  }, [existing.data]);

  const direction = useMemo(
    () => directions.data?.find((d) => d.id === dirId),
    [directions.data, dirId],
  );
  // Авто-раскрытие custom при отсутствии цены
  useEffect(() => {
    if (direction && (direction.subscription_price === null || direction.subscription_price === undefined)) {
      setCustomPriceOpen(true);
    }
  }, [direction]);

  const basePrice = direction?.subscription_price != null ? Number(direction.subscription_price) : null;
  const noCapacity = !!direction && (!direction.total_lessons || direction.total_lessons <= 0);

  // Скидки
  const discounts = useDiscounts(); // только активные по дефолту

  const selectedDiscounts: Discount[] = useMemo(() => {
    if (!discounts.data) return [];
    return discountIds
      .map((id) => discounts.data!.find((d) => d.id === id))
      .filter((d): d is Discount => Boolean(d));
  }, [discounts.data, discountIds]);

  const totalDiscountAmount = useMemo(() => {
    return selectedDiscounts.reduce((s, d) => s + Number(d.amount), 0);
  }, [selectedDiscounts]);

  const discountsApplicable = mode === 'blocks' && count === 1;

  // База, к которой применяются скидки: custom при раскрытом поле, иначе цена направления.
  const priceBeforeDiscount = useMemo(() => {
    if (customPriceOpen) return typeof customPrice === 'number' ? customPrice : 0;
    return basePrice ?? 0;
  }, [customPriceOpen, customPrice, basePrice]);

  // Финальная unitPrice с учётом скидок
  const computedUnitPrice = useMemo(() => {
    if (!discountsApplicable || selectedDiscounts.length === 0) return priceBeforeDiscount;
    const factor = Math.max(0, 1 - totalDiscountAmount);
    // Округляем до копеек.
    return Math.round(priceBeforeDiscount * factor * 100) / 100;
  }, [priceBeforeDiscount, discountsApplicable, selectedDiscounts.length, totalDiscountAmount]);

  const perLesson = basePrice != null ? Math.round((basePrice / 4) * 100) / 100 : 0;
  const pieceCount = typeof pieceLessons === 'number' ? pieceLessons : 0;
  const lessonsCount = mode === 'blocks' ? count * 4 : pieceCount;
  const total =
    mode === 'lessons'
      ? (typeof pieceSum === 'number' ? pieceSum : 0)
      : priceMode === 'total'
        ? (typeof totalInput === 'number' ? totalInput : 0)
        : computedUnitPrice * count;
  const derivedPerBlock =
    priceMode === 'total' && count > 0 && typeof totalInput === 'number'
      ? Math.round((totalInput / count) * 100) / 100
      : null;

  // Ёмкость направления считается в УРОКАХ — уроки поштучно тоже занимают ёмкость.
  const looseLessons = Math.max(0, alreadyPurchasedLessons - alreadyPurchased * 4);
  const availableLessons = Math.max(0, (direction?.total_lessons ?? 0) - alreadyPurchasedLessons);
  const maxAdditionalBlocks = Math.floor(availableLessons / 4);

  const studentOptions = useMemo(() => {
    if (!students.data) return [];
    return students.data
      .slice()
      .sort((a, b) => a.full_name.localeCompare(b.full_name))
      .map((s) => ({
        value: String(s.id),
        label: studentOptionLabel(s),
      }));
  }, [students.data]);

  const directionOptions = useMemo(() => {
    if (!directions.data) return [];
    return directions.data
      .filter((d) => d.active)
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((d) => ({
        value: String(d.id),
        label: d.total_lessons ? d.name : `${d.name} (курс не задан)`,
      }));
  }, [directions.data]);

  // ── Helpers для очистки конкретной ошибки при правке поля. ──
  const clearError = (key: keyof FormErrors) =>
    setErrors((prev) => ({ ...prev, [key]: undefined }));

  const validate = (): FormErrors => {
    const e: FormErrors = {};
    if (!stId) e.student = FILL_FIELD;
    if (!dirId) e.direction = FILL_FIELD;
    else if (noCapacity) e.direction = 'У направления не задан total_lessons';
    if (dirId && !noCapacity) {
      // Доплата сверх курса (isExtra) намеренно ИДЁТ мимо лимита — cap не проверяем.
      if (mode === 'lessons') {
        if (!Number.isInteger(pieceCount) || pieceCount < 1) e.count = FILL_FIELD;
        else if (!isExtra && alreadyPurchasedLessons + pieceCount > (direction?.total_lessons ?? 0)) e.count = 'Превышена ёмкость курса';
      } else {
        if (count < 1) e.count = FILL_FIELD;
        else if (!isExtra && alreadyPurchasedLessons + count * 4 > (direction?.total_lessons ?? 0)) e.count = 'Превышена ёмкость курса';
      }
    }
    if (!paidAt) e.date = FILL_FIELD;
    if (mode === 'lessons') {
      if (typeof pieceSum !== 'number' || pieceSum <= 0) e.price = FILL_FIELD;
    } else if (mode === 'blocks' && count >= 2 && priceMode === 'total') {
      if (typeof totalInput !== 'number' || totalInput <= 0) e.price = FILL_FIELD;
    } else if (customPriceOpen && customPrice === '') {
      e.price = FILL_FIELD;
    }
    return e;
  };

  const handleSubmit = async () => {
    const e = validate();
    setErrors(e);
    if (Object.keys(e).length > 0) return;

    let finalNote = note.trim();
    if (selectedDiscounts.length > 0 && discountsApplicable) {
      const discountLine = 'Скидки: ' + selectedDiscounts
        .map((d) => `${d.name} (−${(Number(d.amount) * 100).toFixed(Number.isInteger(Number(d.amount) * 100) ? 0 : 1)}%)`)
        .join(', ');
      finalNote = finalNote ? `${discountLine}\n${finalNote}` : discountLine;
    }

    try {
      await muts.create.mutateAsync({
        student_id: stId!,
        direction_id: dirId!,
        lessons_count: lessonsCount,
        total_amount: total,
        paid_at: paidAt,
        note: finalNote || null,
        ...(isExtra ? { kind: 'extra' as const } : {}),
      });
      toast(`Оплата внесена: ${fmtRub(total)}`, 'ok');
      onClose();
    } catch (err) {
      showError(err);
    }
  };

  // ── Зона блок-селектора: три состояния. ──
  const renderBlocksArea = () => {
    if (!dirId || !direction) {
      return (
        <div className="payment-form__placeholder">
          Сначала выберите ученика и направление — здесь появится выбор блоков.
        </div>
      );
    }
    if (noCapacity) {
      return (
        <div className="payment-form__warn">
          У направления «{direction.name}» не задан total_lessons. Настройте курс в разделе
          «Направления», чтобы продавать абонементы.
        </div>
      );
    }
    return (
      <>
        <div className="payment-form__hint">
          Уже куплено: {alreadyPurchased} аб.{looseLessons > 0 ? ` + ${looseLessons} уроков поштучно` : ''} = {alreadyPurchasedLessons} из {direction.total_lessons} уроков курса; свободно {availableLessons}.
        </div>
        <div className="payment-form__segment" role="tablist">
          <button type="button" role="tab" aria-selected={mode === 'blocks'}
            className={`seg${mode === 'blocks' ? ' seg--on' : ''}`}
            onClick={() => { setMode('blocks'); setPieceLessons(''); setPieceSum(''); clearError('count'); }}>
            Блоки по 4
          </button>
          <button type="button" role="tab" aria-selected={mode === 'lessons'}
            className={`seg${mode === 'lessons' ? ' seg--on' : ''}`}
            onClick={() => { setMode('lessons'); setCount(0); setDiscountIds([]); clearError('count'); }}>
            Поштучно
          </button>
        </div>
        {mode === 'lessons' ? (
          <Field label="Количество уроков" error={errors.count}>
            <div className="payment-form__price-row">
              <NumberInput
                value={pieceLessons}
                min={1}
                max={isExtra ? undefined : availableLessons}
                step={1}
                onChange={(e) => {
                  const n = e.target.value === '' ? '' : Math.trunc(Number(e.target.value));
                  setPieceLessons(n);
                  // Дефолт суммы — пропорционально цене абонемента; пользователь может изменить.
                  setPieceSum(typeof n === 'number' && n > 0 && perLesson > 0
                    ? Math.round(perLesson * n * 100) / 100 : '');
                  clearError('count');
                }}
                style={{ width: 120 }}
              />
              {!isExtra && <span className="muted">свободно {availableLessons}</span>}
            </div>
          </Field>
        ) : (
          <Field label="Блоки (4 урока в блоке)" error={errors.count}>
            <BlockSelector
              totalSubscriptions={alreadyPurchased + maxAdditionalBlocks}
              alreadyPurchased={alreadyPurchased}
              selected={count}
              color={direction.color}
              onChange={(n) => {
                setCount(n);
                clearError('count');
                if (n > 1) setDiscountIds([]);
              }}
            />
          </Field>
        )}
      </>
    );
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="Внести оплату" wide>
      <div className="payment-form">
        <Field label="Ученик" error={errors.student}>
          <Combobox
            value={stId !== undefined ? String(stId) : ''}
            onChange={(v) => { setStId(v ? Number(v) : undefined); clearError('student'); }}
            options={studentOptions}
            placeholder="Начни вводить имя..."
            maxVisible={10}
          />
        </Field>

        <Field label="Направление" error={errors.direction}>
          <Combobox
            value={dirId !== undefined ? String(dirId) : ''}
            onChange={(v) => {
              setDirId(v ? Number(v) : undefined);
              setCount(0);
              clearError('direction');
              clearError('count');
            }}
            options={directionOptions}
            placeholder="Выберите направление"
            maxVisible={10}
          />
        </Field>

        {renderBlocksArea()}

        {dirId && !noCapacity && (
          <Field label="">
            <Checkbox
              checked={isExtra}
              onChange={(e) => { setIsExtra(e.target.checked); clearError('count'); }}
              label="Доплата сверх курса (мимо лимита)"
            />
            {isExtra && (
              <div className="payment-form__hint" style={{ fontStyle: 'italic' }}>
                Оплата за доп.урок сверх курса — в это направление, но без учёта лимита.
                Для отдельных уроков удобнее режим «Поштучно».
              </div>
            )}
          </Field>
        )}

        <Field label="Дата оплаты" error={errors.date}>
          <DateInput
            value={paidAt}
            onChange={(e) => { setPaidAt(e.target.value); clearError('date'); }}
          />
        </Field>

        <Field label={mode === 'lessons' ? 'Сумма за уроки' : 'Цена за абонемент'} error={errors.price}>
          {mode === 'lessons' ? (
            <div className="payment-form__price-row">
              <NumberInput
                value={pieceSum}
                min={0}
                step="0.01"
                onChange={(e) => { setPieceSum(e.target.value === '' ? '' : Number(e.target.value)); clearError('price'); }}
                style={{ width: 160 }}
              />
              <span>₽{pieceCount > 0 ? ` за ${pieceCount} ур.` : ''}</span>
              {basePrice != null && pieceCount > 0 && (
                <span className="muted">по умолчанию {fmtRub(Math.round(perLesson * pieceCount * 100) / 100)} ({fmtRub(perLesson)}/урок)</span>
              )}
            </div>
          ) : (
          <>
          {mode === 'blocks' && count >= 2 && (
            <div className="payment-form__segment" role="tablist" style={{ marginBottom: 8 }}>
              <button type="button" role="tab" aria-selected={priceMode === 'per_block'}
                className={`seg${priceMode === 'per_block' ? ' seg--on' : ''}`}
                onClick={() => setPriceMode('per_block')}>За абонемент</button>
              <button type="button" role="tab" aria-selected={priceMode === 'total'}
                className={`seg${priceMode === 'total' ? ' seg--on' : ''}`}
                onClick={() => setPriceMode('total')}>Единой суммой</button>
            </div>
          )}
          {mode === 'blocks' && count >= 2 && priceMode === 'total' ? (
            <div className="payment-form__price-row">
              <NumberInput
                value={totalInput}
                min={0}
                step="0.01"
                onChange={(e) => { setTotalInput(e.target.value === '' ? '' : Number(e.target.value)); clearError('price'); }}
                style={{ width: 160 }}
              />
              <span>₽ за все {count} абонемента</span>
              {derivedPerBlock != null && (
                <span className="muted">≈ {fmtRub(derivedPerBlock)}/абонемент</span>
              )}
            </div>
          ) : !customPriceOpen ? (
            <div className="payment-form__price-row">
              <span className="payment-form__price">
                {basePrice != null ? fmtRub(basePrice) : 'не настроена'}
              </span>
              <button
                type="button"
                className="btn-link"
                onClick={() => {
                  setCustomPriceOpen(true);
                  setCustomPrice(basePrice ?? '');
                }}
              >
                + Задать свою сумму за абонемент
              </button>
            </div>
          ) : (
            <div className="payment-form__price-row">
              <NumberInput
                value={customPrice}
                min={0}
                step="0.01"
                onChange={(e) => {
                  setCustomPrice(e.target.value === '' ? '' : Number(e.target.value));
                  clearError('price');
                }}
                style={{ width: 160 }}
              />
              <span>₽</span>
              {basePrice != null && (
                <button
                  type="button"
                  className="btn-link"
                  onClick={() => { setCustomPriceOpen(false); setCustomPrice(''); clearError('price'); }}
                >
                  × Вернуть базовую цену
                </button>
              )}
            </div>
          )}
          </>
          )}
        </Field>

        <Field label={`Скидки${selectedDiscounts.length > 0 ? ` (применено ${selectedDiscounts.length})` : ''}`}>
          {!discountsApplicable ? (
            <div className="payment-form__hint" style={{ fontStyle: 'italic' }}>
              Скидки применяются только при покупке 1 абонемента за раз.
              {count > 1 && ' Если нужно несколько со скидками — внеси оплату несколько раз.'}
            </div>
          ) : (
            <div className="discounts-picker">
              {discountIds.map((id, idx) => {
                const usedIds = new Set(discountIds.filter((_, i) => i !== idx));
                const options = (discounts.data || [])
                  .filter((d) => d.id === id || !usedIds.has(d.id))
                  .map((d) => ({
                    value: String(d.id),
                    label: `${d.name} (−${(Number(d.amount) * 100).toFixed(Number.isInteger(Number(d.amount) * 100) ? 0 : 1)}%)`,
                  }));
                return (
                  <div key={idx} className="discounts-picker__row">
                    <div style={{ flex: 1 }}>
                      <Combobox
                        value={String(id)}
                        onChange={(v) => {
                          const newId = Number(v);
                          if (customPriceOpen) {
                            setCustomPriceOpen(false);
                            setCustomPrice('');
                          }
                          setDiscountIds((prev) => prev.map((x, i) => i === idx ? newId : x));
                        }}
                        options={options}
                        placeholder="Выберите скидку"
                        maxVisible={10}
                      />
                    </div>
                    <button
                      type="button"
                      className="btn-link"
                      style={{ color: 'var(--bad)' }}
                      onClick={() => setDiscountIds((prev) => prev.filter((_, i) => i !== idx))}
                      aria-label="Убрать скидку"
                    >×</button>
                  </div>
                );
              })}

              {(() => {
                const usedIds = new Set(discountIds);
                const availableCount = (discounts.data || []).filter((d) => !usedIds.has(d.id)).length;
                if (availableCount === 0) {
                  return discountIds.length === 0 ? (
                    <div className="payment-form__hint" style={{ fontStyle: 'italic' }}>
                      Нет настроенных скидок. Добавь их в разделе «Абонементы → Скидки».
                    </div>
                  ) : null;
                }
                return (
                  <button
                    type="button"
                    className="btn-link"
                    onClick={() => {
                      const first = (discounts.data || []).find((d) => !usedIds.has(d.id));
                      if (first) setDiscountIds((prev) => [...prev, first.id]);
                    }}
                  >
                    + Добавить скидку
                  </button>
                );
              })()}

              {selectedDiscounts.length > 0 && (
                <div className="discounts-picker__summary">
                  Суммарная скидка: <strong>−{(totalDiscountAmount * 100).toFixed(Number.isInteger(totalDiscountAmount * 100) ? 0 : 1)}%</strong>
                  {' = '}
                  <strong>{fmtRub(computedUnitPrice)}</strong>
                  <span className="muted"> вместо {fmtRub(priceBeforeDiscount)}</span>
                </div>
              )}
            </div>
          )}
        </Field>

        <Field label="Комментарий">
          <Textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
        </Field>

        <div className="payment-form__total">
          Итого: <strong>{fmtRub(total)}</strong>
          {mode === 'blocks' && count > 0 && priceMode === 'total' && derivedPerBlock != null &&
            ` (${count} аб. × ${fmtRub(derivedPerBlock)})`}
          {mode === 'blocks' && count > 0 && priceMode === 'per_block' &&
            ` (${count} аб. × ${fmtRub(computedUnitPrice)})`}
          {mode === 'lessons' && pieceCount > 0 && total > 0 &&
            ` (${pieceCount} ур. × ${fmtRub(Math.round((total / pieceCount) * 100) / 100)})`}
        </div>

        <div className="payment-form__footer">
          <button type="button" className="btn-cancel" onClick={onClose}>Отмена</button>
          <button
            type="button"
            className="btn-save"
            onClick={() => { void handleSubmit(); }}
            disabled={muts.create.isPending}
          >
            Внести оплату
          </button>
        </div>
      </div>
    </Dialog>
  );
}
