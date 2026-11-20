import logging
import os
from functools import wraps

import requests
from django import forms
from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.views import View

from core.tenant import TENANT_SESSION_KEY, is_valid_config

logger = logging.getLogger(__name__)

SUPABASE_TIMEOUT = 10
SESSION_USER_KEY = 'pos_user'

MSG_EMAIL_NO_REGISTRADO = 'El correo ingresado no se encuentra registrado en la tabla de usuarios.'
MSG_USUARIO_INACTIVO = 'El usuario se encuentra inactivo.'
MSG_CLAVE_INCORRECTA = 'Contraseña incorrecta.'
MSG_SIN_EMPRESAS = 'El usuario no tiene empresas activas asignadas.'
MSG_EMPRESA_INVALIDA = 'La empresa seleccionada no es válida para este usuario.'
MSG_EMPRESA_SUSPENDIDA = 'La operación de la empresa se encuentra suspendida.'


class SupabaseError(Exception):
    """Error controlado con un mensaje apto para mostrar al cajero."""


class PosLoginForm(forms.Form):
    email = forms.EmailField(label='Correo')
    password = forms.CharField(
        label='Contraseña', widget=forms.PasswordInput(render_value=True)
    )
    id_sistema = forms.CharField(label='Empresa', required=False)


def _supabase_headers():
    """
    Cabeceras REST. north_usuarios contiene hashes de clave, por lo que se
    consulta solo desde el servidor con la service role key (si existe).
    """
    key = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or settings.SUPABASE_ANON_KEY
    return {'apikey': key, 'Authorization': f'Bearer {key}'}


def _supabase_select(table, params, error_msg):
    """GET /rest/v1/<table> y retorna la lista de filas (JSON)."""
    try:
        resp = requests.get(
            f'{settings.SUPABASE_URL}/rest/v1/{table}',
            params=params,
            headers=_supabase_headers(),
            timeout=SUPABASE_TIMEOUT,
        )
    except requests.RequestException:
        logger.exception('Supabase REST no disponible (%s)', table)
        raise SupabaseError(error_msg)
    if resp.status_code != 200:
        logger.error('Supabase %s respondió %s', table, resp.status_code)
        raise SupabaseError(error_msg)
    return resp.json()


def _verificar_clave(password_ingresado, usuario_clave_hash):
    """Verificación aislada de la clave. Por ahora comparación en texto plano."""
    if password_ingresado is None or usuario_clave_hash is None:
        return False
    return password_ingresado == usuario_clave_hash


def _fetch_usuarios_por_email(email):
    """Filas de north_usuarios cuyo usuario_email coincide (sin distinguir mayúsculas)."""
    rows = _supabase_select(
        'north_usuarios',
        {
            'select': (
                'id_sistema,id_usuario,usuario_clave_hash,usuario_nombre,'
                'usuario_email,usuario_user_bd,usuario_rol,usuario_estado'
            ),
            'usuario_email': f'ilike.{email}',
        },
        'No fue posible consultar los usuarios.',
    )
    # ilike interpreta % y _ como comodines: se confirma la igualdad exacta.
    return [r for r in rows if (r.get('usuario_email') or '').strip().lower() == email]


def _fetch_empresas(ids_sistema):
    """Filas de north_configuraciones para los id_sistema indicados."""
    if not ids_sistema:
        return []
    lista = ','.join('"{}"'.format(str(i).replace('"', '')) for i in ids_sistema)
    return _supabase_select(
        'north_configuraciones',
        {
            'select': (
                'id_sistema,nom_empresa,token,operacion_suspendida,'
                'mensaje_suspension,db_host,db_user,db_password'
            ),
            'id_sistema': f'in.({lista})',
        },
        'No fue posible leer la configuración de la empresa.',
    )


def _validar_credenciales(email, password):
    """
    Pasos B y C. Retorna las filas de north_usuarios activas con clave válida.
    Lanza SupabaseError con el mensaje a mostrar si falla alguna validación.
    """
    usuarios = _fetch_usuarios_por_email(email)
    if not usuarios:
        raise SupabaseError(MSG_EMAIL_NO_REGISTRADO)

    activos = [u for u in usuarios if (u.get('usuario_estado') or '').strip().upper() == 'A']
    if not activos:
        raise SupabaseError(MSG_USUARIO_INACTIVO)

    validos = [u for u in activos if _verificar_clave(password, u.get('usuario_clave_hash'))]
    if not validos:
        raise SupabaseError(MSG_CLAVE_INCORRECTA)
    return validos


def _resolver_empresas(usuarios):
    """
    Paso D. Retorna (empresas_habilitadas, empresas_suspendidas) a partir de los
    id_sistema de los usuarios validados.
    """
    ids = sorted({str(u['id_sistema']) for u in usuarios if u.get('id_sistema') is not None})
    empresas = _fetch_empresas(ids)
    habilitadas = [e for e in empresas if e.get('operacion_suspendida') is not True]
    suspendidas = [e for e in empresas if e.get('operacion_suspendida') is True]
    return habilitadas, suspendidas


def _mensaje_suspension(empresa):
    return empresa.get('mensaje_suspension') or MSG_EMPRESA_SUSPENDIDA


def pos_login_required(view_func):
    """Exige una sesión POS con tenant activo (sustituye a @login_required)."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not is_valid_config(request.session.get(TENANT_SESSION_KEY)):
            return redirect('pos:login')
        return view_func(request, *args, **kwargs)
    return _wrapped


def _normalizar_rol(rol):
    """Rol en mayúsculas y sin espacios; '' si viene vacío o None."""
    return str(rol or '').strip().upper()


def _es_peticion_json(request):
    accept = request.headers.get('Accept', '')
    content_type = request.headers.get('Content-Type', '')
    return (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in accept
        or 'application/json' in content_type
    )


def pos_role_required(*roles):
    """
    Exige sesión POS válida (igual que pos_login_required) y que el rol de la
    sesión esté entre los permitidos. Sin sesión redirige a pos:login; con rol
    no permitido responde 403 (JSON si la petición es AJAX/JSON).
    """
    permitidos = {_normalizar_rol(r) for r in roles}

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not is_valid_config(request.session.get(TENANT_SESSION_KEY)):
                return redirect('pos:login')
            rol = _normalizar_rol(request.session.get('usuario_rol'))
            if rol not in permitidos:
                if _es_peticion_json(request):
                    return JsonResponse({'error': 'Permiso denegado para su perfil.'}, status=403)
                return HttpResponseForbidden('Permiso denegado para su perfil.')
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


class PosLoginView(View):
    template_name = 'pos/auth/login.html'

    def get(self, request):
        return render(request, self.template_name, {'form': PosLoginForm()})

    def post(self, request):
        form = PosLoginForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})

        email = form.cleaned_data['email'].strip().lower()
        password = form.cleaned_data['password']
        id_seleccionado = (form.cleaned_data.get('id_sistema') or '').strip()

        try:
            usuarios = _validar_credenciales(email, password)
            habilitadas, suspendidas = _resolver_empresas(usuarios)

            if id_seleccionado:
                # Caso 4: la empresa elegida debe pertenecer al usuario validado.
                empresa = next(
                    (e for e in habilitadas + suspendidas
                     if str(e.get('id_sistema')) == id_seleccionado),
                    None,
                )
                if empresa is None:
                    raise SupabaseError(MSG_EMPRESA_INVALIDA)
                if empresa.get('operacion_suspendida') is True:
                    raise SupabaseError(_mensaje_suspension(empresa))
            elif not habilitadas:
                # Caso 1 (o todas las empresas están suspendidas).
                if suspendidas:
                    raise SupabaseError(_mensaje_suspension(suspendidas[0]))
                raise SupabaseError(MSG_SIN_EMPRESAS)
            elif len(habilitadas) == 1:
                # Caso 2: una sola empresa, sin selección.
                empresa = habilitadas[0]
            else:
                # Caso 3: varias empresas, se pide seleccionar.
                return render(request, self.template_name, {
                    'form': form,
                    'empresas': [
                        {'id_sistema': e.get('id_sistema'), 'nom_empresa': e.get('nom_empresa')}
                        for e in habilitadas
                    ],
                })

            usuario = next(
                u for u in usuarios if str(u.get('id_sistema')) == str(empresa.get('id_sistema'))
            )
            tenant_config = {
                'HOST': empresa.get('db_host'),
                'USER': empresa.get('db_user'),
                'PASSWORD': empresa.get('db_password'),
            }
            if not is_valid_config(tenant_config):
                raise SupabaseError('La configuración Oracle de la empresa está incompleta.')
        except SupabaseError as exc:
            form.add_error(None, str(exc))
            return render(request, self.template_name, {'form': form})

        rol = _normalizar_rol(usuario.get('usuario_rol'))
        request.session.cycle_key()
        request.session[TENANT_SESSION_KEY] = tenant_config
        request.session['token_empresa'] = empresa.get('token')
        request.session['id_sistema'] = empresa.get('id_sistema')
        request.session['nom_empresa'] = empresa.get('nom_empresa')
        request.session['id_usuario'] = usuario.get('id_usuario')
        request.session['usuario_nombre'] = usuario.get('usuario_nombre')
        request.session['usuario_rol'] = rol
        request.session[SESSION_USER_KEY] = {
            'id': usuario.get('id_usuario'),
            'email': usuario.get('usuario_email') or email,
            'nombre': usuario.get('usuario_nombre'),
            'rol': rol,
        }
        return redirect('reportes:dashboard')


def pos_logout(request):
    request.session.flush()
    return redirect('pos:login')
