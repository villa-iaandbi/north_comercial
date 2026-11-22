from django.contrib import admin

from pos.models import PrAccion, PrCondicion, PrPromocion


class PrCondicionInline(admin.TabularInline):
    model = PrCondicion
    extra = 0


class PrAccionInline(admin.TabularInline):
    model = PrAccion
    extra = 0


@admin.register(PrPromocion)
class PrPromocionAdmin(admin.ModelAdmin):
    list_display = ('nom_promocion', 'activo', 'prioridad', 'fch_inicio', 'fch_fin')
    list_filter = ('activo',)
    search_fields = ('nom_promocion',)
    inlines = [PrCondicionInline, PrAccionInline]
