from django.contrib import admin

from compras.models import Compra, CompraItem, OrdenCompra, OrdenCompraItem
from finanzas.models import Cuenta, Movimiento
from ventas.models import Cotizacion, CotizacionItem, Venta, VentaItem

from .models import Empresa, Kardex, Producto, Serie, Tercero

admin.site.site_header = 'ERP V1.0 — Administración'

for modelo in (Empresa, Serie, Kardex, Cuenta):
    admin.site.register(modelo)


@admin.register(Tercero)
class TerceroAdmin(admin.ModelAdmin):
    list_display = ('numero_doc', 'nombre', 'tipo', 'activo')
    search_fields = ('numero_doc', 'nombre')
    list_filter = ('tipo',)


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'tipo', 'precio_venta', 'stock')
    search_fields = ('codigo', 'nombre')


def _inline(modelo):
    return type(f'{modelo.__name__}Inline', (admin.TabularInline,), {'model': modelo, 'extra': 0})


for doc, item in ((Compra, CompraItem), (Venta, VentaItem), (OrdenCompra, OrdenCompraItem),
                  (Cotizacion, CotizacionItem)):
    admin.site.register(doc, type(f'{doc.__name__}Admin', (admin.ModelAdmin,), {
        'inlines': [_inline(item)], 'list_display': ('__str__', 'tercero', 'total', 'estado'),
        'search_fields': ('numero', 'tercero__nombre')}))


@admin.register(Movimiento)
class MovimientoAdmin(admin.ModelAdmin):
    list_display = ('voucher', 'fecha', 'cuenta', 'tipo', 'concepto', 'monto', 'conciliado')
    list_filter = ('cuenta', 'tipo', 'concepto', 'conciliado')
