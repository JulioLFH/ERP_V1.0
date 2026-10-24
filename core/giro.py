"""Giro y régimen tributario de la empresa (Ajustes › Giro y régimen): qué hace la empresa define su plantilla
contable (cuentas de existencias, uso de la clase 9 y 79 o gasto directo por naturaleza, sistema de costeo) y los
módulos que usa; el régimen define los libros electrónicos obligatorios y los pagos a cuenta. Todo se puede cambiar
después: cambiar de giro no borra asientos (solo cambia cómo se generan los nuevos) y queda en la auditoría.
Montos y porcentajes: Ajustes › Parámetros tributarios (confírmelos con su contador)."""
from decimal import Decimal

from django.db import transaction

from . import parametros

# giro -> (para qué sirve, módulos que suele usar, cuentas de existencias, costeo sugerido)
GIROS = {
    'COMERCIAL': ('Compra y vende mercadería sin transformarla',
                  {'crm', 'ventas', 'compras', 'inventario', 'logistica', 'tesoreria', 'finanzas', 'contabilidad'},
                  '20 Mercaderías y 28 por recibir; costo de ventas 69 desde el kardex', 'NATURALEZA'),
    'MANUFACTURA': ('Transforma insumos en productos (recetas, órdenes de fabricación)',
                    {'crm', 'manufactura', 'costos', 'inventario', 'compras', 'ventas', 'planillas', 'contabilidad'},
                    '24 materias primas, 25 suministros, 23 productos en proceso y 21 terminados; variación 61 y 71',
                    'PROCESOS'),
    'CONSTRUCCION': ('Obras por contrato para un cliente (valorizaciones)',  # el CRM lleva las licitaciones
                     {'crm', 'obras', 'compras', 'inventario', 'requerimientos', 'planillas', 'contabilidad'},
                     'Almacén por obra (25 suministros); costo por obra con su centro de costo', 'ORDENES'),
    'INMOBILIARIA': ('Construye para vender inmuebles (departamentos, lotes)',
                     {'crm', 'obras', 'ventas', 'compras', 'tesoreria', 'finanzas', 'contabilidad'},
                     '2341 inmuebles en construcción, 2141 terminados, 7134 variación, 7024 ventas y 6924 costo',
                     'ORDENES'),
    'SERVICIOS': ('Presta servicios (sin inventario propio relevante)',
                  {'crm', 'ventas', 'compras', 'tesoreria', 'finanzas', 'contabilidad', 'planillas'},
                  'Sin existencias: gastos por naturaleza; 70 servicios', 'NATURALEZA'),
}
# módulos que no dependen del giro (siempre visibles; SST es obligatorio para todo empleador)
SIEMPRE = {'tablero', 'ajustes', 'contactos', 'cliente', 'sst', 'calidad'}


def obligaciones(regimen, ingresos_uit=None, fecha=None):
    """[(tema, detalle)] según el régimen y los ingresos anuales en UIT (Ley del Impuesto a la Renta, art. 65 y
    regímenes MYPE, RER y NRUS)."""
    p = lambda clave: parametros.valor(clave, fecha)  # noqa: E731
    uit = parametros.uit(fecha)
    ingresos = Decimal(ingresos_uit) if ingresos_uit not in (None, '') else None
    filas = []
    if regimen == 'NRUS':
        filas.append(('Comprobantes', 'Solo boletas de venta y tickets (no emite facturas)'))
        filas.append(('Libros', 'No lleva libros contables'))
        filas.append(('Pagos', 'Cuota mensual fija según su categoría (ingresos y compras del mes)'))
        return filas
    if regimen == 'RER':
        filas.append(('Libros', 'Registro de compras y registro de ventas (electrónicos si está obligado al PLE '
                                'o al SIRE)'))
        filas.append(('Pagos', f'Cuota mensual del {p("RER_CUOTA_PCT"):g} % de los ingresos netos (cancelatoria: '
                               f'no hay declaración anual)'))
        return filas
    simple, mayor, completa = p('LIBROS_SIMPLIFICADO_UIT'), p('LIBROS_MAYOR_UIT'), p('LIBROS_COMPLETA_UIT')
    if ingresos is None:
        filas.append(('Libros', f'Hasta {simple:g} UIT: registro de ventas, de compras y diario simplificado; hasta '
                                f'{mayor:g} UIT: más diario y mayor; hasta {completa:g} UIT: más inventarios y '
                                f'balances; más de {completa:g} UIT: contabilidad completa'))
    elif ingresos <= simple:
        filas.append(('Libros', 'Registro de ventas, registro de compras y libro diario de formato simplificado'))
    elif ingresos <= mayor:
        filas.append(('Libros', 'Registro de ventas, registro de compras, libro diario y libro mayor'))
    elif ingresos <= completa:
        filas.append(('Libros', 'Registro de ventas, registro de compras, libro diario, libro mayor y libro de '
                                'inventarios y balances'))
    else:
        filas.append(('Libros', 'Contabilidad completa (incluye registro de inventario permanente valorizado, '
                                'caja y bancos y activos fijos)'))
    if regimen == 'MYPE':
        filas.append(('Pagos a cuenta', f'{p("RENTA_PAGO_CUENTA_MYPE_PCT"):g} % de los ingresos netos mientras los '
                                        f'ingresos del año no superen {p("MYPE_LIMITE_UIT"):g} UIT '
                                        f'(S/ {p("MYPE_LIMITE_UIT") * uit:,.0f}); luego coeficiente o '
                                        f'{p("RENTA_PAGO_CUENTA_PCT"):g} %'))
        filas.append(('Renta anual', '10 % hasta 15 UIT de renta neta y 29.5 % por el exceso'))
    else:
        filas.append(('Pagos a cuenta', f'Coeficiente o {p("RENTA_PAGO_CUENTA_PCT"):g} % de los ingresos netos '
                                        f'(el mayor)'))
        filas.append(('Renta anual', '29.5 % de la renta neta'))
    return filas


def sugerir_ocultos(giros):
    """Módulos que ninguno de los giros usa (sugeridos para ocultar)."""
    from .modulos import MODULOS
    usados = set().union(*(GIROS[g][1] for g in giros if g in GIROS)) if giros else {m['clave'] for m in MODULOS}
    return sorted(m['clave'] for m in MODULOS if m['clave'] not in usados | SIEMPRE)


def _destinos_gasto(activar):
    """Gasto por naturaleza con destinos a la clase 9 y 79 (costeo) o sin ellos (sin costeo). Solo las cuentas de gasto
    62-68; las compras 60 conservan su destino a existencias por recibir."""
    from contabilidad import pcge
    from contabilidad.models import CuentaContable
    plan = pcge.plan()
    cuentas = CuentaContable.objects.filter(imputable=True, codigo__regex=r'^6[2-8]')
    if not activar:
        return cuentas.exclude(destino_debe=None).update(destino_debe=None, destino_haber=None)
    por_codigo = {c.codigo: c for c in CuentaContable.objects.filter(codigo__regex=r'^(9|79)')}
    n = 0
    for c in cuentas.filter(destino_debe=None):
        debe, haber = pcge.destinos(c.codigo, plan)
        if debe in por_codigo and haber in por_codigo:
            c.destino_debe, c.destino_haber = por_codigo[debe], por_codigo[haber]
            c.save(update_fields=['destino_debe', 'destino_haber'])
            n += 1
    return n


@transaction.atomic
def aplicar(empresa, giros, regimen, sistema_costeo, ciiu='', actividad='', ocultos=None):
    """Guarda el giro y el régimen, crea las cuentas que falten, activa o quita los destinos de costeo y deja la
    traza en la auditoría. Devuelve [mensajes]."""
    from contabilidad import pcge
    from contabilidad.automatico import marcar_pendiente
    from contabilidad.models import CuentaContable, CuentaDefecto, PeriodoContable

    from .auditoria import registrar
    antes = {'giros': list(empresa.giros or []), 'regimen_tributario': empresa.regimen_tributario,
             'sistema_costeo': empresa.sistema_costeo, 'modulos_ocultos': list(empresa.modulos_ocultos or [])}
    empresa.giros, empresa.regimen_tributario, empresa.sistema_costeo = list(giros), regimen, sistema_costeo
    empresa.ciiu, empresa.actividad_economica = (ciiu or '')[:10], (actividad or '')[:200]
    empresa.modulos_ocultos = sorted(set(ocultos or []) - SIEMPRE)
    empresa.save(update_fields=['giros', 'regimen_tributario', 'sistema_costeo', 'ciiu', 'actividad_economica',
                                'modulos_ocultos'])
    mensajes = []
    antes_cuentas = CuentaContable.objects.count()
    pcge.cargar(CuentaContable, CuentaDefecto)  # cuentas del catálogo (existencias, inmuebles…) y por defecto
    nuevas = CuentaContable.objects.count() - antes_cuentas
    if nuevas:
        mensajes.append(f'{nuevas} cuenta(s) del plan creadas.')
    if (antes['sistema_costeo'] == 'NATURALEZA') != (sistema_costeo == 'NATURALEZA'):
        n = _destinos_gasto(sistema_costeo != 'NATURALEZA')
        mensajes.append(f'{n} cuenta(s) de gasto {"con" if sistema_costeo != "NATURALEZA" else "sin"} destino a la '
                        f'clase 9 y 79.')
        primero = PeriodoContable.objects.filter(cerrado=False).order_by('periodo').values_list(
            'periodo', flat=True).first()
        if primero:
            marcar_pendiente(primero)  # los periodos abiertos se vuelven a centralizar con el nuevo tratamiento
    despues = {'giros': empresa.giros, 'regimen_tributario': regimen, 'sistema_costeo': sistema_costeo,
               'modulos_ocultos': empresa.modulos_ocultos}
    cambios = {k: [antes[k], despues[k]] for k in antes if antes[k] != despues[k]}
    if cambios:
        registrar('MODIFICAR', empresa, cambios, 'Giro y régimen tributario de la empresa')
    return mensajes
