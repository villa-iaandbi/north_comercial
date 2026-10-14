window.globalData = null;
window.nivelGeoActual = 'muni';
window.filtroZonaActivo = null;
window.offsetMatriz = 0;

document.addEventListener('DOMContentLoaded', function () {

    // --- SOLUCIÓN NATIVA AL MENÚ COLAPSADO ---
    document.body.classList.add('sidebar-collapse');
    const btnToggle = document.querySelector('[data-widget="pushmenu"]') || document.querySelector('.sidebar-toggle');
    if (btnToggle && !document.body.classList.contains('sidebar-collapse')) {
        btnToggle.click();
    }

    const url = '/reportes/analisis-ventas/';

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

    const chartCanal = echarts.init(document.getElementById('chart-canal'));
    const chartTreemap = echarts.init(document.getElementById('chart-treemap'));
    const chartGeo = echarts.init(document.getElementById('chart-geo'));

    // Switch Vendedores
    const btnGrafica = document.getElementById('btn-vista-grafica');
    const btnTabla = document.getElementById('btn-vista-tabla');
    const boxTreemap = document.getElementById('contenedor-treemap');
    const boxTabla = document.getElementById('contenedor-tabla-vendedores');

    if (btnGrafica && btnTabla && boxTreemap && boxTabla) {
        btnGrafica.addEventListener('click', function () {
            boxTreemap.classList.remove('hidden');
            boxTabla.classList.add('hidden');
            btnGrafica.className = "px-2.5 py-1 text-xs font-medium text-white bg-indigo-600 rounded-l-lg border border-indigo-600";
            btnTabla.className = "px-2.5 py-1 text-xs font-medium text-gray-300 bg-gray-800 rounded-r-lg border border-gray-700 hover:bg-gray-700";
            chartTreemap.resize();
        });

        btnTabla.addEventListener('click', function () {
            boxTreemap.classList.add('hidden');
            boxTabla.classList.remove('hidden');
            btnTabla.className = "px-2.5 py-1 text-xs font-medium text-white bg-indigo-600 rounded-r-lg border border-indigo-600";
            btnGrafica.className = "px-2.5 py-1 text-xs font-medium text-gray-300 bg-gray-800 rounded-l-lg border border-gray-700 hover:bg-gray-700";
        });
    }

    // Switch Geo
    const btnGeoGrafica = document.getElementById('btn-geo-grafica');
    const btnGeoTabla = document.getElementById('btn-geo-tabla');
    const boxGeoGrafica = document.getElementById('contenedor-geo-grafica');
    const boxGeoTabla = document.getElementById('contenedor-geo-tabla');

    if (btnGeoGrafica && btnGeoTabla && boxGeoGrafica && boxGeoTabla) {
        btnGeoGrafica.addEventListener('click', function () {
            boxGeoGrafica.classList.remove('hidden');
            boxGeoTabla.classList.add('hidden');
            btnGeoGrafica.className = "px-2.5 py-1 text-xs font-medium text-white bg-emerald-600 rounded-l-lg border border-emerald-600";
            btnGeoTabla.className = "px-2.5 py-1 text-xs font-medium text-gray-300 bg-gray-800 rounded-r-lg border border-gray-700 hover:bg-gray-700";
            chartGeo.resize();
        });

        btnGeoTabla.addEventListener('click', function () {
            boxGeoGrafica.classList.add('hidden');
            boxGeoTabla.classList.remove('hidden');
            btnGeoTabla.className = "px-2.5 py-1 text-xs font-medium text-white bg-emerald-600 rounded-r-lg border border-emerald-600";
            btnGeoGrafica.className = "px-2.5 py-1 text-xs font-medium text-gray-300 bg-gray-800 rounded-l-lg border border-gray-700 hover:bg-gray-700";
        });
    }

    const btnDepto = document.getElementById('btn-geo-depto');
    const btnMuni = document.getElementById('btn-geo-muni');
    const btnBarrio = document.getElementById('btn-geo-barrio');

    function setNivelGeo(nivel) {
        window.nivelGeoActual = nivel;
        const activeClass = "px-2.5 py-1 text-xs font-medium text-white bg-emerald-600 border border-emerald-600";
        const inactiveClass = "px-2.5 py-1 text-xs font-medium text-gray-300 bg-gray-800 border border-gray-700 hover:bg-gray-700";

        if (btnDepto && btnMuni && btnBarrio) {
            btnDepto.className = (nivel === 'depto' ? activeClass : inactiveClass) + " rounded-l-lg";
            btnMuni.className = (nivel === 'muni' ? activeClass : inactiveClass);
            btnBarrio.className = (nivel === 'barrio' ? activeClass : inactiveClass) + " rounded-r-lg";
        }

        const thHeader = document.getElementById('th-geo-nombre');
        if (thHeader) {
            thHeader.textContent = nivel === 'depto' ? 'Departamento' : (nivel === 'muni' ? 'Municipio' : 'Barrio');
        }
        renderGeoVisuals();
    }

    if (btnDepto) btnDepto.addEventListener('click', () => setNivelGeo('depto'));
    if (btnMuni) btnMuni.addEventListener('click', () => setNivelGeo('muni'));
    if (btnBarrio) btnBarrio.addEventListener('click', () => setNivelGeo('barrio'));

    const btnResetZona = document.getElementById('btn-reset-zona');
    if (btnResetZona) {
        btnResetZona.addEventListener('click', function () {
            window.filtroZonaActivo = null;
            this.classList.add('hidden');
            document.getElementById('lbl-zona-activa').textContent = "Filtrando: Toda la empresa (Global)";
            renderTopClientes();
        });
    }

    window.addEventListener('resize', function () {
        chartCanal.resize();
        chartTreemap.resize();
        chartGeo.resize();
    });

    window.seleccionarZona = function (nombre) {
        window.filtroZonaActivo = { tipo: window.nivelGeoActual, valor: nombre };
        const tipoLabel = window.nivelGeoActual === 'depto' ? 'Departamento' : (window.nivelGeoActual === 'muni' ? 'Municipio' : 'Barrio');
        document.getElementById('lbl-zona-activa').textContent = `Filtrando: ${tipoLabel} -> ${nombre}`;
        const btnReset = document.getElementById('btn-reset-zona');
        if (btnReset) btnReset.classList.remove('hidden');
        renderTopClientes();
    };

    window.filtrarDesdeTabla = function (nombre) {
        window.seleccionarZona(nombre);
    };

    async function renderTopClientes() {
        const tbody = document.getElementById('tabla-top-clientes-body');
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

        const queryParamsFiltros = new URLSearchParams(filtros).toString();
        let tipo = 'GLOBAL';
        let valor = '';

        if (window.filtroZonaActivo && window.filtroZonaActivo.valor) {
            const mapaNiveles = { 'depto': 'DEPARTAMENTO', 'muni': 'MUNICIPIO', 'barrio': 'BARRIO' };
            tipo = mapaNiveles[window.nivelGeoActual] || 'GLOBAL';
            valor = window.filtroZonaActivo.valor;
        }

        try {
            const response = await fetch(`/reportes/analisis-ventas/api/top-clientes-zona/?tipo=${tipo}&valor=${encodeURIComponent(valor)}&${queryParamsFiltros}`, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });
            const result = await response.json();

            if (result.clientes && result.clientes.length > 0) {
                tbody.innerHTML = result.clientes.map(item => {
                    const ventaFormateada = `$ ${parseInt(item.venta).toLocaleString('es-CO')}`;
                    return `
                        <tr class="hover:bg-gray-700 transition-colors">
                            <td class="py-1 px-2 font-medium text-gray-200 truncate max-w-[200px]" title="${item.cliente}">${item.cliente}</td>
                            <td class="py-1 px-2 text-right font-mono font-bold text-gray-100">${ventaFormateada}</td>
                            <td class="py-1 px-2 text-right font-mono font-semibold text-blue-400">${item.participacion} %</td>
                        </tr>
                    `;
                }).join('');
            } else {
                tbody.innerHTML = `<tr><td colspan="3" class="py-2 text-center text-gray-500">Sin clientes en esta zona.</td></tr>`;
            }
        } catch (error) {
            tbody.innerHTML = `<tr><td colspan="3" class="py-2 text-center text-red-500">Error al cargar clientes.</td></tr>`;
        }
    }

    function renderGeoVisuals() {
        if (!window.globalData) return;

        let lista = [];
        if (window.nivelGeoActual === 'depto') lista = window.globalData.deptos || [];
        else if (window.nivelGeoActual === 'muni') lista = window.globalData.municipios || [];
        else if (window.nivelGeoActual === 'barrio') lista = window.globalData.barrios || [];

        const totalVentaGeo = lista.reduce((acc, curr) => acc + (Number(curr.venta) || 0), 0);

        if (chartGeo) {
            chartGeo.clear();
            if (lista.length === 0) {
                chartGeo.showLoading({ text: 'Sin registros', color: '#10b981', textColor: '#9ca3af', maskColor: 'rgba(17, 24, 39, 0.8)' });
            } else {
                chartGeo.hideLoading();
                const top10 = lista.slice(0, 10);
                const top10Asc = [...top10].reverse();
                const nombresY = top10Asc.map(x => x.nombre);
                const valoresX = top10Asc.map(x => Number(x.venta) || 0);

                chartGeo.setOption({
                    tooltip: {
                        trigger: 'axis', axisPointer: { type: 'shadow' },
                        formatter: function (params) {
                            const item = params[0];
                            const val = item.value || 0;
                            const porc = totalVentaGeo > 0 ? ((val / totalVentaGeo) * 100).toFixed(2) : '0.00';
                            return `<strong>${item.name}</strong><br/>Venta: $ ${parseInt(val).toLocaleString('es-CO')}<br/>Participación: ${porc} %<br/><span style="color:#6ee7b7;font-size:10px;">(Clic para filtrar)</span>`;
                        }
                    },
                    grid: { left: '3%', right: '14%', bottom: '5%', top: '5%', containLabel: true },
                    xAxis: {
                        type: 'value', splitLine: { lineStyle: { color: '#374151', type: 'dashed' } },
                        axisLabel: { formatter: (val) => val >= 1000000 ? `$${Math.round(val / 1000000)}M` : `$${Math.round(val / 1000)}K` }
                    },
                    yAxis: { type: 'category', data: nombresY, axisLabel: { fontSize: 10, align: 'right', formatter: v => v.length > 18 ? v.substring(0, 16) + '...' : v } },
                    series: [{
                        name: 'Venta', type: 'bar', data: valoresX,
                        itemStyle: { color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [{ offset: 0, color: '#10b981' }, { offset: 1, color: '#047857' }]), borderRadius: [0, 4, 4, 0] },
                        label: {
                            show: true, position: 'right', color: '#6ee7b7', fontSize: 10,
                            formatter: function (p) {
                                const val = p.value || 0;
                                const porc = totalVentaGeo > 0 ? ((val / totalVentaGeo) * 100).toFixed(1) : '0';
                                if (val >= 1000000000) return `$${(val / 1000000000).toFixed(1)}B (${porc}%)`;
                                if (val >= 1000000) return `$${Math.round(val / 1000000)}M (${porc}%)`;
                                return `$${Math.round(val / 1000)}K (${porc}%)`;
                            }
                        }
                    }]
                }, true);

                chartGeo.off('click');
                chartGeo.on('click', function (params) {
                    if (params.name) window.seleccionarZona(params.name);
                });
            }
        }

        const tbodyGeo = document.getElementById('tabla-geo-body');
        if (tbodyGeo) {
            if (lista.length > 0) {
                tbodyGeo.innerHTML = lista.map(item => `
                    <tr class="hover:bg-gray-700 transition-colors">
                        <td class="py-1 px-2 font-medium text-gray-200">${item.nombre}</td>
                        <td class="py-1 px-2 text-right font-mono font-bold text-gray-100">$ ${parseInt(item.venta).toLocaleString('es-CO')}</td>
                        <td class="py-1 px-2 text-right font-mono text-gray-300">${item.clientes || 0}</td>
                        <td class="py-1 px-2 text-center">
                            <button onclick="window.filtrarDesdeTabla('${item.nombre.replace(/'/g, "\\'")}')" class="text-xs bg-emerald-600 hover:bg-emerald-500 text-white px-2 py-0.5 rounded transition-colors">Ver Top</button>
                        </td>
                    </tr>
                `).join('');
            } else {
                tbodyGeo.innerHTML = `<tr><td colspan="4" class="py-2 text-center text-gray-500">Sin datos.</td></tr>`;
            }
        }
    }

    function renderData(data) {
        if (!data) return;
        window.globalData = data;

        // 1. Chart Canal (Cyan + Rose)
        if (chartCanal) {
            chartCanal.setOption({
                tooltip: { trigger: 'axis' },
                grid: { left: '8%', right: '8%', bottom: '22%', containLabel: true },
                legend: { data: ['Venta (sin IVA)', '# Clientes'], textStyle: { color: '#9ca3af' } },
                xAxis: { type: 'category', data: (data.canal || []).map(c => c.CANAL), axisLabel: { interval: 0, rotate: 45, fontSize: 10 } },
                yAxis: [
                    { type: 'value', name: 'Venta', splitLine: { lineStyle: { color: '#374151', type: 'dashed' } }, axisLabel: { formatter: (v) => v >= 1000000 ? `$${Math.round(v / 1000000)}M` : `$${Math.round(v / 1000)}K` } },
                    { type: 'value', name: '# Clientes', splitLine: { show: false } }
                ],
                series: [
                    { name: 'Venta (sin IVA)', type: 'bar', data: (data.canal || []).map(c => c.VENTA_SIN_IVA), itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: '#06b6d4' }, { offset: 1, color: '#0369a1' }]), borderRadius: [4, 4, 0, 0] } },
                    { name: '# Clientes', type: 'line', yAxisIndex: 1, data: (data.canal || []).map(c => c.QCLIENTES), itemStyle: { color: '#f43f5e' }, symbolSize: 6, lineStyle: { width: 3, type: 'dashed' } }
                ]
            }, true);
        }

        // 2. Chart Vendedores (Indigo)
        const treemapData = data.treemap || [];
        const totalVentaVendedores = treemapData.reduce((acc, curr) => acc + (Number(curr.value) || 0), 0);
        const vendedoresDesc = [...treemapData].sort((a, b) => (Number(b.value) || 0) - (Number(a.value) || 0));

        if (chartTreemap) {
            const top10Desc = vendedoresDesc.slice(0, 10);
            const top10Asc = [...top10Desc].reverse();

            chartTreemap.setOption({
                tooltip: {
                    trigger: 'axis', axisPointer: { type: 'shadow' },
                    formatter: function (params) {
                        const val = params[0].value || 0;
                        const porc = totalVentaVendedores > 0 ? ((val / totalVentaVendedores) * 100).toFixed(2) : '0.00';
                        return `<strong>${params[0].name}</strong><br/>Venta: $ ${parseInt(val).toLocaleString('es-CO')}<br/>Participación: ${porc} %`;
                    }
                },
                grid: { left: '3%', right: '14%', bottom: '5%', top: '5%', containLabel: true },
                xAxis: { type: 'value', splitLine: { lineStyle: { color: '#374151', type: 'dashed' } }, axisLabel: { formatter: (val) => val >= 1000000 ? `$${Math.round(val / 1000000)}M` : `$${Math.round(val / 1000)}K` } },
                yAxis: { type: 'category', data: top10Asc.map(v => v.name), axisLabel: { fontSize: 10, align: 'right', formatter: v => v.length > 20 ? v.substring(0, 18) + '...' : v } },
                series: [{
                    name: 'Venta', type: 'bar', data: top10Asc.map(v => Number(v.value) || 0),
                    itemStyle: { color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [{ offset: 0, color: '#8b5cf6' }, { offset: 1, color: '#4f46e5' }]), borderRadius: [0, 4, 4, 0] },
                    label: {
                        show: true, position: 'right', color: '#c4b5fd', fontSize: 10,
                        formatter: function (p) {
                            const val = p.value || 0;
                            const porc = totalVentaVendedores > 0 ? ((val / totalVentaVendedores) * 100).toFixed(1) : '0';
                            if (val >= 1000000000) return `$${(val / 1000000000).toFixed(1)}B (${porc}%)`;
                            if (val >= 1000000) return `$${Math.round(val / 1000000)}M (${porc}%)`;
                            return `$${Math.round(val / 1000)}K (${porc}%)`;
                        }
                    }
                }]
            }, true);
        }

        const tablaVendedoresBody = document.getElementById('tabla-vendedores-body');
        if (tablaVendedoresBody) {
            tablaVendedoresBody.innerHTML = vendedoresDesc.map(v => {
                const val = Number(v.value) || 0;
                const porc = totalVentaVendedores > 0 ? ((val / totalVentaVendedores) * 100).toFixed(2) : '0.00';
                return `
                    <tr class="hover:bg-gray-700 transition-colors">
                        <td class="py-1 px-2 font-medium text-gray-200">${v.name}</td>
                        <td class="py-1 px-2 text-right font-mono font-bold text-gray-100">$ ${parseInt(val).toLocaleString('es-CO')}</td>
                        <td class="py-1 px-2 text-right font-mono font-semibold text-indigo-400">${porc} %</td>
                    </tr>
                `;
            }).join('');
        }

        renderGeoVisuals();
        renderTopClientes();

        // 4. Renderizar Matriz Pivot
        const matrizTbody = document.getElementById('matriz-tbody');
        const btnCargarMas = document.getElementById('btn-cargar-mas-clientes');
        const filasHtml = data.rows_html || data.nuevas_filas_html || '';

        if (matrizTbody) {
            matrizTbody.innerHTML = '';
            if (filasHtml && filasHtml.trim() !== '') {
                matrizTbody.innerHTML = filasHtml;
                window.offsetMatriz = 50;
            } else {
                matrizTbody.innerHTML = `<tr><td colspan="14" class="py-4 text-center text-gray-500 font-sans">No hay datos disponibles.</td></tr>`;
            }
        }

        if (btnCargarMas) {
            if (data.has_more) {
                btnCargarMas.classList.remove('hidden');
            } else {
                btnCargarMas.classList.add('hidden');
            }
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

    async function loadMoreClientes() {
        showLoader("CARGANDO CLIENTES...");
        const btnCargarMas = document.getElementById('btn-cargar-mas-clientes');
        if (btnCargarMas) btnCargarMas.disabled = true;

        const filtros = {
            solo_matriz: '1', offset_matriz: window.offsetMatriz, limit_matriz: 50,
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

            const matrizTbody = document.getElementById('matriz-tbody');
            const filasNuevas = data.nuevas_filas_html || data.rows_html || '';
            if (matrizTbody && filasNuevas) {
                matrizTbody.insertAdjacentHTML('beforeend', filasNuevas);
                window.offsetMatriz += (data.total_filas || 50);
            }

            if (btnCargarMas) {
                if (data.has_more) btnCargarMas.classList.remove('hidden');
                else btnCargarMas.classList.add('hidden');
            }
        } catch (error) {
            console.error("Error al paginar matriz:", error);
        } finally {
            if (btnCargarMas) btnCargarMas.disabled = false;
            hideLoader();
        }
    }

    document.getElementById('btn-filtrar')?.addEventListener('click', () => { window.offsetMatriz = 0; fetchData(); });
    document.getElementById('btn-limpiar')?.addEventListener('click', () => {
        if (document.getElementById('filtro-anio')) document.getElementById('filtro-anio').selectedIndex = 0;
        ['mes', 'zona', 'proveedor', 'linea', 'canal'].forEach(f => {
            if (document.getElementById(`filtro-${f}`)) document.getElementById(`filtro-${f}`).value = '';
        });
        window.filtroZonaActivo = null;
        document.getElementById('btn-reset-zona')?.classList.add('hidden');
        document.getElementById('lbl-zona-activa').textContent = "Filtrando: Toda la empresa (Global)";
        window.offsetMatriz = 0;
        fetchData();
    });

    document.getElementById('btn-cargar-mas-clientes')?.addEventListener('click', loadMoreClientes);

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