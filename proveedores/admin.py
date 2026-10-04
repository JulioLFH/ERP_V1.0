from django.contrib import admin

from .models import AccesoProveedor, FacturaProveedor, FacturaProveedorItem

admin.site.register(AccesoProveedor)


class FacturaProveedorItemInline(admin.TabularInline):
    model = FacturaProveedorItem
    extra = 0


@admin.register(FacturaProveedor)
class FacturaProveedorAdmin(admin.ModelAdmin):
    inlines = [FacturaProveedorItemInline]
    list_display = ('__str__', 'tercero', 'orden_compra', 'fecha_emision', 'total', 'estado', 'estado_sunat')
    list_filter = ('estado', 'estado_sunat')
