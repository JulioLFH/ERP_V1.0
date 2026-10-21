"""Panel del estudio contable (n.º 53): por empresa, el periodo que se declara (abierto o cerrado, centralizado o
no), el SIRE de ventas y compras, el vencimiento SUNAT según el último dígito del RUC y los comprobantes observados;
y procesos masivos sobre varias empresas: centralizar, cerrar el periodo y descargar los PLE de todas en un ZIP.

Las funciones de datos se llaman con la base de la empresa ya activa (erp.empresas.activar)."""
import io
import zipfile
from datetime import timedelta

from django.utils import timezone

from erp import empresas as reg

from .cronograma_sunat import periodo_anterior, vencimiento


def resumen_contable(ruc):
    """Datos contables de la empresa activa para el periodo que se declara este mes."""
    from contabilidad.models import PeriodoContable
    from ventas.models import Venta

    from .models import PropuestaSIRE
    hoy = timezone.localdate()
    periodo = periodo_anterior(hoy)
    p = PeriodoContable.objects.filter(periodo=periodo).first()
    sire = {}
    for libro in ('RVIE', 'RCE'):
        prop = PropuestaSIRE.objects.filter(libro=libro, periodo=periodo).first()
        sire[libro] = ('aceptada' if prop and prop.aceptada_en else prop.get_estado_display().lower() if prop
                       else 'sin traer')
    vence = vencimiento(ruc, periodo)
    observados = Venta.objects.filter(estado_sunat__in=['RECHAZADO', 'ERROR'], fecha_emision__gte=hoy - timedelta(60)) \
        .exclude(estado='ANULADO').count()
    sin_centralizar = list(PeriodoContable.objects.filter(pendiente=True, cerrado=False, periodo__lte=periodo)
                           .values_list('periodo', flat=True)[:6])
    return {'periodo': periodo, 'periodo_texto': f'{periodo[4:]}/{periodo[:4]}',
            'cerrado': bool(p and p.cerrado), 'centralizado': not sin_centralizar,
            'sin_centralizar': [f'{x[4:]}/{x[:4]}' for x in sin_centralizar], 'sire_ventas': sire['RVIE'],
            'sire_compras': sire['RCE'], 'vence': vence, 'dias_vence': (vence - hoy).days if vence else None,
            'observados': observados}


def _sin_usuario_auditoria():
    """La auditoría de otra base no puede apuntar al usuario de la sesión (FK a otra base)."""
    from . import auditoria
    anterior = getattr(auditoria._local, 'usuario', None)
    auditoria._local.usuario = None
    return anterior


def ejecutar(aliases, accion, periodo):
    """Proceso masivo en cada empresa: 'centralizar' o 'cerrar' (centraliza si hace falta y cierra).
    [(alias, ok, mensaje)]"""
    from contabilidad.centralizar import ErrorContable, centralizar_periodo
    from contabilidad.models import PeriodoContable

    from . import auditoria
    resultados = []
    for alias in aliases:
        token = reg.activar(alias)
        anterior = _sin_usuario_auditoria()
        try:
            if PeriodoContable.esta_cerrado(periodo):
                resultados.append((alias, accion == 'cerrar', f'el periodo {periodo[4:]}/{periodo[:4]} ya está cerrado'))
                continue
            r = centralizar_periodo(periodo)
            texto = (f"centralizado: {r['compras']} compras, {r['ventas']} ventas, {r['tesoreria']} movimientos"
                     + (f" ({len(r['errores'])} aviso(s))" if r['errores'] else ''))
            if accion == 'cerrar':
                PeriodoContable.objects.update_or_create(periodo=periodo, defaults={'cerrado': True})
                texto += ' y cerrado'
            resultados.append((alias, True, texto))
        except ErrorContable as exc:
            resultados.append((alias, False, str(exc)))
        except Exception as exc:  # base no disponible: se informa y se sigue con las demás
            resultados.append((alias, False, f'no disponible ({type(exc).__name__})'))
        finally:
            auditoria._local.usuario = anterior
            reg.restaurar(token)
    return resultados


def ple_zip(aliases, periodo):
    """ZIP con el PLE de ventas (14.1) y compras (8.1) del periodo de cada empresa. (bytes, [(alias, mensaje)])"""
    from compras.views import compras_views
    from ventas.views import ventas_views

    from .models import Empresa
    buf, notas = io.BytesIO(), []
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for alias in aliases:
            token = reg.activar(alias)
            try:
                empresa = Empresa.actual()
                for vista in (ventas_views, compras_views):
                    docs = list(vista._registro_qs(periodo))
                    lineas = [vista.linea_ple(periodo, n, d) for n, d in enumerate(docs, 1)]
                    indicador = '1' if lineas else '0'
                    nombre = f'LE{empresa.ruc}{periodo}00{vista.codigo_ple}00{indicador}11.txt'
                    z.writestr(f'{empresa.ruc} {empresa.razon_social[:40]}/{nombre}', '\r\n'.join(lineas) +
                               ('\r\n' if lineas else ''))
                notas.append((alias, f'{empresa.razon_social}: PLE de ventas y compras generados'))
            except Exception as exc:
                notas.append((alias, f'no disponible ({type(exc).__name__})'))
            finally:
                reg.restaurar(token)
    return buf.getvalue(), notas
