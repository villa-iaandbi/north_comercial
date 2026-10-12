import sys
import oracledb

# --- Inicialización Oracle Thick Mode ---
try:
    if sys.platform.startswith("win"):
        oracledb.init_oracle_client()
    else:
        oracledb.init_oracle_client(lib_dir="/opt/oracle/instantclient_23_26")
except Exception:
    pass

# --- Compatibilidad Pandas (Retrocompatibilidad para Windows Server / Linux) ---
try:
    import pandas as pd
    if not hasattr(pd.DataFrame, "applymap"):
        pd.DataFrame.applymap = pd.DataFrame.map
except ImportError:
    pass
