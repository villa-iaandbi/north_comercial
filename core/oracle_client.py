import os
import oracledb
from dotenv import load_dotenv

# Cargar variables de entorno, ideal por si este módulo se invoca de manera independiente
load_dotenv()

class OracleClient:
    """
    Singleton para manejar la conexión a Oracle central (11g).
    """
    _instance = None
    _connection = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(OracleClient, cls).__new__(cls, *args, **kwargs)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        # --------------------------------------------------------------------------------
        # NOTA CRÍTICA DE DISEÑO PARA ORACLE 11g:
        # El modo "Thin" de la librería python-oracledb soporta nativamente Oracle 12.1+.
        # Para conectarse a Oracle 11g DEBES habilitar el "Thick Mode".
        # Requerimiento: Oracle Instant Client (Ej. 19c) instalado en el SO y accesible.
        #
        # Descomenta la siguiente línea y/o pásale el parámetro lib_dir=r"C:\ruta\instantclient"
        # si las librerías no están en el PATH del sistema operativo.
        # --------------------------------------------------------------------------------
        try:
            oracledb.init_oracle_client() # ¡Necesario para Oracle 11g!
        except Exception as e:
            # Capturamos en caso de que ya se haya inicializado en otro punto o
            # falle silenciosamente (por ej. si está ejecutando en Thin y no hay Oracle Client).
            print(f"[Opcional/Aviso] oracledb Thick mode init: {e}")
            pass

        self.user = os.getenv("ORACLE_USER")
        self.password = os.getenv("ORACLE_PASSWORD")
        self.dsn = os.getenv("ORACLE_DSN")

    def get_connection(self):
        """
        Retorna la conexión activa y viva. Si se cayó o no existe, la instancia nuevamente.
        """
        try:
            # oracledb connection is_healthy verifica si el ping es exitoso
            if self._connection and self._connection.is_healthy():
                return self._connection
        except Exception:
            self._connection = None

        if not self._connection:
            if not self.user or not self.password or not self.dsn:
                raise ValueError("Credenciales de Oracle incompletas en el .env")
            
            self._connection = oracledb.connect(
                user=self.user,
                password=self.password,
                dsn=self.dsn
            )
            
        return self._connection

    def execute_query(self, sql, params=None):
        """
        Ejecuta un query abstraido del manejo de cursores.
        Si es select, retorna lista de diccionarios. 
        Si es DML, hace commit y retorna rowcount.
        """
        conn = self.get_connection()
        with conn.cursor() as cursor:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            
            # Verifica si el statement devolvió descripción (columnas) => es un SELECT
            if cursor.description:
                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchall()
                # Retorna lista de diccionarios {columna: valor}
                return [dict(zip(columns, row)) for row in rows]
            else:
                # DML (INSERT, UPDATE, DELETE)
                conn.commit()
                return cursor.rowcount
