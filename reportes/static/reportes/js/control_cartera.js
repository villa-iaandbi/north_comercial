// -*- coding: utf-8 -*-
/**
 * Tablero de Control de Cartera y Recaudos (SPEC-005 - Fase 3)
 * Cumple con ADR-015 (Inyección SSR JSON) y ADR-016 (Alta Densidad Visual).
 * Gráficos ECharts: Top 10 Clientes con Mayor Deuda y Top 10 Vendedores con Cartera Vencida (Barras Horizontales).
 */

// Formateadores globales para Alpine y ECharts
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

    let chartClientes = null;
    let chartVendedores = null;

    function initCarteraDashboard() {
        const dataElement = document.getElementById('dashboard-data');
        if (!dataElement) {
            console.warn('[control_cartera.js] Elemento #dashboard-data no encontrado.');
            return;
        }

        let dbData;
        try {
            dbData = JSON.parse(dataElement.textContent);
        } catch (e) {
            console.error('[control_cartera.js] Error al parsear JSON de cartera:', e);
            return;
        }

        const domClientes = document.getElementById('chart-clientes');
        const domVendedores = document.getElementById('chart-vendedores');

        if (domClientes && !chartClientes) {
            chartClientes = echarts.init(domClientes);
        }

        if (domVendedores && !chartVendedores) {
            chartVendedores = echarts.init(domVendedores);
        }

        renderCharts(dbData);
    }

    function renderCharts(dbData) {
        if (!dbData) return;

        // =========================================================================
        // Gráfico 1: Top 10 Clientes con Mayor Deuda Vencida (Barras Horizontales)
        // =========================================================================
        if (chartClientes) {
            const clientesData = dbData.grafico_clientes ||
                (dbData.graficos && dbData.graficos.clientes) ||
                { nombres: [], valores: [] };

            const nombres = clientesData.nombres || [];
            const valores = (clientesData.valores || []).map(v => parseFloat(v) || 0);

            const optionClientes = {
                tooltip: {
                    trigger: 'axis',
                    axisPointer: { type: 'shadow' },
                    textStyle: { fontSize: 14 },
                    formatter: function (params) {
                        if (!params || !params[0]) return '';
                        const p = params[0];
                        return `<strong>${p.name}</strong><br/>Saldo Vencido: <strong style="color: #ef4444">${window.formatCop(p.value)}</strong>`;
                    }
                },
                grid: {
                    left: '3%',
                    right: '8%',
                    bottom: '8%',
                    top: '8%',
                    containLabel: true
                },
                xAxis: {
                    type: 'value',
                    axisLabel: {
                        color: '#9ca3af',
                        fontSize: 14,
                        formatter: window.formatAbrevMoney
                    },
                    splitLine: {
                        lineStyle: { color: '#1f2937', type: 'dashed' }
                    }
                },
                yAxis: {
                    type: 'category',
                    inverse: true, // El cliente con mayor deuda queda en la parte superior
                    data: nombres,
                    axisLabel: {
                        color: '#d1d5db',
                        fontSize: 14,
                        width: 140,
                        overflow: 'truncate'
                    },
                    axisLine: {
                        lineStyle: { color: '#374151' }
                    }
                },
                series: [
                    {
                        name: 'Saldo Vencido',
                        type: 'bar',
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

            chartClientes.setOption(optionClientes, true);
        }

        // =========================================================================
        // Gráfico 2: Top 10 Vendedores con Cartera Vencida (Barras Horizontales)
        // =========================================================================
        if (chartVendedores) {
            const vendedoresData = dbData.grafico_vendedores ||
                (dbData.graficos && dbData.graficos.vendedores) ||
                { nombres: [], valores: [] };

            const nombres = vendedoresData.nombres || [];
            const valores = (vendedoresData.valores || []).map(v => parseFloat(v) || 0);

            const optionVendedores = {
                tooltip: {
                    trigger: 'axis',
                    axisPointer: { type: 'shadow' },
                    textStyle: { fontSize: 14 },
                    formatter: function (params) {
                        if (!params || !params[0]) return '';
                        const p = params[0];
                        return `<strong>${p.name}</strong><br/>Cartera en Mora: <strong style="color: #f59e0b">${window.formatCop(p.value)}</strong>`;
                    }
                },
                grid: {
                    left: '3%',
                    right: '8%',
                    bottom: '8%',
                    top: '8%',
                    containLabel: true
                },
                xAxis: {
                    type: 'value',
                    axisLabel: {
                        color: '#9ca3af',
                        fontSize: 14,
                        formatter: window.formatAbrevMoney
                    },
                    splitLine: {
                        lineStyle: { color: '#1f2937', type: 'dashed' }
                    }
                },
                yAxis: {
                    type: 'category',
                    inverse: true, // El vendedor con mayor cartera vencida queda arriba
                    data: nombres,
                    axisLabel: {
                        color: '#d1d5db',
                        fontSize: 14,
                        width: 140,
                        overflow: 'truncate'
                    },
                    axisLine: {
                        lineStyle: { color: '#374151' }
                    }
                },
                series: [
                    {
                        name: 'Cartera en Mora',
                        type: 'bar',
                        data: valores,
                        itemStyle: {
                            color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                                { offset: 0, color: '#3b82f6' },
                                { offset: 1, color: '#f59e0b' }
                            ]),
                            borderRadius: [0, 4, 4, 0]
                        },
                        emphasis: {
                            itemStyle: {
                                color: '#f59e0b'
                            }
                        }
                    }
                ]
            };

            chartVendedores.setOption(optionVendedores, true);
        }
    }

    // Responsividad automática ante redimensionamiento de ventana
    window.addEventListener('resize', function () {
        if (chartClientes) chartClientes.resize();
        if (chartVendedores) chartVendedores.resize();
    });

    // Carga tras parseo del DOM
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initCarteraDashboard);
    } else {
        initCarteraDashboard();
    }
})();
