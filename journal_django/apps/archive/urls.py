from django.urls import path
from .views import ArchiveView

urlpatterns = [path('', ArchiveView.as_view(), name='admin-archive')]
