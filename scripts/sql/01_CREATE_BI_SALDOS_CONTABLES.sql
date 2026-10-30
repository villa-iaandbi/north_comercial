-- DDL Arquitectura Analítica: Cubo Contable Multianual (Fase DIAN)
-- Creado para soportar SPEC-010 (P&G) y SPEC-012 (Balance por Tercero)

DROP TABLE BI_SALDOS_CONTABLES CASCADE CONSTRAINTS;

CREATE TABLE BI_SALDOS_CONTABLES (
    PERIODO           VARCHAR2(6) NOT NULL,  -- Formato YYYYMM (Ej. '202610')
    ANIO              NUMBER(4) NOT NULL,    -- Extraído para particionamiento/filtros rápidos
    MES               NUMBER(2) NOT NULL,    -- Extraído para particionamiento/filtros rápidos
    
    -- Ejes Dimensionales (Inmutables)
    COD_CUENTA        VARCHAR2(30) NOT NULL, -- Código contable natural (Ej. '13050501'). Reemplaza a ID_CUENTA.
    ID_TERCERO        VARCHAR2(8) DEFAULT '0' NOT NULL, -- '0' para saldos sin tercero (huérfanos/consolidados)
    ID_CENTRO_COSTO   VARCHAR2(8) DEFAULT '0' NOT NULL, -- '0' para cuentas de balance sin centro
    
    -- Desglose jerárquico para queries O(1) en Django / ECharts
    NIVEL_1           VARCHAR2(1),  -- Clase (1, 2, 3...)
    NIVEL_2           VARCHAR2(2),  -- Grupo
    NIVEL_4           VARCHAR2(4),  -- Cuenta Mayor
    NIVEL_6           VARCHAR2(6),  -- Subcuenta
    
    -- Métricas Financieras (Con Efecto Bola de Nieve)
    SALDO_INICIAL     NUMBER(18,2) DEFAULT 0, -- Arrastre del SALDO_FINAL del mes anterior
    DEBITOS_MES       NUMBER(18,2) DEFAULT 0, -- Movimientos puros del mes
    CREDITOS_MES      NUMBER(18,2) DEFAULT 0, -- Movimientos puros del mes
    SALDO_FINAL       NUMBER(18,2) DEFAULT 0, -- (SALDO_INICIAL + DEBITOS_MES - CREDITOS_MES)
    
    -- Auditoría
    ULTIMA_ACTUALIZACION TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT PK_BI_SALDOS PRIMARY KEY (PERIODO, COD_CUENTA, ID_TERCERO, ID_CENTRO_COSTO)
);

-- Índices recomendados para la vista de Django
CREATE INDEX IDX_BI_SALDOS_ANIO ON BI_SALDOS_CONTABLES(ANIO);
CREATE INDEX IDX_BI_SALDOS_JERARQUIA ON BI_SALDOS_CONTABLES(NIVEL_1, NIVEL_2);
