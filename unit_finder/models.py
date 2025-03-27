from django.db import models
from datetime import timedelta
from django.utils.timezone import now

class Deals(models.Model):
    file = models.FileField(upload_to='data/deals/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.file.name
    

class Tokens(models.Model):
    token = models.CharField(max_length=255)
    email = models.EmailField(max_length=255, null=True, blank=True)
    password = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expired_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.token

    def is_token_active(self):
        if self.expired_at > now():
            return True
        return False

    def save(self, *args, **kwargs):
        if not self.created_at:
            self.created_at = now()
        if not self.expired_at:
            self.expired_at = self.created_at + timedelta(days=3)
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = 'Token'
        verbose_name_plural = 'Tokens'
        ordering = ['-id']