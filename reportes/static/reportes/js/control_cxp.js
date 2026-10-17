// -*- coding: utf-8 -*-
/**
 * Tablero de Cuentas por Pagar - CxP (SPEC-006)
 * Cumple con ADR-015 (Inyección SSR JSON) y ADR-016 (Alta Densidad Visual).
 * Gráficos ECharts:
 *   1. Top 15 Proveedores en Mora (Barras Horizontales).
 *   2. Proyección de Flujo de Pago 4 Semanas (Pago Inteligente vs Pago Estratégico) con markLine de Recaudo Prom. Semanal.
 */

window.formatCop = function (val) {
    if (val === null || val === undefined) return '$ 0';
    const num = Math.round(Number(val));
    if (isNaN(num)) return '$ 0';
    return '$ ' + num.toLocaleString('es-CO');
};

window.formatNumber = function (val) {
    if (val === null || val === undefined) return '0';
    const num = Number(val);
    if (isNaN(num)) return '0';
    return num.toLocaleString('es-CO');
};

window.formatAbrevMoney = function (val) {
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
};

(function () {
    'use strict';

    let chartProveedores = null;
    let chartProyeccionCxp = null;

    function truncateLabel(value) {
        if (!value) return '';
        const str = String(value).trim();
        return str.length > 30 ? str.substring(0, 30) + '...' : str;
    }

    window.initChartProyeccionCxp = function (domEl, initialData, recaudoSemanal) {
        if (!domEl || typeof echarts === 'undefined') return null;
        if (chartProyeccionCxp) {
            chartProyeccionCxp.dispose();
        }
        chartProyeccionCxp = echarts.init(domEl);

        const recaudoMeta = Math.round(Number(recaudoSemanal) || 0);
        const seriesData = (initialData || [0, 0, 0, 0, 0]).map(v => parseFloat(v) || 0);

        const optionProyeccion = {
            tooltip: {
                trigger: 'axis',
                axisPointer: { type: 'shadow' },
                textStyle: { fontSize: 13 },
                formatter: function (params) {
                    if (!params || !params[0]) return '';
                    const p = params[0];
                    return `<strong>${p.name}</strong><br/>Flujo de Pago: <strong>${window.formatCop(p.value)}</strong><br/>Recaudo Prom. Semanal: <strong style="color: #10b981">${window.formatCop(recaudoMeta)}</strong>`;
                }
            },
            grid: {
                left: '4%',
                right: '6%',
                bottom: '8%',
                top: '14%',
                containLabel: true
            },
            xAxis: {
                type: 'category',
                data: ['Inmediato', 'Semana 1', 'Semana 2', 'Semana 3', 'Semana 4'],
                axisLabel: {
                    color: '#d1d5db',
                    fontSize: 12,
                    fontWeight: 'bold'
                },
                axisLine: {
                    lineStyle: { color: '#374151' }
                }
            },
            yAxis: {
                type: 'value',
                max: function (value) {
                    return Math.ceil(Math.max(value.max, recaudoMeta) * 1.12);
                },
                axisLabel: {
                    color: '#9ca3af',
                    fontSize: 12,
                    formatter: window.formatAbrevMoney
                },
                splitLine: {
                    lineStyle: { color: '#1f2937', type: 'dashed' }
                }
            },
            series: [
                {
                    name: 'Proyección de Pago',
                    type: 'bar',
                    barWidth: '42%',
                    data: seriesData,
                    itemStyle: {
                        color: '#10b981',
                        borderRadius: [6, 6, 0, 0]
                    },
                    markLine: {
                        symbol: ['none', 'none'],
                        silent: false,
                        lineStyle: {
                            color: '#10b981',
                            type: 'dashed',
                            width: 2
                        },
                        label: {
                            show: true,
                            position: 'insideEndTop',
                            color: '#10b981',
                            fontWeight: 'bold',
                            fontSize: 11,
                            backgroundColor: 'rgba(6, 78, 59, 0.85)',
                            padding: [3, 6],
                            borderRadius: 4,
                            formatter: 'Recaudo Prom. Semanal: ' + window.formatAbrevMoney(recaudoMeta)
                        },
                        data: [
                            {
                                yAxis: recaudoMeta,
                                name: 'Recaudo Prom. Semanal'
                            }
                        ]
                    }
                }
            ]
        };

        chartProyeccionCxp.setOption(optionProyeccion, true);

        return {
            setOption: function (opt) {
                if (chartProyeccionCxp) {
                    chartProyeccionCxp.setOption(opt);
                }
            },
            resize: function () {
                if (chartProyeccionCxp) {
                    chartProyeccionCxp.resize();
                }
            }
        };
    };

    function initCxpDashboard() {
        const dataElement = document.getElementById('cxp-data');
        if (!dataElement) {
            console.warn('[control_cxp.js] Elemento #cxp-data no encontrado.');
            return;
        }

        let dbData;
        try {
            dbData = JSON.parse(dataElement.textContent);
        } catch (e) {
            console.error('[control_cxp.js] Error al parsear JSON de CxP:', e);
            return;
        }

        const domProveedores = document.getElementById('chart-proveedores');
        if (domProveedores && !chartProveedores) {
            chartProveedores = echarts.init(domProveedores);
        }

        renderProveedoresChart(dbData);
    }

    function renderProveedoresChart(dbData) {
        if (!dbData || !chartProveedores) return;

        const provList = dbData.top_proveedores || [];
        const top15 = provList.slice(0, 15);
        const nombres = top15.map(item => item.nombre || item.proveedor_nombre || item.PROVEEDOR_NOMBRE || '');
        const valores = top15.map(item => parseFloat(item.saldo || item.valor || item.SALDO_VENCIDO || 0) || 0);

        const optionProveedores = {
            tooltip: {
                trigger: 'axis',
                axisPointer: { type: 'shadow' },
                textStyle: { fontSize: 13 },
                formatter: function (params) {
                    if (!params || !params[0]) return '';
                    const p = params[0];
                    return `<strong>${p.name}</strong><br/>CxP Vencida: <strong style="color: #ef4444">${window.formatCop(p.value)}</strong>`;
                }
            },
            grid: {
                left: '200px',
                right: '8%',
                bottom: '8%',
                top: '6%',
                containLabel: true
            },
            xAxis: {
                type: 'value',
                axisLabel: {
                    color: '#9ca3af',
                    fontSize: 12,
                    formatter: window.formatAbrevMoney
                },
                splitLine: {
                    lineStyle: { color: '#1f2937', type: 'dashed' }
                }
            },
            yAxis: {
                type: 'category',
                inverse: true,
                data: nombres,
                axisLabel: {
                    color: '#d1d5db',
                    fontSize: 12,
                    width: 190,
                    overflow: 'truncate',
                    formatter: truncateLabel
                },
                axisLine: {
                    lineStyle: { color: '#374151' }
                }
            },
            series: [
                {
                    name: 'CxP Vencida',
                    type: 'bar',
                    barWidth: 14,
                    data: valores,
                    itemStyle: {
                        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                            { offset: 0, color: '#f59e0b' },
                            { offset: 1, color: '#ef4444' }
                        ]),
                        borderRadius: [0, 4, 4, 0]
                    },
                    emphasis: {
                        itemStyle: {
                            color: '#ef4444'
                        }
                    }
                }
            ]
        };

        chartProveedores.setOption(optionProveedores, true);
    }

    window.addEventListener('resize', function () {
        if (chartProveedores) chartProveedores.resize();
        if (chartProyeccionCxp) chartProyeccionCxp.resize();
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initCxpDashboard);
    } else {
        initCxpDashboard();
    }
})();
