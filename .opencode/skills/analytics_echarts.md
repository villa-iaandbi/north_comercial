# Analytics & BI Skills (ECharts & Pandas)

## Reglas Maestras del Módulo de Analítica

1.  **Fuente de Datos (Source of Truth):**
    *   Toda lectura analítica debe consultar **vistas agregadas de Oracle** (ej. `BI_VENTASNETAS`), nunca las tablas transaccionales crudas en tiempo real.
    *   La conexión se realiza con `oracledb` en **Thick mode**.
    *   El tenant es fijo: `ID_SISTEMA = '1'`.
    *   Nombres de objetos Oracle (vistas, columnas) siempre en **MAYÚSCULAS**.

2.  **Precisión Financiera:**
    *   **Prohibido** el uso de `float()` para importes monetarios. Es obligatorio usar `decimal.Decimal` para toda operación y casteo de valores de dinero.
    *   La zona horaria de referencia es `America/Bogota`.

3.  **Procesamiento de Datos (Pandas):**
    *   La construcción de matrices pivot se estandariza con `pandas.pivot_table`.
    *   Ejemplo de matriz pivot de ventas por cliente y mes:
        ```python
        df_pivot = pd.pivot_table(
            df,
            index='NOM_CLIENTE',
            columns='MES_NUM',
            values='VENTA_SIN_IVA',
            aggfunc='sum',
            fill_value=0
        )
        ```

4.  **Respuesta al Frontend (API para ECharts):**
    *   El formato de respuesta para los gráficos debe ser compatible con la estructura de `series` de Apache ECharts. Esto implica enviar los datos desacoplados:
        *   Un array para las categorías del eje X (ej. meses, vendedores).
        *   Un array de valores numéricos limpios para el eje Y principal (ej. ventas).
        *   Un array de enteros para el eje Y secundario (ej. cantidad de clientes).
    *   No enviar al frontend una lista de diccionarios que el cliente deba procesar.

5.  **Flujo de Desarrollo (Spec-Driven Development):**
    *   Ningún cambio, refactorización o nueva funcionalidad en el módulo de analítica puede realizarse si no está respaldado por un **documento de especificación (SPEC)**.
    *   Los SPECs deben estar ubicados en `C:\Movil_apps\.ai\specs\north_comercial\`.
    *   Esta regla solo se puede omitir si el usuario lo indica explícitamente.
