from core.tenant import (
    DYNAMIC_ALIAS,
    activate_dynamic_connection,
    get_tenant_config,
    is_valid_config,
)


class TenantDatabaseRouter:
    """
    Enrutamiento estricto por tipo de modelo:

    - Modelos locales (PosTurno, PosTicketHeader, PosTicketDetail, PosPuntosCliente,
      Pr*): siempre SQLite, sin importar el tenant. El alias de SQLite en este
      proyecto es 'qcluster_db' ('default' es la Oracle del .env y la usa el código
      existente con connection.cursor()).
    - Modelos Oracle (pos.TsCaja y las tablas ``managed = False`` de core,
      facturacion, etc.): con tenant activo devuelve 'dynamic_oracle', con las
      credenciales de la sesión aplicadas a la conexión del hilo. Sin tenant
      (admin, tareas, scripts) devuelve 'default'.
    """
    sqlite_alias = 'qcluster_db'
    oracle_app_labels = {
        'core', 'facturacion', 'recibos', 'impresion', 'pos', 'logistica', 'reportes',
    }

    def _is_oracle_model(self, model):
        return model._meta.app_label in self.oracle_app_labels and not model._meta.managed

    def _route(self, model):
        if model._meta.app_label == 'pos' and model._meta.managed:
            return self.sqlite_alias
        if not self._is_oracle_model(model):
            return None
        config = get_tenant_config()
        if is_valid_config(config):
            activate_dynamic_connection(config)
            return DYNAMIC_ALIAS
        return 'default'

    def db_for_read(self, model, **hints):
        return self._route(model)

    def db_for_write(self, model, **hints):
        return self._route(model)

    def allow_relation(self, obj1, obj2, **hints):
        oracle_dbs = {'default', DYNAMIC_ALIAS}
        if obj1._state.db in oracle_dbs and obj2._state.db in oracle_dbs:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # La conexión dinámica nunca recibe migraciones.
        if db == DYNAMIC_ALIAS:
            return False
        return None


class DjangoQRouter:
    """
    Un enrutador para dirigir las operaciones de base de datos de Django-Q2
    hacia la base de datos SQLite local, evitando ORA-00933 y errores de Identity
    en Oracle 11g.
    """
    route_app_labels = {'django_q', 'admin', 'auth', 'contenttypes', 'sessions', 'pos'}

    def db_for_read(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return 'qcluster_db'
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return 'qcluster_db'
        return None

    def allow_relation(self, obj1, obj2, **hints):
        if (
            obj1._meta.app_label in self.route_app_labels or
            obj2._meta.app_label in self.route_app_labels
        ):
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.route_app_labels:
            return db == 'qcluster_db'
        # Do not allow other apps to migrate to qcluster_db
        if db == 'qcluster_db':
            return False
        return None
