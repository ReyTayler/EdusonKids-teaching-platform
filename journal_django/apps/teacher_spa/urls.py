"""
URL-конфиг teacher SPA.

Пути монтируются под /api (НЕ /api/admin).
APPEND_SLASH=False — без trailing slash (как Express).
"""
from django.urls import path

from apps.teacher_spa import views

urlpatterns = [
    path('/getData', views.GetDataView.as_view(), name='teacher-get-data'),
    path('/getAllData', views.GetAllDataView.as_view(), name='teacher-get-all-data'),
    path('/submitLesson', views.SubmitLessonView.as_view(), name='teacher-submit-lesson'),
    path('/refreshData', views.RefreshDataView.as_view(), name='teacher-refresh-data'),
    path('/lessons', views.MyLessonsView.as_view(), name='teacher-my-lessons'),
    path('/group-directions', views.GroupDirectionsView.as_view(), name='teacher-group-directions'),
    path('/group-progress', views.GroupProgressView.as_view(), name='teacher-group-progress'),
]
