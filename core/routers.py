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
