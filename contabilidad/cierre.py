"""Cierre guiado del mes, conciliación entre el libro NIIF y el tributario, y consolidación de las empresas del grupo."""
from decimal import Decimal

from django.db.models import Q, Sum
from django.urls import reverse

from core.models import r2

from . import reportes
from .centralizar import _rango
from .models import Asiento, PeriodoContable, usar_norma

D0 = Decimal('0')


# ---------------------------------------------------------------- cierre guiado
def pasos(periodo):
    """Lista de verificación del cierre del mes: [{titulo, estado (ok|pendiente|aviso|na), detalle, url}]."""
    from activos.models import ActivoFijo, ProcesoDepreciacion
    from core.models import TipoCambio
    from finanzas.models import Cuenta, Movimiento
    from inventario.models import CierreKardex
    from planillas.models import Planilla, Trabajador
    desde, hasta = _rango(periodo)
    mes = f'{periodo[:4]}-{periodo[4:]}'
    p = PeriodoContable.objects.filter(periodo=periodo).first()
    salida = []

    def paso(titulo, estado, detalle, url=''):
        salida.append({'titulo': titulo, 'estado': estado, 'detalle': detalle, 'url': url})

    usd = Cuenta.objects.filter(moneda='USD', activo=True).exists()
    tc = TipoCambio.objects.filter(fecha__lte=hasta, fecha__gte=desde).order_by('-fecha').first()
    if not usd:
        paso('Tipo de cambio de cierre', 'na', 'No hay cajas ni bancos en dólares.')
    elif tc and tc.fecha == hasta:
        paso('Tipo de cambio de cierre', 'ok', f'{hasta:%d/%m/%Y}: compra {tc.compra}, venta {tc.venta}.')
    else:
        paso('Tipo de cambio de cierre', 'pendiente', f'Registre el tipo de cambio del {hasta:%d/%m/%Y} (ajusta los '
             'saldos en dólares).', reverse('tipos_cambio'))
    sin_conciliar = Movimiento.objects.filter(fecha__range=[desde, hasta], conciliado=False,
                                              cuenta__tipo='BANCO').count()
    paso('Conciliación bancaria', 'ok' if not sin_conciliar else 'aviso',
         'Todos los movimientos de bancos del mes están conciliados.' if not sin_conciliar else
         f'{sin_conciliar} movimiento(s) de bancos sin conciliar con el estado de cuenta.',
         reverse('finanzas:conciliacion'))
    kardex = CierreKardex.objects.filter(periodo=periodo, estado='CERRADO').exists()
    paso('Cierre del kardex', 'ok' if kardex else 'pendiente',
         'El inventario del mes está cerrado y valorizado.' if kardex else
         'Cierre el kardex del mes para que nadie mueva el inventario con fecha pasada.',
         reverse('inventario:cierres'))
    activos = ActivoFijo.objects.filter(estado='ACTIVO', fecha_alta__lte=hasta).exists()
    if not activos:
        paso('Depreciación de activos fijos', 'na', 'No hay activos fijos en uso.')
    else:
        dep = ProcesoDepreciacion.objects.filter(periodo=periodo).first()
        paso('Depreciación de activos fijos', 'ok' if dep else 'pendiente',
             f'Calculada: S/ {dep.total:,.2f}.' if dep else 'Calcule la depreciación del mes.',
             reverse('activos:depreciacion'))
    con_personal = any(t.activo_en(desde, hasta) for t in Trabajador.objects.exclude(fecha_ingreso=None))
    if not con_personal:
        paso('Planilla del mes', 'na', 'No hay trabajadores con vínculo en el mes.')
    else:
        pl = Planilla.objects.filter(periodo=periodo, tipo='MENSUAL').first()
        estado = 'ok' if pl and pl.estado in ('CERRADA', 'PAGADA') else 'pendiente'
        paso('Planilla del mes', estado, f'Planilla {pl.get_estado_display().lower()}.' if pl else
             'Genere, calcule y cierre la planilla del mes.',
             reverse('planillas:detalle', args=[pl.pk]) if pl else reverse('planillas:nueva'))
        prov = bool(p and p.provisiones)
        paso('Provisiones de beneficios sociales', 'ok' if prov else 'pendiente',
             'Gratificaciones, CTS y vacaciones provisionadas al centralizar.' if prov else
             'Active la provisión mensual de gratificaciones, CTS y vacaciones (botón abajo).', '')
    centralizado = bool(p and p.fecha_centralizacion and not p.pendiente)
    paso('Centralización contable', 'ok' if centralizado else 'pendiente',
         f'Centralizado el {p.fecha_centralizacion:%d/%m/%Y %H:%M}.' if centralizado else
         'Hay cambios sin contabilizar: centralice el periodo.', '')
    filas, tot = reportes.balance_comprobacion(periodo, periodo)
    cuadra = tot['d'] == tot['h']
    paso('Balance de comprobación cuadrado', 'ok' if cuadra else 'pendiente',
         f'Debe = haber = S/ {tot["d"]:,.2f}.' if cuadra else
         f'No cuadra: debe {tot["d"]:,.2f} / haber {tot["h"]:,.2f}.',
         f'{reverse("contabilidad:balance")}?desde={mes}&hasta={mes}')
    cerrado = bool(p and p.cerrado)
    paso('Cierre del periodo contable', 'ok' if cerrado else 'pendiente',
         'Periodo cerrado: ya no se registran documentos con esta fecha.' if cerrado else
         'Cuando todo esté conforme, cierre el periodo.', '')
    return salida, p


# ---------------------------------------------------------------- conciliación NIIF - tributario
def conciliacion_normas(desde, hasta):
    """Cuentas cuyo saldo difiere entre el libro NIIF y el tributario, y la diferencia en el resultado (base de las
    adiciones y deducciones de la declaración jurada anual)."""
    with usar_norma('NIIF'):
        niif = reportes.sumas_por_cuenta(desde, hasta)
    with usar_norma('TRIBUTARIO'):
        trib = reportes.sumas_por_cuenta(desde, hasta)
    from .models import CuentaContable
    nombres = dict(CuentaContable.objects.filter(codigo__in=set(niif) | set(trib)).values_list('codigo', 'nombre'))
    filas = []
    for codigo in sorted(set(niif) | set(trib)):
        dn, hn = niif.get(codigo, (D0, D0))
        dt, ht = trib.get(codigo, (D0, D0))
        sn, st = dn - hn, dt - ht
        if sn != st:
            filas.append({'codigo': codigo, 'nombre': nombres.get(codigo, ''), 'niif': sn, 'trib': st,
                          'diferencia': st - sn})
    res_niif, res_trib = reportes.resultado_ejercicio(niif), reportes.resultado_ejercicio(trib)
    asientos = Asiento.objects.filter(periodo__gte=desde, periodo__lte=hasta).exclude(norma='AMBOS') \
        .order_by('fecha', 'numero')
    return {'filas': filas, 'res_niif': res_niif, 'res_trib': res_trib, 'dif_resultado': res_trib - res_niif,
            'asientos': asientos}


# ---------------------------------------------------------------- consolidación del grupo
def _filtro_intercompania(rucs):
    return (Q(asiento__venta__tercero__numero_doc__in=rucs) | Q(asiento__compra__tercero__numero_doc__in=rucs) |
            Q(asiento__movimiento__tercero__numero_doc__in=rucs))


def _sumas(qs):
    return {f['cuenta__codigo']: (r2(f['d']), r2(f['h']))
            for f in qs.values('cuenta__codigo').annotate(d=Sum('debe'), h=Sum('haber'))}


def _sumar(destino, origen, signo=1):
    for codigo, (d, h) in origen.items():
        ad, ah = destino.get(codigo, (D0, D0))
        destino[codigo] = (ad + d * signo, ah + h * signo)


def consolidar(hasta):
    """Suma los libros de todas las empresas del grupo y elimina las operaciones entre ellas: los asientos de
    ventas, compras, cobros y pagos cuyo cliente o proveedor es otra empresa del grupo (por su RUC).
    Devuelve las empresas, el estado de situación y el de resultados consolidados y el detalle de eliminaciones."""
    from core.models import Empresa
    from erp.empresas import activar, empresas, restaurar
    from .models import AsientoLinea
    desde_ej = f'{hasta[:4]}01'
    previo = f'{int(desde_ej) - 1:06d}'
    datos = []
    for alias, nombre in empresas().items():
        token = activar(alias)
        try:
            e = Empresa.objects.first()
            datos.append({'alias': alias, 'nombre': e.razon_social if e else nombre, 'ruc': e.ruc if e else ''})
        finally:
            restaurar(token)
    rucs = [d['ruc'] for d in datos if d['ruc']]
    acumulado, ejercicio, anterior = {}, {}, {}
    for d in datos:
        token = activar(d['alias'])
        try:
            otros = [r for r in rucs if r != d['ruc']]
            base = AsientoLinea.objects.all()
            rangos = {'acumulado': base.filter(asiento__periodo__lte=hasta),
                      'ejercicio': base.filter(asiento__periodo__gte=desde_ej, asiento__periodo__lte=hasta),
                      'anterior': base.filter(asiento__periodo__lte=previo)}
            propios = {k: _sumas(qs) for k, qs in rangos.items()}
            inter = {k: _sumas(qs.filter(_filtro_intercompania(otros))) if otros else {} for k, qs in rangos.items()}
        finally:
            restaurar(token)
        for k, dest in (('acumulado', acumulado), ('ejercicio', ejercicio), ('anterior', anterior)):
            _sumar(dest, propios[k])
            _sumar(dest, inter[k], -1)
        d['resultado'] = reportes.resultado_ejercicio(propios['ejercicio'])
        d['eliminado'] = reportes.resultado_ejercicio(inter['ejercicio'])
        d['ventas_grupo'] = -reportes._neto(inter['ejercicio'], ('70',))
        d['cxc_grupo'] = reportes._neto(inter['acumulado'], ('12', '13'))
        d['cxp_grupo'] = -reportes._neto(inter['acumulado'], ('42', '43'))
    situacion = reportes.situacion_financiera(hasta, desde_ej, saldos=acumulado, sumas_ejercicio=ejercicio,
                                              anterior=anterior)
    lineas, neta = reportes.estado_resultados(desde_ej, hasta, sumas=ejercicio)
    return {'empresas': datos, 'situacion': situacion, 'resultados': lineas, 'neta': neta,
            'sin_ruc': [d['nombre'] for d in datos if not d['ruc']]}
