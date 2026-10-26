"""Calidad a nivel farmacéutico (v1.84, CAL-01 a CAL-08): rastro de auditoría de los resultados, OOS, reapertura
con motivo y firma, segregación y firma electrónica en la liberación, revisión de QA antes de liberar,
especificaciones versionadas, registro de lote, desviaciones, control de cambios, quejas, retiros, revisión anual de
producto y calibración de instrumentos."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.firmas import ErrorFirma, firmar
from core.permisos import puede

from .models import (CalibracionEquipo, CambioResultado, CertificadoAnalisis, ControlCambio, Desviacion,
                     EspecificacionCalidad, EstudioEstabilidad, InspeccionCalidad, InvestigacionOOS, OrdenProduccion,
                     ParametroCalidad, PasoRegistroLote, PuntoEstabilidad, QuejaLote, RegistroLote, RetiroMercado)
from .planta import ErrorPlanta

DECIDIDAS = ('APROBADO', 'OBSERVADO', 'REPROCESO', 'RECHAZADO')


def _firma(usuario, clave, significado, obj, ip=''):
    try:
        return firmar(usuario, clave, significado, obj, ip)
    except ErrorFirma as exc:
        raise ErrorPlanta(str(exc)) from exc


def _texto(valor):
    return '' if valor is None else (f'{valor.normalize():f}' if isinstance(valor, Decimal) else str(valor))


# ---------------------------------------------------------------- instrumentos (CAL-08)
def estado_calibracion(equipo, fecha=None):
    """VIGENTE, VENCIDA, NO_CONFORME o SIN_CALIBRAR."""
    fecha = fecha or timezone.localdate()
    ultima = equipo.calibraciones.order_by('-fecha', '-id').first()
    if ultima is None:
        return 'SIN_CALIBRAR'
    if ultima.resultado == 'NO_CONFORME':
        return 'NO_CONFORME'
    if ultima.proxima and ultima.proxima < fecha:
        return 'VENCIDA'
    return 'VIGENTE'


def validar_instrumento(equipo, fecha=None):
    if equipo is None:
        return
    estado = estado_calibracion(equipo, fecha)
    if estado != 'VIGENTE':
        texto = {'SIN_CALIBRAR': 'no tiene calibración registrada', 'VENCIDA': 'tiene la calibración vencida',
                 'NO_CONFORME': 'tiene la última calibración no conforme'}[estado]
        raise ErrorPlanta(f'El instrumento {equipo} {texto}: no se acepta en la inspección.')


@transaction.atomic
def registrar_calibracion(cal, usuario):
    if cal.proxima <= cal.fecha:
        raise ErrorPlanta('La próxima calibración debe ser posterior a la fecha de calibración.')
    cal.registrado_por = usuario if usuario and usuario.is_authenticated else None
    cal.save()
    eq = cal.equipo
    eq.es_instrumento = True
    eq.proxima_calibracion = cal.proxima
    eq.save(update_fields=['es_instrumento', 'proxima_calibracion'])
    return cal


def instrumentos_por_vencer(dias=15):
    from .models import Equipo
    hasta = timezone.localdate() + timedelta(days=dias)
    return Equipo.objects.filter(es_instrumento=True, activo=True).filter(
        Q(proxima_calibracion__lte=hasta) | Q(proxima_calibracion__isnull=True))


# ---------------------------------------------------------------- especificaciones (CAL-05)
def especificacion_vigente(producto):
    return producto.especificaciones.filter(estado='APROBADA').order_by('-version').first()


def _caracteristicas(producto):
    return [{'nombre': p.nombre, 'unidad': p.unidad, 'minimo': _texto(p.minimo), 'maximo': _texto(p.maximo),
             'especificacion': p.especificacion, 'metodo': p.metodo, 'referencia': p.referencia}
            for p in ParametroCalidad.objects.filter(producto=producto)]


@transaction.atomic
def nueva_version(producto, usuario, cambios='', **datos):
    """Nueva versión (por aprobar) con las características actuales del plan de calidad."""
    if producto.especificaciones.filter(estado='BORRADOR').exists():
        raise ErrorPlanta('Ya hay una versión por aprobar: apruébela o edítela.')
    ultima = producto.especificaciones.order_by('-version').first()
    if not ParametroCalidad.objects.filter(producto=producto).exists():
        raise ErrorPlanta('Defina primero las características del plan de calidad.')
    return EspecificacionCalidad.objects.create(
        producto=producto, version=(ultima.version + 1) if ultima else 1, caracteristicas=_caracteristicas(producto),
        cambios=cambios, referencia=datos.get('referencia') or (ultima.referencia if ultima else ''),
        muestreo=datos.get('muestreo') or (ultima.muestreo if ultima else ''),
        tamano_muestra=datos.get('tamano_muestra') or (ultima.tamano_muestra if ultima else 0),
        envases_muestreo=datos.get('envases_muestreo') or (ultima.envases_muestreo if ultima else 0))


@transaction.atomic
def aprobar_especificacion(esp, usuario, clave, ip=''):
    if esp.estado != 'BORRADOR':
        raise ErrorPlanta('La especificación ya está aprobada u obsoleta.')
    if not (usuario.is_superuser or puede(usuario, 'control_calidad.especificaciones')):
        raise ErrorPlanta('Aprueba las especificaciones QA (permiso «aprobar especificaciones»).')
    esp.caracteristicas = _caracteristicas(esp.producto)  # lo que se aprueba es el plan en este momento
    _firma(usuario, clave, f'Aprobada la especificación v{esp.version}', esp, ip)
    esp.producto.especificaciones.filter(estado='APROBADA').update(estado='OBSOLETA')
    esp.estado, esp.aprobado_por, esp.aprobado_en = 'APROBADA', usuario, timezone.now()
    esp.vigente_desde = esp.vigente_desde or timezone.localdate()
    esp.save()
    return esp


# ---------------------------------------------------------------- inspecciones (CAL-01, CAL-02, CAL-04)
def tipo_coherente(tipo, producto):
    """CAL-04: el tipo de inspección según la clase del producto."""
    permitidos = {
        'RECEPCION': ('MATERIA_PRIMA', 'MERCADERIA', 'SUMINISTRO', 'ENVASE', 'SEMIELABORADO', 'PRODUCTO_TERMINADO'),
        'PROCESO': ('SEMIELABORADO', 'PRODUCTO_TERMINADO'),
        'TERMINADO': ('PRODUCTO_TERMINADO', 'SEMIELABORADO'),
        'DEVOLUCION': ('PRODUCTO_TERMINADO', 'MERCADERIA', 'SEMIELABORADO'),
    }
    return producto.clase in permitidos.get(tipo, ())


def lote_de(insp):
    from core.models import Lote
    return Lote.objects.filter(producto=insp.producto, codigo=insp.lote).first() if insp.lote else None


def certificado_liberado(insp):
    return insp.certificados.filter(estado='APROBADO').exists()


@transaction.atomic
def registrar_resultados(insp, valores, resultado, observaciones, usuario, motivo=''):
    """Registra los valores con su rastro (CAL-01): el primer valor queda como registro inicial y cada corrección exige
    motivo; una inspección decidida no se cambia (se reabre con motivo y firma de QA). Un valor fuera de
    especificación abre una investigación OOS y la inspección queda «en investigación» (CAL-02)."""
    from core import parametros
    if insp.cuarentena_id:
        raise ErrorPlanta('La inspección ya envió la mercadería a cuarentena.')
    if insp.resultado in DECIDIDAS or certificado_liberado(insp):
        raise ErrorPlanta(f'La inspección ya se decidió ({insp.get_resultado_display().lower()}): QA debe reabrirla '
                          f'con su motivo para corregirla.')
    if resultado not in dict(InspeccionCalidad.RESULTADOS):
        raise ErrorPlanta('Resultado no válido.')
    validar_instrumento(insp.instrumento, insp.fecha)
    for r in insp.resultados.all():
        if r.pk not in valores:
            continue
        valor, texto, conforme = valores[r.pk]
        cambios = [(c, _texto(getattr(r, c)), _texto(v)) for c, v in (('valor', valor), ('texto', texto))
                   if _texto(getattr(r, c)) != _texto(v)]
        if not cambios and conforme == r.conforme:
            continue
        registrado = r.valor is not None or bool(r.texto)
        if registrado and cambios and len((motivo or '').strip()) < 5:
            raise ErrorPlanta(f'{r.caracteristica}: indique el motivo de la corrección (el valor registrado '
                              f'{_texto(r.valor) or r.texto} queda en el rastro de auditoría).')
        for campo, antes, ahora in cambios:
            CambioResultado.objects.create(resultado=r, campo=campo, valor_anterior=antes, valor_nuevo=ahora,
                                           motivo=(motivo.strip() if registrado else 'Registro inicial')[:250],
                                           usuario=usuario)
        r.valor, r.texto, r.conforme = valor, texto, conforme
        r.evaluar()
        r.save()
    fuera = [r for r in insp.resultados.all() if r.conforme is False]
    abiertas = []
    for r in fuera:
        if not r.oos.exists():
            abiertas.append(InvestigacionOOS.objects.create(inspeccion=insp, resultado=r,
                                                            valor_original=_texto(r.valor) or r.texto,
                                                            abierta_por=usuario))
    pendientes = insp.oos.filter(estado='ABIERTA')
    confirmados = insp.oos.filter(estado='CERRADA', conclusion='CONFIRMADO')
    if pendientes.exists() and resultado != 'RECHAZADO':  # rechazar sin esperar la investigación es lo conservador
        resultado = 'OOS'
    elif confirmados.exists() and resultado not in ('RECHAZADO', 'PENDIENTE'):
        raise ErrorPlanta('El OOS se confirmó: el lote solo puede rechazarse.')
    if resultado in ('APROBADO', 'OBSERVADO') and insp.resultados.filter(conforme=False).exists():
        raise ErrorPlanta('Hay características fuera de especificación sin investigación cerrada.')
    if resultado == 'OBSERVADO':
        if parametros.valor('CAL_PROHIBIR_OBSERVADO'):
            raise ErrorPlanta('La empresa no permite «aprobado con observaciones» (BPM): apruebe, rechace o abra una '
                              'desviación.')
        if len((observaciones or '').strip()) < 10:
            raise ErrorPlanta('Explique las observaciones (mínimo 10 caracteres).')
    insp.resultado, insp.observaciones, insp.inspector = resultado, observaciones, usuario
    insp.decidida_en = timezone.now() if resultado in DECIDIDAS else None
    insp.save()
    insp.oos_abiertas = abiertas
    return insp


@transaction.atomic
def reabrir(insp, usuario, motivo, clave, ip=''):
    """CAL-01: solo QA (permiso «reabrir») y con motivo; no si el lote ya se liberó con su certificado."""
    from core.auditoria import registrar
    if not (usuario.is_superuser or puede(usuario, 'control_calidad.reabrir')):
        raise ErrorPlanta('Reabre una inspección decidida QA (permiso «reabrir inspecciones»).')
    if certificado_liberado(insp):
        raise ErrorPlanta('El lote ya se liberó con su certificado: anúlelo primero (con su motivo).')
    if len((motivo or '').strip()) < 10:
        raise ErrorPlanta('Indique el motivo de la reapertura (mínimo 10 caracteres).')
    _firma(usuario, clave, 'Reapertura de la inspección', insp, ip)
    antes = insp.get_resultado_display()
    insp.resultado, insp.decidida_en = 'PENDIENTE', None
    insp.save(update_fields=['resultado', 'decidida_en'])
    registrar('MODIFICAR', insp, {'Resultado': [antes, 'Pendiente (reabierta)']}, motivo.strip())
    return insp


@transaction.atomic
def cerrar_oos(oos, datos, usuario, clave, ip=''):
    """CAL-02: QA cierra el OOS con su conclusión. Error de laboratorio: el reanálisis reemplaza el valor (queda en el
    rastro) y la inspección vuelve a decidirse; OOS confirmado: el lote se rechaza."""
    if oos.estado != 'ABIERTA':
        raise ErrorPlanta('La investigación ya está cerrada.')
    if not (usuario.is_superuser or puede(usuario, 'control_calidad.aprobar_oos')):
        raise ErrorPlanta('Cierra la investigación OOS QA (permiso «aprobar investigaciones OOS»).')
    for campo in ('fase', 'hipotesis', 'investigacion_proceso', 'reanalisis', 'causa_raiz', 'conclusion', 'capa'):
        if campo in datos:
            setattr(oos, campo, datos[campo])
    if oos.conclusion not in ('ERROR_LAB', 'CONFIRMADO'):
        raise ErrorPlanta('Indique la conclusión de la investigación.')
    if len((oos.causa_raiz or '').strip()) < 10:
        raise ErrorPlanta('Describa la causa raíz (mínimo 10 caracteres).')
    r = oos.resultado
    if oos.conclusion == 'ERROR_LAB':
        if len((oos.hipotesis or '').strip()) < 10:
            raise ErrorPlanta('Documente la revisión de laboratorio que demuestra el error (fase I).')
        if oos.reanalisis is None:
            raise ErrorPlanta('Registre el valor del reanálisis.')
        antes = _texto(r.valor)
        r.valor = oos.reanalisis
        r.evaluar()
        if r.conforme is False:
            raise ErrorPlanta('El reanálisis también está fuera de especificación: el OOS no se invalida.')
        CambioResultado.objects.create(resultado=r, campo='valor', valor_anterior=antes,
                                       valor_nuevo=_texto(oos.reanalisis), usuario=usuario,
                                       motivo=f'OOS {oos.numero}: error de laboratorio demostrado, reanálisis'[:250])
        r.save()
    _firma(usuario, clave, f'Cierre del OOS ({oos.get_conclusion_display()})', oos, ip)
    oos.estado, oos.cerrada_por, oos.cerrada_en = 'CERRADA', usuario, timezone.now()
    oos.save()
    insp = oos.inspeccion
    if not insp.oos.filter(estado='ABIERTA').exists():
        if insp.oos.filter(conclusion='CONFIRMADO').exists():
            insp.resultado, insp.decidida_en = 'RECHAZADO', timezone.now()
            lote = lote_de(insp)
            if lote is not None:
                lote.estado_calidad, lote.motivo_estado = 'RECHAZADO', f'OOS {oos.numero} confirmado'
                lote.save(update_fields=['estado_calidad', 'motivo_estado'])
        else:
            insp.resultado, insp.decidida_en = 'PENDIENTE', None
        insp.save(update_fields=['resultado', 'decidida_en'])
    return oos


# ---------------------------------------------------------------- liberación (CAL-02, CAL-03, CAL-06, CAL-07)
def orden_del_lote(lote):
    return (OrdenProduccion.objects.filter(Q(lote_producido=lote.codigo) | Q(numero=lote.codigo, lote_producido=''),
                                           producto=lote.producto).first())


def bloqueos_liberacion(lote, inspeccion=None, hoy=None):
    """Lista de revisión de QA antes de liberar: lo que impide liberar el lote."""
    hoy = hoy or timezone.localdate()
    bloqueos = []
    inspecciones = InspeccionCalidad.objects.filter(producto=lote.producto, lote=lote.codigo)
    if inspeccion is not None and inspeccion.resultado not in ('APROBADO', 'OBSERVADO'):
        bloqueos.append(f'La inspección {inspeccion.numero} no está aprobada ({inspeccion.get_resultado_display()}).')
    if InvestigacionOOS.objects.filter(inspeccion__in=inspecciones, estado='ABIERTA').exists():
        bloqueos.append('Hay investigaciones OOS abiertas del lote.')
    if inspeccion is not None and inspeccion.resultados.filter(conforme=False).exists():
        bloqueos.append('Hay resultados fuera de especificación: no se libera (abra o cierre su OOS).')
    orden = orden_del_lote(lote)
    abiertas = Desviacion.objects.exclude(estado='CERRADA').filter(Q(lote=lote) | Q(inspeccion__in=inspecciones) |
                                                                   (Q(orden=orden) if orden else Q(pk__in=[])))
    for d in abiertas:
        bloqueos.append(f'Desviación {d.numero} ({d.get_criticidad_display().lower()}) abierta.')
    for c in ControlCambio.objects.filter(productos=lote.producto, estado__in=['PROPUESTO', 'APROBADO']):
        bloqueos.append(f'Control de cambio {c.numero} abierto sobre el producto: {c.titulo}.')
    if orden is not None:
        reg = getattr(orden, 'registro_lote', None)
        if reg is not None and not reg.completo:
            bloqueos.append(f'El registro de lote de {orden.numero} está incompleto (pasos sin hacer o verificar o sin '
                            f'despeje de línea).')
    if lote.vencimiento and lote.vencimiento < hoy:
        bloqueos.append(f'El lote venció el {lote.vencimiento:%d/%m/%Y}.')
    return bloqueos


def liberable(insp):
    """CAL-13: el lote de una inspección aprobada que sigue en cuarentena (se libera desde la misma inspección)."""
    if insp.resultado not in ('APROBADO', 'OBSERVADO'):
        return None
    lote = lote_de(insp)
    return lote if lote is not None and lote.estado_calidad in ('CUARENTENA', 'BLOQUEADO') else None


@transaction.atomic
def liberar_desde_inspeccion(insp, usuario, clave, comentario='', ip=''):
    """CAL-13: QA libera el lote de la inspección aprobada con su firma: emite el certificado de análisis con los
    resultados (o usa el que está por aprobar) y lo aprueba; queda quién y cuándo."""
    from .planta import crear_certificado
    if not (usuario.is_superuser or puede(usuario, 'control_calidad.liberar_lotes')):
        raise ErrorPlanta('Libera lotes QA (permiso «liberar lotes»).')
    lote = liberable(insp)
    if lote is None:
        raise ErrorPlanta('El lote no está en cuarentena o la inspección no está aprobada.')
    cert = lote.certificados.filter(estado='BORRADOR').first()
    if cert is None:
        cert = crear_certificado(CertificadoAnalisis(lote=lote, inspeccion=insp, fecha=timezone.localdate()), usuario)
    elif cert.inspeccion_id is None:
        cert.inspeccion = insp
        cert.save(update_fields=['inspeccion'])
    return liberar(cert, usuario, clave, comentario, ip)


@transaction.atomic
def liberar(cert, usuario, clave, comentario='', ip=''):
    """CAL-03: quien analizó no libera; la liberación va con firma electrónica «Liberado por QA» y la revisión de QA
    sin pendientes."""
    from .planta import resolver_certificado
    if cert.estado != 'BORRADOR':
        raise ErrorPlanta('El certificado ya fue resuelto.')
    insp = cert.inspeccion
    if insp is not None and insp.inspector_id and insp.inspector_id == usuario.pk:
        raise ErrorPlanta('Quien registró el análisis no libera el lote (segregación de funciones): lo libera otra '
                          'persona de QA.')
    bloqueos = bloqueos_liberacion(cert.lote, insp)
    if bloqueos:
        raise ErrorPlanta('No se libera: ' + ' '.join(bloqueos))
    firma = _firma(usuario, clave, 'Liberado por QA', cert, ip)
    cert.firma = firma
    return resolver_certificado(cert, usuario, 'aprobar', comentario, firmado=True)


# ---------------------------------------------------------------- registro de lote (CAL-06)
@transaction.atomic
def crear_registro_lote(orden):
    """Pasos desde la hoja de ruta de la orden (sus operaciones) o, si no tiene, los pasos básicos."""
    reg, creado = RegistroLote.objects.get_or_create(orden=orden)
    if creado:
        operaciones = list(orden.horas.select_related('centro'))
        pasos = [(10 * (i + 1), f'{h.centro} · {h.descripcion or "operación"}'[:200]) for i, h in enumerate(operaciones)]
        if not pasos:
            pasos = [(10, 'Verificación de materiales e insumos dispensados'), (20, 'Fabricación según la fórmula'),
                     (30, 'Controles en proceso'), (40, 'Envasado y rotulado'), (50, 'Conciliación de materiales')]
        for sec, texto in pasos:
            PasoRegistroLote.objects.create(registro=reg, secuencia=sec, descripcion=texto)
    return reg


def firmar_paso(paso, usuario, accion, valor=''):
    """El que hace el paso y el que lo verifica son personas distintas."""
    ahora = timezone.now()
    if accion == 'hecho':
        if paso.hecho_por_id:
            raise ErrorPlanta('El paso ya está registrado.')
        paso.hecho_por, paso.hecho_en, paso.valor = usuario, ahora, (valor or paso.valor)[:120]
    elif accion == 'verificado':
        if not paso.hecho_por_id:
            raise ErrorPlanta('Primero se registra el paso.')
        if paso.hecho_por_id == usuario.pk:
            raise ErrorPlanta('Lo verifica otra persona (no quien lo hizo).')
        paso.verificado_por, paso.verificado_en = usuario, ahora
    else:
        raise ErrorPlanta('Acción no válida.')
    paso.save()
    return paso


def conciliacion(orden):
    """Rendimiento y conciliación de materiales: planificado frente a consumido."""
    filas = [{'producto': c.producto, 'plan': c.cantidad_plan, 'real': c.cantidad_real,
              'diferencia': c.cantidad_real - c.cantidad_plan,
              'pct': ((c.cantidad_real - c.cantidad_plan) * 100 / c.cantidad_plan).quantize(Decimal('0.1'))
              if c.cantidad_plan else None} for c in orden.consumos.select_related('producto')]
    rendimiento = ((orden.cantidad_producida * 100 / orden.cantidad).quantize(Decimal('0.1'))
                   if orden.cantidad and orden.cantidad_producida else None)
    return filas, rendimiento


# ---------------------------------------------------------------- desviaciones, cambios, quejas, retiros (CAL-07)
def cerrar_desviacion(d, usuario, clave, ip=''):
    if d.estado == 'CERRADA':
        raise ErrorPlanta('La desviación ya está cerrada.')
    if not (usuario.is_superuser or puede(usuario, 'control_calidad.aprobar_qa')):
        raise ErrorPlanta('Cierra la desviación QA (permiso «aprobación de QA»).')
    if len((d.causa_raiz or '').strip()) < 10 or len((d.impacto or '').strip()) < 5:
        raise ErrorPlanta('Complete la evaluación del impacto y la causa raíz antes de cerrar.')
    _firma(usuario, clave, 'Cierre de la desviación', d, ip)
    d.estado, d.cerrado_por, d.cerrado_en = 'CERRADA', usuario, timezone.now()
    d.save()
    return d


def resolver_cambio(c, usuario, accion, clave, ip=''):
    estados = {'aprobar': ('PROPUESTO', 'APROBADO'), 'rechazar': ('PROPUESTO', 'RECHAZADO'),
               'implementar': ('APROBADO', 'IMPLEMENTADO'), 'cerrar': ('IMPLEMENTADO', 'CERRADO')}
    if accion not in estados:
        raise ErrorPlanta('Acción no válida.')
    desde, hacia = estados[accion]
    if c.estado != desde:
        raise ErrorPlanta(f'El cambio está {c.get_estado_display().lower()}.')
    if accion in ('aprobar', 'rechazar', 'cerrar'):
        if not (usuario.is_superuser or puede(usuario, 'control_calidad.aprobar_qa')):
            raise ErrorPlanta('Aprueba y cierra los cambios QA (permiso «aprobación de QA»).')
        if accion == 'aprobar' and len((c.impacto or '').strip()) < 10:
            raise ErrorPlanta('Complete la evaluación del impacto antes de aprobar.')
        _firma(usuario, clave, f'Control de cambio: {dict(ControlCambio.ESTADOS)[hacia]}', c, ip)
        if accion == 'aprobar':
            c.aprobado_por = usuario
    c.estado = hacia
    c.save()
    return c


def clientes_del_lote(lote):
    """Clientes que recibieron el lote (de la trazabilidad: salidas del lote por ventas)."""
    from types import SimpleNamespace

    from .reportes_produccion import trazabilidad
    d = trazabilidad(SimpleNamespace(lote=lote.codigo, cliente=None, producto=lote.producto_id))
    filas = {}
    for info in d['lotes']:
        for v in info['ventas']:
            clave = v['cliente'].pk
            f = filas.setdefault(clave, {'cliente': v['cliente'].nombre, 'documento': v['cliente'].numero_doc,
                                         'telefono': v['cliente'].telefono, 'comprobantes': [],
                                         'cantidad': Decimal('0')})
            f['comprobantes'].append(str(v['venta']))
            f['cantidad'] += v['cantidad']
    return [{**f, 'cantidad': str(f['cantidad'])} for f in filas.values()]


@transaction.atomic
def iniciar_retiro(ret):
    ret.clientes = clientes_del_lote(ret.lote)
    ret.save()
    if ret.tipo == 'REAL':
        lote = ret.lote
        lote.estado_calidad, lote.motivo_estado = 'BLOQUEADO', f'Retiro de mercado {ret.numero}'
        lote.save(update_fields=['estado_calidad', 'motivo_estado'])
    return ret


# ---------------------------------------------------------------- estabilidad (CAL-05)
@transaction.atomic
def crear_estudio(estudio):
    estudio.save()
    for texto in str(estudio.meses).split(','):
        if texto.strip().isdigit():
            mes = int(texto.strip())
            PuntoEstabilidad.objects.create(estudio=estudio, mes=mes,
                                            programado=estudio.inicio + timedelta(days=round(mes * 30.44)))
    return estudio


# ---------------------------------------------------------------- revisión anual de producto (CAL-07)
def revision_anual(producto, anio):
    """PQR: lotes producidos y liberados, inspecciones y su resultado, OOS, desviaciones, quejas, cambios, retiros y
    estabilidad del producto en el año."""
    from core.models import Lote
    lotes = Lote.objects.filter(producto=producto, creado__year=anio)
    codigos = list(lotes.values_list('codigo', flat=True))
    inspecciones = InspeccionCalidad.objects.filter(producto=producto, fecha__year=anio)
    return {
        'producto': producto, 'anio': anio, 'lotes': lotes.count(),
        'liberados': CertificadoAnalisis.objects.filter(lote__producto=producto, estado='APROBADO',
                                                        aprobado_en__year=anio).count(),
        'rechazados': lotes.filter(estado_calidad='RECHAZADO').count(),
        'ordenes': OrdenProduccion.objects.filter(producto=producto, estado='TERMINADA', fecha_fin__year=anio).count(),
        'inspecciones': inspecciones.count(),
        'por_resultado': {n: inspecciones.filter(resultado=v).count() for v, n in InspeccionCalidad.RESULTADOS},
        'oos': InvestigacionOOS.objects.filter(inspeccion__in=inspecciones),
        'desviaciones': Desviacion.objects.filter(Q(lote__producto=producto) | Q(orden__producto=producto),
                                                  fecha__year=anio).distinct(),
        'quejas': QuejaLote.objects.filter(lote__producto=producto, fecha__year=anio),
        'cambios': ControlCambio.objects.filter(productos=producto, fecha__year=anio),
        'retiros': RetiroMercado.objects.filter(lote__producto=producto, fecha__year=anio),
        'estabilidad': EstudioEstabilidad.objects.filter(lote__producto=producto).filter(
            Q(inicio__year=anio) | Q(estado='EN_CURSO')),
        'codigos': codigos[:50],
    }
