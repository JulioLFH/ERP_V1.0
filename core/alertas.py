"""Alertas del sistema: vencimientos, aprobaciones pendientes, stock bajo y tareas de cierre.

Cada alerta se calcula solo si el usuario tiene el módulo (y el permiso, cuando aplica). El resultado se guarda en
caché unos minutos por usuario para que la campana no haga más lenta ninguna pantalla."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from .modulos import modulos_del_usuario
from .permisos import puede

D0 = Decimal('0')
DIAS_AVISO = 7
SEGUNDOS_CACHE = 180


def _alerta(nivel, icono, titulo, detalle, url, cantidad=0, monto=None):
    return {'nivel': nivel, 'icono': icono, 'titulo': titulo, 'detalle': detalle, 'url': url, 'cantidad': cantidad,
            'monto': monto}


def _vencimientos(modelo, hoy):
    """(vencidos, por vencer en DIAS_AVISO días) con saldo pendiente: [(documento, saldo S/)]."""
    vencidos, por_vencer = [], []
    qs = (modelo.objects.con_saldos().cobrables().filter(estado='REGISTRADO').exclude(tipo_comprobante='07')
          .select_related('tercero'))
    for d in qs:
        if d.saldo <= 0 or not d.fecha_vencimiento:
            continue
        if d.fecha_vencimiento < hoy:
            vencidos.append((d, d.saldo_pen))
        elif d.fecha_vencimiento <= hoy + timedelta(days=DIAS_AVISO):
            por_vencer.append((d, d.saldo_pen))
    return vencidos, por_vencer


def calcular(user):
    hoy = timezone.localdate()
    mods = modulos_del_usuario(user)
    alertas = []
    if 'ventas' in mods:
        from ventas.models import Venta
        vencidos, por_vencer = _vencimientos(Venta, hoy)
        if vencidos:
            alertas.append(_alerta('danger', 'bi-cash-coin', 'Cobranzas vencidas',
                                   f'{len(vencidos)} comprobante(s) de clientes vencidos e impagos.',
                                   reverse('ventas:pendientes'), len(vencidos), sum((s for _, s in vencidos), D0)))
        if por_vencer:
            alertas.append(_alerta('warning', 'bi-calendar-event', 'Cobranzas por vencer',
                                   f'{len(por_vencer)} comprobante(s) vencen en los próximos {DIAS_AVISO} días.',
                                   reverse('ventas:pendientes'), len(por_vencer), sum((s for _, s in por_vencer), D0)))
        from .models import FacturacionConfig
        if FacturacionConfig.actual().activa:
            sin_sunat = Venta.objects.filter(estado='REGISTRADO', es_saldo_inicial=False, es_historico=False,
                                             tipo_comprobante__in=['01', '03', '07', '08'],
                                             estado_sunat__in=['ERROR', 'RECHAZADO', 'NO_ENVIADO']).count()
            if sin_sunat:
                alertas.append(_alerta('danger', 'bi-cloud-slash', 'Comprobantes no aceptados por SUNAT',
                                       f'{sin_sunat} comprobante(s) con error, rechazados o sin enviar.',
                                       reverse('ventas:lista'), sin_sunat))
    if 'compras' in mods:
        from compras.models import Compra, OrdenCompra
        vencidos, por_vencer = _vencimientos(Compra, hoy)
        if vencidos:
            alertas.append(_alerta('danger', 'bi-credit-card', 'Pagos a proveedores vencidos',
                                   f'{len(vencidos)} factura(s) de proveedores vencidas e impagas.',
                                   reverse('compras:pendientes'), len(vencidos), sum((s for _, s in vencidos), D0)))
        if por_vencer:
            alertas.append(_alerta('warning', 'bi-calendar-event', 'Pagos por vencer',
                                   f'{len(por_vencer)} factura(s) vencen en los próximos {DIAS_AVISO} días.',
                                   reverse('compras:pendientes'), len(por_vencer), sum((s for _, s in por_vencer), D0)))
        if puede(user, 'compras.aprobar_oc'):
            n = OrdenCompra.objects.filter(estado='PENDIENTE').count()
            if n:
                alertas.append(_alerta('info', 'bi-clipboard-check', 'Órdenes de compra por aprobar',
                                       f'{n} orden(es) de compra pendientes de aprobación.',
                                       reverse('compras:oc_lista'), n))
        if puede(user, 'compras.portal'):
            from proveedores.models import FacturaProveedor
            n = FacturaProveedor.objects.filter(estado='ENVIADA').count()
            if n:
                alertas.append(_alerta('info', 'bi-inbox', 'Facturas de proveedores por revisar',
                                       f'{n} factura(s) cargadas en el portal esperan su revisión.',
                                       reverse('compras:portal_facturas'), n))
    if 'inventario' in mods or 'compras' in mods:
        from .models import Producto
        reponer = [p for p in Producto.objects.filter(activo=True, tipo='BIEN') if p.requiere_reposicion]
        if reponer:
            alertas.append(_alerta('warning', 'bi-box-seam', 'Stock bajo',
                                   f'{len(reponer)} producto(s) en o bajo su punto de reorden: '
                                   f'{", ".join(p.nombre for p in reponer[:3])}{"…" if len(reponer) > 3 else ""}.',
                                   reverse('inv_reposicion'), len(reponer)))
        from .lotes import por_vencer
        lotes = por_vencer(hoy)
        if lotes:
            vencidos = sum(1 for l in lotes if l.vencido)
            alertas.append(_alerta('danger' if vencidos else 'warning', 'bi-hourglass-split', 'Lotes por vencer',
                                   f'{len(lotes)} lote(s) con stock vencen pronto' +
                                   (f' ({vencidos} ya vencidos).' if vencidos else '.'),
                                   reverse('inv_lotes') + '?vencimiento=1', len(lotes)))
    from inventario.models import RequerimientoInterno
    if puede(user, 'requerimientos.aprobar'):
        n = RequerimientoInterno.objects.filter(estado='ENVIADO').exclude(solicitante=user).count()
        if n:
            alertas.append(_alerta('info', 'bi-clipboard-check', 'Requerimientos por aprobar',
                                   f'{n} requerimiento(s) de materiales esperan su aprobación.',
                                   reverse('requerimientos:lista') + '?estado=ENVIADO', n))
    if 'inventario' in mods and puede(user, 'inventario.operar'):
        n = RequerimientoInterno.objects.filter(estado__in=['APROBADO', 'PARCIAL']).count()
        if n:
            alertas.append(_alerta('warning', 'bi-box-arrow-up', 'Requerimientos por atender',
                                   f'{n} requerimiento(s) aprobados esperan la entrega del almacén.',
                                   reverse('requerimientos:lista') + '?estado=APROBADO', n))
    if 'manufactura' in mods:
        from produccion.servicios import requerimientos
        faltan = [f for f in requerimientos() if f['faltante'] > 0]
        if faltan:
            alertas.append(_alerta('warning', 'bi-basket', 'Insumos insuficientes para producir',
                                   f'{len(faltan)} insumo(s) no alcanzan para las órdenes de producción abiertas.',
                                   reverse('manufactura:requerimiento'), len(faltan)))
    mes_anterior = (hoy.replace(day=1) - timedelta(days=1)).strftime('%Y%m')
    if 'activos' in mods and puede(user, 'activos.depreciar'):
        from activos.models import ActivoFijo
        from activos.servicios import periodo_siguiente
        siguiente = periodo_siguiente()
        if siguiente and siguiente <= mes_anterior and ActivoFijo.objects.filter(estado='ACTIVO').exists():
            alertas.append(_alerta('warning', 'bi-graph-down', 'Depreciación pendiente',
                                   f'Falta calcular la depreciación de {siguiente[4:]}/{siguiente[:4]}.',
                                   reverse('activos:depreciacion'), 1))
    if 'contabilidad' in mods and puede(user, 'contabilidad.periodos'):
        from contabilidad.models import PeriodoContable
        pendientes = list(PeriodoContable.objects.filter(pendiente=True, cerrado=False, periodo__lte=mes_anterior)
                          .values_list('periodo', flat=True)[:6])
        if pendientes:
            alertas.append(_alerta('info', 'bi-arrow-repeat', 'Periodos con cambios sin centralizar',
                                   'Centralice: ' + ', '.join(f'{p[4:]}/{p[:4]}' for p in pendientes) + '.',
                                   reverse('contabilidad:periodos'), len(pendientes)))
    orden = {'danger': 0, 'warning': 1, 'info': 2}
    return sorted(alertas, key=lambda a: orden[a['nivel']])


def de_usuario(user, refrescar=False):
    clave = f'alertas:{user.pk}'
    datos = None if refrescar else cache.get(clave)
    if datos is None:
        datos = calcular(user)
        cache.set(clave, datos, SEGUNDOS_CACHE)
    return datos


@login_required
def resumen_json(request):
    """Para la campana de la barra superior (se pide en segundo plano)."""
    alertas = de_usuario(request.user)
    return JsonResponse({'total': len(alertas), 'urgentes': sum(1 for a in alertas if a['nivel'] == 'danger'),
                         'alertas': [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in a.items()}
                                     for a in alertas[:8]]})


@login_required
def pagina(request):
    return render(request, 'core/alertas.html', {'alertas': de_usuario(request.user, refrescar=True)})
