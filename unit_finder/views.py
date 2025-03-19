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
from io import BytesIO

token = 'apify_api_HMvSVv0jj56tMSsHcg0wEWGnOUkOOc1sN8o1'

# Base directory for storing Excel files with timestamps
EXCEL_DIR = "property_exports"
os.makedirs(EXCEL_DIR, exist_ok=True)

class TokenHandler:
    def __init__(self, session):
        self.session = session
        self.zoho_refresh_token = "1000.95b91213dae5a69797e85808d5c67885.1e6fc7447945dffc3a1806cb348aab22"
        self.zoho_client_id = "1000.ZLDMF5RCB0YSUH3CAMRXNW7RUH1YDG"
        self.zoho_client_secret = "3e5a1be504005d2a0eeb5a542b1be58ef0f0836c7e"
        self.zoho_token_url = "https://accounts.zoho.com/oauth/v2/token"

    def regenerate_zoho_token(self):
        """Regenerate Zoho access token using refresh token."""
        data = {
            'refresh_token': self.zoho_refresh_token,
            'client_id': self.zoho_client_id,
            'client_secret': self.zoho_client_secret,
            'grant_type': 'refresh_token'
        }

        response = requests.post(self.zoho_token_url, data=data)

        if response.status_code == 200:
            result = response.json()
            self.session['zoho_access_token'] = result['access_token']
            self.session['zoho_token_expiration'] = time.time() + 3600
            return result['access_token']
        raise ValueError(f"Failed to regenerate Zoho token: {response.json()}")

    def get_zoho_token(self):
        """Get valid Zoho token, regenerating if needed."""
        access_token = self.session.get('zoho_access_token')
        expiration_time = self.session.get('zoho_token_expiration', 0)

        if access_token and time.time() < expiration_time:
            print('Zoho Token Exists')
            return access_token
        return self.regenerate_zoho_token()

    def unset_zoho_token(self):
        """Remove Zoho token from session."""
        self.session.pop('zoho_access_token', None)
        self.session.pop('zoho_token_expiration', None)

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
                    'url': url.strip(),
                    "Area": item.get("ZoneNameEn", ""),
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

def get_deal_owners(req):
    # Fetch and return agents details
    token_handler = TokenHandler(req.session)
    zoho_token = token_handler.get_zoho_token()

    url = "https://www.zohoapis.com/bigin/v2/users"
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Zoho-oauthtoken {zoho_token}'
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        search_user_response = response.json()
        if 'users' in search_user_response:
            active_users = [
                {"id": user["id"], "name": user["full_name"]}
                for user in search_user_response["users"]
                if user["status"] == "active"
            ]
            return active_users
        else:
            return []
    else:
        return []



    # deal_owners = [
    #     {"id": 1, "name": "Alex Johnson"},
    #     {"id": 2, "name": "Maria Garcia"},
    #     {"id": 3, "name": "James Wilson"},
    #     {"id": 4, "name": "Sarah Ahmed"},
    #     {"id": 5, "name": "David Lee"}
    # ]
    # return deal_owners

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
    
    # Add real-time data preview as processing happens
    if processor.processed_data:
        df = pd.DataFrame(processor.processed_data)
        response_data['preview_html'] = df.to_html(classes='table table-striped table-hover', index=False)
    
    # Add download information if completed
    if processor.status == 'completed':
        response_data['excel_filename'] = os.path.basename(processor.excel_filename)
        
        # Convert processed data to HTML for display
        if processor.processed_data:
            df = pd.DataFrame(processor.processed_data)
            response_data['table_html'] = df.to_html(classes='table table-striped table-hover', index=False)
            response_data['data_json'] = json.dumps(processor.processed_data)
        
        # Add deal owners data
        response_data['deal_owners'] = get_deal_owners(request)
    
    return JsonResponse(response_data)

def format_deal_name(row):
    units = row['UnitNumber'].split(', ')
    if len(units) > 1:
        return f"{units[0]} # {row['BuildingNameEn']}"
    return f"{units[0]} | {row['BuildingNameEn']}"

def format_phone_number(phone_number):
    phone_number = str(phone_number).replace('+', '')
    return f"+{phone_number.replace('-', ' ')}"

def download_excel(request):
    filename = request.GET.get('filename')

    try:
        if not filename:
            latest_file = max(
                (f for f in os.listdir(EXCEL_DIR) if f.startswith("property_data_") and f.endswith(".xlsx")),
                key=lambda f: os.path.getmtime(os.path.join(EXCEL_DIR, f)),
                default=None
            )

            if not latest_file:
                return HttpResponse("No processed data available", status=400)

            filename = latest_file

        filepath = os.path.join(EXCEL_DIR, filename)

        if not os.path.exists(filepath):
            return HttpResponse("File not found", status=404)

        data_frame = pd.read_excel(filepath)
        data_frame = data_frame[data_frame['owner_name'] != 'NILL']
        data_frame = data_frame[data_frame['owner_phone'].notna() & (data_frame['owner_phone'] != '')]

        if not data_frame.empty:
            final_result = data_frame.groupby('owner_phone', as_index=False).agg({
                'UnitNumber': lambda x: ', '.join(sorted(set(x))),
                'url': lambda x: ', '.join(sorted(set(x))),
                **{col: 'first' for col in data_frame.columns if col not in ['owner_phone', 'UnitNumber', 'url']}
            })

            final_result['Deal Name'] = final_result.apply(format_deal_name, axis=1)
            final_result['permit_type'] = final_result['permit_type'].str.lower()

            deal_data = pd.DataFrame({
                'Deal Name': final_result['Deal Name'],
                'Amount': final_result['Amount'],
                'Pipeline Name': final_result['permit_type'].apply(lambda x: 'Seller Pipeline' if x in ['sell', 'buy'] else 'Landlord Pipeline'),
                'Stage': 'New enquiry',
                'Lead Source': 'Campaign',
                'Last Name': final_result['owner_name'],
                'Tag': 'Warm Lead',
                'Follow up date': datetime.today().strftime('%d/%m/%Y %H:%M'),
                'Phone': final_result['owner_phone'],
                'Description': final_result['url'],
                'Unit No': final_result['UnitNumber'],
            })

            deal_data['Phone'] = deal_data['Phone'].apply(format_phone_number)

            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                deal_data.to_excel(writer, index=False, sheet_name="CRM Data")

            output.seek(0)

            response = HttpResponse(
                output.read(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = f'attachment; filename="modified_{filename}"'
            return response

        return HttpResponse("No valid data to process", status=400)

    except Exception as e:
        return HttpResponse(f"Error processing request: {str(e)}", status=500)
    
@csrf_exempt
@csrf_exempt
def add_to_crm(request):
    """Receive property data and add it to CRM."""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            property_data = data.get('property', [])
            deal_owner_id = data.get('deal_owner_id')
            tags = data.get('tags', '')
            
            if not property_data:
                return JsonResponse({'status': 'error', 'message': 'No property data received'}, status=400)
            
            if not deal_owner_id:
                return JsonResponse({'status': 'error', 'message': 'Deal owner is required'}, status=400)
            
            formatted_tags = tags
            if tags:
                formatted_tags = ', '.join(word.title() for word in tags.split(','))
            else:
                formatted_tags = "Warm Lead"

            # Group properties by owner_phone
            owner_properties = {}
            for lead in property_data:
                if lead['owner_name'] == 'NILL' or not lead['owner_phone']:
                    continue
                
                phone = str(lead['owner_phone']).replace('+', '')
                formatted_phone = f"+{phone.replace('-', ' ')}"
                
                if formatted_phone not in owner_properties:
                    owner_properties[formatted_phone] = {
                        'owner_name': lead['owner_name'],
                        'properties': [],
                        'total_amount': 0,
                        'permit_types': set()
                    }
                
                owner_properties[formatted_phone]['properties'].append(lead)
                owner_properties[formatted_phone]['total_amount'] += float(lead.get('Amount', 0))
                
                permit_type = lead.get('permit_type', '').lower() if lead.get('permit_type') else ''
                if permit_type:
                    owner_properties[formatted_phone]['permit_types'].add(permit_type)
            
            # Process each owner's grouped properties
            for phone, owner_data in owner_properties.items():
                # Aggregate unit numbers and URLs
                unit_numbers = []
                urls = []
                for prop in owner_data['properties']:
                    units = prop.get('UnitNumber', '').split(', ')
                    unit_numbers.extend(units)
                    if prop.get('url'):
                        urls.append(prop.get('url'))
                
                # Remove duplicates and sort
                unit_numbers = sorted(set(unit_numbers))
                urls = sorted(set(urls))
                
                # Create deal name from first unit and building
                first_property = owner_data['properties'][0]
                building_name = first_property.get('BuildingNameEn', '')
                
                if len(unit_numbers) > 1:
                    deal_name = f"{unit_numbers[0]} # {building_name} (+{len(unit_numbers)-1} more)"
                else:
                    deal_name = f"{unit_numbers[0]} | {building_name}"
                
                # Determine pipeline based on permit types
                pipeline_name = 'Seller Pipeline' if any(pt in ['sell', 'buy'] for pt in owner_data['permit_types']) else 'Landlord Pipeline'
                
                # Create deal data with all properties
                deal_data = {
                    "Owner": {"id": deal_owner_id},
                    "Deal_Name": deal_name,
                    "Amount": owner_data['total_amount'],
                    "Sub_Pipeline": pipeline_name,
                    "Stage": "New enquiry",
                    "Lead_Source": "Campaign",
                    "Tag": formatted_tags,
                    "Follow_up_date": datetime.today().strftime('%Y-%m-%dT%H:%M:%S'),
                    "Last_Name": owner_data['owner_name'],
                    "Phone": phone,
                    "Description": ', '.join(urls),
                    "Unit_No": ', '.join(unit_numbers),
                    "Property_Count": len(unit_numbers),  # New field for property count
                    "Property_Value": owner_data['total_amount'],  # New field for total value
                    "Pipeline": {
                        "name": "Real Estate Pipeline",
                        "id": "6428826000000091023"
                    },
                }

                response = data_to_crm(request, deal_data)
                print(response)
                
            return JsonResponse({'status': 'success', 'message': 'Added to CRM successfully'})
            
        except json.JSONDecodeError as e:
            return JsonResponse({'status': 'error', 'message': f'Invalid JSON: {str(e)}'}, status=400)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f'Error processing data: {str(e)}'}, status=500)
    
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)


def data_to_crm(req, deal_data):
    try:
        token_handler = TokenHandler(req.session)
        zoho_token = token_handler.get_zoho_token()

        # Extract property count and value for contact creation/update
        property_count = deal_data.get('Property_Count', 1)
        property_value = deal_data.get('Property_Value', deal_data['Amount'])

        contact_payload = {
            'Last_Name': deal_data['Last_Name'],
            'Phone': deal_data['Phone'],
            'Lead_Source': deal_data['Lead_Source'],
            'Client_Value': property_value,
            'No_of_Properties': property_count
        }
        contact_phone = deal_data['Phone']

        # Prepare search URL
        search_url = f"https://www.zohoapis.com/bigin/v2/Contacts/search?phone={contact_phone}"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Zoho-oauthtoken {zoho_token}'
        }

        search_response = requests.get(search_url, headers=headers)
        contact_id = None
        if search_response.status_code == 200:
            search_response_json = search_response.json()
            if 'data' in search_response_json and search_response_json['data']:
                # Contact exists
                contact = search_response_json['data'][0]
                contact_id = contact.get('id')
                
                # Get current values for existing contact
                current_client_value = float(contact.get('Client_Value', 0))
                current_properties = int(contact.get('No_of_Properties', 0))

                print(f"Current client value: {current_client_value}")
                print(f"Current properties count: {current_properties}")
                
                # Update contact with incremented values - add the new property count and value
                update_contact_url = f"https://www.zohoapis.com/bigin/v2/Contacts/{contact_id}"
                update_payload = {
                    "data": [{
                        "Client_Value": current_client_value + property_value,
                        "No_of_Properties": current_properties + property_count
                    }]
                }
                
                # Update the contact with new values
                update_response = requests.put(update_contact_url, headers=headers, json=update_payload)
                if update_response.status_code not in [200, 201, 202]:
                    print(f"[WARNING] Failed to update contact properties: {update_response.text}")
        
        elif search_response.status_code == 204:
            create_contact_url = "https://www.zohoapis.com/bigin/v2/Contacts"
            contact_data = {"data": [contact_payload]}

            # Creating a new contact
            contact_response = requests.post(create_contact_url, headers=headers, json=contact_data)
            contact_resp_json = contact_response.json()

            if contact_response.status_code == 201 and 'data' in contact_resp_json:
                created_contact = contact_resp_json['data'][0]
                contact_id = created_contact.get('details', {}).get('id')

                if contact_id:
                    tag_url = f"https://www.zohoapis.com/bigin/v1/Contacts/actions/add_tags?ids={contact_id}&tag_names={deal_data['Tag']}&over_write=true"
                    tag_response = requests.post(tag_url, headers=headers)
                    print(f"[LOG] Contact tag response: {tag_response.text}")
                else:
                    print("[ERROR] Failed to extract contact ID from response")
                    token_handler.unset_zoho_token()
                    return JsonResponse({'status': 'error', 'message': 'Failed to create contact 1'}, status=400)
            else:
                print(f"[ERROR] Contact creation failed: {contact_response.text}")
                token_handler.unset_zoho_token()
                return JsonResponse({'status': 'error', 'message': 'Failed to create contact 2'}, status=400)
            
        else:
            print(f"[ERROR] Contact search failed: {search_response.text}")
            token_handler.unset_zoho_token()
            return JsonResponse({'status': 'error', 'message': 'Failed to search for contact'}, status=400)
        
        print("Deal creation started")
        # Remove our custom fields before creating the deal in Zoho
        deal_payload_data = {k: v for k, v in deal_data.items() if k not in ['Property_Count', 'Property_Value']}
        deal_payload_data['Contact_Name'] = {"id": contact_id}
        
        deal_payload = {
            "data": [deal_payload_data]
        }
        
        deal_url = "https://www.zohoapis.com/bigin/v2/Pipelines"
        deal_response = requests.post(deal_url, headers=headers, json=deal_payload)
        
        if deal_response.status_code == 201:
            deal_resp_data = deal_response.json()
            if 'data' in deal_resp_data and 'details' in deal_resp_data['data'][0]:
                print(f"New lead added")
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                success_message = f"{timestamp} | New Lead added successfully --- {deal_data['Deal_Name']} with {property_count} properties"
                return success_message
            else:
                print(f"Failed to create deal")
                print("Failed to extract deal ID from response")
                token_handler.unset_zoho_token()
                return JsonResponse({'status': 'error', 'message': 'Failed to create deal'}, status=400)
        else:
            print(f"Error creating deal: {deal_response.text}")
            token_handler.unset_zoho_token()
            return JsonResponse({'status': 'error', 'message': 'Failed to create deal'}, status=400)
            
    except Exception as e:
        print(f"Exception in process_deal_data: {str(e)}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    

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