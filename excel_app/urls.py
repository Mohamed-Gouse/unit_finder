from django.urls import path
from . import views

app_name = 'excel_app'

urlpatterns = [
    path('formattor/', views.index, name='index'),
    path('process/', views.process_files, name='process_files'),
    path('results/', views.results, name='results'),
    path('download/<int:file_id>/', views.download_file, name='download_file'),
    path('delete/<int:file_id>/', views.delete_file, name='delete_file'),
    path('merge/', views.merge_files, name='merge_files'),
    path('download_merged/<int:merged_id>/', views.download_merged, name='download_merged'),
    path('clear/', views.clear_files, name='clear_files'),
    path('clear_master_data/', views.clear_master_data, name='clear_master_data'),
]
