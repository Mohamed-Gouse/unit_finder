import os
import pandas as pd
from django.shortcuts import render, redirect, get_object_or_404
from django.http import FileResponse
from django.conf import settings
from django.contrib import messages
from .models import ExcelFile, MasterData, MergedFile


def index(request):
    """Homepage view for file upload."""
    files = ExcelFile.objects.all().order_by('-uploaded_at')
    merged_files = MergedFile.objects.all().order_by('-created_at')
    
    context = {
        'files': files,
        'merged_files': merged_files
    }
    return render(request, 'excel_app/index.html', context)

def old_data(excel_file):
    """Process data from the first sheet."""
    input_path = excel_file.file.path
    data_frame = pd.read_excel(input_path, sheet_name=0)

    sheet1_optional_columns = [
        'Regis', 'ProcedureValue', 'Project', 'Building No', 'BuildingNameEn',
        'Size', 'UnitNumber', 'PropertyTypeEn', 'LandNumber', 'ProcedurePartyTypeNameEn',
        'NameEn', 'Mobile', 'CountryNameEn', 'BirthDate', 'Area'
    ]

    sheet1_available_columns = [col for col in sheet1_optional_columns if col in data_frame.columns]
    missing_columns = [col for col in sheet1_optional_columns if col not in data_frame.columns]

    data_frame = data_frame[sheet1_available_columns]
    data_frame = data_frame.fillna('NIL').replace('', 'NIL')

    data_frame['has_owner'] = (data_frame['NameEn'].ne('NIL')) & (data_frame['Mobile'].ne('NIL'))

    if 'Regis' in data_frame.columns:
        data_frame['Regis'] = pd.to_datetime(data_frame['Regis'], errors='coerce')

    data_frame = data_frame.sort_values(by=['has_owner', 'Regis'], ascending=[False, False])
    data_frame.columns = data_frame.columns.str.strip().str.lower()

    deduplication_columns = ['building no', 'buildingnameen', 'unitnumber', 'project', 'landnumber', 'procedurepartytypenameen']
    available_columns = [col for col in deduplication_columns if col in data_frame.columns]

    if available_columns:
        data_frame[available_columns] = data_frame[available_columns].astype(str)
        data_frame = data_frame.drop_duplicates(subset=available_columns, keep='first')

    if 'procedurepartytypenameen' in data_frame.columns:
        data_frame = data_frame[data_frame['procedurepartytypenameen'] == 'Buyer']

    if 'has_owner' in data_frame.columns:
        data_frame.drop(columns=['has_owner'], inplace=True)

    # Add source indicator column
    data_frame['source'] = 'old'
    
    return data_frame, missing_columns

def new_data(excel_file):
    """Process data from the second sheet."""
    try:
        input_path = excel_file.file.path
        data_frame = pd.read_excel(input_path, sheet_name=1)
        
        sheet2_optional_columns = [
            'Date',
            'Area',
            'Transaction Date',
            'Master Projects',
            'Building 1',
            'property_number',
            'Property Type',
            'Transaction Amount',
            'Actual Size',
            'LandNumber',
            'Owner Name',
            'Phone 1',
            'Phone 2',
            'Mobile 1',
            'Mobile 2',
            'Secondary Mobile'
        ]
        
        sheet2_available_columns = [col for col in sheet2_optional_columns if col in data_frame.columns]
        missing_columns = [col for col in sheet2_optional_columns if col not in data_frame.columns]
        
        # Only keep available columns
        data_frame = data_frame[sheet2_available_columns]
        data_frame = data_frame.fillna('NIL').replace('', 'NIL')

        if 'Date' in data_frame.columns:
            data_frame['Date'] = pd.to_datetime(data_frame['Date'], errors='coerce')
        
        if 'Transaction Date' in data_frame.columns:
            data_frame['Transaction Date'] = pd.to_datetime(data_frame['Transaction Date'], errors='coerce')
        
        # Process phone numbers
        phone_fields = ['Phone 1', 'Phone 2', 'Mobile 1', 'Mobile 2', 'Secondary Mobile']
        phone_fields = [f for f in phone_fields if f in data_frame.columns]
        
        def pick_best_number(row):
            numbers = [
                str(row[col]) for col in phone_fields 
                if pd.notna(row[col]) and str(row[col]).strip() not in ['', '#N/A', 'NIL']
            ]
            if not numbers:
                return 'NIL'
            num_counts = {num: numbers.count(num) for num in numbers}
            best_number = max(num_counts, key=lambda x: (num_counts[x], numbers.index(x)))
            return best_number
        
        if phone_fields:
            data_frame['Selected Phone'] = data_frame.apply(pick_best_number, axis=1)
            data_frame.drop(columns=phone_fields, inplace=True)
        else:
            data_frame['Selected Phone'] = 'NIL'

        # Remove duplicate properties based on specified columns
        deduplication_columns = ['Building 1', 'property_number', 'project', 'LandNumber', 'Master Projects', 'Actual Size']
        available_columns = [col for col in deduplication_columns if col in data_frame.columns]

        if available_columns:
            data_frame[available_columns] = data_frame[available_columns].astype(str)
            data_frame = data_frame.drop_duplicates(subset=available_columns, keep='first')
        
        # Create mapping dictionary for standardizing column names
        column_mapping = {
            'Date': 'regis',
            'Transaction Date': 'regis',
            'Master Projects': 'project',
            'Building 1': 'buildingnameen',
            'property_number': 'unitnumber',
            'Property Type': 'propertytypeen',
            'Transaction Amount': 'procedurevalue',
            'Actual Size': 'size',
            'LandNumber': 'landnumber',
            'Owner Name': 'nameen',
            'Selected Phone': 'mobile',
            'Area': 'area'
        }
        
        # Only rename columns that exist in the dataframe
        rename_mapping = {k: v for k, v in column_mapping.items() if k in data_frame.columns}
        data_frame = data_frame.rename(columns=rename_mapping)
        
        # Ensure datetime format for regis column
        if 'regis' in data_frame.columns:
            data_frame['regis'] = pd.to_datetime(data_frame['regis'], errors='coerce')
        
        # Add source indicator column
        data_frame['source'] = 'new'
        
        return data_frame, missing_columns
    except Exception as e:
        raise Exception(f"Error processing sheet 2: {str(e)}")

def process_files(request):
    """Process uploaded Excel files with two sheets and concatenate them."""
    if request.method == 'POST':
        files = request.FILES.getlist('excel_files')

        if not files:
            messages.error(request, 'No files were uploaded.')
            return redirect('excel_app:index')

        if not os.path.exists(settings.PROCESSED_DIR):
            os.makedirs(settings.PROCESSED_DIR)

        for file in files:
            excel_file = ExcelFile(file=file)
            excel_file.save()

            try:
                # Process both sheets
                sheet1_data, sheet1_missing = old_data(excel_file)
                sheet2_data, sheet2_missing = new_data(excel_file)
                
                # Report missing columns
                if sheet1_missing:
                    messages.warning(request, f"Missing columns in {file.name} (Sheet 1): {', '.join(sheet1_missing)}")
                if sheet2_missing:
                    messages.warning(request, f"Missing columns in {file.name} (Sheet 2): {', '.join(sheet2_missing)}")
                
                # Ensure column names match between sheets for concatenation
                common_columns = list(set(sheet1_data.columns) & set(sheet2_data.columns))
                
                # Add any missing columns to each dataframe with default values
                all_columns = list(set(sheet1_data.columns) | set(sheet2_data.columns))
                
                for col in all_columns:
                    if col not in sheet1_data.columns:
                        sheet1_data[col] = 'NIL'
                    if col not in sheet2_data.columns:
                        sheet2_data[col] = 'NIL'
                
                # Concatenate the two dataframes
                combined_data = pd.concat([sheet1_data, sheet2_data], ignore_index=True)
                
                # Sort the combined data
                if 'regis' in combined_data.columns:
                    combined_data = combined_data.sort_values(by='regis', ascending=False)
                
                # Create the output file
                filename = f"processed_{os.path.basename(excel_file.file.path)}"
                output_path = os.path.join(settings.PROCESSED_DIR, filename)
                
                # Write to Excel
                with pd.ExcelWriter(output_path) as writer:
                    combined_data.to_excel(writer, sheet_name='Combined Data', index=False)
                    sheet1_data.to_excel(writer, sheet_name='Sheet1 Data', index=False)
                    sheet2_data.to_excel(writer, sheet_name='Sheet2 Data', index=False)
                
                # Update the model with the new file path
                relative_path = os.path.join('processed', filename)
                excel_file.processed_file.name = relative_path
                excel_file.processed = True
                excel_file.save()
                
                messages.success(request, f"Successfully processed {file.name}")

            except Exception as e:
                messages.error(request, f"Error processing {file.name}: {str(e)}")
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

def delete_file(request, file_id):
    """Download a processed file."""
    excel_file = get_object_or_404(ExcelFile, id=file_id, processed=True)
    
    excel_file.delete()
    
    messages.success(request, f"File {excel_file.processed_filename()} deleted successfully.")
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

def clear_master_data(request):
    MasterData.objects.all().delete()
    
    messages.success(request, "All data's have been cleared from Master Data.")
    return redirect('excel_app:index')
