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
        if puede(user, 'portal_proveedores.aprobar'):
            from proveedores.models import FacturaProveedor
            n = FacturaProveedor.objects.filter(estado='ENVIADA').count()
            if n:
                alertas.append(_alerta('info', 'bi-inbox', 'Facturas de proveedores por revisar',
                                       f'{n} factura(s) cargadas en el portal esperan su revisión.',
                                       reverse('compras:portal_facturas'), n))
            from compras.models import Compra
            n = Compra.objects.filter(pago_bloqueado=True, estado='REGISTRADO').count()
            if n:
                alertas.append(_alerta('danger', 'bi-lock', 'Facturas con pago bloqueado',
                                       f'{n} factura(s) con precio fuera de la tolerancia: Finanzas no las paga '
                                       f'hasta que Compras libere el pago.',
                                       reverse('compras:lista') + '?pago_bloqueado=1', n))
    if 'compras' in mods:  # líneas por pedir con el plazo objetivo vencido: al comprador y a su jefe (COM-10)
        from compras.compradores import vencidas
        propias, del_equipo = vencidas(usuario=user), vencidas(jefe=user)
        if propias:
            alertas.append(_alerta('danger', 'bi-alarm', 'Líneas por pedir vencidas',
                                   f'{len(propias)} línea(s) de solicitudes asignadas a usted pasaron su plazo objetivo.',
                                   reverse('compras:solped_bandeja') + '?comprador=mias', len(propias)))
        otras = [l for l in del_equipo if l.comprador_id != user.pk]
        if otras:
            alertas.append(_alerta('warning', 'bi-people', 'Líneas vencidas de sus compradores',
                                   f'{len(otras)} línea(s) de sus grupos de compras pasaron su plazo objetivo.',
                                   reverse('compras:desempeno'), len(otras)))
    if 'inventario' in mods or 'compras' in mods:
        from .models import Producto
        from django.db.models import Q
        reponer = [p for p in Producto.objects.filter(Q(punto_reorden__gt=0) | Q(stock_minimo__gt=0), activo=True,
                                                      tipo='BIEN') if p.requiere_reposicion]
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
        from inventario.fisico import DIAS_ABIERTO, abiertos_antiguos
        viejos = list(abiertos_antiguos()[:20])
        if viejos:  # el stock se sigue moviendo: las diferencias de un conteo viejo son falsas (INV-24)
            alertas.append(_alerta('warning', 'bi-clipboard2-x', 'Inventarios físicos abiertos hace días',
                                   f'{len(viejos)} conteo(s) abiertos hace más de {DIAS_ABIERTO} días: '
                                   + ', '.join(f'{d.numero} ({d.almacen})' for d in viejos[:3]) +
                                   '. Contabilícelos o anúlelos.', reverse('inventario:fisico_lista'), len(viejos)))
        n = RequerimientoInterno.objects.filter(estado__in=['APROBADO', 'PARCIAL']).count()
        if n:
            alertas.append(_alerta('warning', 'bi-box-arrow-up', 'Requerimientos por atender',
                                   f'{n} requerimiento(s) aprobados esperan la entrega del almacén.',
                                   reverse('requerimientos:lista') + '?estado=APROBADO', n))
    if mods & {'manufactura', 'planificacion'}:
        from produccion.servicios import requerimientos
        faltan = [f for f in requerimientos() if f['faltante'] > 0]
        if faltan:
            alertas.append(_alerta('warning', 'bi-basket', 'Insumos insuficientes para producir',
                                   f'{len(faltan)} insumo(s) no alcanzan para las órdenes de producción abiertas.',
                                   reverse('manufactura:requerimiento'), len(faltan)))
    if mods & {'logistica', 'manufactura', 'mantenimiento'}:  # SOAT, revisión técnica, MTC, licencias (FLO-04)
        from flota.servicios import por_vencer as docs_por_vencer
        docs, licencias = docs_por_vencer()
        if docs or licencias:
            vencidos = sum(1 for d in docs if d.vencido) + sum(1 for c in licencias if c.licencia_vencimiento < hoy)
            alertas.append(_alerta('danger' if vencidos else 'warning', 'bi-calendar-x',
                                   'Documentos de flota por vencer',
                                   f'{len(docs) + len(licencias)} documento(s) de vehículos, equipos o conductores '
                                   f'vencen en 30 días' + (f' ({vencidos} ya vencidos).' if vencidos else '.'),
                                   reverse('flota:documentos'), len(docs) + len(licencias)))
        from flota.models import CargaCombustible
        from flota.operacion import prestamos_vencidos, ultimos_dias
        vencidos = prestamos_vencidos()
        if vencidos:
            alertas.append(_alerta('warning', 'bi-box-arrow-up-right', 'Equipos prestados sin devolver',
                                   f'{len(vencidos)} equipo(s) en comodato pasaron su fecha de devolución: '
                                   + ', '.join(f'{p.equipo.codigo} ({p.cliente.nombre})' for p in vencidos[:3]),
                                   reverse('flota:prestamos'), len(vencidos)))
        n = CargaCombustible.objects.filter(desvio=True, fecha__gte=ultimos_dias(30)).count()
        if n:
            alertas.append(_alerta('warning', 'bi-fuel-pump', 'Rendimiento de combustible fuera de lo esperado',
                                   f'{n} carga(s) de los últimos 30 días con rendimiento fuera de ±15 % del esperado.',
                                   reverse('flota:combustible') + '?desvio=1', n))
    if 'rendiciones' in mods:
        alertas += _alertas_rendiciones(user)
    if 'planillas' in mods:  # contratos y documentos del legajo (EMO, antecedentes) que vencen en 30 días
        from planillas.organizacion import por_vencer
        contratos, documentos = por_vencer()
        if contratos:
            alertas.append(_alerta('warning', 'bi-file-earmark-person', 'Contratos por vencer',
                                   f'{len(contratos)} contrato(s) vencen en 30 días o ya vencieron sin renovación: '
                                   + ', '.join(c.trabajador.nombre_completo for c in contratos[:4]),
                                   reverse('planillas:contratos'), len(contratos)))
        if documentos:
            alertas.append(_alerta('info', 'bi-folder2-open', 'Documentos del legajo por vencer',
                                   f'{len(documentos)} documento(s) (EMO, antecedentes…) vencen en 30 días.',
                                   reverse('planillas:contratos'), len(documentos)))
        # trabajadores activos sin centro de costo: la planilla no se cierra hasta asignarlo (PLA-07)
        from planillas.models import Trabajador
        n = (Trabajador.objects.filter(fecha_ingreso__isnull=False, centro_costo__isnull=True)
             .exclude(fecha_cese__lt=hoy).count())
        if n:
            alertas.append(_alerta('danger', 'bi-diagram-3', 'Trabajadores sin centro de costo',
                                   f'{n} trabajador(es) activos sin centro de costo: la planilla no se cierra hasta '
                                   f'asignarlo (use la asignación masiva).',
                                   reverse('planillas:trabajadores') + '?estado=sin_centro', n))
    if puede(user, 'tesoreria.datos_bancarios'):  # SEG-10: doble control de las cuentas bancarias de terceros
        from .models import Tercero
        pendientes = list(Tercero.objects.exclude(cambio_bancario={}).exclude(cambio_bancario_por=user)[:20])
        if pendientes:
            alertas.append(_alerta('danger', 'bi-shield-lock', 'Cuentas bancarias por aprobar',
                                   f'{len(pendientes)} cambio(s) de cuenta bancaria esperan otra aprobación: '
                                   + ', '.join(t.nombre[:30] for t in pendientes[:4]),
                                   reverse('tercero_editar', args=[pendientes[0].pk]), len(pendientes)))
    if 'obras' in mods:
        alertas += _alertas_obras(hoy)
        alertas += _alertas_servicios(user, hoy)
    if 'crm' in mods or mods & {'obras', 'manufactura', 'logistica'}:
        alertas += _alertas_crm(user, mods, hoy)
    if 'sst' in mods or 'calidad' in mods:
        alertas += _alertas_sst_calidad(user, mods)
    from .models import Empresa
    if user.is_superuser and not Empresa.objects.values_list('giros', flat=True).first():
        alertas.append(_alerta('info', 'bi-sliders', 'Configure el giro y el régimen de la empresa',
                               'Define la plantilla contable, el costeo, los módulos que se usan y los libros '
                               'obligatorios (Ajustes › Giro y régimen).', reverse('empresa_giro'), 1))
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
    if user.is_superuser:
        alertas += _alertas_seguridad()
    orden = {'danger': 0, 'warning': 1, 'info': 2}
    return sorted(alertas, key=lambda a: orden[a['nivel']])


def _alertas_servicios(user, hoy):
    """Servicios por proyecto: partes de horas por aprobar (jefe de proyecto), cartas fianza por vencer y entregables
    atrasados."""
    from obras.models import EntregableProyecto, GarantiaContrato, ParteHoras
    salida = []
    partes = ParteHoras.objects.filter(estado='ENVIADO')
    if not (user.is_superuser or puede(user, 'obras.aprobar')):
        partes = partes.filter(obra__residente=user)
    if partes.exists():
        salida.append(_alerta('warning', 'bi-clock-history', 'Partes de horas por aprobar',
                              f'{partes.count()} parte(s) de horas esperan su aprobación.',
                              reverse('obras:partes_aprobar'), partes.count()))
    fianzas = GarantiaContrato.objects.filter(estado='VIGENTE', vencimiento__lte=hoy + timedelta(days=15)).count()
    if fianzas:
        salida.append(_alerta('danger', 'bi-shield-exclamation', 'Cartas fianza por vencer',
                              f'{fianzas} garantía(s) del contrato vencen en 15 días o menos: renuévelas.',
                              reverse('obras:garantias'), fianzas))
    atrasados = EntregableProyecto.objects.filter(estado__in=['PENDIENTE', 'OBSERVADO'],
                                                  fecha_comprometida__lt=hoy).count()
    if atrasados:
        salida.append(_alerta('warning', 'bi-calendar-x', 'Entregables atrasados',
                              f'{atrasados} entregable(s) pasaron su fecha comprometida (corre la penalidad).',
                              reverse('obras:lista'), atrasados))
    return salida


def _alertas_crm(user, mods, hoy):
    """CRM (v1.81): actividades del día y vencidas, oportunidades sin siguiente paso o sin movimiento, cotizaciones
    vencidas, clientes sin compras, contratos por renovar y casos de postventa vencidos; a operaciones, lo ganado."""
    from django.db.models import Q
    from ventas import crm_servicios as srv
    from ventas.models import ActividadCRM, CasoPostventa, ContratoServicio, Oportunidad
    salida = []
    if 'crm' in mods:
        mias = ActividadCRM.objects.filter(hecha=False, recordatorio=True, fecha__lte=hoy).filter(
            Q(responsable=user) | Q(responsable__isnull=True, usuario=user))
        vencidas, de_hoy = mias.filter(fecha__lt=hoy).count(), mias.filter(fecha=hoy).count()
        if vencidas:
            salida.append(_alerta('danger', 'bi-alarm', 'Actividades del CRM vencidas',
                                  f'{vencidas} actividad(es) de seguimiento vencidas.', reverse('crm:actividades'),
                                  vencidas))
        if de_hoy:
            salida.append(_alerta('warning', 'bi-calendar-check', 'Actividades del CRM para hoy',
                                  f'{de_hoy} actividad(es) agendadas para hoy.', reverse('crm:actividades'), de_hoy))
        abiertas = srv.visibles(user, Oportunidad.objects.filter(etapa__in=Oportunidad.ABIERTAS))
        if not srv.ve_todo(user):
            abiertas = abiertas.filter(responsable=user)
        sin_paso = abiertas.exclude(actividades__hecha=False).count()
        if sin_paso:
            salida.append(_alerta('warning', 'bi-signpost', 'Oportunidades sin siguiente paso',
                                  f'{sin_paso} oportunidad(es) abiertas sin próxima actividad.',
                                  reverse('crm:actividades'), sin_paso))
        limite = timezone.now() - timedelta(days=int(srv.parametro('CRM_DIAS_ESTANCADA')))
        estancadas = abiertas.filter(actualizado__lt=limite).count()
        if estancadas:
            salida.append(_alerta('warning', 'bi-hourglass-bottom', 'Oportunidades sin movimiento',
                                  f'{estancadas} oportunidad(es) sin movimiento hace más de '
                                  f'{int(srv.parametro("CRM_DIAS_ESTANCADA"))} días.', reverse('crm:lista') + '?mias=1',
                                  estancadas))
        ids = None if srv.ve_todo(user) else {user.pk}
        cot = srv.cotizaciones_por_vencer(ids)
        if cot:
            salida.append(_alerta('info', 'bi-file-earmark-x', 'Cotizaciones vencidas sin respuesta',
                                  f'{len(cot)} cotización(es) de oportunidades abiertas pasaron su validez.',
                                  reverse('crm:lista') + '?mias=1', len(cot)))
        contratos = [c for c in ContratoServicio.objects.filter(estado='VIGENTE', oportunidad_renovacion__isnull=True)
                     if c.por_renovar and (ids is None or c.responsable_id in ids)]
        if contratos:
            salida.append(_alerta('warning', 'bi-arrow-repeat', 'Contratos por renovar',
                                  f'{len(contratos)} contrato(s) terminan pronto: cree la oportunidad de renovación.',
                                  reverse('crm:contratos'), len(contratos)))
        casos = CasoPostventa.objects.exclude(estado='CERRADO').filter(fecha_limite__lt=hoy)
        if ids is not None:
            casos = casos.filter(responsable=user)
        if casos.exists():
            salida.append(_alerta('danger', 'bi-life-preserver', 'Casos de postventa vencidos',
                                  f'{casos.count()} caso(s) pasaron su plazo de respuesta.', reverse('crm:casos'),
                                  casos.count()))
        if int(srv.parametro('CRM_DIAS_CLIENTE_INACTIVO')) > 0 and ids is not None:
            inactivos = srv.clientes_inactivos(usuario_ids=ids)
            if inactivos:
                salida.append(_alerta('info', 'bi-person-dash', 'Clientes de su cartera sin compras',
                                      f'{len(inactivos)} cliente(s) no compran hace más de '
                                      f'{int(srv.parametro("CRM_DIAS_CLIENTE_INACTIVO"))} días.',
                                      reverse('crm:inactivos'), len(inactivos)))
    if mods & {'obras', 'manufactura', 'logistica'}:  # CRM-21: operaciones se entera de lo ganado
        ganadas = Oportunidad.objects.filter(etapa='GANADA', cerrada_en__gte=hoy - timedelta(days=7)).count()
        if ganadas:
            salida.append(_alerta('info', 'bi-trophy', 'Oportunidades ganadas esta semana',
                                  f'{ganadas} oportunidad(es) ganadas: revise su inicio probable para planificar.',
                                  reverse('crm:operaciones'), ganadas))
    return salida


def _alertas_obras(hoy):
    """Licitaciones: hitos del cronograma en 3 días y requisitos del postor (RNP, garantía…) vencidos o por vencer.
    Proyectos inmobiliarios: cuotas vencidas y separaciones por vencer."""
    from obras import inmobiliario, licitaciones
    from obras.models import RequisitoPostor
    salida = []
    hitos = list(licitaciones.proximos_hitos())
    if hitos:
        vencidos = [h for h in hitos if h.fecha < hoy]
        salida.append(_alerta('danger' if vencidos else 'warning', 'bi-calendar-event', 'Plazos de licitaciones',
                              f'{len(hitos)} hito(s) en {licitaciones.DIAS_AVISO} días o vencidos sin marcar: ' + ', '.join(
                                  f'{h.get_tipo_display()} {h.licitacion.obra.nombre[:30]} ({h.fecha:%d/%m})'
                                  for h in hitos[:3]), reverse('obras:licitaciones'), len(hitos)))
    requisitos = list(RequisitoPostor.objects.filter(
        licitacion__resultado='EN_CURSO', vence__isnull=False,
        vence__lte=hoy + timedelta(days=licitaciones.DIAS_AVISO)).select_related('licitacion__obra')[:20])
    if requisitos:
        salida.append(_alerta('danger', 'bi-shield-x', 'Requisitos del postor vencidos o por vencer',
                              ', '.join(f'{r.get_tipo_display()} ({r.vence:%d/%m}) en {r.licitacion.obra.nombre[:30]}'
                                        for r in requisitos[:3]), reverse('obras:licitaciones'), len(requisitos)))
    cuotas = list(inmobiliario.cuotas_vencidas()[:200])
    if cuotas:
        salida.append(_alerta('danger', 'bi-house-exclamation', 'Cuotas inmobiliarias vencidas',
                              f'{len(cuotas)} cuota(s) vencidas por S/ {sum(c.monto - c.pagado for c in cuotas):,.2f}.',
                              reverse('obras:inm_cobranza'), len(cuotas)))
    fichas = inmobiliario.fichas_incompletas()
    if fichas:
        salida.append(_alerta('warning', 'bi-person-vcard', 'Fichas del comprador incompletas (lavado de activos)',
                              ', '.join(f'{v.numero} {v.cliente.nombre[:30]}' for v in fichas[:3]),
                              reverse('obras:inm_reportes') + '?reporte=fichas', len(fichas)))
    separaciones = list(inmobiliario.separaciones_por_vencer())
    if separaciones:
        salida.append(_alerta('warning', 'bi-house-dash', 'Separaciones por vencer',
                              ', '.join(f'{v.numero} {v.cliente.nombre[:30]} ({v.vence_separacion:%d/%m})'
                                        for v in separaciones[:3]), reverse('obras:inm_cobranza'), len(separaciones)))
    # v1.86 (HR-07 y HR-14): costo de partidas sobre lo esperado y margen proyectado bajo la meta
    from obras.gestion import alertas_proyectos
    costo, margen = alertas_proyectos()
    if costo:
        salida.append(_alerta('warning', 'bi-cash-stack', 'Partidas con costo sobre lo esperado',
                              ', '.join(f'{o.codigo}: {len(a)} partida(s)' for o, a in costo[:3]),
                              reverse('obras:proyeccion', args=[costo[0][0].pk]) if len(costo) == 1
                              else reverse('obras:cartera'), sum(len(a) for _, a in costo)))
    if margen:
        salida.append(_alerta('danger', 'bi-graph-down-arrow', 'Margen proyectado bajo la meta',
                              ', '.join(f'{o.codigo} {m} % (meta {meta:g} %)' for o, m, meta in margen[:3]),
                              reverse('obras:proyeccion', args=[margen[0][0].pk]) if len(margen) == 1
                              else reverse('obras:cartera'), len(margen)))
    return salida


def _alertas_sst_calidad(user, mods):
    """SST: accidentes por notificar y vencimientos (EMO, SCTR, equipos, EPP, monitoreos). Registros por validar
    (para quien valida) y acciones correctivas vencidas."""
    from calidad.servicios import acciones_vencidas

    from .validacion import pendientes
    salida = []
    if 'sst' in mods:
        from sst.servicios import vencimientos
        vence = vencimientos(15)
        notificar = vence.pop('Accidentes por notificar', [])
        vence.pop('Trabajadores sin examen médico', None)
        if notificar:
            salida.append(_alerta('danger', 'bi-exclamation-octagon', 'Accidentes por notificar a la autoridad',
                                  ', '.join(f'{d} (hasta {f:%d/%m})' for _, f, d in notificar[:3]),
                                  reverse('sst:registros') + '?tipo=eventos', len(notificar)))
        total = sum(len(v) for v in vence.values())
        if total:
            salida.append(_alerta('warning', 'bi-heart-pulse', 'Vencimientos de SST en 15 días',
                                  ', '.join(f'{k.lower()} ({len(v)})' for k, v in vence.items() if v),
                                  reverse('sst:tablero'), total))
    for modulo in ('sst', 'calidad'):
        if modulo in mods and puede(user, f'{modulo}.validar'):
            filas = [(m, pv) for m, pv, _ in pendientes() if getattr(m, 'MODULO', '') == modulo and pv]
            if filas:
                salida.append(_alerta('info', 'bi-patch-check', 'Registros por validar',
                                      ', '.join(f'{m._meta.verbose_name_plural} ({n})' for m, n in filas),
                                      reverse('sst:tablero' if modulo == 'sst' else 'calidad:tablero'),
                                      sum(n for _, n in filas)))
    vencidas = [a for a in acciones_vencidas() if a.modulo in mods]
    if vencidas:
        salida.append(_alerta('warning', 'bi-check2-square', 'Acciones correctivas vencidas',
                              ', '.join(a.descripcion[:40] for a in vencidas[:3]),
                              reverse('calidad:acciones') + '?estado=abiertas' if 'calidad' in mods else
                              reverse('sst:tablero'), len(vencidas)))
    return salida


def _alertas_seguridad():
    """Para los administradores: controles de seguridad apagados antes de operar en producción."""
    from .models import Empresa
    e = Empresa.actual()
    salida = []
    if not e.segregacion_estricta:
        salida.append(_alerta('warning', 'bi-shield-exclamation', 'Segregación de funciones no estricta',
                              'Un mismo usuario puede registrar y aprobar su propio documento. Actívela en Ajustes › '
                              'Empresa antes de operar en producción.', reverse('segregacion'), 1))
    if not e.exigir_doble_factor:
        salida.append(_alerta('info', 'bi-phone', 'Verificación en dos pasos opcional',
                              'Conviene exigirla al menos a administradores y aprobadores (Ajustes › Empresa).',
                              reverse('empresa'), 1))
    return salida


def _alertas_rendiciones(user):
    from finanzas import rendiciones as rd
    from finanzas.models import Rendicion
    salida = []
    por_aprobar = [r for r in Rendicion.objects.filter(estado__in=Rendicion.POR_APROBAR) if rd.puede_aprobar(user, r)]
    if por_aprobar:
        salida.append(_alerta('warning', 'bi-check2-circle', 'Rendiciones por aprobar',
                              f'{len(por_aprobar)} rendición(es) de gastos esperan su aprobación.',
                              reverse('rendiciones:lista') + '?vista=aprobar', len(por_aprobar)))
    observadas = Rendicion.objects.filter(rendidor=user, estado='OBSERVADA').count()
    if observadas:
        salida.append(_alerta('danger', 'bi-exclamation-octagon', 'Rendiciones observadas',
                              f'{observadas} rendición(es) suyas tienen observaciones por corregir.',
                              reverse('rendiciones:lista') + '?vista=mias&estado=OBSERVADA', observadas))
    if puede(user, 'rendiciones.contabilizar'):
        n = Rendicion.objects.filter(estado='APROBADA').count()
        if n:
            salida.append(_alerta('info', 'bi-journal-check', 'Rendiciones por contabilizar',
                                  f'{n} rendición(es) aprobadas esperan la revisión contable.',
                                  reverse('rendiciones:lista') + '?vista=contabilizar', n))
    corte = rd.proximo_corte()
    if corte is not None:
        from finanzas.models import ConfigRendicion
        dias = (corte - timezone.localdate()).days
        mias = Rendicion.objects.filter(rendidor=user, estado__in=Rendicion.EDITABLES).count()
        if dias <= ConfigRendicion.actual().dias_aviso_corte and (mias or por_aprobar):
            salida.append(_alerta('warning', 'bi-calendar-x', f'Corte de rendiciones el {corte:%d/%m}',
                                  f'Envíe o apruebe antes del corte ({mias + len(por_aprobar)} pendiente(s)); lo '
                                  f'aprobado después se contabiliza el mes siguiente.',
                                  reverse('rendiciones:lista'), mias + len(por_aprobar)))
    if puede(user, 'rendiciones.contabilizar'):
        from finanzas.models import CategoriaCuentaHistorial
        cambios = CategoriaCuentaHistorial.objects.filter(
            fecha__gte=timezone.now() - timedelta(days=15)).count()
        if cambios:
            salida.append(_alerta('info', 'bi-clock-history', 'Cambios de imputación de categorías',
                                  f'{cambios} cambio(s) de cuenta contable en categorías de gasto (últimos 15 días).',
                                  reverse('rendiciones:configuracion'), cambios))
    t = rd.trabajador_de(user)
    vencidas = [e for e, _ in rd.vencidas() if t is not None and e.responsable_id == t.pk]
    if vencidas:
        salida.append(_alerta('danger', 'bi-hourglass-bottom', 'Entregas a rendir vencidas',
                              f'Tiene {len(vencidas)} entrega(s) fuera del plazo para rendir.',
                              reverse('rendiciones:nueva'), len(vencidas)))
    return salida


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
