// -*- coding: utf-8 -*-
/**
 * Tablero Ejecutivo de Análisis Ventas Global y Rentabilidad (SPEC-003)
 * Cumple con ADR-015 (Inyección SSR JSON) y ADR-016 (Alta Densidad Visual).
 * Prohibido cualquier manipulación directa de clases de sidebar (delegado a base.html).
 */

(function () {
    'use strict';

    // Formateadores numéricos de precisión
    function formatCop(val) {
        if (val === null || val === undefined) return '$ 0';
        const num = Math.round(Number(val));
        if (isNaN(num)) return '$ 0';
        return '$ ' + num.toLocaleString('es-CO');
    }

    function formatPercent(val) {
        if (val === null || val === undefined) return '0.00 %';
        const num = Number(val);
        if (isNaN(num)) return '0.00 %';
        return num.toFixed(2) + ' %';
    }

    function formatAbrevMoney(val) {
        const num = Number(val);
        if (isNaN(num)) return '$ 0';
        if (Math.abs(num) >= 1e9) {
            return '$' + (num / 1e9).toFixed(1) + 'B';
        } else if (Math.abs(num) >= 1e6) {
            return '$' + (num / 1e6).toFixed(0) + 'M';
        } else if (Math.abs(num) >= 1e3) {
            return '$' + (num / 1e3).toFixed(0) + 'K';
        }
        return '$' + num;
    }

    // Instancias de ECharts
    let chartVentas = null;
    let chartRentabilidad = null;
    let chartHistorico = null;

    const MESES_LABELS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'];

    // 1. Inicialización de Gráficos ECharts
    function initCharts() {
        const domVentas = document.getElementById('chart-ventas-mensual');
        const domRentabilidad = document.getElementById('chart-rentabilidad-mensual');
        const domHistorico = document.getElementById('chart-historico-12a');

        if (domVentas && !chartVentas) {
            chartVentas = echarts.init(domVentas);
        }
        if (domRentabilidad && !chartRentabilidad) {
            chartRentabilidad = echarts.init(domRentabilidad);
        }
        if (domHistorico && !chartHistorico) {
            chartHistorico = echarts.init(domHistorico);
        }
    }

    // 2. Renderizado de los 3 Gráficos
    function renderCharts(data) {
        if (!data) return;

        initCharts();

        const anioActual = String(data.anio_actual || '');
        const anioPrevio = String(data.anio_previo || '');
        const curvas = data.curvas || { actual: {}, previo: {} };
        const historico = data.historico || [];

        // --- Gráfico 1: Ventas Mensuales (Barras Actual vs Línea Previo) ---
        if (chartVentas) {
            const vActual = (curvas.actual && curvas.actual.venta_neta) ? curvas.actual.venta_neta.map(v => parseFloat(v) || 0) : [];
            const vPrevio = (curvas.previo && curvas.previo.venta_neta) ? curvas.previo.venta_neta.map(v => parseFloat(v) || 0) : [];

            chartVentas.setOption({
                tooltip: {
                    trigger: 'axis',
                    axisPointer: { type: 'shadow' },
                    formatter: function (params) {
                        if (!params || params.length === 0) return '';
                        let header = `<strong>Mes: ${params[0].name}</strong><br/>`;
                        let body = '';
                        params.forEach(p => {
                            body += `${p.marker} ${p.seriesName}: <strong>${formatCop(p.value)}</strong><br/>`;
                        });
                        return header + body;
                    }
                },
                legend: {
                    data: [anioActual, anioPrevio],
                    textStyle: { color: '#9ca3af' },
                    top: '2%'
                },
                grid: { left: '3%', right: '4%', bottom: '5%', top: '15%', containLabel: true },
                xAxis: {
                    type: 'category',
                    data: MESES_LABELS,
                    axisLabel: { color: '#9ca3af', fontSize: 11 },
                    axisLine: { lineStyle: { color: '#374151' } }
                },
                yAxis: {
                    type: 'value',
                    axisLabel: {
                        formatter: formatAbrevMoney,
                        color: '#9ca3af',
                        fontSize: 10
                    },
                    splitLine: { lineStyle: { color: '#1f2937', type: 'dashed' } }
                },
                series: [
                    {
                        name: anioActual,
                        type: 'bar',
                        data: vActual,
                        itemStyle: {
                            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                                { offset: 0, color: '#3b82f6' },
                                { offset: 1, color: '#1d4ed8' }
                            ]),
                            borderRadius: [4, 4, 0, 0]
                        },
                        barMaxWidth: 24
                    },
                    {
                        name: anioPrevio,
                        type: 'line',
                        data: vPrevio,
                        smooth: true,
                        symbol: 'circle',
                        symbolSize: 6,
                        itemStyle: { color: '#94a3b8' },
                        lineStyle: { width: 2, type: 'dashed', color: '#94a3b8' }
                    }
                ]
            }, true);
        }

        // --- Gráfico 2: Rentabilidad Mensual (%) (Barras Actual vs Línea Previo) ---
        if (chartRentabilidad) {
            const rActual = (curvas.actual && curvas.actual.rentabilidad_porcentaje) ? curvas.actual.rentabilidad_porcentaje.map(v => parseFloat(v) || 0) : [];
            const rPrevio = (curvas.previo && curvas.previo.rentabilidad_porcentaje) ? curvas.previo.rentabilidad_porcentaje.map(v => parseFloat(v) || 0) : [];

            chartRentabilidad.setOption({
                tooltip: {
                    trigger: 'axis',
                    axisPointer: { type: 'shadow' },
                    formatter: function (params) {
                        if (!params || params.length === 0) return '';
                        let header = `<strong>Mes: ${params[0].name}</strong><br/>`;
                        let body = '';
                        params.forEach(p => {
                            body += `${p.marker} ${p.seriesName}: <strong>${Number(p.value).toFixed(2)} %</strong><br/>`;
                        });
                        return header + body;
                    }
                },
                legend: {
                    data: [`${anioActual} (%)`, `${anioPrevio} (%)`],
                    textStyle: { color: '#9ca3af' },
                    top: '2%'
                },
                grid: { left: '3%', right: '4%', bottom: '5%', top: '15%', containLabel: true },
                xAxis: {
                    type: 'category',
                    data: MESES_LABELS,
                    axisLabel: { color: '#9ca3af', fontSize: 11 },
                    axisLine: { lineStyle: { color: '#374151' } }
                },
                yAxis: {
                    type: 'value',
                    axisLabel: {
                        formatter: '{value} %',
                        color: '#9ca3af',
                        fontSize: 10
                    },
                    splitLine: { lineStyle: { color: '#1f2937', type: 'dashed' } }
                },
                series: [
                    {
                        name: `${anioActual} (%)`,
                        type: 'bar',
                        data: rActual,
                        itemStyle: {
                            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                                { offset: 0, color: '#10b981' },
                                { offset: 1, color: '#047857' }
                            ]),
                            borderRadius: [4, 4, 0, 0]
                        },
                        barMaxWidth: 24
                    },
                    {
                        name: `${anioPrevio} (%)`,
                        type: 'line',
                        data: rPrevio,
                        smooth: true,
                        symbol: 'circle',
                        symbolSize: 6,
                        itemStyle: { color: '#f59e0b' },
                        lineStyle: { width: 2, type: 'dashed', color: '#f59e0b' }
                    }
                ]
            }, true);
        }

        // --- Gráfico 3: Histórico 12 Años (Dual-Y Axis: Barras Ventas vs Línea Rentabilidad) ---
        if (chartHistorico) {
            // Se invierte copia del array para mostrar cronológicamente (más antiguo -> más reciente)
            const cronologico = historico.slice().reverse();
            const aniosLabels = cronologico.map(h => String(h.anio));
            const ventasHist = cronologico.map(h => parseFloat(h.venta_neta_anual) || 0);
            const rentHist = cronologico.map(h => parseFloat(h.rentabilidad_anual_porcentaje) || 0);

            chartHistorico.setOption({
                tooltip: {
                    trigger: 'axis',
                    axisPointer: { type: 'cross' },
                    formatter: function (params) {
                        if (!params || params.length === 0) return '';
                        let header = `<strong>Año: ${params[0].name}</strong><br/>`;
                        let body = '';
                        params.forEach(p => {
                            if (p.seriesName === 'Venta Neta') {
                                body += `${p.marker} Venta Neta: <strong>${formatCop(p.value)}</strong><br/>`;
                            } else {
                                body += `${p.marker} Rentabilidad: <strong>${Number(p.value).toFixed(2)} %</strong><br/>`;
                            }
                        });
                        return header + body;
                    }
                },
                legend: {
                    data: ['Venta Neta', 'Rentabilidad (%)'],
                    textStyle: { color: '#9ca3af' },
                    top: '2%'
                },
                grid: { left: '3%', right: '6%', bottom: '5%', top: '15%', containLabel: true },
                xAxis: {
                    type: 'category',
                    data: aniosLabels,
                    axisLabel: { color: '#9ca3af', fontSize: 10, rotate: 30 },
                    axisLine: { lineStyle: { color: '#374151' } }
                },
                yAxis: [
                    {
                        type: 'value',
                        name: 'Ventas ($)',
                        nameTextStyle: { color: '#9ca3af', fontSize: 10 },
                        axisLabel: {
                            formatter: formatAbrevMoney,
                            color: '#9ca3af',
                            fontSize: 10
                        },
                        splitLine: { lineStyle: { color: '#1f2937', type: 'dashed' } }
                    },
                    {
                        type: 'value',
                        name: 'Rentabilidad (%)',
                        nameTextStyle: { color: '#f59e0b', fontSize: 10 },
                        axisLabel: {
                            formatter: '{value}%',
                            color: '#f59e0b',
                            fontSize: 10
                        },
                        splitLine: { show: false }
                    }
                ],
                series: [
                    {
                        name: 'Venta Neta',
                        type: 'bar',
                        yAxisIndex: 0,
                        data: ventasHist,
                        itemStyle: {
                            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                                { offset: 0, color: '#6366f1' },
                                { offset: 1, color: '#4338ca' }
                            ]),
                            borderRadius: [4, 4, 0, 0]
                        },
                        barMaxWidth: 20
                    },
                    {
                        name: 'Rentabilidad (%)',
                        type: 'line',
                        yAxisIndex: 1,
                        data: rentHist,
                        smooth: true,
                        symbol: 'circle',
                        symbolSize: 6,
                        itemStyle: { color: '#f59e0b' },
                        lineStyle: { width: 3, color: '#f59e0b' }
                    }
                ]
            }, true);
        }
    }

    // 3. Listener Global de Redimensionamiento
    window.addEventListener('resize', function () {
        if (chartVentas) chartVentas.resize();
        if (chartRentabilidad) chartRentabilidad.resize();
        if (chartHistorico) chartHistorico.resize();
    });

    // 4. Componente Reactivo Alpine.js para el Tablero
    window.dashboardEjecutivo = function () {
        let initialData = null;
        try {
            const dataElement = document.getElementById('dashboard-data');
            if (dataElement && dataElement.textContent.trim()) {
                initialData = JSON.parse(dataElement.textContent);
            }
        } catch (e) {
            console.error('Error al parsear dashboard-data inicial:', e);
        }

        const safeData = initialData || {
            anio_actual: new Date().getFullYear(),
            anio_previo: new Date().getFullYear() - 1,
            kpis: { actual: {}, previo: {}, deltas_yoy: {} },
            curvas: { actual: {}, previo: {} },
            historico: [],
            filtros_aplicados: {}
        };

        return {
            dbData: safeData,
            kpis: safeData.kpis || { actual: {}, previo: {}, deltas_yoy: {} },
            curvas: safeData.curvas || { actual: {}, previo: {} },
            historico: safeData.historico || [],
            anioActual: safeData.anio_actual,
            anioPrevio: safeData.anio_previo,

            // Filtros reactivos
            filtroAnio: String(safeData.filtros_aplicados?.anio || safeData.anio_actual || ''),
            filtroZona: safeData.filtros_aplicados?.zona || '',
            filtroProveedor: safeData.filtros_aplicados?.proveedor || '',
            filtroLinea: safeData.filtros_aplicados?.linea || '',

            init() {
                // Renderizar gráficos con el dataset inicial inyectado vía SSR
                this.$nextTick(() => {
                    renderCharts(this.dbData);
                });
            },

            formatCop(val) {
                return formatCop(val);
            },

            formatPercent(val) {
                return formatPercent(val);
            },

            formatDelta(val) {
                const num = Number(val) || 0;
                const sign = num > 0 ? '+' : '';
                return sign + num.toFixed(2) + ' %';
            },

            deltaIcon(val) {
                const num = Number(val) || 0;
                return num >= 0 ? '▲' : '▼';
            },

            badgeClass(delta, isInverse = false) {
                const num = Number(delta) || 0;
                const isPositive = isInverse ? num <= 0 : num >= 0;
                if (isPositive) {
                    return 'bg-emerald-950/70 text-emerald-400 border border-emerald-700/60';
                } else {
                    return 'bg-rose-950/70 text-rose-400 border border-rose-700/60';
                }
            },

            showLoader(msg = 'ACTUALIZANDO...') {
                const loader = document.getElementById('floating-loader');
                const txt = document.getElementById('loader-text');
                if (txt) txt.textContent = msg;
                if (loader) {
                    loader.classList.remove('opacity-0', 'pointer-events-none');
                    loader.classList.add('opacity-100');
                }
            },

            hideLoader() {
                const loader = document.getElementById('floating-loader');
                if (loader) {
                    loader.classList.remove('opacity-100');
                    loader.classList.add('opacity-0', 'pointer-events-none');
                }
            },

            async aplicarFiltros() {
                this.showLoader('ACTUALIZANDO TABLERO...');
                try {
                    const params = new URLSearchParams();
                    if (this.filtroAnio) params.append('anio', this.filtroAnio);
                    if (this.filtroZona) params.append('zona', this.filtroZona);
                    if (this.filtroProveedor) params.append('proveedor', this.filtroProveedor);
                    if (this.filtroLinea) params.append('linea', this.filtroLinea);
                    params.append('ajax', '1');

                    const response = await fetch(`/reportes/analisis-ventas-global/?${params.toString()}`, {
                        headers: { 'X-Requested-With': 'XMLHttpRequest' }
                    });

                    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

                    const payload = await response.json();
                    this.dbData = payload;
                    this.kpis = payload.kpis || { actual: {}, previo: {}, deltas_yoy: {} };
                    this.curvas = payload.curvas || { actual: {}, previo: {} };
                    this.historico = payload.historico || [];
                    this.anioActual = payload.anio_actual;
                    this.anioPrevio = payload.anio_previo;

                    // Actualizar los 3 gráficos
                    renderCharts(this.dbData);
                } catch (error) {
                    console.error('Error al actualizar datos del tablero:', error);
                } finally {
                    this.hideLoader();
                }
            },

            limpiarFiltros() {
                this.filtroZona = '';
                this.filtroProveedor = '';
                this.filtroLinea = '';
                this.aplicarFiltros();
            }
        };
    };

    // Inicialización al cargar el DOM si Alpine aún no está listo
    document.addEventListener('DOMContentLoaded', function () {
        const rawElement = document.getElementById('dashboard-data');
        if (rawElement && rawElement.textContent.trim()) {
            try {
                const parsed = JSON.parse(rawElement.textContent);
                renderCharts(parsed);
            } catch (err) {
                console.error('Error al inicializar charts en DOMContentLoaded:', err);
            }
        }
    });
})();
