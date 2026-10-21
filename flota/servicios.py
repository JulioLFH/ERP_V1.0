"""Reglas de flota: sincronización vehículo ↔ equipo, documentos vencidos (bloqueo de guías) y alertas."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import DocumentoFlota

DIAS_AVISO = 30


class ErrorFlota(Exception):
    pass


# ---------------------------------------------------------------- maestro único (FLO-01)
@transaction.atomic
def equipo_de_vehiculo(vehiculo):
    """El equipo del vehículo de Logística (lo crea si no existe) con su placa, marca y modelo al día."""
    from produccion.models import Equipo
    eq = vehiculo.equipo
    if eq is None:
        eq = Equipo.objects.filter(tipo='VEHICULO', placa=vehiculo.placa).first()
    if eq is None:
        codigo = f'VH-{vehiculo.placa.replace("-", "")}'[:20]
        n = 1
        while Equipo.objects.filter(codigo=codigo).exists():
            n += 1
            codigo = f'VH-{vehiculo.placa.replace("-", "")}-{n}'[:20]
        eq = Equipo(codigo=codigo, tipo='VEHICULO', medidor='KM')
    eq.placa = vehiculo.placa
    eq.marca_modelo = f'{vehiculo.marca} {vehiculo.modelo}'.strip() or eq.marca_modelo
    eq.nombre = eq.nombre or f'{vehiculo.marca} {vehiculo.modelo} {vehiculo.placa}'.strip()[:120]
    eq.activo = vehiculo.activo
    eq.save()
    if vehiculo.equipo_id != eq.pk:
        type(vehiculo).objects.filter(pk=vehiculo.pk).update(equipo=eq)
        vehiculo.equipo = eq
    return eq


@transaction.atomic
def vehiculo_de_equipo(equipo):
    """Un equipo de tipo vehículo con placa aparece en Logística › Vehículos (para las guías)."""
    from logistica.models import Vehiculo
    if equipo.tipo != 'VEHICULO' or not equipo.placa:
        return None
    v = getattr(equipo, 'vehiculo', None) or Vehiculo.objects.filter(placa=equipo.placa).first() or Vehiculo()
    v.placa = equipo.placa
    partes = (equipo.marca_modelo or '').split(' ', 1)
    v.marca, v.modelo = partes[0][:50], (partes[1] if len(partes) > 1 else '')[:50]
    v.activo = equipo.activo and equipo.estado != 'BAJA'
    v.equipo = equipo
    v.save()
    return v


# ---------------------------------------------------------------- documentos (FLO-04)
def vigentes(qs):
    """Último documento de cada tipo (los renovados no cuentan)."""
    return qs.filter(renovado=False)


@transaction.atomic
def registrar_documento(doc, usuario, archivo=None):
    if not doc.equipo_id and not doc.conductor_id:
        raise ErrorFlota('Indique el equipo o el conductor del documento.')
    if doc.emision and doc.vencimiento < doc.emision:
        raise ErrorFlota('El vencimiento es anterior a la emisión.')
    if archivo is not None:
        contenido = archivo.read()
        if len(contenido) > 8 * 1024 * 1024:
            raise ErrorFlota('El archivo supera los 8 MB.')
        doc.archivo, doc.archivo_nombre = contenido, archivo.name[:150]
        doc.archivo_tipo = 'application/pdf' if contenido[:5] == b'%PDF-' else (archivo.content_type or '')[:80]
    doc.registrado_por = usuario
    doc.save()
    # el nuevo reemplaza al anterior del mismo tipo (renovación)
    (DocumentoFlota.objects.filter(equipo=doc.equipo, conductor=doc.conductor, tipo=doc.tipo, renovado=False)
     .exclude(pk=doc.pk).filter(vencimiento__lte=doc.vencimiento).update(renovado=True))
    if doc.tipo == 'LICENCIA' and doc.conductor_id:
        c = doc.conductor
        c.licencia_vencimiento = doc.vencimiento
        c.licencia_categoria = doc.categoria or c.licencia_categoria
        if doc.numero:
            c.licencia = doc.numero[:20]
        c.save(update_fields=['licencia_vencimiento', 'licencia_categoria', 'licencia'])
    return doc


def bloqueos(vehiculo=None, conductor=None, fecha=None):
    """Mensajes por documentos vencidos que impiden usar el vehículo o el conductor en una guía."""
    fecha = fecha or timezone.localdate()
    mensajes = []
    if vehiculo is not None and vehiculo.equipo_id:
        for d in vigentes(vehiculo.equipo.documentos.filter(bloquea=True, vencimiento__lt=fecha)):
            mensajes.append(f'El vehículo {vehiculo.placa} tiene el {d.get_tipo_display()} vencido '
                            f'({d.vencimiento:%d/%m/%Y}).')
    if conductor is not None:
        if conductor.licencia_vencimiento and conductor.licencia_vencimiento < fecha:
            mensajes.append(f'La licencia de {conductor.nombres} {conductor.apellidos} venció el '
                            f'{conductor.licencia_vencimiento:%d/%m/%Y}.')
        for d in vigentes(conductor.documentos.filter(bloquea=True, vencimiento__lt=fecha)).exclude(tipo='LICENCIA'):
            mensajes.append(f'{conductor.nombres} {conductor.apellidos}: {d.get_tipo_display()} vencido '
                            f'({d.vencimiento:%d/%m/%Y}).')
    return mensajes


def por_vencer(dias=DIAS_AVISO):
    """Documentos vigentes que vencen en `dias` días o ya vencieron, más licencias de conductores."""
    from logistica.models import Conductor
    limite = timezone.localdate() + timedelta(days=dias)
    docs = list(vigentes(DocumentoFlota.objects.filter(vencimiento__lte=limite))
                .select_related('equipo', 'conductor').exclude(equipo__activo=False).exclude(conductor__activo=False))
    con_doc = {d.conductor_id for d in docs if d.tipo == 'LICENCIA'}
    licencias = [c for c in Conductor.objects.filter(activo=True, licencia_vencimiento__lte=limite)
                 if c.pk not in con_doc]
    return docs, licencias
