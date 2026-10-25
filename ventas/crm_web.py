"""Formulario de la página web conectado al CRM (CRM-09, fase 1): cada consulta crea la oportunidad con origen «Web»,
la asigna por regla (responsable fijo o rotativo), agenda «Llamar hoy», avisa posibles duplicados y registra el
consentimiento de datos personales (Ley 29733). Público: lo protege la clave de la empresa, un campo trampa contra
robots y un tiempo mínimo de llenado."""
import time

from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt

from erp import empresas as emp

SEGUNDOS_MINIMOS = 3


def _limpio(request, campo, largo):
    return ' '.join((request.POST.get(campo) or '').split())[:largo]


def registrar(datos, ip='', origen='WEB'):
    """Crea la oportunidad de la web (o del buzón comercial). datos: nombre, empresa, telefono, email, mensaje.
    Devuelve la oportunidad."""
    from . import crm_servicios as srv
    from .models import ActividadCRM, ConfigCRM, ConsentimientoDatos, Oportunidad
    responsable = srv.asignar_web()
    quien = datos.get('empresa') or datos['nombre']
    medio = 'la página web' if origen == 'WEB' else 'el correo'
    o = Oportunidad(nombre=f'Consulta {"web" if origen == "WEB" else "por correo"}: {quien}'[:150],
                    prospecto=quien[:150], contacto=datos['nombre'][:120],
                    telefono=datos.get('telefono', '')[:40], email=datos.get('email', '')[:254], etapa='PROSPECTO',
                    origen=origen, responsable=responsable,
                    notas=f'{datos.get("mensaje", "")}\n(Recibida por {medio}{f" desde {ip}" if ip else ""})'.strip())
    repetidas = srv.duplicados(o)
    if repetidas:
        o.notas += '\nPosible duplicado de: ' + ', '.join(f'«{d.nombre}» (#{d.pk})' for d in repetidas[:3])
    o.save()
    from .models import CambioEtapa
    CambioEtapa.objects.create(oportunidad=o, de='', a='PROSPECTO')
    ActividadCRM.objects.create(oportunidad=o, tipo='LLAMADA' if o.telefono else 'CORREO', fecha=timezone.localdate(),
                                descripcion=f'{"Llamar" if o.telefono else "Responder"} hoy: consulta recibida por '
                                            f'{medio}', responsable=responsable, usuario=responsable)
    if origen == 'WEB':  # en la web autorizó el uso de sus datos (casilla obligatoria)
        ConsentimientoDatos.objects.create(titular=datos['nombre'][:150], email=o.email, telefono=o.telefono,
                                           oportunidad=o, canal='WEB', finalidad=ConfigCRM.actual().finalidad_datos)
    return o


def procesar_correo(mensaje):
    """Un correo del buzón comercial -> oportunidad (origen «Correo»). Devuelve la oportunidad o None."""
    from email.header import decode_header, make_header
    from email.utils import parseaddr
    nombre, direccion = parseaddr(mensaje.get('From', ''))
    if not direccion:
        return None
    try:
        nombre = str(make_header(decode_header(nombre))) if nombre else ''
        asunto = str(make_header(decode_header(mensaje.get('Subject', '') or '')))
    except Exception:  # noqa: BLE001  (cabeceras mal codificadas)
        asunto = mensaje.get('Subject', '') or ''
    cuerpo = ''
    for parte in mensaje.walk():
        if parte.get_content_type() == 'text/plain' and not parte.get_filename():
            carga = parte.get_payload(decode=True) or b''
            cuerpo = carga.decode(parte.get_content_charset() or 'utf-8', errors='replace')
            break
    empresa = direccion.split('@')[1].split('.')[0].upper() if '@' in direccion else ''
    if empresa in ('GMAIL', 'HOTMAIL', 'OUTLOOK', 'YAHOO', 'LIVE', 'ICLOUD'):
        empresa = ''
    return registrar({'nombre': (nombre or direccion)[:120], 'empresa': empresa, 'email': direccion.lower(),
                      'mensaje': f'{asunto}\n{cuerpo}'.strip()[:1500]}, origen='CORREO')


@csrf_exempt
@xframe_options_exempt
def formulario(request, alias, clave):
    if alias not in emp.empresas():
        raise Http404
    token = emp.activar(alias)
    try:
        from .models import ConfigCRM
        cfg = ConfigCRM.actual()
        if not cfg.web_activo or clave != cfg.token_web:
            raise Http404
        empresa = emp.razon_social(alias)
        if request.method != 'POST':
            return render(request, 'ventas/crm_web.html', {'empresa': empresa, 't': int(time.time()),
                                                           'finalidad': cfg.finalidad_datos})
        errores = []
        nombre = _limpio(request, 'nombre', 120)
        telefono, email = _limpio(request, 'telefono', 40), _limpio(request, 'email', 254)
        try:
            enviado = int(request.POST.get('t') or 0)
        except ValueError:
            enviado = 0
        robot = bool(request.POST.get('sitio_web')) or (enviado and time.time() - enviado < SEGUNDOS_MINIMOS)
        if not nombre:
            errores.append('Indique su nombre.')
        if not telefono and not email:
            errores.append('Indique su teléfono o su correo.')
        if not request.POST.get('consentimiento'):
            errores.append('Debe autorizar el uso de sus datos para que podamos responderle.')
        quiere_json = 'application/json' in request.headers.get('Accept', '')
        if robot:  # se responde como si estuviera bien (no se le da pistas al robot)
            return JsonResponse({'ok': True}) if quiere_json else render(request, 'ventas/crm_web.html', {
                'empresa': empresa, 'gracias': True})
        if errores:
            if quiere_json:
                return JsonResponse({'ok': False, 'errores': errores}, status=400)
            return render(request, 'ventas/crm_web.html', {'empresa': empresa, 'errores': errores, 'd': request.POST,
                                                           't': int(time.time()), 'finalidad': cfg.finalidad_datos})
        ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR', ''))
        o = registrar({'nombre': nombre, 'empresa': _limpio(request, 'empresa', 150), 'telefono': telefono,
                       'email': email, 'mensaje': (request.POST.get('mensaje') or '')[:1500]}, ip[:45])
        if quiere_json:
            return JsonResponse({'ok': True, 'id': o.pk})
        return render(request, 'ventas/crm_web.html', {'empresa': empresa, 'gracias': True})
    finally:
        emp.restaurar(token)
