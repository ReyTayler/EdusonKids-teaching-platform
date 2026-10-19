"""
Маршруты раздела «База знаний».

Монтируются в config/urls.py как:
  path('api/admin/knowledge', include('apps.knowledge.urls'))
"""
from django.urls import path

from apps.knowledge.file_views import FileDownloadView, FileUploadView
from apps.knowledge.image_views import ImageServeView, ImageUploadView
from apps.knowledge.views import (
    DocumentDetailView,
    DocumentDuplicateView,
    DocumentFavoriteView,
    DocumentListCreateView,
    DocumentPublishView,
    DocumentReorderView,
    DocumentRestoreView,
    DocumentUnpublishView,
    SectionDetailView,
    SectionListCreateView,
    SectionReorderView,
)

urlpatterns = [
    path('/sections', SectionListCreateView.as_view(), name='knowledge-sections'),
    path('/sections/reorder', SectionReorderView.as_view(), name='knowledge-sections-reorder'),
    path('/sections/<int:pk>', SectionDetailView.as_view(), name='knowledge-section-detail'),
    path('/documents', DocumentListCreateView.as_view(), name='knowledge-documents'),
    path('/documents/reorder', DocumentReorderView.as_view(), name='knowledge-documents-reorder'),
    path('/documents/<int:pk>', DocumentDetailView.as_view(), name='knowledge-document-detail'),
    path('/documents/<int:pk>/publish', DocumentPublishView.as_view(), name='knowledge-document-publish'),
    path('/documents/<int:pk>/unpublish', DocumentUnpublishView.as_view(), name='knowledge-document-unpublish'),
    path('/documents/<int:pk>/restore', DocumentRestoreView.as_view(), name='knowledge-document-restore'),
    path('/documents/<int:pk>/duplicate', DocumentDuplicateView.as_view(), name='knowledge-document-duplicate'),
    path('/documents/<int:pk>/favorite', DocumentFavoriteView.as_view(), name='knowledge-document-favorite'),
    path('/images', ImageUploadView.as_view(), name='knowledge-images'),
    path('/images/<int:pk>', ImageServeView.as_view(), name='knowledge-image-serve'),
    path('/files', FileUploadView.as_view(), name='knowledge-files'),
    path('/files/<int:pk>', FileDownloadView.as_view(), name='knowledge-file-download'),
]
