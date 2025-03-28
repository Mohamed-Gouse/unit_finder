from django.urls import reverse
import requests
import pandas as pd
from django.shortcuts import redirect, render
from django.http import HttpResponse, JsonResponse
import json, os
from excel_app.models import MergedFile
from datetime import datetime
import threading
import uuid
from django.views.decorators.csrf import csrf_exempt
import time
from io import BytesIO
from telethon import TelegramClient, events
from queue import Queue
import asyncio
import logging
import re
from .models import Deals, Tokens
token = 'apify_api_ccc4jjrBaNvKbAO8CE9LWOdSAytJSy1TDfSS'

# Base directory for storing Excel files with timestamps
EXCEL_DIR = "property_exports"
os.makedirs(EXCEL_DIR, exist_ok=True)

class TokenHandler:
    def __init__(self, session):
        self.session = session
        self.zoho_refresh_token = os.getenv("ZOHO_REFRESH_TOKEN", "")
        self.zoho_client_id = os.getenv("ZOHO_CLIENT_ID", "")
        self.zoho_client_secret = os.getenv("ZOHO_CLIENT_SECRET", "")
        self.zoho_token_url = os.getenv("ZOHO_TOKEN_URL", "https://accounts.zoho.com/oauth/v2/token")

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

class URLProcessorUser:
    def __init__(self, api_id, api_hash, bot_user_id):
        print("Initializing Telegram User Client...")
        self.client = TelegramClient("unit_finder", api_id, api_hash)
        self.bot_user_id = bot_user_id
        self.queue = Queue()
        self.responses = []
        self.last_message = None
        self.response_received = asyncio.Event()
        self.response_count = 0
        print("Telegram User Client initialized successfully.")

        @self.client.on(events.NewMessage(from_users=self.bot_user_id))
        async def handle_responses(event):
            message_text = event.message.text
            print(f"Received message: {message_text[:50]}..." if len(message_text) > 50 else f"Received message: {message_text}")
            
            self.responses.append(message_text)
            self.last_message = event.message
            self.response_count += 1

            if event.message.reply_markup:
                buttons = [btn.text for row in event.message.reply_markup.rows for btn in row.buttons]
                print(f"Buttons available: {buttons}")

            self.response_received.set()

    async def send_message_and_wait_for_response(self, message, expected_responses=1):
        """ Sends a message and waits for the specified number of responses """
        try:
            self.response_received.clear()
            initial_count = self.response_count
            await self.client.send_message(self.bot_user_id, message)

            while self.response_count < initial_count + expected_responses:
                try:
                    await asyncio.wait_for(self.response_received.wait(), 10)
                    self.response_received.clear()
                except asyncio.TimeoutError:
                    print(f"Timeout waiting for response after sending: {message}")
                    break
            
            return self.last_message
        except Exception as ex:
            logging.error(f"Error in send_message_and_wait_for_response: {ex}")
            return None

    async def click_button_containing(self, button_text, message, expected_responses=1):
        if not message or not message.reply_markup:
            print(f"No reply markup available to click button containing '{button_text}'")
            return False

        clicked = False
        for row_idx, row in enumerate(message.reply_markup.rows):
            for btn_idx, button in enumerate(row.buttons):
                button_label = getattr(button, "text", "")
                if button_text.lower() in button_label.lower():
                    print(f"Clicking button '{button_label}'...")
                    
                    self.response_received.clear()
                    initial_count = self.response_count
                    await message.click(row_idx, btn_idx)
                    clicked = True

                    while self.response_count < initial_count + expected_responses:
                        try:
                            await asyncio.wait_for(self.response_received.wait(), 10)
                            self.response_received.clear()
                        except asyncio.TimeoutError:
                            print(f"Timeout waiting for response after clicking: {button_label}")
                            break
                    
                    return True
        
        if not clicked:
            print(f"No button containing '{button_text}' found in available buttons")
        return False

    async def process_url(self, url):
        try:
            self.responses = []
            self.last_message = None
            self.response_count = 0
            
            print("\n=== Step 1: Sending /start command ===")
            message = await self.send_message_and_wait_for_response("/start", expected_responses=2)
            
            print("\n=== Step 2: Clicking first 'Get Unit' button ===")
            if message:
                await self.click_button_containing("Get Unit", message, expected_responses=1)
            else:
                print("No message object available after /start")
            
            print(f"\n=== Step 3: Sending URL: {url} ===")
            message = await self.send_message_and_wait_for_response(url, expected_responses=2)
            
            print("\n=== Step 4: Clicking second 'Get Unit' button after URL submission ===")
            if self.last_message:
                await self.click_button_containing("Get Unit", self.last_message, expected_responses=1)

            return self.responses[-1] if self.responses else None

        except Exception as ex:
            logging.error(f"Error processing URL {url}: {ex}")
            return None

    def parse_response(self, url, response: str) -> dict:
        """ Extracts relevant information from the response """
        data = {
            "url": url,
            "area": re.search(r"• Area:\s*(.+)", response).group(1) if re.search(r"• Area:\s*(.+)", response) else "",
            "master_project": re.search(r"• Master Project:\s*(.+)", response).group(1) if re.search(r"• Master Project:\s*(.+)", response) else "",
            "BuildingNameEn": re.search(r"• Project:\s*(.+)", response).group(1) if re.search(r"• Project:\s*(.+)", response) else "",
            "UnitNumber": re.search(r"• 🔑 Property Number:\s*(.+)", response).group(1) if re.search(r"• 🔑 Property Number:\s*(.+)", response) else "",
            "property_type": re.search(r"• Type:\s*(.+)", response).group(1) if re.search(r"• Type:\s*(.+)", response) else "",
            "size": re.search(r"• Area:\s*(.+?)\s*sqm", response).group(1) if re.search(r"• Area:\s*(.+?)\s*sqm", response) else "",
            "rooms": re.search(r"• Rooms:\s*(.+)", response).group(1) if re.search(r"• Rooms:\s*(.+)", response) else "",
        }

        if not any(data[field] for field in data if field != "url"):
            print("All extracted fields are empty. Setting values to 'null'.")
            for field in data:
                if field != "url":
                    data[field] = "null"
        return data

# Dictionary to track processing status
processing_tasks = {}
class PropertyProcessor:
    def __init__(self, task_id, urls_list, source):
        self.task_id = task_id
        self.urls_list = urls_list
        self.source = source
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
            headers = ["url", "Area", "BuildingNameEn", "UnitNumber",
                      "property_type", "size", "rooms", "name", "phone"]
            df = pd.DataFrame(columns=headers)
            
            # Save initial empty file
            with pd.ExcelWriter(self.excel_filename, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Properties')
            
            # Get owner details lookup source
            merged_file = MergedFile.objects.order_by('-created_at').first()
            
            for url in self.urls_list:
                # Process URL based on source
                if self.source == 'api':
                    url_data = self._process_api_url(url, merged_file)
                    print(url_data)
                elif self.source == 'bot':
                    url_data = self._process_bot_url(url, merged_file)
                    print(url_data)
                else:
                    url_data = []
                    print(f"Unknown source: {self.source}")
                
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
    
    def _process_api_url(self, url, merged_file):
        """Process a single URL using API and return the data (formerly _process_single_url)"""
        if not url.strip():
            return []
            
        token = Tokens.objects.first().token
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
                building_name = item.get("BuildingNameEn", "")
                unit_number = item.get("PropertyUnitNumber", "")
                
                # Fetch owner details
                owner_details = {}
                if merged_file:
                    owner_details = merged_file.get_owner_details(building_name, unit_number)
                
                formatted_items.append({
                    'url': item.get("Url", ""),
                    "Area": item.get("ZoneNameEn", ""),
                    "BuildingNameEn": building_name,
                    "UnitNumber": unit_number,
                    "property_type": item.get("PropertyType", ""),
                    "size": item.get("PropertySize", ""),
                    "rooms": item.get("Bedrooms", ""),
                    "name": owner_details.get('owner_name', 'NIL'),
                    "phone": owner_details.get('owner_phone', 'NIL'),
                })
            
            return formatted_items
            
        except requests.RequestException as e:
            print(f"Request error for {url}: {str(e)}")
            return []
        except Exception as e:
            print(f"General error processing {url}: {str(e)}")
            return []
    
    def _process_bot_url(self, url, merged_file):
        API_ID = int(os.getenv("API_ID", ""))
        API_HASH = os.getenv("API_HASH", "")
        BOT_USER_ID = os.getenv("BOT_USER_ID", "")

        try:
            print("Processing URL with bot...")

            async def process_with_bot():
                try:
                    processor = URLProcessorUser(API_ID, API_HASH, BOT_USER_ID)
                    await processor.client.start()
                    
                    response = await processor.process_url(url)

                    if response:
                        extracted_data = processor.parse_response(url, response)
                    else:
                        extracted_data = {}

                    return extracted_data
                finally:
                    await processor.client.disconnect()

            extracted_data = asyncio.run(process_with_bot())

            owner_details = {}
            if merged_file:
                building_name = extracted_data.get("BuildingNameEn", "")
                unit_number = extracted_data.get("UnitNumber", "")
                owner_details = merged_file.get_owner_details(building_name, unit_number)

            return [{
                'url': url.strip(),
                "Area": extracted_data.get("area", "NIL"),
                "BuildingNameEn": extracted_data.get("BuildingNameEn", "NIL"),
                "UnitNumber": extracted_data.get("UnitNumber", "NIL"),
                "property_type": extracted_data.get("property_type", "NIL"),
                "size": extracted_data.get("size", "NIL"),
                "rooms": extracted_data.get("rooms", "NIL"),
                "owner_name": owner_details.get('owner_name', 'NIL'),
                "owner_phone": owner_details.get('owner_phone', 'NIL'),
            }]

        except Exception as e:
            print(f"Bot processing error for {url}: {str(e)}")
            return []

def api_token(request):
    if request.method == 'POST':
        token = request.POST.get('token', '')
        if not token:
            return JsonResponse({'status': 'error', 'message': 'Token is required'}, status=400)
        
        token_obj = Tokens.objects.filter(token=token).first()
        if token_obj:
            if token_obj.is_token_active():
                return JsonResponse({'status': 'success', 'message': 'Token already exists'})
            else:
                return JsonResponse({'status': 'error', 'message': 'Token expired'}, status=400)
        else:
            token_obj = Tokens.objects.create(token=token)
            token_obj.save()
            return JsonResponse({'status': 'success', 'message': 'Token updated successfully'})

def index(request):
    """Main view for URL processing form and results display"""
    token_obj = Tokens.objects.all().first()
    token = token_obj.token if token_obj else ""
    context = {
        'token': token,
        'processing_status': None,
        'task_id': None,
        'data_available': False
    }
    
    if request.method == 'POST':
        if 'urls' in request.POST:
            # Start new processing job
            urls = request.POST.get('urls', '').strip()
            source = request.POST.get('source', '')
            
            urls_list = [url for url in urls.splitlines() if url.strip()]
            
            if not urls_list:
                context['error'] = "Please provide at least one valid URL"
                return render(request, 'index.html', context)
            
            if not source or source not in ['api', 'bot']:
                context['error'] = "Please select a valid source for processing (API or Bot)"
                return render(request, 'index.html', context)
            
            # Generate unique task ID
            task_id = str(uuid.uuid4())
            
            # Create processor with source and start thread
            processor = PropertyProcessor(task_id, urls_list, source)
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
        columns = ["url", "BuildingNameEn", "UnitNumber", "property_type", "name", "phone"]
        df = df[columns]
        response_data['preview_html'] = df.to_html(classes='table table-striped table-hover', index=False)
    
    # Add download information if completed
    if processor.status == 'completed':
        response_data['excel_filename'] = os.path.basename(processor.excel_filename)
        
        # Convert processed data to HTML for display
        if processor.processed_data:
            df = pd.DataFrame(processor.processed_data)
            columns = ["url", "BuildingNameEn", "UnitNumber", "property_type", "name", "phone"]
            df = df[columns]
            response_data['table_html'] = df.to_html(classes='table table-striped table-hover', index=False)
            response_data['data_json'] = json.dumps(processor.processed_data)
        
        # Add deal owners data
        response_data['deal_owners'] = get_deal_owners(request)
    
    return JsonResponse(response_data)

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
        if not data_frame.empty:       
            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                data_frame.to_excel(writer, index=False, sheet_name="CRM Data")
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

            for lead in property_data:
                if lead['name'] == 'NIL' or lead['phone'] == 'NIL':
                    continue
                units = lead['UnitNumber'].split(', ')
                if len(units) > 1:
                    deal_name = f"{units[0]} # {lead['BuildingNameEn']}"
                else:
                    deal_name = f"{units[0]} | {lead['BuildingNameEn']}"
                
                phone = str(lead['owner_phone']).replace('+', '')
                formatted_phone = f"+{phone.replace('-', ' ')}"
                
                permit_type = lead['permit_type'].lower() if lead['permit_type'] else ''
                pipeline_name = 'Seller Pipeline' if permit_type in ['sell', 'buy'] else 'Landlord Pipeline'
                
                deal_data = {
                    "Owner": {"id": deal_owner_id},
                    "Deal_Name": deal_name,
                    "Amount": lead['Amount'],
                    "Sub_Pipeline": pipeline_name,
                    "Stage": "New enquiry",
                    "Lead_Source": "Campaign",
                    "Tag": formatted_tags,
                    "Follow_up_date": datetime.today().strftime('%Y-%m-%dT%H:%M:%S'),
                    "Last_Name": lead['owner_name'],
                    "Phone": formatted_phone,
                    # "Last_Name": "Mohamed Gouse",
                    # "Phone": "+91 9048567736",
                    "Description": lead['url'],
                    "Unit_No": lead['UnitNumber'],
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
            return JsonResponse({'status': 'error', 'message': f'Error processing data: {str(e)}'}, status=400)
    
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

def data_to_crm(req, deal_data):
    try:
        print(deal_data)

        token_handler = TokenHandler(req.session)
        zoho_token = token_handler.get_zoho_token()

        contact_payload = {
            'Last_Name': deal_data['Last_Name'],
            'Phone': deal_data['Phone'],
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
        deal_payload = {
            "data": [{
                "Owner": deal_data["Owner"],
                "Deal_Name": deal_data["Deal_Name"],
                "Amount": deal_data["Amount"],
                "Sub_Pipeline": deal_data["Sub_Pipeline"],
                "Stage": deal_data["Stage"],
                "Lead_Source": deal_data["Lead_Source"],
                "Tag": deal_data["Tag"],
                "Follow_up_date": deal_data["Follow_up_date"],
                "Description": deal_data["Description"],
                "Pipeline": deal_data["Pipeline"],
                "Unit_No": deal_data["Unit_No"],
                "Contact_Name": {"id": contact_id},
            }]
        }
        
        deal_url = "https://www.zohoapis.com/bigin/v2/Pipelines"
        deal_response = requests.post(deal_url, headers=headers, json=deal_payload)
        
        if deal_response.status_code == 201:
            deal_resp_data = deal_response.json()
            if 'data' in deal_resp_data and 'details' in deal_resp_data['data'][0]:
                print(f"New lead added")
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                success_message = f"{timestamp} | New Lead added successfully --- {deal_data['Deal_Name']}"
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

