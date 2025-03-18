from django.db import models
import uuid
import os
import pandas as pd

def get_upload_path(instance, filename):
    """Generate a unique path for uploaded files."""
    return os.path.join('uploads', f"{uuid.uuid4().hex}_{filename}")

class ExcelFile(models.Model):
    """Model to store uploaded Excel files."""
    file = models.FileField(upload_to=get_upload_path)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)
    processed_file = models.FileField(upload_to='processed', null=True, blank=True)
    
    def __str__(self):
        return os.path.basename(self.file.name)
    
    def filename(self):
        return os.path.basename(self.file.name)
    
    def processed_filename(self):
        if self.processed_file:
            return os.path.basename(self.processed_file.name)
        return None

class MergedFile(models.Model):
    """Model to store merged Excel files."""
    file = models.FileField(upload_to='processed')
    created_at = models.DateTimeField(auto_now_add=True)
    files = models.ManyToManyField(ExcelFile, related_name='merged_files')
    
    def __str__(self):
        return os.path.basename(self.file.name)
    
    def filename(self):
        return os.path.basename(self.file.name)
    
    def get_owner_details(self, building_name, unit_number):
        try:
            df = pd.read_excel(self.file.path)

            if building_name and isinstance(building_name, str) and building_name.lower().startswith("tag"):
                match = df[(df['building no'].astype(str).str.lower() == building_name.lower()) & 
                        (df['procedurepartytypenameen'] == 'Buyer')]
            else:
                match = df[(df['buildingnameen'].astype(str).str.lower() == str(building_name).lower()) & 
                        (df['unitnumber'] == unit_number) & 
                        (df['procedurepartytypenameen'] == 'Buyer')]

            if not match.empty:
                return {
                    'owner_name': match.iloc[0].get('nameen', 'NILL'),
                    'owner_phone': match.iloc[0].get('mobile', 'NILL')
                }
        except Exception as e:
            print(f"Error fetching owner details: {e}")

        return {
            'owner_name': 'NILL',
            'owner_phone': 'NILL'
        }
