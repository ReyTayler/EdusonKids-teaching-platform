import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../lib/api';

export type SaveState = 'idle' | 'saving' | 'saved' | 'error' | 'conflict';

/** Пауза в наборе, после которой уходит запрос. */
const DELAY = 1000;

/**
 * Потолок ожидания: дольше этого работа не остаётся несохранённой, сколько бы
 * человек ни печатал без остановки.
 */
const MAX_WAIT = 5000;

/**
 * Автосохранение с задержкой и потолком ожидания.
 *
 * Правила, из которых состоит вся сложность:
 *
 * 1. Запрос уходит через секунду после ПОСЛЕДНЕГО изменения, а не каждую
 *    секунду: иначе набор абзаца превращается в два десятка запросов.
 * 2. Но не позже MAX_WAIT от ПЕРВОГО неотправленного изменения. Одной только
 *    паузы мало: пока человек печатает не останавливаясь, паузы не наступает
 *    никогда — и запрос не уходит вовсе. Именно в этот момент вкладку и
 *    закрывают. Второй таймер не сбрасывается новыми правками и потому даёт
 *    гарантию: несохранённой работы не бывает старше пяти секунд.
 * 3. Пока запрос в полёте, новые правки не отправляются параллельно — они
 *    ждут его завершения и уходят одним следующим. Параллельные PATCH одного
 *    документа приходят на сервер в непредсказуемом порядке, и последним
 *    записанным может оказаться не последнее состояние.
 * 4. Конфликт (409) останавливает автосохранение совсем. Повторять попытку
 *    бессмысленно: документ изменили в другом месте, и следующий запрос либо
 *    снова получит отказ, либо затрёт чужую работу.
 * 5. Сокрытие вкладки — сигнал сохранить немедленно. Это последний надёжный
 *    момент: браузеры не обещают, что при закрытии успеет отработать
 *    beforeunload, особенно на мобильных.
 *
 * Состояние возвращается наружу, потому что без кнопки «Сохранить» оно —
 * единственный признак, что текст не потерян.
 */
export function useAutosave<T>(save: (payload: T) => Promise<void>) {
  const [state, setState] = useState<SaveState>('idle');
  const [pending, setPending] = useState(false);
  // Причина отказа. Сервер объясняет его по-русски («Неподдерживаемый узел…»),
  // и без этого текста автор видит только «Не сохранено» — то есть знает, что
  // работа не уходит, но не знает, что убрать из документа.
  const [reason, setReason] = useState<string | null>(null);

  const timer = useRef<number | null>(null);
  // Второй таймер живёт своей жизнью: ставится на первое изменение и НЕ
  // перезапускается следующими. Сбрасывать его вместе с первым значило бы
  // вернуть исходную дыру — при непрерывном наборе он тоже никогда не сработал
  // бы.
  const maxTimer = useRef<number | null>(null);
  const queued = useRef<T | null>(null);
  const inFlight = useRef(false);
  const stopped = useRef(false);
  // Держим в ref: функция сохранения пересоздаётся на каждый рендер страницы,
  // и от неё нельзя зависеть — таймер перезапускался бы бесконечно.
  const saveRef = useRef(save);
  saveRef.current = save;

  /** Снять оба таймера — очередь либо уходит на сервер, либо больше не нужна. */
  const clearTimers = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    if (maxTimer.current !== null) {
      window.clearTimeout(maxTimer.current);
      maxTimer.current = null;
    }
  }, []);

  const run = useCallback(async () => {
    if (inFlight.current || stopped.current) return;
    const payload = queued.current;
    if (payload === null) return;

    // Очередь уходит на сервер — оба таймера отсчитывают заново со следующей
    // правки. Иначе потолок продолжал бы тикать от уже сохранённого изменения.
    clearTimers();
    queued.current = null;
    inFlight.current = true;
    setState('saving');
    try {
      await saveRef.current(payload);
      setState('saved');
      setReason(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        stopped.current = true;
        setState('conflict');
        setReason(null);
      } else {
        setState('error');
        setReason(err instanceof ApiError ? err.message : null);
      }
      // Данные не потеряны: они остались в полях формы. Возвращать их в
      // очередь нельзя — на ошибке валидации это дало бы бесконечный цикл
      // отправки одного и того же отвергнутого документа.
    } finally {
      inFlight.current = false;
      // Пока запрос летел, могли добавиться новые правки — отправляем их.
      if (queued.current !== null && !stopped.current) void run();
      else setPending(queued.current !== null);
    }
  }, [clearTimers]);

  const schedule = useCallback((payload: T) => {
    if (stopped.current) return;
    queued.current = payload;
    setPending(true);

    // Обычный отсчёт — от последней правки, поэтому перезапускается.
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      void run();
    }, DELAY);

    // Потолок — от первой неотправленной правки, поэтому ставится только раз.
    if (maxTimer.current === null) {
      maxTimer.current = window.setTimeout(() => {
        maxTimer.current = null;
        void run();
      }, MAX_WAIT);
    }
  }, [run]);

  /** Сохранить немедленно — при выходе из режима правки и при сокрытии вкладки. */
  const flush = useCallback(() => {
    clearTimers();
    void run();
  }, [clearTimers, run]);

  /**
   * Скрытая вкладка — последняя точка, где сохранение ещё гарантированно
   * успевает. На закрытие вкладки полагаться нельзя: браузеры не обещают, что
   * beforeunload и unload вообще отработают, а на мобильных приложение часто
   * убивают без единого из этих событий. pagehide добавлен рядом — он
   * закрывает случаи, до которых visibilitychange не доходит.
   */
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === 'hidden') flush();
    };
    document.addEventListener('visibilitychange', onHide);
    window.addEventListener('pagehide', flush);
    return () => {
      document.removeEventListener('visibilitychange', onHide);
      window.removeEventListener('pagehide', flush);
    };
  }, [flush]);

  // Уход со страницы не должен оставлять запланированное сохранение висеть.
  useEffect(() => () => clearTimers(), [clearTimers]);

  return { state, pending, reason, schedule, flush };
}
