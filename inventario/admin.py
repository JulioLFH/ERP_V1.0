from django.contrib import admin

from .models import Operacion, OperacionItem, TipoOperacion


@admin.register(TipoOperacion)
class TipoOperacionAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'clase', 'origen', 'cuenta_contable', 'codigo_sunat', 'activo')


class OperacionItemInline(admin.TabularInline):
    model = OperacionItem
    extra = 0


@admin.register(Operacion)
class OperacionAdmin(admin.ModelAdmin):
    inlines = [OperacionItemInline]
    list_display = ('__str__', 'fecha', 'almacen_origen', 'almacen_destino', 'estado')
    list_filter = ('tipo', 'estado')
