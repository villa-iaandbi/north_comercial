from django.db.models import Sum
from django.views.generic import ListView

from pos.models import PosTicketHeader, TsCaja
from pos.views import _token_empresa_sesion


class ArqueoCalienteView(ListView):
    """
    Portal de auditoría: tickets POS aún no consolidados por el Cierre Z.
    Filtro opcional por caja mediante ?caja=<ID_CAJA>.
    """
    model = PosTicketHeader
    template_name = 'pos/arqueo/lista.html'
    context_object_name = 'tickets'

    def _cajas_activas(self):
        # TsCaja vive en Oracle: se materializa en lista (evita FETCH FIRST / ORA-00933).
        return list(TsCaja.objects.filter(estado='A').order_by('id_caja'))

    def _caja_seleccionada(self):
        caja_id = self.request.GET.get('caja', '').strip()
        if not caja_id:
            return None
        for caja in self._cajas_activas():
            if str(caja.id_caja) == caja_id:
                return caja
        return None

    def get_queryset(self):
        # PosTurno.caja_id es texto libre (p. ej. 'CAJA-01'): se compara contra
        # id_caja y cod_caja de TsCaja, ya que están en bases de datos distintas.
        queryset = (
            PosTicketHeader.objects
            .filter(consolidado_cierre=False, token_empresa=_token_empresa_sesion(self.request))
            .select_related('turno')
            .prefetch_related('items')
            .order_by('-fch_ticket')
        )
        caja = self._caja_seleccionada()
        if caja is not None:
            identificadores = {str(caja.id_caja)}
            if caja.cod_caja:
                identificadores.add(str(caja.cod_caja))
            queryset = queryset.filter(turno__caja_id__in=identificadores)
        elif self.request.GET.get('caja', '').strip():
            queryset = queryset.none()
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        caja = self._caja_seleccionada()
        totales = self.object_list.aggregate(
            efectivo=Sum('pago_efectivo'),
            tarjeta=Sum('pago_tarjeta'),
            transferencia=Sum('pago_transferencia'),
            total=Sum('tot_ticket'),
        )
        context.update({
            'cajas': self._cajas_activas(),
            'caja_seleccionada': str(caja.id_caja) if caja else '',
            'totales': totales,
        })
        return context
