from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('check-status/', views.check_status, name='check_status'),
    path('download-excel/', views.download_excel, name='download_excel'),
    path('add-to-crm/', views.add_to_crm, name='add_to_crm'),
    path('clear-task/', views.clear_task, name='clear_task'),
]