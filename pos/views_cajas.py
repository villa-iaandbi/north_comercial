import logging
from datetime import datetime

from django import forms
from django.contrib import messages
from django.http import Http404, JsonResponse
from core.tenant import get_oracle_connection
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from core.models import CoCentroCosto, CoTipoDocumento, InBodega
from pos.models import TsCaja

logger = logging.getLogger(__name__)

MODULO_TIPO_DOC = 'CT_VENTAS_CONTADO'

TW_INPUT = (
    'w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 '
    'shadow-sm focus:border-purple-500 focus:outline-none focus:ring-2 focus:ring-purple-200 '
    'disabled:bg-gray-100 disabled:text-gray-500'
)


def _consecutivos_oracle():
    """Retorna lista de (id_doc_consecutivo, nom, prefijo) desde CO_DOC_CONSECUTIVOS."""
    try:
        with get_oracle_connection().cursor() as cursor:
            cursor.execute(
                "SELECT ID_DOC_CONSECUTIVO, NOM_DOC_CONSECUTIVO, PREFIJO "
                "FROM CO_DOC_CONSECUTIVOS ORDER BY ID_DOC_CONSECUTIVO"
            )
            return [(r[0], r[1] or '', r[2] or '') for r in cursor.fetchall()]
    except Exception:
        logger.exception("No fue posible leer CO_DOC_CONSECUTIVOS")
        return []


def _ano_actual():
    return str(datetime.now().year)


def _consecutivos_por_tipo_documento(id_tipo_documento, ano=None):
    """
    Retorna lista de (id_doc_consecutivo, nom, prefijo) válidos para un tipo de
    documento en el año indicado (por defecto el año actual).
    """
    if not id_tipo_documento:
        return []
    ano = ano or _ano_actual()
    try:
        with get_oracle_connection().cursor() as cursor:
            cursor.execute(
                "SELECT DC.ID_DOC_CONSECUTIVO, DC.NOM_DOC_CONSECUTIVO, DC.PREFIJO "
                "FROM CO_TIPO_DOC_CONSECUTIVOS TDC "
                "JOIN CO_DOC_CONSECUTIVOS DC "
                "  ON DC.ID_DOC_CONSECUTIVO = TDC.ID_DOC_CONSECUTIVO "
                " AND DC.ID_ANO = TDC.ID_ANO "
                "WHERE TDC.ID_TIPO_DOCUMENTO = %s AND TDC.ID_ANO = %s "
                "ORDER BY DC.ID_DOC_CONSECUTIVO",
                [id_tipo_documento, ano],
            )
            return [(r[0], r[1] or '', r[2] or '') for r in cursor.fetchall()]
    except Exception:
        logger.exception("No fue posible leer los consecutivos del tipo de documento %s", id_tipo_documento)
        return []


def _etiqueta_consecutivo(id_, nom, prefijo):
    return f"{id_} - {nom}" + (f" [{prefijo}]" if prefijo else "")


def _prefijos_por_consecutivo():
    return {c[0]: c[2] for c in _consecutivos_oracle()}


class TsCajaForm(forms.ModelForm):
    id_bodega = forms.ChoiceField(label='Bodega')
    id_centro_costo = forms.ChoiceField(label='Centro de Costo')
    id_tipo_documento = forms.ChoiceField(label='Tipo de Documento')
    id_doc_consecutivo = forms.ChoiceField(label='Consecutivo')

    class Meta:
        model = TsCaja
        fields = [
            'cod_caja', 'descripcion', 'id_bodega', 'id_centro_costo',
            'id_tipo_documento', 'id_doc_consecutivo', 'estado',
        ]
        labels = {
            'cod_caja': 'Código',
            'descripcion': 'Descripción',
            'estado': 'Estado',
        }
        widgets = {
            'cod_caja': forms.TextInput(),
            'descripcion': forms.TextInput(),
            'estado': forms.Select(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        editando = bool(self.instance and not self.instance._state.adding)
        ano_actual = _ano_actual()

        self.fields['id_bodega'].choices = self._choices(
            [(b.id_bodega, f"{b.id_bodega} - {b.nom_bodega}") for b in InBodega.objects.order_by('id_bodega')],
            self.instance.id_bodega if editando else None,
        )
        self.fields['id_centro_costo'].choices = self._choices(
            [(c.id_centro_costo, f"{c.cod_centro_costo} - {c.nom_centro_costo}")
             for c in CoCentroCosto.objects.filter(id_ano=ano_actual).order_by('cod_centro_costo')],
            self.instance.id_centro_costo if editando else None,
        )
        self.fields['id_tipo_documento'].choices = self._choices(
            [(t.id_tipo_documento, f"{t.cod_tipo_documento} - {t.nom_tipo_documento}")
             for t in CoTipoDocumento.objects.filter(
                 id_ano=ano_actual, modulo=MODULO_TIPO_DOC
             ).order_by('cod_tipo_documento')],
            self.instance.id_tipo_documento if editando else None,
        )

        # Consecutivos: dependen del tipo de documento (enviado, o el de la instancia al editar).
        if self.is_bound:
            tipo_doc = self.data.get(self.add_prefix('id_tipo_documento'))
        elif editando:
            tipo_doc = self.instance.id_tipo_documento
        else:
            tipo_doc = None
        self.fields['id_doc_consecutivo'].choices = self._choices(
            [(i, _etiqueta_consecutivo(i, nom, pref))
             for i, nom, pref in _consecutivos_por_tipo_documento(tipo_doc, ano_actual)],
            self.instance.id_doc_consecutivo if editando else None,
        )

        for field in self.fields.values():
            existente = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = f"{existente} {TW_INPUT}".strip()

    @staticmethod
    def _choices(opciones, valor_actual=None):
        choices = [('', '-- Seleccione --')] + list(opciones)
        if valor_actual and valor_actual not in {c[0] for c in choices}:
            choices.append((valor_actual, valor_actual))
        return choices

    def validate_unique(self):
        # id_caja se genera desde SEC_TS_CAJAS en la vista; además el validate_unique
        # estándar usa .exists() -> FETCH FIRST, incompatible con Oracle 11g.
        pass


class CajaListView(ListView):
    model = TsCaja
    template_name = 'pos/cajas/lista.html'
    context_object_name = 'cajas'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        bodegas = {b.id_bodega: b.nom_bodega for b in InBodega.objects.all()}
        prefijos = _prefijos_por_consecutivo()
        filas = []
        for caja in ctx['cajas']:
            filas.append({
                'caja': caja,
                'bodega': bodegas.get(caja.id_bodega, caja.id_bodega),
                'prefijo': prefijos.get(caja.id_doc_consecutivo, ''),
            })
        ctx['filas'] = filas
        return ctx


class CajaCreateView(CreateView):
    model = TsCaja
    form_class = TsCajaForm
    template_name = 'pos/cajas/form.html'
    success_url = reverse_lazy('pos:cajas_lista')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['titulo'] = 'Nueva Caja'
        return ctx

    def form_valid(self, form):
        # El ID se toma de la secuencia de Oracle antes de insertar.
        with get_oracle_connection().cursor() as cursor:
            cursor.execute("SELECT SEC_TS_CAJAS.NEXTVAL FROM DUAL")
            form.instance.id_caja = str(int(cursor.fetchone()[0]))
        messages.success(self.request, 'Caja creada correctamente.')
        return super().form_valid(form)


class CajaUpdateView(UpdateView):
    model = TsCaja
    form_class = TsCajaForm
    template_name = 'pos/cajas/form.html'
    success_url = reverse_lazy('pos:cajas_lista')

    def get_object(self, queryset=None):
        # .get() aplica slicing (FETCH FIRST) -> ORA-00933 en Oracle 11g.
        resultados = list(TsCaja.objects.filter(pk=self.kwargs['pk']))
        if not resultados:
            raise Http404('Caja no encontrada.')
        return resultados[0]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['titulo'] = f'Editar Caja {self.object.id_caja}'
        return ctx

    def form_valid(self, form):
        messages.success(self.request, 'Caja actualizada correctamente.')
        return super().form_valid(form)


def api_consecutivos(request):
    """Retorna los consecutivos válidos (año actual) para un tipo de documento."""
    id_tipo_documento = (request.GET.get('id_tipo_documento') or '').strip()
    consecutivos = [
        {'id': i, 'label': _etiqueta_consecutivo(i, nom, pref)}
        for i, nom, pref in _consecutivos_por_tipo_documento(id_tipo_documento)
    ]
    return JsonResponse({'consecutivos': consecutivos})
