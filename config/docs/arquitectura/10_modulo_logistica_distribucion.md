# Capítulo 10: Arquitectura del Módulo de Logística y Distribución

Este capítulo detalla la arquitectura de software y los flujos operativos diseñados para gestionar el ciclo de vida completo de la distribución de mercancía. El módulo conecta el back-office desarrollado en Django (`north_comercial`), la aplicación móvil para transportadores desarrollada en Flutter (`north`), y el portal de auditoría de bodega (`north_admin`), garantizando la integridad de los datos desde la asignación de carga hasta la conciliación financiera y actualización en el ERP legado (Oracle 11g).

## 10.1. Fase de Despacho y Enrutamiento (Back-Office `north_comercial`)

El proceso logístico inicia en el centro de distribución mediante el sistema web principal.

*   **Gestión de Entregas**: Creación del modelo `Entrega` (o Planilla de Ruta) con un sistema de consecutivo propio y trazabilidad de estados (Borrador, En Ruta, Liquidada).
*   **Asignación de Carga**: Interfaz de usuario dinámica que permite filtrar facturas electrónicas con estado "Sin Asignar", ordenarlas lógicamente por zonas geográficas y vincularlas a una `Entrega` específica a cargo de un transportador y vehículo determinado.
*   **Documentación Física**: Motor de renderizado (WeasyPrint) para la generación de la "Planilla de Envíos" en PDF. Este documento consolida la lista de clientes, consecutivos de facturas, datos del transportador designado y la totalización de pesos/volúmenes para el control de carga.
*   **Puente de Sincronización (DOORS/Supabase)**: Una vez la entrega es autorizada, un *webhook* o *background worker* (mediante `django-q2`) se encarga del volcado de los datos de la entrega hacia las tablas relacionales de Supabase. Esto expone inmediatamente la ruta al dispositivo móvil del transportador.

## 10.2. Fase de Operación en Calle (App Móvil `north` - Flutter)

El transportador opera una aplicación móvil construida bajo el ecosistema de Flutter, diseñada para condiciones de alta movilidad y conectividad intermitente.

*   **Arquitectura Offline-First**: Implementación de una base de datos local embebida (Drift/SQLite). Los transportadores descargarán su ruta al iniciar la jornada operativa, permitiendo trabajar en zonas sin cobertura de red. La sincronización hacia Supabase se gestiona en *background* mediante un *SyncOrchestrator* tan pronto se detecta conexión a internet.
*   **UX/UI Accesible**: Diseño ergonómico de alta legibilidad, utilizando tipografías claras, botones de gran tamaño y flujos de pantalla intuitivos pensados para reducir la carga cognitiva del usuario en calle, minimizando la probabilidad de errores de digitación durante las entregas bajo presión.
*   **Gestión de la Parada (Flujo de Decisión por Factura)**: En cada punto de entrega, el sistema obliga al transportador a definir el estado de la factura a través de un árbol de decisión estricto:
    *   **Escenario A (Pago Total / Entrega Perfecta)**: El cliente recibe a satisfacción y paga la totalidad. Se genera automáticamente el Recibo de Caja (RC) por el 100% del valor de la factura.
    *   **Escenario B (Devolución Total / Rechazo)**: El cliente rechaza la entrega. Se exige marcación del motivo de rechazo y el sistema genera internamente una pre-Nota Crédito (NC) por el 100% de la mercancía, preservando intacto el inventario a bordo.
    *   **Escenario C (Entrega Parcial / Novedades)**: El cliente recibe solo una parte del pedido. El sistema despliega una grilla de edición de cantidades (esquema *Anti-Paginación*) mostrando las cajas/unidades originales. El transportador ajusta lo devuelto, el motor calcula la diferencia matemática, y automáticamente genera:
        1. La pre-NC por el valor y cantidades exactas devueltas.
        2. El RC por el saldo final aceptado (que corresponde al dinero recaudado).

## 10.3. Fase de Verificación Física (Portal `north_admin`)

Cuando el camión retorna al centro de distribución (CEDI), intervienen los auditores logísticos.

*   **Auditoría de Bodega**: Interfaz web fluida y optimizada para tablets, donde el Jefe de Bodega intercepta las pre-Notas Crédito (generadas desde la calle por los transportadores) tan pronto se sincronizan.
*   **Consolidación y Validación Cruzada**: Vista gerencial del inventario devuelto (agrupado lógicamente por artículo/SKU). El sistema permite hacer el *check-in* físico de la mercancía descargada contra lo reportado lógicamente por el transportador en el dispositivo móvil. Solo cuando cuadran ambos valores se autoriza el ingreso formal al inventario.

## 10.4. Fase de Conciliación y Cierre Financiero (Back-Office `north_comercial`)

El paso final garantiza la sanidad financiera de la compañía y la correcta inyección de la operación al ERP.

*   **Arqueo de Caja (Tesorería)**: Pantalla de cuadre financiero consolidado por `Entrega` (Planilla de Ruta). El cajero visualiza lo que el transportador teóricamente debe entregar en efectivo o transferencias.
*   **Fórmula de Liquidación**: El balance debe obedecer estrictamente a:
  
  `Liquidación = (Total Facturas Despachadas) - (NC Devoluciones) + (RC Pagos de la Ruta) + (RC Cartera Externa)`

*   **Autorización Final**: Al confirmar matemáticamente el cierre de la planilla y recibir el dinero/soportes, Tesorería presiona el botón de liquidación. Los Recibos de Caja (RC) y Notas Crédito (NC) transicionan internamente del estado "Borrador" a "Definitivo".
*   **Sincronización DOORS (Oracle 11g)**: El cambio a estado "Definitivo" dispara el *pipeline* de escritura final hacia la base de datos Oracle 11g. Se inyectan las tramas correspondientes para afectar la contabilidad, depurar la cartera, y reintegrar el inventario de las devoluciones de forma atómica e inmutable, cerrando definitivamente el ciclo de la distribución.
