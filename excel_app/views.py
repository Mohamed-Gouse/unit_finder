import os
import pandas as pd
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, FileResponse
from django.conf import settings
from django.contrib import messages
from django.urls import reverse
from .models import ExcelFile, MergedFile
import uuid

def index(request):
    """Homepage view for file upload."""
    files = ExcelFile.objects.all().order_by('-uploaded_at')
    merged_files = MergedFile.objects.all().order_by('-created_at')
    
    context = {
        'files': files,
        'merged_files': merged_files
    }
    return render(request, 'excel_app/index.html', context)

def process_files(request):
    """Process uploaded Excel files."""
    if request.method == 'POST':
        files = request.FILES.getlist('excel_files')
        
        if not files:
            messages.error(request, 'No files were uploaded.')
            return redirect('excel_app:index')
        
        for file in files:
            excel_file = ExcelFile(file=file)
            excel_file.save()
            
            try:
                input_path = excel_file.file.path
                
                data_frame = pd.read_excel(input_path)
                
                base_columns = []
                
                optional_columns = [
                    'Regis',
                    'ProcedureValue',
                    'Project',
                    'Building No',
                    'BuildingNameEn',
                    'Size',
                    'UnitNumber',
                    'PropertyTypeEn', 
                    'LandNumber',
                    'ProcedurePartyTypeNameEn',
                    'NameEn',
                    'Mobile',
                    'CountryNameEn',
                    'BirthDate',
                    'Area'
                ]

                available_columns = [col for col in base_columns + optional_columns if col in data_frame.columns]
                missing_columns = [col for col in base_columns if col not in data_frame.columns]
                if missing_columns:
                    error_message = f"Missing required columns in {excel_file.filename()}: {', '.join(missing_columns)}"
                    messages.error(request, error_message)
                    excel_file.delete()
                    continue

                data_frame = data_frame[available_columns]
                
                data_frame['Mobile'] = data_frame['Mobile'].fillna('NILL').replace('', 'NILL')
                data_frame = data_frame[data_frame['ProcedurePartyTypeNameEn'] == 'Buyer']
                
                if 'Regis' in data_frame.columns:
                    data_frame['Regis'] = pd.to_datetime(data_frame['Regis'], errors='coerce')

                data_frame = data_frame.sort_values(by='Regis', ascending=False)

                data_frame.columns = data_frame.columns.str.strip().str.lower()
                
                deduplication_columns = ['building no', 'unitnumber', 'project', 'landnumber', 'size']

                available_columns = [col for col in deduplication_columns if col in data_frame.columns]

                if available_columns:
                    data_frame[available_columns] = data_frame[available_columns].astype(str)

                    data_frame = data_frame.drop_duplicates(subset=available_columns, keep='first')
                
                filename = f"processed_{os.path.basename(input_path)}"
                output_path = os.path.join(settings.PROCESSED_DIR, filename)
                
                data_frame.to_excel(output_path, index=False)
                
                relative_path = os.path.join('processed', filename)
                excel_file.processed_file.name = relative_path
                excel_file.processed = True
                excel_file.save()
                
                messages.success(request, f"Successfully processed {excel_file.filename()}")
                
            except Exception as e:
                messages.error(request, f"Error processing {excel_file.filename()}: {str(e)}")
                excel_file.delete()
        
        return redirect('excel_app:index')
    
    return redirect('excel_app:index')

def results(request):
    """Display results of processing."""
    files = ExcelFile.objects.filter(processed=True).order_by('-uploaded_at')
    merged_files = MergedFile.objects.all().order_by('-created_at')
    
    context = {
        'files': files,
        'merged_files': merged_files
    }
    return render(request, 'excel_app/results.html', context)

def download_file(request, file_id):
    """Download a processed file."""
    excel_file = get_object_or_404(ExcelFile, id=file_id, processed=True)
    file_path = excel_file.processed_file.path
    
    if os.path.exists(file_path):
        response = FileResponse(open(file_path, 'rb'))
        response['Content-Disposition'] = f'attachment; filename="{excel_file.processed_filename()}"'
        return response
    
    messages.error(request, f"File {excel_file.processed_filename()} not found.")
    return redirect('excel_app:index')

def merge_files(request):
    """Merge all processed files into one."""
    processed_files = ExcelFile.objects.filter(processed=True)
    
    if not processed_files.exists():
        messages.error(request, "No processed files to merge.")
        return redirect('excel_app:index')
    
    try:
        # Create an empty list to store all dataframes
        dfs = []
        
        for excel_file in processed_files:
            file_path = excel_file.processed_file.path
            df = pd.read_excel(file_path)
            dfs.append(df)
        
        merged_df = pd.concat(dfs, ignore_index=True)
        
        filename = f"Master Data.xlsx"
        output_path = os.path.join(settings.PROCESSED_DIR, filename)
        
        merged_df.to_excel(output_path, index=False)
        
        relative_path = os.path.join('processed', filename)
        merged_file = MergedFile(file=relative_path)
        merged_file.save()

        merged_file.files.set(processed_files)
        
        messages.success(request, f"Successfully merged {processed_files.count()} files.")
        
        return redirect('excel_app:index')
        
    except Exception as e:
        messages.error(request, f"Error merging files: {str(e)}")
        return redirect('excel_app:index')

def download_merged(request, merged_id):
    merged_file = get_object_or_404(MergedFile, id=merged_id)
    file_path = merged_file.file.path
    
    if os.path.exists(file_path):
        response = FileResponse(open(file_path, 'rb'))
        response['Content-Disposition'] = f'attachment; filename="{merged_file.filename()}"'
        return response
    
    messages.error(request, f"File {merged_file.filename()} not found.")
    return redirect('excel_app:index')

def clear_files(request):
    ExcelFile.objects.all().delete()
    MergedFile.objects.all().delete()
    
    # Clear the upload and processed directories
    for filename in os.listdir(settings.UPLOAD_DIR):
        file_path = os.path.join(settings.UPLOAD_DIR, filename)
        if os.path.isfile(file_path):
            os.unlink(file_path)
    
    for filename in os.listdir(settings.PROCESSED_DIR):
        file_path = os.path.join(settings.PROCESSED_DIR, filename)
        if os.path.isfile(file_path):
            os.unlink(file_path)
    
    messages.success(request, "All files have been cleared.")
    return redirect('excel_app:index')
