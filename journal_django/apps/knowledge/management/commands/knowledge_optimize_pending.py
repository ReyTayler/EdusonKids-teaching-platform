"""
Догнать картинки, застрявшие в состоянии pending.

Нужна в двух случаях: Celery не работал в момент загрузки (локальная разработка
без Redis) и задача упала. Запускается вручную или из cron.

    python manage.py knowledge_optimize_pending

С ключом --rebuild-transparent — разовая починка: пересобрать варианты у
картинок с прозрачностью, построенные до того, как build_variants научился её
сохранять (прозрачный фон в них стал чёрным). Повторный запуск безопасен:
уже пересобранные картинки пропускаются.

    python manage.py knowledge_optimize_pending --rebuild-transparent
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.knowledge import images, repository
from apps.knowledge.tasks import optimize_image

# JPEG прозрачности не бывает — открывать его оригиналы незачем.
FORMATS_WITH_ALPHA = ('image/png', 'image/webp')


class Command(BaseCommand):
    help = 'Построить WebP-варианты для картинок в состоянии pending.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument(
            '--rebuild-transparent',
            action='store_true',
            help='Пересобрать варианты картинок с прозрачностью (чёрный фон).',
        )

    def handle(self, *args, **options):
        if options['rebuild_transparent']:
            self._rebuild_transparent()
            return

        image_ids = repository.pending_image_ids(limit=options['limit'])
        if not image_ids:
            self.stdout.write('Нечего обрабатывать.')
            return
        for image_id in image_ids:
            # Синхронный вызов задачи: команда для того и нужна, чтобы обойтись
            # без брокера.
            result = optimize_image(image_id)
            self.stdout.write(f'{image_id}: {result}')
        self.stdout.write(self.style.SUCCESS(f'Обработано: {len(image_ids)}'))

    def _rebuild_transparent(self):
        fixed_suffix = f'.w{images.OPTIMIZED_WIDTH}a.webp'
        rebuilt = 0
        for image_id, original, optimized in repository.ready_images_in_formats(
            FORMATS_WITH_ALPHA,
        ):
            if optimized and optimized.endswith(fixed_suffix):
                continue                            # уже пересобрана
            try:
                transparent = images.has_transparency(original)
            except (OSError, ValueError) as exc:
                self.stderr.write(f'{image_id}: оригинал не читается ({exc})')
                continue
            if not transparent:
                continue
            result = optimize_image(image_id)
            self.stdout.write(f'{image_id}: {result}')
            rebuilt += 1
        self.stdout.write(self.style.SUCCESS(f'Пересобрано: {rebuilt}'))
