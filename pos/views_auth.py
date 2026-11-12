import logging
from functools import wraps

import requests
from django import forms
from django.conf import settings
from django.shortcuts import redirect, render
from django.views import View

from core.tenant import TENANT_SESSION_KEY, is_valid_config

logger = logging.getLogger(__name__)

SUPABASE_TIMEOUT = 10
SESSION_USER_KEY = 'pos_user'


class SupabaseError(Exception):
    """Error controlado con un mensaje apto para mostrar al cajero."""


class PosLoginForm(forms.Form):
    email = forms.EmailField(label='Correo')
    password = forms.CharField(label='Contraseña', widget=forms.PasswordInput)
    token_empresa = forms.CharField(label='Token de empresa', max_length=200)


def _supabase_authenticate(email, password):
    """Login por REST (GoTrue). Retorna el JSON con access_token y user."""
    try:
        resp = requests.post(
            f'{settings.SUPABASE_URL}/auth/v1/token?grant_type=password',
            headers={'apikey': settings.SUPABASE_ANON_KEY},
            json={'email': email, 'password': password},
            timeout=SUPABASE_TIMEOUT,
        )
    except requests.RequestException:
        logger.exception('Supabase auth no disponible')
        raise SupabaseError('No fue posible contactar el servicio de autenticación.')
    if resp.status_code in (400, 401, 403):
        raise SupabaseError('Credenciales inválidas.')
    if resp.status_code != 200:
        logger.error('Supabase auth respondió %s', resp.status_code)
        raise SupabaseError('Error en el servicio de autenticación.')
    return resp.json()


def _fetch_tenant_config(token_empresa, access_token):
    """
    GET /rest/v1/north_configuraciones filtrando por token, autorizado con el
    Bearer del usuario (la política RLS de Supabase decide qué filas ve).
    """
    try:
        resp = requests.get(
            f'{settings.SUPABASE_URL}/rest/v1/north_configuraciones',
            params={
                'select': 'db_host,db_user,db_password',
                'token': f'eq.{token_empresa}',
            },
            headers={
                'apikey': settings.SUPABASE_ANON_KEY,
                'Authorization': f'Bearer {access_token}',
            },
            timeout=SUPABASE_TIMEOUT,
        )
    except requests.RequestException:
        logger.exception('Supabase REST no disponible')
        raise SupabaseError('No fue posible leer la configuración de la empresa.')
    if resp.status_code != 200:
        logger.error('Supabase north_configuraciones respondió %s', resp.status_code)
        raise SupabaseError('No fue posible leer la configuración de la empresa.')

    rows = resp.json()
    if not rows:
        raise SupabaseError('Token de empresa inválido o sin acceso.')
    row = rows[0]
    config = {
        'HOST': row.get('db_host'),
        'USER': row.get('db_user'),
        'PASSWORD': row.get('db_password'),
    }
    if not is_valid_config(config):
        raise SupabaseError('La configuración Oracle de la empresa está incompleta.')
    return config


def pos_login_required(view_func):
    """Exige una sesión POS con tenant activo (sustituye a @login_required)."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not is_valid_config(request.session.get(TENANT_SESSION_KEY)):
            return redirect('pos:login')
        return view_func(request, *args, **kwargs)
    return _wrapped


class PosLoginView(View):
    template_name = 'pos/auth/login.html'

    def get(self, request):
        return render(request, self.template_name, {'form': PosLoginForm()})

    def post(self, request):
        form = PosLoginForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})

        token_empresa = form.cleaned_data['token_empresa'].strip()
        try:
            auth = _supabase_authenticate(
                form.cleaned_data['email'], form.cleaned_data['password']
            )
            tenant_config = _fetch_tenant_config(token_empresa, auth.get('access_token'))
        except SupabaseError as exc:
            form.add_error(None, str(exc))
            return render(request, self.template_name, {'form': form})

        user = auth.get('user') or {}
        request.session.cycle_key()
        request.session[TENANT_SESSION_KEY] = tenant_config
        request.session['token_empresa'] = token_empresa
        request.session[SESSION_USER_KEY] = {
            'id': user.get('id'),
            'email': user.get('email') or form.cleaned_data['email'],
        }
        return redirect('pos:apertura_turno')


def pos_logout(request):
    request.session.flush()
    return redirect('pos:login')
