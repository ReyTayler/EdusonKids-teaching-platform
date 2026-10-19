"""
Тесты кэша СВОДКИ «Реестра куратора» (Фаза 2, вариант B).

Кэшируется только сводка (KPI/сигналы/поток) — список пагинируется в БД и не
кэшируется. Проверяем: сводка кэшируется, invalidate сбрасывает, graceful при
мёртвом кэше, refresh_summary прогревает, оплата инвалидирует.
"""
from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import override_settings

from apps.dashboard import registry_service as svc

pytestmark = pytest.mark.django_db

_LOCMEM = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'test-registry-cache',
    },
}


@override_settings(CACHES=_LOCMEM)
def test_summary_is_cached():
    cache.delete(svc.SUMMARY_CACHE_KEY)
    assert cache.get(svc.SUMMARY_CACHE_KEY) is None

    first = svc.get_summary()
    assert cache.get(svc.SUMMARY_CACHE_KEY) is not None
    second = svc.get_summary()
    assert first['generated_at'] == second['generated_at']


@override_settings(CACHES=_LOCMEM)
def test_invalidate_clears_summary():
    svc.get_summary()
    assert cache.get(svc.SUMMARY_CACHE_KEY) is not None
    svc.invalidate_registry_cache()
    assert cache.get(svc.SUMMARY_CACHE_KEY) is None


@override_settings(CACHES={
    'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'},
})
def test_summary_survives_dead_cache():
    summary = svc.get_summary()
    assert 'kpis' in summary and 'signals' in summary
    assert 'kpis' in svc.get_summary()


@override_settings(CACHES=_LOCMEM)
def test_refresh_summary_warms_cache():
    cache.delete(svc.SUMMARY_CACHE_KEY)
    generated_at = svc.refresh_summary()
    assert isinstance(generated_at, str) and generated_at
    cached = cache.get(svc.SUMMARY_CACHE_KEY)
    assert cached is not None and cached['generated_at'] == generated_at


@override_settings(CACHES=_LOCMEM)
def test_payment_save_invalidates_registry_cache(django_capture_on_commit_callbacks):
    # Дашборд подписан на Payment.post_save → сброс кэша ПОСЛЕ коммита (on_commit).
    from django.db.models.signals import post_save
    from apps.payments.models import Payment

    cache.set(svc.SUMMARY_CACHE_KEY, {'stale': True}, svc.SUMMARY_TTL)
    with django_capture_on_commit_callbacks(execute=True):
        post_save.send(sender=Payment, instance=Payment(), created=True)
    assert cache.get(svc.SUMMARY_CACHE_KEY) is None


@override_settings(CACHES=_LOCMEM)
def test_payment_delete_invalidates_registry_cache(django_capture_on_commit_callbacks):
    from django.db.models.signals import post_delete
    from apps.payments.models import Payment

    cache.set(svc.SUMMARY_CACHE_KEY, {'stale': True}, svc.SUMMARY_TTL)
    with django_capture_on_commit_callbacks(execute=True):
        post_delete.send(sender=Payment, instance=Payment())
    assert cache.get(svc.SUMMARY_CACHE_KEY) is None


@override_settings(CACHES=_LOCMEM)
def test_rollback_keeps_registry_cache(django_capture_on_commit_callbacks):
    # Инвалидация отложена на on_commit: без коммита (execute=False = откат) кэш
    # НЕ трогается — при откате оплаты сводка не сбрасывается зря.
    from django.db.models.signals import post_save
    from apps.payments.models import Payment

    cache.set(svc.SUMMARY_CACHE_KEY, {'stale': True}, svc.SUMMARY_TTL)
    with django_capture_on_commit_callbacks(execute=False):
        post_save.send(sender=Payment, instance=Payment(), created=True)
    assert cache.get(svc.SUMMARY_CACHE_KEY) == {'stale': True}
