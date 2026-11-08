from django.views.generic import ListView

from pos.models import PrPromocion


class PromocionListView(ListView):
    """Listado de promociones configuradas (PR_PROMOCION)."""
    model = PrPromocion
    template_name = 'pos/promociones/lista.html'
    context_object_name = 'promociones'

    def get_queryset(self):
        return (
            PrPromocion.objects
            .prefetch_related('condiciones', 'acciones')
            .order_by('-activo', 'prioridad', '-fch_inicio')
        )
