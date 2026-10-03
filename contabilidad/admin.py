from django.contrib import admin

from .models import Asiento, AsientoLinea, CentroCosto, CuentaContable, CuentaDefecto, PeriodoContable


class LineaInline(admin.TabularInline):
    model = AsientoLinea
    extra = 0


@admin.register(Asiento)
class AsientoAdmin(admin.ModelAdmin):
    inlines = [LineaInline]
    list_display = ('numero', 'fecha', 'libro', 'origen', 'glosa')
    list_filter = ('periodo', 'libro', 'origen')


@admin.register(CuentaContable)
class CuentaContableAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'naturaleza', 'imputable', 'destino_debe', 'destino_haber')
    search_fields = ('codigo', 'nombre')


for modelo in (CuentaDefecto, CentroCosto, PeriodoContable):
    admin.site.register(modelo)
