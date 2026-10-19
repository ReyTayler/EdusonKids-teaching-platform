"""
Celery-задачи раздела «База знаний».

optimize_image — перекодирование в WebP. Живёт вне запроса намеренно: на VPS с
двумя ядрами перекодирование в воркере gunicorn отняло бы CPU у всех остальных
запросов.

Модуль импортируется автодискавером Celery только когда запущен воркер или beat.
Логика — в images.build_variants, она тестируется без Celery.
"""
from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.knowledge import images, repository, storage
from apps.knowledge.models import KnowledgeImage

# Отсрочка удаления осиротевших картинок: картинка появляется при загрузке, а в
# документ попадает при сохранении. Немедленная уборка ломала бы черновики.
ORPHAN_GRACE_DAYS = 7


@shared_task(name='apps.knowledge.tasks.optimize_image')
def optimize_image(image_id: int) -> str:
    """Сделать WebP-варианты картинки. Возвращает итоговое состояние."""
    image = repository.get_image(image_id)
    if image is None:
        return 'missing'
    try:
        variants = images.build_variants(image.sha256, image.original_path)
    except Exception:                                  # noqa: BLE001
        KnowledgeImage.objects.filter(id=image_id).update(
            optimize_state=KnowledgeImage.OptimizeState.FAILED,
        )
        raise
    KnowledgeImage.objects.filter(id=image_id).update(
        optimized_path=variants.optimized_path,
        thumb_path=variants.thumb_path,
        optimize_state=KnowledgeImage.OptimizeState.READY,
    )
    # Пересборка под новым именем (см. build_variants) оставила бы прежние
    # файлы сиротами на диске: уборка осиротевших картинок смотрит на записи в
    # базе, а не на каталоги. Имена построены от хеша содержимого, и одной
    # картинке соответствует одна запись — чужой файл здесь удалить нельзя.
    for old, new in (
        (image.optimized_path, variants.optimized_path),
        (image.thumb_path, variants.thumb_path),
    ):
        if old and old != new:
            images.absolute_path(old).unlink(missing_ok=True)
    return 'ready'


@shared_task(name='apps.knowledge.tasks.cleanup_orphan_images')
def cleanup_orphan_images() -> int:
    """Удалить картинки без единого использования старше ORPHAN_GRACE_DAYS."""
    cutoff = timezone.now() - timedelta(days=ORPHAN_GRACE_DAYS)
    removed = 0
    for image_id in repository.orphan_image_ids(cutoff):
        image = repository.get_image(image_id)
        if image is None:
            continue
        for rel in (image.original_path, image.optimized_path, image.thumb_path):
            if not rel:
                continue
            path = images.absolute_path(rel)
            if path.exists():
                path.unlink()
        image.delete()
        removed += 1
    return removed


@shared_task(name='apps.knowledge.tasks.cleanup_orphan_files')
def cleanup_orphan_files() -> int:
    """
    Удалить прикреплённые файлы без единого использования старше отсрочки.

    Отсрочка та же и по той же причине: файл появляется при загрузке, а в
    документ попадает при сохранении — немедленная уборка ломала бы черновики.
    """
    cutoff = timezone.now() - timedelta(days=ORPHAN_GRACE_DAYS)
    removed = 0
    for file_id in repository.orphan_file_ids(cutoff):
        file = repository.get_file(file_id)
        if file is None:
            continue
        path = storage.absolute_path(file.path)
        if path.exists():
            path.unlink()
        file.delete()
        removed += 1
    return removed
