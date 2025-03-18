from django.db import models

class Deals(models.Model):
    file = models.FileField(upload_to='data/deals/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.file.name