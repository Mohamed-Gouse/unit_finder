from django.contrib import admin
from .models import ExcelFile, MergedFile, MasterData
# Register your models here.

@admin.register(ExcelFile)
class ExcelFileAdmin(admin.ModelAdmin):
    list_display = ('filename', 'uploaded_at', 'processed', 'processed_filename')
    list_filter = ('processed', 'uploaded_at')
    search_fields = ('file',)

@admin.register(MergedFile)
class MergedFileAdmin(admin.ModelAdmin):
    list_display = ('filename', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('file',)
    filter_horizontal = ('files',)

@admin.register(MasterData)
class MasterDataAdmin(admin.ModelAdmin):
    list_display = ('name_en', 'unit_number', 'building_name_en', 'procedure_party_type_name_en', 'mobile')
    list_filter = ('building_name_en', 'procedure_party_type_name_en', 'country_name_en')
    search_fields = ('name_en', 'unit_number', 'building_name_en', 'mobile')