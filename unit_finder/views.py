import requests
import pandas as pd
from django.shortcuts import render
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
import json, os
from excel_app.models import MergedFile
from datetime import datetime
import threading
import queue
import uuid
from django.views.decorators.csrf import csrf_exempt
import time

token = 'apify_api_HMvSVv0jj56tMSsHcg0wEWGnOUkOOc1sN8o1'

# Base directory for storing Excel files with timestamps
EXCEL_DIR = "property_exports"
os.makedirs(EXCEL_DIR, exist_ok=True)

# Dictionary to track processing status
processing_tasks = {}

class PropertyProcessor:
    def __init__(self, task_id, urls_list):
        self.task_id = task_id
        self.urls_list = urls_list
        self.processed_data = []
        self.total_urls = len(urls_list)
        self.processed_count = 0
        self.status = "processing"
        self.excel_filename = None
        self.last_update = datetime.now()
        
    def process_urls(self):
        """Process URLs and update status in a separate thread"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.excel_filename = f"{EXCEL_DIR}/property_data_{timestamp}.xlsx"
            
            # Create an empty DataFrame with headers
            headers = ["Area", "master_project", "BuildingNameEn", "UnitNumber", 
                      "property_type", "size", "rooms", "Amount", 
                      "permit_end_date", "permit_type", "owner_name", "owner_phone"]
            df = pd.DataFrame(columns=headers)
            
            # Save initial empty file
            with pd.ExcelWriter(self.excel_filename, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Properties')
            
            # Get owner details lookup source
            merged_file = MergedFile.objects.order_by('-created_at').first()
            
            for url in self.urls_list:
                # Process single URL
                url_data = self._process_single_url(url, merged_file)
                
                if url_data:
                    # Add to in-memory data
                    self.processed_data.extend(url_data)
                    
                    # Update Excel file with new data
                    if os.path.exists(self.excel_filename):
                        existing_df = pd.read_excel(self.excel_filename)
                        new_df = pd.DataFrame(url_data)
                        combined_df = pd.concat([existing_df, new_df])
                    else:
                        combined_df = pd.DataFrame(url_data)
                        
                    # Save updated data
                    with pd.ExcelWriter(self.excel_filename, engine='xlsxwriter') as writer:
                        combined_df.to_excel(writer, index=False, sheet_name='Properties')
                
                # Update progress
                self.processed_count += 1
                self.last_update = datetime.now()
            
            self.status = "completed"
            
        except Exception as e:
            print(f"Error in processing task {self.task_id}: {str(e)}")
            self.status = f"failed: {str(e)}"
    
    def _process_single_url(self, url, merged_file):
        """Process a single URL and return the data"""
        if not url.strip():
            return []
            
        data = {
            "propertyUrls": [{"url": url.strip(), "method": "GET"}],
            "retrieveContactDetails": False,
            "proxy": {"useApifyProxy": False}
        }
        
        try:

            
            response = requests.post(
                f"https://api.apify.com/v2/acts/dhrumil~uae-dubai-property-leads-finder/run-sync-get-dataset-items?token={token}",
                json=data,
            )
                
            result = response.json()
            if not result:
                return []
                
            formatted_items = []
            for item in result:
                building_name = item.get("PropertyNameEn", "")
                unit_number = item.get("PropertyUnitNumber", "")
                
                # Fetch owner details
                owner_details = {}
                if merged_file:
                    owner_details = merged_file.get_owner_details(building_name, unit_number)
                
                formatted_items.append({
                    "Area": item.get("ZoneNameEn", ""),
                    "master_project": "",
                    "BuildingNameEn": building_name,
                    "UnitNumber": unit_number,
                    "property_type": item.get("PropertyTypeNameEn", ""),
                    "size": item.get("PropertySize", ""),
                    "rooms": item.get("RoomTypeEn", ""),
                    "Amount": item.get("PropertyValue", ""),
                    "permit_end_date": item.get("PermitEndDate", ""),
                    "permit_type": item.get("PermitTypeNameEn", ""),
                    "owner_name": owner_details.get('owner_name', 'NILL'),
                    "owner_phone": owner_details.get('owner_phone', 'NILL'),
                })
            
            return formatted_items
            
        except requests.RequestException as e:
            print(f"Request error for {url}: {str(e)}")
            return []
        except Exception as e:
            print(f"General error processing {url}: {str(e)}")
            return []

def index(request):
    """Main view for URL processing form and results display"""
    context = {
        'processing_status': None,
        'task_id': None,
        'data_available': False
    }
    
    if request.method == 'POST':
        if 'urls' in request.POST:
            # Start new processing job
            urls = request.POST.get('urls', '').strip()
            urls_list = [url for url in urls.splitlines() if url.strip()]
            
            if not urls_list:
                context['error'] = "Please provide at least one valid URL"
                return render(request, 'index.html', context)
            
            # Generate unique task ID
            task_id = str(uuid.uuid4())
            
            # Create processor and start thread
            processor = PropertyProcessor(task_id, urls_list)
            processing_tasks[task_id] = processor
            
            # Start processing in background
            thread = threading.Thread(target=processor.process_urls)
            thread.daemon = True
            thread.start()
            
            # Return with task ID for status checking
            context['processing_status'] = 'started'
            context['task_id'] = task_id
            context['total_urls'] = len(urls_list)
            
    return render(request, 'index.html', context)

def check_status(request):
    """AJAX endpoint to check processing status"""
    task_id = request.GET.get('task_id')
    
    if not task_id or task_id not in processing_tasks:
        return JsonResponse({'status': 'error', 'message': 'Invalid task ID'})
    
    processor = processing_tasks[task_id]
    
    # Calculate progress percentage
    progress = int((processor.processed_count / processor.total_urls) * 100) if processor.total_urls > 0 else 0
    
    response_data = {
        'status': processor.status,
        'progress': progress,
        'processed': processor.processed_count,
        'total': processor.total_urls,
        'last_update': processor.last_update.strftime("%H:%M:%S")
    }
    
    # Add download information if completed
    if processor.status == 'completed':
        response_data['excel_filename'] = os.path.basename(processor.excel_filename)
        
        # Convert processed data to HTML for display
        if processor.processed_data:
            df = pd.DataFrame(processor.processed_data)
            response_data['table_html'] = df.to_html(classes='table table-striped table-hover', index=False)
            response_data['data_json'] = json.dumps(processor.processed_data)
    
    return JsonResponse(response_data)

def download_excel(request):
    """Download the processed Excel file"""
    filename = request.GET.get('filename')
    
    if not filename:
        # For backward compatibility
        latest_file = None
        latest_time = 0
        
        for file in os.listdir(EXCEL_DIR):
            if file.startswith("property_data_") and file.endswith(".xlsx"):
                filepath = os.path.join(EXCEL_DIR, file)
                file_time = os.path.getmtime(filepath)
                
                if file_time > latest_time:
                    latest_time = file_time
                    latest_file = filepath
        
        if latest_file:
            filename = os.path.basename(latest_file)
        else:
            return HttpResponse("No data available", status=400)
    
    filepath = os.path.join(EXCEL_DIR, filename)
    
    if os.path.exists(filepath):
        with open(filepath, 'rb') as file:
            response = HttpResponse(
                file.read(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
    
    return HttpResponse("File not found", status=404)

@csrf_exempt
def add_to_crm(request):
    """Receive property data and add it to CRM."""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            property_data = data.get('property', [])
            
            if not property_data:
                return JsonResponse({'status': 'error', 'message': 'No property data received'}, status=400)
            
            print(f"Adding {len(property_data)} properties to CRM")
                
            return JsonResponse({'status': 'success', 'message': 'Added to CRM successfully'})
            
        except json.JSONDecodeError as e:
            return JsonResponse({'status': 'error', 'message': f'Invalid JSON: {str(e)}'}, status=400)
    
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

def clear_task(request):
    """Clear completed or failed tasks to free memory"""
    task_id = request.GET.get('task_id')
    
    if task_id and task_id in processing_tasks:
        status = processing_tasks[task_id].status
        if status in ['completed', 'failed']:
            del processing_tasks[task_id]
            return JsonResponse({'status': 'success', 'message': f'Task {task_id} cleared'})
        else:
            return JsonResponse({'status': 'error', 'message': 'Cannot clear active task'})
            
    return JsonResponse({'status': 'error', 'message': 'Invalid task ID'})