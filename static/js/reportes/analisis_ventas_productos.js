window.globalData = null;
window.dimensionActiva = 'PROVEEDOR';
window.valorFiltroActivo = null;
window.offsetResumen = 0;

document.addEventListener('DOMContentLoaded', function () {

    const url = '/reportes/analisis-productos/';

    function showLoader(msg = 'ACTUALIZANDO...') {
        const loader = document.getElementById('floating-loader');
        const txt = document.getElementById('loader-text');
        if (txt) txt.textContent = msg;
        if (loader) {
            loader.classList.remove('opacity-0', 'pointer-events-none');
            loader.classList.add('opacity-100');
        }
    }

    function hideLoader() {
        const loader = document.getElementById('floating-loader');
        if (loader) {
            loader.classList.remove('opacity-100');
            loader.classList.add('opacity-0', 'pointer-events-none');
        }
    }

    const chartComparativo = echarts.init(document.getElementById('chart-comparativo'));
    const chartCubrimiento = echarts.init(document.getElementById('chart-cubrimiento'));
    const chartAgrupacion = echarts.init(document.getElementById('chart-agrupacion'));
    const chartKilos = echarts.init(document.getElementById('chart-kilos'));

    const btnCubGrafica = document.getElementById('btn-cub-grafica');
    const btnCubTabla = document.getElementById('btn-cub-tabla');
    const boxCubGrafica = document.getElementById('contenedor-cub-grafica');
    const boxCubTabla = document.getElementById('contenedor-cub-tabla');

    btnCubGrafica.addEventListener('click', function () {
        boxCubTabla.classList.add('hidden');
        boxCubGrafica.classList.remove('hidden');
        btnCubGrafica.className = "px-2.5 py-1 text-xs font-medium text-white bg-indigo-600 rounded-l-lg border border-indigo-600";
        btnCubTabla.className = "px-2.5 py-1 text-xs font-medium text-gray-300 bg-gray-800 rounded-r-lg border border-gray-700 hover:bg-gray-700";
        chartCubrimiento.resize();
    });

    btnCubTabla.addEventListener('click', function () {
        boxCubGrafica.classList.add('hidden');
        boxCubTabla.classList.remove('hidden');
        btnCubTabla.className = "px-2.5 py-1 text-xs font-medium text-white bg-indigo-600 rounded-r-lg border border-indigo-600";
        btnCubGrafica.className = "px-2.5 py-1 text-xs font-medium text-gray-300 bg-gray-800 rounded-l-lg border border-gray-700 hover:bg-gray-700";
    });

    const btnAgrupGrupo = document.getElementById('btn-agrup-grupo');
    const btnAgrupLinea = document.getElementById('btn-agrup-linea');

    function setDimensionAgrupacion(dimension) {
        window.dimensionActiva = dimension;
        if (dimension === 'PROVEEDOR') {
            btnAgrupGrupo.className = "px-2.5 py-1 text-xs font-medium text-white bg-amber-600 rounded-l-lg border border-amber-600";
            btnAgrupLinea.className = "px-2.5 py-1 text-xs font-medium text-gray-300 bg-gray-800 rounded-r-lg border border-gray-700 hover:bg-gray-700";
        } else {
            btnAgrupLinea.className = "px-2.5 py-1 text-xs font-medium text-white bg-amber-600 rounded-r-lg border border-amber-600";
            btnAgrupGrupo.className = "px-2.5 py-1 text-xs font-medium text-gray-300 bg-gray-800 rounded-l-lg border border-gray-700 hover:bg-gray-700";
        }
        renderChartAgrupacion();
    }

    btnAgrupGrupo.addEventListener('click', () => setDimensionAgrupacion('PROVEEDOR'));
    btnAgrupLinea.addEventListener('click', () => setDimensionAgrupacion('LINEA'));

    const btnResetTop = document.getElementById('btn-reset-top');
    btnResetTop.addEventListener('click', function () {
        window.valorFiltroActivo = null;
        this.classList.add('hidden');
        document.getElementById('lbl-seleccion-activa').textContent = "Filtrando: Todo el portafolio (Global)";
        renderTopProductos();
    });

    window.addEventListener('resize', function () {
        chartComparativo.resize();
        chartCubrimiento.resize();
        chartAgrupacion.resize();
        chartKilos.resize();
    });

    async function renderTopProductos() {
        const tbody = document.getElementById('tabla-top-productos-body');
        if (!tbody) return;
        tbody.innerHTML = `<tr><td colspan="3" class="py-2 text-center text-gray-500">Cargando Top 20...</td></tr>`;

        const filtros = {
            anio: document.getElementById('filtro-anio')?.value || '',
            mes: document.getElementById('filtro-mes')?.value || '',
            zona: document.getElementById('filtro-zona')?.value || '',
            proveedor: document.getElementById('filtro-proveedor')?.value || '',
            linea: document.getElementById('filtro-linea')?.value || '',
            canal: document.getElementById('filtro-canal')?.value || '',
        };

        const queryParams = new URLSearchParams(filtros).toString();
        let val = window.valorFiltroActivo || '';
        let dim = window.dimensionActiva || 'PROVEEDOR';

        try {
            const response = await fetch(`${url}api/top-productos-dinamico/?dimension=${dim}&valor=${encodeURIComponent(val)}&${queryParams}`, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });
            const result = await response.json();

            if (result.productos && result.productos.length > 0) {
                tbody.innerHTML = result.productos.map(item => {
                    const ventaFmt = `$ ${parseInt(item.venta).toLocaleString('es-CO')}`;
                    return `<tr class="hover:bg-gray-700 transition-colors">
                            <td class="py-1 px-2 font-medium text-gray-200 truncate max-w-[200px]" title="${item.articulo}">${item.articulo}</td>
                            <td class="py-1 px-2 text-right font-mono font-bold text-gray-100">${ventaFmt}</td>
                            <td class="py-1 px-2 text-right font-mono font-semibold text-amber-400">${item.participacion} %</td>
                        </tr>`;
                }).join('');
            } else {
                tbody.innerHTML = `<tr><td colspan="3" class="py-2 text-center text-gray-500">Sin productos.</td></tr>`;
            }
        } catch (error) {
            tbody.innerHTML = `<tr><td colspan="3" class="py-2 text-center text-red-500">Error.</td></tr>`;
        }
    }

    function renderChartAgrupacion() {
        if (!window.globalData) return;
        const datos = window.dimensionActiva === 'PROVEEDOR' ? window.globalData.ventas_grupo : window.globalData.ventas_linea;
        const total = datos.reduce((sum, item) => sum + item.venta, 0);

        const top15 = datos.slice(0, 15).reverse();
        const yAxisData = top15.map(d => d.nombre);
        const seriesData = top15.map(d => d.venta);

        chartAgrupacion.setOption({
            tooltip: {
                trigger: 'axis', axisPointer: { type: 'shadow' },
                formatter: function (params) {
                    let v = params[0].value;
                    let p = total > 0 ? ((v / total) * 100).toFixed(2) : '0';
                    return `<strong>${params[0].name}</strong><br/>Venta: $ ${parseInt(v).toLocaleString('es-CO')}<br/>Part: ${p}%<br/><span style="color:#fcd34d;font-size:10px;">(Clic para filtrar)</span>`;
                }
            },
            grid: { left: '3%', right: '12%', bottom: '3%', top: '5%', containLabel: true },
            xAxis: { type: 'value', splitLine: { lineStyle: { color: '#374151', type: 'dashed' } }, axisLabel: { formatter: (val) => val >= 1000000 ? `$${Math.round(val / 1000000)}M` : `$${Math.round(val / 1000)}K` } },
            yAxis: { type: 'category', data: yAxisData, axisLabel: { fontSize: 10, align: 'right', formatter: v => v.length > 15 ? v.substring(0, 15) + '...' : v } },
            series: [{
                name: 'Venta', type: 'bar', data: seriesData,
                itemStyle: { color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [{ offset: 0, color: '#f59e0b' }, { offset: 1, color: '#d97706' }]), borderRadius: [0, 4, 4, 0] },
                label: { show: true, position: 'right', color: '#fde68a', fontSize: 10, formatter: p => p.value >= 1000000 ? `$${Math.round(p.value / 1000000)}M` : `$${Math.round(p.value / 1000)}K` }
            }]
        }, true);

        chartAgrupacion.off('click');
        chartAgrupacion.on('click', function (params) {
            window.valorFiltroActivo = params.name;
            const dimLabel = window.dimensionActiva === 'PROVEEDOR' ? 'Grupo' : 'Línea';
            document.getElementById('lbl-seleccion-activa').textContent = `Filtrando: ${dimLabel} -> ${params.name}`;
            document.getElementById('btn-reset-top').classList.remove('hidden');
            renderTopProductos();
        });
    }

    function renderData(data) {
        if (!data) return;
        window.globalData = data;
        let totalProdActivos = data.total_productos_activos || 0;
        document.getElementById('badge-total-productos').textContent = `${totalProdActivos.toLocaleString('es-CO')} Productos Activos`;

        const compData = data.comparativo_proveedor || [];
        const top10Comp = compData.slice(0, 10).reverse();

        chartComparativo.setOption({
            tooltip: {
                trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: function (p) {
                    let actual = p[1].value; let ant = p[0].value;
                    let crec = ant > 0 ? (((actual - ant) / ant) * 100).toFixed(1) : (actual > 0 ? '100' : '0');
                    let color = crec >= 0 ? '#10b981' : '#ef4444';
                    return `<strong>${p[0].name}</strong><br/>Actual: $${parseInt(actual).toLocaleString('es-CO')}<br/>Anterior: $${parseInt(ant).toLocaleString('es-CO')}<br/>Crecimiento: <span style="color:${color}">${crec}%</span>`;
                }
            },
            legend: { data: ['Año Previo', 'Año Actual'], textStyle: { color: '#9ca3af' } },
            grid: { left: '3%', right: '4%', bottom: '5%', containLabel: true },
            xAxis: { type: 'value', splitLine: { lineStyle: { color: '#374151', type: 'dashed' } }, axisLabel: { formatter: (val) => val >= 1000000 ? `$${Math.round(val / 1000000)}M` : `$${Math.round(val / 1000)}K` } },
            yAxis: { type: 'category', data: top10Comp.map(d => d.proveedor), axisLabel: { fontSize: 10, formatter: v => v.length > 15 ? v.substring(0, 15) + '...' : v } },
            series: [
                { name: 'Año Previo', type: 'bar', data: top10Comp.map(d => d.anterior), itemStyle: { color: '#475569', borderRadius: [0, 4, 4, 0] } },
                { name: 'Año Actual', type: 'bar', data: top10Comp.map(d => d.actual), itemStyle: { color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [{ offset: 0, color: '#06b6d4' }, { offset: 1, color: '#0369a1' }]), borderRadius: [0, 4, 4, 0] } }
            ]
        }, true);

        const cubData = data.cubrimiento_zona || [];
        const tbodyCub = document.getElementById('tabla-cubrimiento-body');
        if (tbodyCub) {
            tbodyCub.innerHTML = cubData.map(c => {
                let perc = totalProdActivos > 0 ? ((c.q_productos / totalProdActivos) * 100).toFixed(1) : 0;
                return `<tr class="hover:bg-gray-700 transition-colors">
                    <td class="py-1 px-2 font-medium text-gray-200">${c.zona}</td>
                    <td class="py-1 px-2 text-right font-mono font-bold text-gray-100">${c.q_productos.toLocaleString('es-CO')}</td>
                    <td class="py-1 px-2 text-right font-mono font-bold text-indigo-400">${perc} %</td>
                    <td class="py-1 px-2 text-right font-mono text-gray-300">$ ${parseInt(c.venta).toLocaleString('es-CO')}</td>
                </tr>`;
            }).join('');
        }

        const top15Cub = cubData.slice(0, 15).reverse();
        chartCubrimiento.setOption({
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
            legend: { data: ['Productos Activos', '% Cubrimiento'], textStyle: { color: '#9ca3af' }, bottom: 0 },
            grid: { left: '3%', right: '8%', bottom: '12%', top: '5%', containLabel: true },
            xAxis: [
                { type: 'value', splitLine: { lineStyle: { color: '#374151', type: 'dashed' } } },
                { type: 'value', show: false, max: 100 }
            ],
            yAxis: {
                type: 'category',
                data: top15Cub.map(c => c.zona),
                axisLabel: { fontSize: 10, align: 'left', margin: 150, width: 140, overflow: 'truncate' }
            },
            series: [
                {
                    name: 'Productos Activos', type: 'bar', xAxisIndex: 0,
                    data: top15Cub.map(c => c.q_productos),
                    itemStyle: { color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [{ offset: 0, color: '#8b5cf6' }, { offset: 1, color: '#4f46e5' }]), borderRadius: [0, 4, 4, 0] },
                },
                {
                    name: '% Cubrimiento', type: 'line', xAxisIndex: 1,
                    data: top15Cub.map(c => totalProdActivos > 0 ? ((c.q_productos / totalProdActivos) * 100).toFixed(1) : 0),
                    itemStyle: { color: '#f43f5e' }, symbolSize: 6, lineStyle: { width: 3, type: 'dashed' },
                    label: { show: true, position: 'right', color: '#fda4af', fontSize: 10, formatter: '{c}%' }
                }
            ]
        }, true);

        renderChartAgrupacion();
        renderTopProductos();

        const kilosData = data.kilos_mes || [];
        const mesesNombres = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'];

        chartKilos.setOption({
            tooltip: { trigger: 'axis', formatter: p => `<strong>${p[0].name}</strong><br/>Kilos: ${parseInt(p[0].value).toLocaleString('es-CO')} Kg` },
            grid: { left: '5%', right: '5%', bottom: '10%', top: '15%', containLabel: true },
            xAxis: { type: 'category', data: kilosData.map(d => mesesNombres[d.mes - 1]) },
            yAxis: { type: 'value', splitLine: { lineStyle: { color: '#374151', type: 'dashed' } }, axisLabel: { formatter: v => `${Math.round(v / 1000)}K` } },
            series: [{
                name: 'Kilos', type: 'bar', data: kilosData.map(d => d.kilos),
                itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: '#10b981' }, { offset: 1, color: '#047857' }]), borderRadius: [4, 4, 0, 0] },
                label: { show: true, position: 'top', color: '#6ee7b7', fontSize: 10, formatter: p => `${parseInt(p.value).toLocaleString('es-CO')}` }
            }]
        }, true);

        if (data.filas_resumen_html) {
            const tbodyRes = document.getElementById('tabla-resumen-body');
            if (tbodyRes) tbodyRes.innerHTML = data.filas_resumen_html;
            window.offsetResumen = 100;
        }

        const btnCargarMas = document.getElementById('btn-cargar-mas');
        if (btnCargarMas) {
            if (data.has_more) btnCargarMas.classList.remove('hidden');
            else btnCargarMas.classList.add('hidden');
        }
    }

    async function fetchData() {
        showLoader();
        const filtros = {
            anio: document.getElementById('filtro-anio')?.value || '',
            mes: document.getElementById('filtro-mes')?.value || '',
            zona: document.getElementById('filtro-zona')?.value || '',
            proveedor: document.getElementById('filtro-proveedor')?.value || '',
            linea: document.getElementById('filtro-linea')?.value || '',
            canal: document.getElementById('filtro-canal')?.value || '',
        };

        try {
            const response = await fetch(`${url}?${new URLSearchParams(filtros).toString()}`, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            const data = await response.json();
            renderData(data);
        } catch (error) {
            console.error("Error al obtener datos:", error);
        } finally {
            hideLoader();
        }
    }

    async function loadMoreResumen() {
        showLoader("CARGANDO ARTÍCULOS...");
        const btnCargarMas = document.getElementById('btn-cargar-mas');
        if (btnCargarMas) btnCargarMas.disabled = true;

        const filtros = {
            solo_resumen: '1', offset_resumen: window.offsetResumen, limit_resumen: 100,
            anio: document.getElementById('filtro-anio')?.value || '',
            mes: document.getElementById('filtro-mes')?.value || '',
            zona: document.getElementById('filtro-zona')?.value || '',
            proveedor: document.getElementById('filtro-proveedor')?.value || '',
            linea: document.getElementById('filtro-linea')?.value || '',
            canal: document.getElementById('filtro-canal')?.value || ''
        };

        try {
            const response = await fetch(`${url}?${new URLSearchParams(filtros).toString()}`, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            const data = await response.json();

            const tbodyRes = document.getElementById('tabla-resumen-body');
            if (tbodyRes && data.nuevas_filas_html) {
                tbodyRes.insertAdjacentHTML('beforeend', data.nuevas_filas_html);
                window.offsetResumen += (data.total_filas || 100);
            }

            if (btnCargarMas) {
                if (data.has_more) btnCargarMas.classList.remove('hidden');
                else btnCargarMas.classList.add('hidden');
            }
        } catch (error) {
            console.error("Error al paginar resumen:", error);
        } finally {
            if (btnCargarMas) btnCargarMas.disabled = false;
            hideLoader();
        }
    }

    document.getElementById('btn-filtrar')?.addEventListener('click', () => { window.offsetResumen = 0; fetchData(); });
    document.getElementById('btn-limpiar')?.addEventListener('click', () => {
        if (document.getElementById('filtro-anio')) document.getElementById('filtro-anio').selectedIndex = 0;
        ['mes', 'zona', 'proveedor', 'linea', 'canal'].forEach(f => {
            if (document.getElementById(`filtro-${f}`)) document.getElementById(`filtro-${f}`).value = '';
        });
        window.valorFiltroActivo = null;
        document.getElementById('btn-reset-top')?.classList.add('hidden');
        document.getElementById('lbl-seleccion-activa').textContent = "Filtrando: Todo el portafolio (Global)";
        window.offsetResumen = 0;
        fetchData();
    });

    document.getElementById('btn-cargar-mas')?.addEventListener('click', loadMoreResumen);

    try {
        const rawJson = document.getElementById('initial-data')?.textContent;
        if (rawJson && rawJson.trim() !== '') {
            let cleanJson = rawJson.trim().replace(/\\u0022/g, '"');
            renderData(JSON.parse(cleanJson));
        } else {
            fetchData();
        }
    } catch (e) {
        fetchData();
    }
});