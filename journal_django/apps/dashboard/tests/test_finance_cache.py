"""
Кэш финансового дашборда (Celery-спека 2026-07-13, фаза B).

get_dashboard читает ВСЕ payments+attendance и считает FIFO по каждому
ученику — самый тяжёлый расчёт системы. Кэшируем результат:
  • ключи включают generation (finance:{gen}:…) — инвалидация = смена generation,
    старые ключи умирают по TTL; работает одинаково на LocMem и Redis,
    без delete_pattern;
  • refresh_dashboard прогревает default-ключ (Celery beat, как реестр);
  • мутации Payment и Lesson сбрасывают кэш через сигналы (on_commit);
    точечные правки посещаемости (bulk-операции без сигналов) покрывает TTL.
Кэш — оптимизация, не источник правды: мёртвый кэш → синхронный расчёт.
"""
from __future__ import annotations

from unittest import mock

import pytest
from django.core.cache import cache
from django.test import override_settings

from apps.dashboard import services as svc

pytestmark = pytest.mark.django_db

_LOCMEM = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'test-finance-cache',
    },
}


# ---------------------------------------------------------------------------
# Кэширование чтения
# ---------------------------------------------------------------------------

@override_settings(CACHES=_LOCMEM)
def test_dashboard_is_cached():
    cache.clear()
    with mock.patch.object(svc, 'get_dashboard', wraps=svc.get_dashboard) as spy:
        first = svc.get_dashboard_cached()
        second = svc.get_dashboard_cached()
    assert spy.call_count == 1  # второй ответ — из кэша
    assert first == second


@override_settings(CACHES=_LOCMEM)
def test_range_and_default_use_distinct_keys():
    cache.clear()
    svc.get_dashboard_cached()
    svc.get_dashboard_cached(from_='2026-01-01', to='2026-01-31')
    default_key = svc._dashboard_key(None, None)
    range_key = svc._dashboard_key('2026-01-01', '2026-01-31')
    assert default_key != range_key
    assert cache.get(default_key) is not None
    assert cache.get(range_key) is not None


@override_settings(CACHES={
    'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'},
})
def test_dashboard_survives_dead_cache():
    body = svc.get_dashboard_cached()
    assert 'revenue_month' in body and 'deferred_total' in body


# ---------------------------------------------------------------------------
# Прогрев (beat) и инвалидация
# ---------------------------------------------------------------------------

@override_settings(CACHES=_LOCMEM)
def test_refresh_dashboard_warms_default_key():
    cache.clear()
    month = svc.refresh_dashboard()
    assert isinstance(month, str) and month
    assert cache.get(svc._dashboard_key(None, None)) is not None


@override_settings(CACHES=_LOCMEM)
def test_invalidate_switches_generation():
    cache.clear()
    svc.get_dashboard_cached()
    key_before = svc._dashboard_key(None, None)
    assert cache.get(key_before) is not None
    svc.invalidate_finance_cache()
    key_after = svc._dashboard_key(None, None)
    assert key_after != key_before          # новая генерация
    assert cache.get(key_after) is None     # свежий ключ пуст → пересчёт


def test_beat_schedule_contains_finance_refresh():
    from django.conf import settings
    entry = settings.CELERY_BEAT_SCHEDULE['refresh-finance-dashboard']
    assert entry['task'] == 'apps.dashboard.tasks.refresh_finance_dashboard'
    assert entry['schedule'] <= svc.DASHBOARD_TTL  # кэш всегда тёплый


# ---------------------------------------------------------------------------
# Сигналы: Payment и Lesson сбрасывают кэш ПОСЛЕ коммита
# ---------------------------------------------------------------------------

@override_settings(CACHES=_LOCMEM)
def test_payment_save_invalidates_finance_cache(django_capture_on_commit_callbacks):
    from django.db.models.signals import post_save
    from apps.payments.models import Payment

    cache.clear()
    svc.get_dashboard_cached()
    key_before = svc._dashboard_key(None, None)
    with django_capture_on_commit_callbacks(execute=True):
        post_save.send(sender=Payment, instance=Payment(), created=True)
    assert svc._dashboard_key(None, None) != key_before


@override_settings(CACHES=_LOCMEM)
def test_lesson_save_invalidates_finance_cache(django_capture_on_commit_callbacks):
    from django.db.models.signals import post_save
    from apps.lessons.models import Lesson

    cache.clear()
    svc.get_dashboard_cached()
    key_before = svc._dashboard_key(None, None)
    with django_capture_on_commit_callbacks(execute=True):
        post_save.send(sender=Lesson, instance=Lesson(), created=True)
    assert svc._dashboard_key(None, None) != key_before


@override_settings(CACHES=_LOCMEM)
def test_lesson_delete_invalidates_finance_cache(django_capture_on_commit_callbacks):
    from django.db.models.signals import post_delete
    from apps.lessons.models import Lesson

    cache.clear()
    svc.get_dashboard_cached()
    key_before = svc._dashboard_key(None, None)
    with django_capture_on_commit_callbacks(execute=True):
        post_delete.send(sender=Lesson, instance=Lesson())
    assert svc._dashboard_key(None, None) != key_before


@override_settings(CACHES=_LOCMEM)
def test_rollback_keeps_finance_cache(django_capture_on_commit_callbacks):
    # Инвалидация отложена на on_commit: откат транзакции кэш не трогает.
    from django.db.models.signals import post_save
    from apps.payments.models import Payment

    cache.clear()
    svc.get_dashboard_cached()
    key_before = svc._dashboard_key(None, None)
    with django_capture_on_commit_callbacks(execute=False):
        post_save.send(sender=Payment, instance=Payment(), created=True)
    assert svc._dashboard_key(None, None) == key_before
    assert cache.get(key_before) is not None


@override_settings(CACHES=_LOCMEM)
def test_invalidate_changes_generation_even_with_frozen_clock():
    """
    Часы Windows имеют разрешение ~15 мс: два вызова time_ns() подряд дают одно
    значение. Генерация обязана расти всё равно — иначе сброс кэша молча
    не срабатывает и сводка показывает старые цифры до истечения TTL.

    Замораживаем часы, чтобы проверить это детерминированно, а не «как повезёт».
    """
    cache.clear()
    with mock.patch('apps.dashboard.services.time.time_ns', return_value=1_000):
        first = svc._dashboard_key(None, None)

        svc.invalidate_finance_cache()
        second = svc._dashboard_key(None, None)
        assert second != first, 'сброс кэша не сменил генерацию'

        svc.invalidate_finance_cache()
        third = svc._dashboard_key(None, None)
        assert third != second, 'повторный сброс не сменил генерацию'


@override_settings(CACHES=_LOCMEM)
def test_generation_never_goes_backwards():
    """
    Часы могут прыгнуть назад (перевод времени, синхронизация NTP). Генерация
    обязана только расти, иначе старые ключи «оживут» и вернут устаревшие данные.
    """
    cache.clear()
    with mock.patch('apps.dashboard.services.time.time_ns', return_value=5_000):
        svc._dashboard_key(None, None)
        svc.invalidate_finance_cache()
        after_forward = cache.get(svc._GEN_KEY)

    with mock.patch('apps.dashboard.services.time.time_ns', return_value=1_000):
        svc.invalidate_finance_cache()
        after_backward = cache.get(svc._GEN_KEY)

    assert after_backward > after_forward, 'генерация уехала назад'
