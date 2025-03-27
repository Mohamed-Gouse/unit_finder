from django.contrib import admin
from .models import Tokens
# Register your models here.

class TokensAdmin(admin.ModelAdmin):
    list_display = ('token', 'email', 'created_at', 'expired_at', 'is_token_active')

admin.site.register(Tokens, TokensAdmin)