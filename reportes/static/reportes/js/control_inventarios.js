// -*- coding: utf-8 -*-
/**
 * Tablero de Control de Inventarios (SPEC-004 - Fase 3)
 * Cumple con ADR-015 (Inyección SSR JSON) y ADR-016 (Alta Densidad Visual).
 * Gráficos ECharts: Top 10 Grupos Atrapados (Barras Horizontales) y Distribución por Edades (Dona).
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

    let chartGrupos = null;
    let chartEdades = null;

    function initInventarioDashboard() {
        const dataElement = document.getElementById('dashboard-data');
        if (!dataElement) {
            console.warn('[control_inventarios.js] Elemento #dashboard-data no encontrado.');
            return;
        }

        let dbData;
        try {
            dbData = JSON.parse(dataElement.textContent);
        } catch (e) {
            console.error('[control_inventarios.js] Error al parsear JSON de inventario:', e);
            return;
        }

        const domGrupos = document.getElementById('chart-grupos');
        const domEdades = document.getElementById('chart-edades');

        if (domGrupos && !chartGrupos) {
            chartGrupos = echarts.init(domGrupos);
        }

        if (domEdades && !chartEdades) {
            chartEdades = echarts.init(domEdades);
        }

        renderCharts(dbData);
    }

    function renderCharts(dbData) {
        if (!dbData) return;

        // =========================================================================
        // Gráfico 1: Top 10 Grupos con Mayor Capital Atrapado (Barras Horizontales)
        // =========================================================================
        if (chartGrupos) {
            const gruposData = dbData.grafico_grupos ||
                (dbData.graficos && dbData.graficos.grupos_atrapados) ||
                { nombres: [], valores: [] };

            const nombres = gruposData.nombres || [];
            const valores = (gruposData.valores || []).map(v => parseFloat(v) || 0);

            const optionGrupos = {
                tooltip: {
                    trigger: 'axis',
                    axisPointer: { type: 'shadow' },
                    textStyle: { fontSize: 14 },
                    formatter: function (params) {
                        if (!params || !params[0]) return '';
                        const p = params[0];
                        return `<strong>${p.name}</strong><br/>Capital Atrapado: <strong style="color: #ef4444">${window.formatCop(p.value)}</strong>`;
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
                    inverse: true, // El grupo con mayor capital atrapado queda arriba
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
                        name: 'Capital Atrapado',
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

            chartGrupos.setOption(optionGrupos, true);
        }

        // =========================================================================
        // Gráfico 2: Distribución de Inventario por Edad (Donut Chart)
        // =========================================================================
        if (chartEdades) {
            const dist = dbData.distribucion_edades ||
                (dbData.graficos && dbData.graficos.edades) ||
                {};

            const valSano = parseFloat(dist.sano || 0);
            const valLento = parseFloat(dist.lento || 0);
            const valAtrapado = parseFloat(dist.atrapado || 0);

            const optionEdades = {
                tooltip: {
                    trigger: 'item',
                    textStyle: { fontSize: 14 },
                    formatter: function (params) {
                        return `<strong>${params.name}</strong><br/>` +
                            `Valor: <strong>${window.formatCop(params.value)}</strong> ` +
                            `(${params.percent}%)`;
                    }
                },
                legend: {
                    orient: 'horizontal',
                    bottom: '4%',
                    textStyle: { color: '#d1d5db', fontSize: 14 }
                },
                series: [
                    {
                        name: 'Distribución por Edad',
                        type: 'pie',
                        radius: ['40%', '70%'],
                        center: ['50%', '45%'],
                        avoidLabelOverlap: true,
                        itemStyle: {
                            borderRadius: 6,
                            borderColor: '#111827',
                            borderWidth: 2
                        },
                        label: {
                            show: true,
                            formatter: '{b}\n{d}%',
                            color: '#e5e7eb',
                            fontSize: 14
                        },
                        emphasis: {
                            label: {
                                show: true,
                                fontSize: 16,
                                fontWeight: 'bold'
                            }
                        },
                        data: [
                            {
                                name: 'Sano',
                                value: valSano,
                                itemStyle: { color: '#10b981' } // Verde
                            },
                            {
                                name: 'Lento',
                                value: valLento,
                                itemStyle: { color: '#f59e0b' } // Amarillo / Ámbar
                            },
                            {
                                name: 'Atrapado',
                                value: valAtrapado,
                                itemStyle: { color: '#ef4444' } // Rojo
                            }
                        ]
                    }
                ]
            };

            chartEdades.setOption(optionEdades, true);
        }
    }

    // Responsividad automática ante redimensionamiento de ventana
    window.addEventListener('resize', function () {
        if (chartGrupos) chartGrupos.resize();
        if (chartEdades) chartEdades.resize();
    });

    // Carga tras parseo del DOM
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initInventarioDashboard);
    } else {
        initInventarioDashboard();
    }
})();
