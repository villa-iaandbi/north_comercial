import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings
from django.test import Client

settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

client = Client()

response = client.get('/pos/cajas/')
print(f"Status Code: {response.status_code}")

response_nueva = client.get('/pos/cajas/nueva/')
print(f"Status Code (nueva): {response_nueva.status_code}")
