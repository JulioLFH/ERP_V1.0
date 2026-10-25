"""Trazabilidad por lote desde Inventario y Calidad (INV-30): hacia atrás (orden que lo fabricó y lotes de los
insumos) y hacia adelante (órdenes que lo consumieron y clientes a los que se vendió)."""
from types import SimpleNamespace

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def trazabilidad(request):
    from produccion.reportes_produccion import trazabilidad as calcular
    lote = (request.GET.get('lote') or '').strip()
    datos = calcular(SimpleNamespace(lote=lote, cliente=None, producto=None)) if lote else {'lotes': [], 'avisos': []}
    return render(request, 'inventario/trazabilidad.html', {'d': datos, 'lote': lote})
