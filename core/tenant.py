"""
Identidad Multi-Tenant por petición.

Cada cajero se autentica contra Supabase, que entrega las credenciales de la base
Oracle de su empresa. Esas credenciales viajan en ``request.session['tenant_db_config']``
y este módulo las expone al resto del proceso mediante ``threading.local()``.
"""
import threading

from django.conf import settings
from django.db import connections

TENANT_SESSION_KEY = 'tenant_db_config'
DYNAMIC_ALIAS = 'dynamic_oracle'
REQUIRED_KEYS = ('HOST', 'USER', 'PASSWORD')

_state = threading.local()


def set_tenant_config(config):
    _state.config = config


def get_tenant_config():
    return getattr(_state, 'config', None)


def clear_tenant_config():
    _state.__dict__.pop('config', None)


def is_valid_config(config):
    return isinstance(config, dict) and all(config.get(k) for k in REQUIRED_KEYS)


def activate_dynamic_connection(config):
    """
    Apunta el alias ``dynamic_oracle`` del HILO ACTUAL a las credenciales del tenant.

    La conexión se configura a partir de la plantilla Oracle Thick de
    ``settings.DATABASES['default']`` (ENGINE, OPTIONS, etc.) sustituyendo
    NAME (HOST/DSN), USER y PASSWORD del tenant.

    ``settings.DATABASES['dynamic_oracle']`` es un diccionario compartido por todos
    los hilos: mutarlo directamente haría que dos peticiones simultáneas de empresas
    distintas se pisaran las credenciales. Por eso cada conexión (que Django mantiene
    por hilo) recibe su propia copia del diccionario.
    """
    conn = connections[DYNAMIC_ALIAS]
    settings_dict = conn.settings_dict
    if (
        settings_dict.get('USER') == config['USER']
        and settings_dict.get('PASSWORD') == config['PASSWORD']
        and settings_dict.get('NAME') == config['HOST']
    ):
        return conn

    if conn.in_atomic_block:
        raise RuntimeError('No se puede cambiar de tenant dentro de una transacción.')
    conn.close()
    conn.settings_dict = {
        **settings.DATABASES['default'],
        'NAME': config['HOST'],
        'USER': config['USER'],
        'PASSWORD': config['PASSWORD'],
    }
    return conn


def get_oracle_connection():
    """Conexión Oracle del tenant activo; ``default`` (.env) si no hay tenant."""
    config = get_tenant_config()
    if is_valid_config(config):
        return activate_dynamic_connection(config)
    return connections['default']


class TenantMiddleware:
    """
    Copia ``session['tenant_db_config']`` al ``threading.local()`` al iniciar la
    petición y lo limpia siempre al terminar, para que el hilo (reutilizado por el
    servidor) no arrastre credenciales de una empresa a la petición de otra.
    Debe ir después de SessionMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        config = request.session.get(TENANT_SESSION_KEY)
        if is_valid_config(config):
            set_tenant_config(config)
        else:
            clear_tenant_config()
        try:
            return self.get_response(request)
        finally:
            clear_tenant_config()
