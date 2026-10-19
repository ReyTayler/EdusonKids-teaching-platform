"""
Модели раздела «База знаний».

Таблицы:
  knowledge_sections           — разделы (два уровня: раздел → документы)
  knowledge_documents          — документы, контент — TipTap-JSON в jsonb
  knowledge_favorites          — личные закладки сотрудников
  knowledge_images             — картинки, адресуемые содержимым (sha256)
  knowledge_image_usages       — где какая картинка используется (права + уборка)
  knowledge_files              — прикреплённые файлы, тоже по sha256
  knowledge_file_usages        — где какой файл прикреплён (права + уборка)

Ни счётчика просмотров, ни истории версий здесь нет намеренно — оба сняты по
решению пользователя (просмотры 2026-08-08, версии 2026-08-12).
Он требовал записи при каждом чтении и хранил, кто какой документ открывал, —
сведения о сотрудниках, которые разделу не нужны.

pghistory здесь НЕ применяется — решение пользователя (спека 2026-08-06).
"""
from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models

from apps.core.db_fields import TolerantJSONField

# Роли, которые можно выдать в reader_roles. Совпадает с Account.Role.
READER_ROLES = ['teacher', 'manager', 'admin', 'superadmin']

# Роли, видящие всё: и черновики, и документы с пустым reader_roles.
FULL_ACCESS_ROLES = ('admin', 'superadmin')


class KnowledgeSection(models.Model):
    """Раздел базы знаний («Методика», «Продажи»)."""

    id = models.AutoField(primary_key=True)
    title = models.TextField(unique=True)
    position = models.IntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'knowledge_sections'
        indexes = [
            models.Index(
                fields=['position'], name='knowledge_sect_pos_idx',
                condition=models.Q(active=True),
            ),
        ]

    def __str__(self):
        return self.title


class KnowledgeDocument(models.Model):
    """Документ базы знаний. Источник правды по содержимому — поле content."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Черновик'
        PUBLISHED = 'published', 'Опубликован'

    id = models.AutoField(primary_key=True)
    section = models.ForeignKey(
        'knowledge.KnowledgeSection',
        on_delete=models.PROTECT,
        related_name='documents',
    )
    title = models.TextField()

    # ВАЖНО: TolerantJSONField, а не JSONField. Проект глобально регистрирует
    # jsonb-typecaster psycopg2 (apps/core/apps.py), из-за чего штатный JSONField
    # на чтении падает с «the JSON object must be str ... not dict».
    content = TolerantJSONField(default=dict)

    # Плоский текст документа. Пишет сервер при каждом сохранении
    # (content.extract_plain_text), клиент это поле не присылает.
    plain_text = models.TextField(default='', blank=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT,
    )
    reader_roles = ArrayField(
        models.CharField(max_length=20), default=list, blank=True,
    )
    position = models.IntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        'accounts.Account', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='knowledge_documents_created',
    )
    updated_by = models.ForeignKey(
        'accounts.Account', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='knowledge_documents_updated',
    )
    # Поисковый вектор. Генерируемая колонка, а не поле, которое пишет
    # приложение: так вектор физически не может разойтись с содержимым — его
    # пересчитывает сама СУБД при каждой записи title/plain_text. Ручная
    # синхронизация рано или поздно отстаёт (забыли вызвать, обновили через
    # .update(), залили миграцией данных).
    search_tsv = models.GeneratedField(
        expression=SearchVector('title', 'plain_text', config='russian'),
        output_field=SearchVectorField(),
        db_persist=True,
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'knowledge_documents'
        indexes = [
            models.Index(
                fields=['section', 'position'], name='knowledge_doc_secpos_idx',
                condition=models.Q(active=True),
            ),
            GinIndex(fields=['reader_roles'], name='knowledge_doc_roles_gin'),
            GinIndex(fields=['search_tsv'], name='knowledge_doc_fts_gin'),
            models.Index(
                fields=['status'], name='knowledge_doc_pub_idx',
                condition=models.Q(status='published'),
            ),
        ]
        constraints = [
            models.CheckConstraint(
                name='knowledge_doc_status_check',
                condition=models.Q(status__in=['draft', 'published']),
            ),
            models.CheckConstraint(
                name='knowledge_doc_roles_check',
                condition=models.Q(reader_roles__contained_by=READER_ROLES),
            ),
        ]

    def __str__(self):
        return self.title


class KnowledgeFavorite(models.Model):
    """Документ в избранном у сотрудника. Личное, не общее на всех."""

    id = models.AutoField(primary_key=True)
    account = models.ForeignKey(
        'accounts.Account', on_delete=models.CASCADE,
        related_name='knowledge_favorites',
    )
    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name='favorites',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'knowledge_favorites'
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'document'], name='knowledge_fav_uq',
            ),
        ]


class KnowledgeImage(models.Model):
    """
    Картинка, адресуемая содержимым: имя файла на диске — её sha256.

    Дедупликация: повторная загрузка того же файла возвращает существующую
    запись, второй копии на диске не появляется.
    """

    class OptimizeState(models.TextChoices):
        PENDING = 'pending', 'В очереди'
        READY = 'ready', 'Готово'
        FAILED = 'failed', 'Ошибка'

    id = models.AutoField(primary_key=True)
    sha256 = models.CharField(max_length=64, unique=True)
    original_name = models.TextField()
    mime = models.CharField(max_length=50)
    byte_size = models.BigIntegerField()
    width = models.IntegerField()
    height = models.IntegerField()
    original_path = models.TextField()
    optimized_path = models.TextField(null=True, blank=True)
    thumb_path = models.TextField(null=True, blank=True)
    optimize_state = models.CharField(
        max_length=10, choices=OptimizeState.choices, default=OptimizeState.PENDING,
    )
    uploaded_by = models.ForeignKey(
        'accounts.Account', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='knowledge_images',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'knowledge_images'
        indexes = [
            models.Index(
                fields=['optimize_state'], name='knowledge_img_state_idx',
                condition=models.Q(optimize_state='pending'),
            ),
        ]


class KnowledgeImageUsage(models.Model):
    """
    Где используется картинка. Пересобирается при каждом сохранении документа.

    Нужна для двух вещей: прав на отдачу файла (видишь документ — видишь
    картинку) и уборки осиротевших файлов.
    """

    id = models.AutoField(primary_key=True)
    image = models.ForeignKey(
        KnowledgeImage, on_delete=models.CASCADE, related_name='usages',
    )
    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name='image_usages',
    )

    class Meta:
        managed = True
        db_table = 'knowledge_image_usages'
        constraints = [
            models.UniqueConstraint(
                fields=['image', 'document'], name='knowledge_img_usage_uq',
            ),
        ]


class KnowledgeFile(models.Model):
    """
    Файл, прикреплённый к документу: договор, методичка, презентация.

    Отдельная таблица от картинок, а не общая с полем «вид». У картинки есть
    ширина, высота, состояние оптимизации и пути вариантов — у файла всё это
    пусто. Столбец, который у половины строк всегда NULL, означает, что в одну
    таблицу слили две разные сущности.

    Как и картинка, адресуется содержимым: имя файла на диске — sha256, поэтому
    один и тот же файл не занимает место дважды. А вот `original_name` у файла
    не декорация: это его личность, и при скачивании отдаётся именно оно.
    """

    id = models.AutoField(primary_key=True)
    sha256 = models.CharField(max_length=64, unique=True)
    original_name = models.TextField()
    mime = models.CharField(max_length=100)
    byte_size = models.BigIntegerField()
    path = models.TextField()
    uploaded_by = models.ForeignKey(
        'accounts.Account', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='knowledge_files',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'knowledge_files'


class KnowledgeFileUsage(models.Model):
    """
    Где используется файл. Пересобирается при сохранении документа, но только
    когда набор файлов в тексте действительно изменился.

    Нужна для того же, что и у картинок: прав на скачивание (видишь документ —
    видишь файл) и уборки осиротевших.
    """

    id = models.AutoField(primary_key=True)
    file = models.ForeignKey(
        KnowledgeFile, on_delete=models.CASCADE, related_name='usages',
    )
    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name='file_usages',
    )

    class Meta:
        managed = True
        db_table = 'knowledge_file_usages'
        constraints = [
            models.UniqueConstraint(
                fields=['file', 'document'], name='knowledge_file_usage_uq',
            ),
        ]
