"""Валидатор переходов между стадиями по их виду (kind), признаку is_auto и ключу."""
from __future__ import annotations


class InvalidTransition(Exception):
    """Недопустимый переход стадии."""


_TERMINAL = {'won', 'lost'}

# «Ждём продление» — единственная авто-стадия, с которой менеджер уходит РУКАМИ
# (подтверждение/отклонение продления). Прочие авто-стадии двигает только движок.
AWAITING_RENEWAL_KEY = 'awaiting_renewal'

# Ключ стадии «Заморожен». Сама по себе правил перехода больше не задаёт —
# «пауза» стала свойством стадии (allow_mid_cycle, см. _is_pause_target).
# Ключ остался нужен там, где заморозка отличается ОБВЯЗКОЙ, а не правилами:
# у неё есть срок («до какого месяца», repository.move_deal).
FROZEN_KEY = 'frozen'


def _is_pause_target(to_kind: str, to_allow_mid_cycle: bool) -> bool:
    """Стадия-«пауза» как цель перехода — и только пока она ПРОМЕЖУТОЧНАЯ.

    Пауза — это не решение о продлении, поэтому ей даны обе поблажки: уход с
    авто-стадии и вход посреди незавершённого цикла. Так живёт «Заморожен», и
    так же — любая стадия, которой superadmin поставил allow_mid_cycle
    (например «Закончил курс»: ученик доучился до конца курса, а решение —
    перевод на другой курс или уход — принимается позже).

    Требование kind='decision' закрывает лазейку: superadmin может через
    настройку стадий сменить вид такой стадии на 'won'/'lost', и без этой
    проверки флаг открыл бы путь ЗАКРЫТЬ сделку (в том числе как «Продлён»
    при нулевом балансе), минуя ворота ниже.
    """
    return to_allow_mid_cycle and to_kind == 'decision'


def _is_auto_exit_target(to_kind: str, to_allow_mid_cycle: bool) -> bool:
    """Цели, уход к которым разрешён с авто-стадии прогресса помимо
    «Ждём продление»: стадия-пауза и «Ушёл» — обе случаются в любой момент цикла,
    а не только в точке принятия решения о продлении (решение пользователя
    2026-07-25 приравняло «Ушёл» и заморозку в этом смысле)."""
    return _is_pause_target(to_kind, to_allow_mid_cycle) or to_kind == 'lost'


def is_allowed(*, from_kind: str, to_kind: str,
               from_is_auto: bool = False, to_is_auto: bool = False,
               from_key: str | None = None, to_allow_mid_cycle: bool = False,
               cycle_completed: bool = True, balance: float = 1,
               allow_nonpositive_balance: bool = False) -> bool:
    """
    Авто-стадии (is_auto) двигает движок по событиям. Руками:
    - на авто-стадию встать нельзя (to_is_auto) — прогресс, «Ждём оплату»,
      «Ждём продление». У «Заморожен» с миграции 0012_frozen_manual_stage
      is_auto=False — обычная ручная decision-стадия, этот запрет её не касается;
    - с авто-стадии уйти нельзя (from_is_auto) — КРОМЕ «Ждём продление»
      (from_key == AWAITING_RENEWAL_KEY) и КРОМЕ ухода в стадию-паузу или «Ушёл»
      (_is_auto_exit_target): обе — реакция на обстоятельства ученика
      (болезнь/отпуск, конец курса или решение уйти), не привязаны к точке
      принятия решения о продлении и могут понадобиться с любой прогресс-стадии
      («Урок N»).
    С «Ждём продление» (kind='decision') работают обычные ворота decision-стадий:
    в другую ручную decision / «Продлён» — при завершённом цикле; «Ушёл» — всегда.
    В «Продлён» — дополнительно только при положительном балансе (> 0 уроков):
    без него продление не подкреплено оплатой на следующий цикл. Admin/superadmin
    этот гейт снимают (allow_nonpositive_balance; решение пользователя 2026-09-28:
    продление договорено, оплата придёт позже). Гейт цикла остаётся для всех.

    Решение пользователя 2026-07-19 (послабляет прежний тотальный from_is_auto→False
    от 2026-07-17: без выхода со стадии «Ждём продление» сделку нельзя было закрыть
    как «Продлён»).

    Стадия-пауза посреди незавершённого цикла разрешена решением пользователя
    2026-07-25 (заморозка) — приравнена к «Ушёл»: обе случаются в любой момент,
    а не только после отработанного абонемента; решением 2026-08-06 это правило
    распространено на любую стадию с allow_mid_cycle. Прочие ручные
    decision-стадии («Думает», «Игнорит») по-прежнему требуют завершённого цикла.
    """
    if from_kind in _TERMINAL:
        return False
    if (from_is_auto and from_key != AWAITING_RENEWAL_KEY
            and not _is_auto_exit_target(to_kind, to_allow_mid_cycle)):
        return False
    if to_is_auto:
        return False
    if to_kind == 'progress':
        return False
    if not cycle_completed:
        return to_kind == 'lost' or _is_pause_target(to_kind, to_allow_mid_cycle)
    if to_kind == 'won' and balance <= 0 and not allow_nonpositive_balance:
        return False
    return to_kind in {'decision', 'won', 'lost'}


def assert_allowed(*, from_kind: str, to_kind: str,
                   from_is_auto: bool = False, to_is_auto: bool = False,
                   from_key: str | None = None, to_allow_mid_cycle: bool = False,
                   cycle_completed: bool = True, balance: float = 1,
                   allow_nonpositive_balance: bool = False) -> None:
    if not is_allowed(from_kind=from_kind, to_kind=to_kind,
                      from_is_auto=from_is_auto, to_is_auto=to_is_auto,
                      from_key=from_key, to_allow_mid_cycle=to_allow_mid_cycle,
                      cycle_completed=cycle_completed, balance=balance,
                      allow_nonpositive_balance=allow_nonpositive_balance):
        if to_kind == 'won' and balance <= 0 and not allow_nonpositive_balance:
            raise InvalidTransition(
                'Нельзя отметить как «Продлён»: на балансе ученика должно быть больше 0 уроков')
        raise InvalidTransition(f'Переход {from_kind} → {to_kind} запрещён')
