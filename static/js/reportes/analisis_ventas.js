window.globalData = null;
window.nivelGeoActual = 'muni';
window.filtroZonaActivo = null;
window.offsetMatriz = 0;

document.addEventListener('DOMContentLoaded', function () {
    const url = '/reportes/analisis-ventas/';

    function showLoader() {
        const loader = document.getElementById('global-loader');
        if (loader) loader.classList.remove('hidden');
    }

    function hideLoader() {
        const loader = document.getElementById('global-loader');
        if (loader) loader.classList.add('hidden');
    }

    const canalElem = document.getElementById('chart-canal');
    const treemapElem = document.getElementById('chart-treemap');
    const geoElem = document.getElementById('chart-geo');

    const chartCanal = canalElem ? echarts.init(canalElem) : null;
    const chartTreemap = treemapElem ? echarts.init(treemapElem) : null;
    const chartGeo = geoElem ? echarts.init(geoElem) : null;

    const btnGrafica = document.getElementById('btn-vista-grafica');
    const btnTabla = document.getElementById('btn-vista-tabla');
    const boxTreemap = document.getElementById('contenedor-treemap');
    const boxTabla = document.getElementById('contenedor-tabla-vendedores');

    if (btnGrafica && btnTabla && boxTreemap && boxTabla) {
        btnGrafica.addEventListener('click', function () {
            boxTreemap.classList.remove('hidden');
            boxTabla.classList.add('hidden');
            btnGrafica.className = "px-2.5 py-1 text-xs font-medium text-white bg-blue-600 rounded-l-lg border border-blue-600";
            btnTabla.className = "px-2.5 py-1 text-xs font-medium text-gray-300 bg-gray-800 rounded-r-lg border border-gray-700 hover:bg-gray-700";
            if (chartTreemap) chartTreemap.resize();
        });

        btnTabla.addEventListener('click', function () {
            boxTreemap.classList.add('hidden');
            boxTabla.classList.remove('hidden');
            btnTabla.className = "px-2.5 py-1 text-xs font-medium text-white bg-blue-600 rounded-r-lg border border-blue-600";
            btnGrafica.className = "px-2.5 py-1 text-xs font-medium text-gray-300 bg-gray-800 rounded-l-lg border border-gray-700 hover:bg-gray-700";
        });
    }

    const btnGeoGrafica = document.getElementById('btn-geo-grafica');
    const btnGeoTabla = document.getElementById('btn-geo-tabla');
    const boxGeoGrafica = document.getElementById('contenedor-geo-grafica');
    const boxGeoTabla = document.getElementById('contenedor-geo-tabla');

    if (btnGeoGrafica && btnGeoTabla && boxGeoGrafica && boxGeoTabla) {
        btnGeoGrafica.addEventListener('click', function () {
            boxGeoGrafica.classList.remove('hidden');
            boxGeoTabla.classList.add('hidden');
            btnGeoGrafica.className = "px-2.5 py-1 text-xs font-medium text-white bg-blue-600 rounded-l-lg border border-blue-600";
            btnGeoTabla.className = "px-2.5 py-1 text-xs font-medium text-gray-300 bg-gray-800 rounded-r-lg border border-gray-700 hover:bg-gray-700";
            if (chartGeo) chartGeo.resize();
        });

        btnGeoTabla.addEventListener('click', function () {
            boxGeoGrafica.classList.add('hidden');
            boxGeoTabla.classList.remove('hidden');
            btnGeoTabla.className = "px-2.5 py-1 text-xs font-medium text-white bg-blue-600 rounded-r-lg border border-blue-600";
            btnGeoGrafica.className = "px-2.5 py-1 text-xs font-medium text-gray-300 bg-gray-800 rounded-l-lg border border-gray-700 hover:bg-gray-700";
        });
    }

    const btnDepto = document.getElementById('btn-geo-depto');
    const btnMuni = document.getElementById('btn-geo-muni');
    const btnBarrio = document.getElementById('btn-geo-barrio');

    function setNivelGeo(nivel) {
        window.nivelGeoActual = nivel;
        const activeClass = "px-2.5 py-1 text-xs font-medium text-white bg-blue-600 border border-blue-600";
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
            const lbl = document.getElementById('lbl-zona-activa');
            if (lbl) lbl.textContent = "Filtrando: Toda la empresa (Global)";
            renderTopClientes();
        });
    }

    window.addEventListener('resize', function () {
        if (chartCanal) chartCanal.resize();
        if (chartTreemap) chartTreemap.resize();
        if (chartGeo) chartGeo.resize();
    });

    window.seleccionarZona = function (nombre) {
        window.filtroZonaActivo = { tipo: window.nivelGeoActual, valor: nombre };
        const tipoLabel = window.nivelGeoActual === 'depto' ? 'Departamento' : (window.nivelGeoActual === 'muni' ? 'Municipio' : 'Barrio');

        const lbl = document.getElementById('lbl-zona-activa');
        if (lbl) lbl.textContent = `Filtrando: ${tipoLabel} -> ${nombre}`;

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

        tbody.innerHTML = `<tr><td colspan="3" class="py-2 text-center text-gray-500">
                <svg class="animate-spin inline mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                Cargando Top 20...
            </td></tr>`;

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
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const result = await response.json();

            if (result.clientes && result.clientes.length > 0) {
                tbody.innerHTML = result.clientes.map(item => {
                    const ventaFormateada = `$ ${parseInt(item.venta).toLocaleString('es-CO')}`;
                    return `
                        <tr class="hover:bg-gray-700 transition-colors">
                            <td class="py-1 px-2 font-medium text-gray-200">${item.cliente}</td>
                            <td class="py-1 px-2 text-right font-mono font-bold text-gray-100">${ventaFormateada}</td>
                            <td class="py-1 px-2 text-right font-mono font-semibold text-blue-400">${item.participacion} %</td>
                        </tr>
                    `;
                }).join('');
            } else {
                tbody.innerHTML = `<tr><td colspan="3" class="py-2 text-center text-gray-500">Sin clientes en esta zona.</td></tr>`;
            }
        } catch (error) {
            console.error("Error al cargar el Top 20 Clientes:", error);
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
                chartGeo.showLoading({ text: 'Sin registros', color: '#3b82f6', textColor: '#9ca3af', maskColor: 'rgba(17, 24, 39, 0.8)' });
            } else {
                chartGeo.hideLoading();
                const top10 = lista.slice(0, 10);
                const top10Asc = [...top10].reverse();
                const nombresY = top10Asc.map(x => x.nombre);
                const valoresX = top10Asc.map(x => Number(x.venta) || 0);

                const geoOption = {
                    tooltip: {
                        trigger: 'axis',
                        axisPointer: { type: 'shadow' },
                        formatter: function (params) {
                            const item = params[0];
                            const val = item.value || 0;
                            const porc = totalVentaGeo > 0 ? ((val / totalVentaGeo) * 100).toFixed(2) : '0.00';
                            return `<strong>${item.name}</strong><br/>` +
                                `Venta: $ ${parseInt(val).toLocaleString('es-CO')}<br/>` +
                                `Participación: ${porc} %<br/>` +
                                `<span style="color:#60a5fa;font-size:10px;">(Clic para filtrar)</span>`;
                        }
                    },
                    grid: { left: '3%', right: '14%', bottom: '5%', top: '5%', containLabel: true },
                    xAxis: {
                        type: 'value',
                        axisLabel: {
                            formatter: function (val) {
                                if (val >= 1000000000) return '$' + (val / 1000000000).toFixed(1) + 'B';
                                if (val >= 1000000) return '$' + Math.round(val / 1000000) + 'M';
                                return '$' + Math.round(val / 1000) + 'K';
                            }
                        },
                        splitLine: { lineStyle: { color: '#374151', type: 'dashed' } }
                    },
                    yAxis: {
                        type: 'category',
                        data: nombresY,
                        axisLabel: { fontSize: 10, formatter: function (v) { return v.length > 18 ? v.substring(0, 16) + '...' : v; } }
                    },
                    series: [{
                        name: 'Venta', type: 'bar', data: valoresX,
                        itemStyle: {
                            borderRadius: [0, 4, 4, 0],
                            color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [{ offset: 0, color: '#10b981' }, { offset: 1, color: '#047857' }])
                        },
                        label: {
                            show: true, position: 'right', fontSize: 10, color: '#6ee7b7',
                            formatter: function (p) {
                                const val = p.value || 0;
                                const porc = totalVentaGeo > 0 ? ((val / totalVentaGeo) * 100).toFixed(1) : '0';
                                if (val >= 1000000000) return `$${(val / 1000000000).toFixed(1)}B (${porc}%)`;
                                if (val >= 1000000) return `$${Math.round(val / 1000000)}M (${porc}%)`;
                                return `$${Math.round(val / 1000)}K (${porc}%)`;
                            }
                        }
                    }]
                };
                chartGeo.setOption(geoOption, true);
                chartGeo.resize();
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
                            <button onclick="window.filtrarDesdeTabla('${item.nombre.replace(/'/g, "\\'")}')" 
                                    class="text-xs bg-blue-600 hover:bg-blue-700 text-white px-2 py-0.5 rounded transition-colors">Ver Top</button>
                        </td>
                    </tr>
                `).join('');
            } else {
                tbodyGeo.innerHTML = `<tr><td colspan="4" class="py-2 text-center text-gray-500">Sin datos registrados para esta vista.</td></tr>`;
            }
        }
    }

    function renderData(data) {
        if (!data) return;
        window.globalData = data;

        if (chartCanal) {
            const canalOption = {
                tooltip: { trigger: 'axis' },
                grid: { left: '8%', right: '8%', bottom: '22%', containLabel: true },
                legend: { data: ['Venta (sin IVA)', '# Clientes'], textStyle: { color: '#9ca3af' } },
                xAxis: { type: 'category', data: (data.canal || []).map(c => c.CANAL), axisLabel: { interval: 0, rotate: 45, fontSize: 10 } },
                yAxis: [
                    {
                        type: 'value', name: 'Venta',
                        axisLabel: {
                            formatter: function (value) {
                                if (value >= 1000000000) return '$' + Math.round(value / 1000000000).toFixed(1) + 'B';
                                if (value >= 1000000) return '$' + Math.round(value / 1000000) + 'M';
                                return '$' + Math.round(value / 1000) + 'K';
                            }
                        },
                        splitLine: { lineStyle: { color: '#374151', type: 'dashed' } }
                    },
                    { type: 'value', name: '# Clientes', splitLine: { show: false } }
                ],
                series: [
                    { name: 'Venta (sin IVA)', type: 'bar', itemStyle: { color: '#3b82f6' }, data: (data.canal || []).map(c => c.VENTA_SIN_IVA) },
                    { name: '# Clientes', type: 'line', yAxisIndex: 1, itemStyle: { color: '#10b981' }, data: (data.canal || []).map(c => c.QCLIENTES) }
                ]
            };
            chartCanal.setOption(canalOption);
        }

        const treemapData = data.treemap || [];
        const totalVentaVendedores = treemapData.reduce((acc, curr) => acc + (Number(curr.value) || 0), 0);
        const vendedoresDesc = [...treemapData].sort((a, b) => (Number(b.value) || 0) - (Number(a.value) || 0));

        if (chartTreemap) {
            const top10Desc = vendedoresDesc.slice(0, 10);
            const top10Asc = [...top10Desc].reverse();
            const nombresY = top10Asc.map(v => v.name);
            const valoresX = top10Asc.map(v => Number(v.value) || 0);

            const barOption = {
                tooltip: {
                    trigger: 'axis', axisPointer: { type: 'shadow' },
                    formatter: function (params) {
                        const item = params[0];
                        const val = item.value || 0;
                        const porc = totalVentaVendedores > 0 ? ((val / totalVentaVendedores) * 100).toFixed(2) : '0.00';
                        return `<strong>${item.name}</strong><br/>Venta: $ ${parseInt(val).toLocaleString('es-CO')}<br/>Participación: ${porc} %`;
                    }
                },
                grid: { left: '3%', right: '14%', bottom: '5%', top: '5%', containLabel: true },
                xAxis: {
                    type: 'value',
                    axisLabel: {
                        formatter: function (val) {
                            if (val >= 1000000000) return '$' + (val / 1000000000).toFixed(1) + 'B';
                            if (val >= 1000000) return '$' + Math.round(val / 1000000) + 'M';
                            return '$' + Math.round(val / 1000) + 'K';
                        }
                    },
                    splitLine: { lineStyle: { color: '#374151', type: 'dashed' } }
                },
                yAxis: { type: 'category', data: nombresY, axisLabel: { fontSize: 10, formatter: function (v) { return v.length > 20 ? v.substring(0, 18) + '...' : v; } } },
                series: [{
                    name: 'Venta', type: 'bar', data: valoresX,
                    itemStyle: {
                        borderRadius: [0, 4, 4, 0],
                        color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [{ offset: 0, color: '#3b82f6' }, { offset: 1, color: '#1d4ed8' }])
                    },
                    label: {
                        show: true, position: 'right', fontSize: 10, color: '#93c5fd',
                        formatter: function (p) {
                            const val = p.value || 0;
                            const porc = totalVentaVendedores > 0 ? ((val / totalVentaVendedores) * 100).toFixed(1) : '0';
                            if (val >= 1000000000) return `$${(val / 1000000000).toFixed(1)}B (${porc}%)`;
                            if (val >= 1000000) return `$${Math.round(val / 1000000)}M (${porc}%)`;
                            return `$${Math.round(val / 1000)}K (${porc}%)`;
                        }
                    }
                }]
            };
            chartTreemap.setOption(barOption, true);
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
                        <td class="py-1 px-2 text-right font-mono font-semibold text-blue-400">${porc} %</td>
                    </tr>
                `;
            }).join('');
        }

        renderGeoVisuals();
        renderTopClientes();

        const matrizTbody = document.getElementById('matriz-tbody');
        const btnCargarMas = document.getElementById('btn-cargar-mas-clientes');
        const filasHtml = data.rows_html || data.nuevas_filas_html || '';

        if (matrizTbody) {
            matrizTbody.innerHTML = '';
            if (filasHtml && filasHtml.trim() !== '') {
                matrizTbody.innerHTML = filasHtml;
                window.offsetMatriz = 50;
            } else {
                matrizTbody.innerHTML = `<tr><td colspan="14" class="py-4 text-center text-gray-500 font-sans">No hay datos disponibles para los filtros seleccionados.</td></tr>`;
            }
        }

        if (btnCargarMas) {
            if (data.has_more) {
                btnCargarMas.classList.remove('hidden');
                btnCargarMas.innerHTML = '<span>Cargar más clientes...</span>';
            } else {
                btnCargarMas.classList.add('hidden');
            }
        }
    }

    async function fetchData() {
        showLoader(); // Muestra el overlay de carga general
        const anio = document.getElementById('filtro-anio')?.value || '';
        const mes = document.getElementById('filtro-mes')?.value || '';
        const zona = document.getElementById('filtro-zona')?.value || '';
        const proveedor = document.getElementById('filtro-proveedor')?.value || '';
        const linea = document.getElementById('filtro-linea')?.value || '';
        const canal = document.getElementById('filtro-canal')?.value || '';

        const params = new URLSearchParams({
            anio: anio, mes: mes, zona: zona, proveedor: proveedor,
            linea: linea, canal: canal, offset_matriz: 0, limit_matriz: 50
        });

        try {
            const response = await fetch(`${url}?${params.toString()}`, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json();
            renderData(data);
        } catch (error) {
            console.error("Error al obtener datos:", error);
            alert("Error al cargar los datos. Por favor intente de nuevo.");
        } finally {
            hideLoader(); // Lo oculta al terminar
        }
    }

    async function loadMoreClientes() {
        showLoader(); // Muestra el overlay al cargar la siguiente página de matriz
        const btnCargarMas = document.getElementById('btn-cargar-mas-clientes');
        if (btnCargarMas) {
            btnCargarMas.disabled = true;
            btnCargarMas.innerHTML = `<svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-blue-400 inline" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg><span>Cargando más clientes...</span>`;
        }

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
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json();

            const matrizTbody = document.getElementById('matriz-tbody');
            const filasNuevas = data.nuevas_filas_html || data.rows_html || '';
            if (matrizTbody && filasNuevas) {
                matrizTbody.insertAdjacentHTML('beforeend', filasNuevas);
                window.offsetMatriz += (data.total_filas || 50);
            }

            if (btnCargarMas) {
                if (data.has_more) {
                    btnCargarMas.classList.remove('hidden');
                    btnCargarMas.innerHTML = '<span>Cargar más clientes...</span>';
                } else {
                    btnCargarMas.classList.add('hidden');
                }
            }
        } catch (error) {
            console.error("Error al paginar matriz:", error);
            if (btnCargarMas) btnCargarMas.innerHTML = '<span>Reintentar cargar clientes</span>';
        } finally {
            if (btnCargarMas) btnCargarMas.disabled = false;
            hideLoader();
        }
    }

    const btnFiltrar = document.getElementById('btn-filtrar');
    if (btnFiltrar) {
        btnFiltrar.addEventListener('click', () => { window.offsetMatriz = 0; fetchData(); });
    }

    const btnLimpiar = document.getElementById('btn-limpiar');
    if (btnLimpiar) {
        btnLimpiar.addEventListener('click', () => {
            if (document.getElementById('filtro-anio')) document.getElementById('filtro-anio').selectedIndex = 0;
            if (document.getElementById('filtro-mes')) document.getElementById('filtro-mes').value = '';
            if (document.getElementById('filtro-zona')) document.getElementById('filtro-zona').value = '';
            if (document.getElementById('filtro-proveedor')) document.getElementById('filtro-proveedor').value = '';
            if (document.getElementById('filtro-linea')) document.getElementById('filtro-linea').value = '';
            if (document.getElementById('filtro-canal')) document.getElementById('filtro-canal').value = '';

            window.filtroZonaActivo = null;
            const btnReset = document.getElementById('btn-reset-zona');
            if (btnReset) btnReset.classList.add('hidden');
            const lbl = document.getElementById('lbl-zona-activa');
            if (lbl) lbl.textContent = "Filtrando: Toda la empresa (Global)";

            window.offsetMatriz = 0;
            fetchData();
        });
    }

    const btnCargarMasClientes = document.getElementById('btn-cargar-mas-clientes');
    if (btnCargarMasClientes) {
        btnCargarMasClientes.addEventListener('click', loadMoreClientes);
    }

    try {
        const rawJson = document.getElementById('initial-data')?.textContent;
        if (rawJson && rawJson.trim() !== '') {
            let cleanJson = rawJson.trim();
            if (cleanJson.includes('\\u0022')) cleanJson = cleanJson.replace(/\\u0022/g, '"');
            const initialData = JSON.parse(cleanJson);

            if (!initialData.rows_html && !initialData.nuevas_filas_html) {
                fetchData();
            } else {
                renderData(initialData);
            }
        } else {
            fetchData();
        }
    } catch (e) {
        console.error("Error leyendo datos iniciales:", e);
        fetchData();
    }
});