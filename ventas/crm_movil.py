"""CRM en el celular del vendedor de campo (CRM-19): oportunidad en 30 segundos, visita con ubicación y foto, próximas
actividades y ficha del cliente. Funciona con señal débil: lo registrado sin conexión queda en el teléfono y se envía
al volver la señal (POST /crm/movil/sincronizar/)."""
import base64
import binascii
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from core.models import Tercero

from . import crm_servicios as srv
from .models import ActividadCRM, CambioEtapa, Oportunidad

LIMITE_FOTO = 4 * 1024 * 1024


@login_required
def movil(request):
    hoy = timezone.localdate()
    actividades = ActividadCRM.objects.filter(hecha=False, fecha__lte=hoy + timedelta(days=7)).filter(
        Q(responsable=request.user) | Q(responsable__isnull=True, usuario=request.user)).select_related(
        'oportunidad', 'tercero').order_by('fecha', 'hora')[:60]
    from .models import CarteraCliente
    propios = list(CarteraCliente.objects.filter(responsable=request.user).values_list('tercero_id', flat=True))
    clientes = Tercero.objects.filter(activo=True, tipo__in=['CLIENTE', 'AMBOS'])
    if propios and not srv.ve_todo(request.user):
        clientes = clientes.filter(pk__in=propios)
    datos = {
        'clientes': [{'id': t.pk, 'nombre': t.nombre, 'doc': t.numero_doc, 'telefono': t.telefono}
                     for t in clientes.order_by('nombre')[:1500]],
        'actividades': [{'id': a.pk, 'fecha': a.fecha.isoformat(), 'hora': a.hora.strftime('%H:%M') if a.hora else '',
                         'tipo': a.get_tipo_display(), 'texto': a.descripcion, 'cliente': a.cliente,
                         'oportunidad': a.oportunidad_id} for a in actividades],
        'oportunidades': [{'id': o.pk, 'nombre': o.nombre, 'cliente': o.cliente}
                          for o in srv.visibles(request.user, Oportunidad.objects.filter(
                              etapa__in=Oportunidad.ABIERTAS)).filter(responsable=request.user)[:200]],
    }
    return render(request, 'ventas/crm_movil.html', {'datos': datos, 'hoy': hoy})


def _decimal(v):
    try:
        return Decimal(str(v)) if v not in (None, '') else None
    except InvalidOperation:
        return None


def _foto(texto):
    """Foto en dataURL (base64) desde el celular -> archivo."""
    if not texto or ',' not in texto:
        return None
    cabecera, contenido = texto.split(',', 1)
    try:
        crudo = base64.b64decode(contenido, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(crudo) > LIMITE_FOTO or not cabecera.startswith('data:image/'):
        return None
    extension = 'png' if 'png' in cabecera else 'jpg'
    return ContentFile(crudo, name=f'visita_{timezone.now():%Y%m%d%H%M%S}.{extension}')


def _registrar(item, user):
    tipo = item.get('tipo')
    hoy = timezone.localdate()
    if tipo == 'oportunidad':
        nombre = (item.get('nombre') or '').strip()
        tercero = Tercero.objects.filter(pk=item.get('cliente') or 0).first()
        prospecto = (item.get('prospecto') or '').strip()
        monto = _decimal(item.get('monto'))
        if not nombre or not (tercero or prospecto) or not monto or monto <= 0:
            return 'Faltan el nombre, el cliente o prospecto o el monto.'
        o = Oportunidad.objects.create(nombre=nombre[:150], tercero=tercero, prospecto=prospecto[:150],
                                       telefono=(item.get('telefono') or '')[:40], monto=monto, etapa='PROSPECTO',
                                       origen='VISITA', responsable=user, notas=(item.get('nota') or '')[:1000])
        CambioEtapa.objects.create(oportunidad=o, de='', a='PROSPECTO', usuario=user)
        ActividadCRM.objects.create(oportunidad=o, tipo='LLAMADA', fecha=hoy, descripcion='Primer seguimiento',
                                    usuario=user, responsable=user)
        srv.asignar_cartera(tercero, user)
        return o
    if tipo == 'visita':
        tercero = Tercero.objects.filter(pk=item.get('cliente') or 0).first()
        o = Oportunidad.objects.filter(pk=item.get('oportunidad') or 0).first()
        if tercero is None and o is None:
            return 'Elija el cliente o la oportunidad visitada.'
        try:
            fecha = date.fromisoformat(item.get('fecha') or '')
        except ValueError:
            fecha = hoy
        a = ActividadCRM.objects.create(
            tipo='VISITA', oportunidad=o, tercero=tercero or (o.tercero if o else None), fecha=min(fecha, hoy),
            hecha=True, descripcion=(item.get('nota') or 'Visita')[:250], resultado=(item.get('resultado') or '')[:250],
            usuario=user, responsable=user, recordatorio=False,
            latitud=_decimal(item.get('lat')), longitud=_decimal(item.get('lng')))
        if o is not None:
            o.save(update_fields=['actualizado'])
        archivo = _foto(item.get('foto'))
        if archivo is not None:
            from core.sustentos import adjuntar
            adjuntar(a, archivo, user, f'Foto de la visita del {a.fecha:%d/%m/%Y}')
        return a
    return 'Tipo desconocido.'


@login_required
def sincronizar(request):
    """Recibe lo registrado en el celular (también lo guardado sin señal): [{tipo, uid, ...}]. Devuelve por uid si se
    guardó o el error; lo ya recibido (mismo uid) no se duplica."""
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    try:
        items = json.loads(request.body.decode('utf-8') or '[]')
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'Datos no válidos.'}, status=400)
    from django.core.cache import cache
    resultado = {}
    for item in items[:50] if isinstance(items, list) else []:
        uid = str(item.get('uid') or '')[:40]
        clave = f'crm-movil-{request.user.pk}-{uid}'
        if uid and cache.get(clave):
            resultado[uid] = {'ok': True, 'repetido': True}
            continue
        with transaction.atomic():
            r = _registrar(item, request.user)
        if isinstance(r, str):
            resultado[uid] = {'ok': False, 'error': r}
        else:
            resultado[uid] = {'ok': True, 'id': r.pk}
            if uid:
                cache.set(clave, 1, 60 * 60 * 24 * 7)
    return JsonResponse({'ok': True, 'resultado': resultado})
