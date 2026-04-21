"""
Django settings for north_comercial project.
"""

from pathlib import Path
import os
import oracledb
from dotenv import load_dotenv

# FORZAR MODO THICK NATIVO DE ORACLE (Obligatorio para 11g)
try:
    oracledb.init_oracle_client()
except Exception as e:
    pass

# Carga .env para las configuraciones locales
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
SECRET_KEY = 'django-insecure--9ykgp*2uyape@gl43fp5ub)3ddo1_8(!xajj)vay4^i1_k+9s'
DEBUG = True
ALLOWED_HOSTS = []

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
    'facturacion',
    'impresion',
    'pos',
    'logistica',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.oracle',
        'NAME': os.getenv('ORACLE_DSN', ''),
        'USER': os.getenv('ORACLE_USER', ''),
        'PASSWORD': os.getenv('ORACLE_PASSWORD', ''),
        'HOST': '',
        'PORT': '',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ----------------------------------------------------------------------
# FIX PARA ORACLE 11G: BYPASS DE VERSIÓN SOPORTADA EN DJANGO
# Django 5+ lanza "NotSupportedError: Oracle 19 or later is required".
# Como estamos usando Thick Mode en 11g y queries estándar, lo saltamos.
# ----------------------------------------------------------------------
from django.db.backends.oracle.base import DatabaseWrapper
DatabaseWrapper.check_database_version_supported = lambda self: True
