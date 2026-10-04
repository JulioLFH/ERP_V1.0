"""Envío del comprobante al cliente por correo (con PDF y XML) o por WhatsApp."""
import re
import urllib.request
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect

from core import correo
from core.auditoria import registrar

from .models import Venta

EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _descargar(url, nombre, tipo):
    """Archivo del comprobante electrónico (PDF/XML/CDR del OSE); None si no está disponible."""
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=15) as r:  # noqa: S310 - enlaces del OSE configurado
            return nombre, r.read(), tipo
    except Exception:
        return None


def adjuntos(venta):
    from core.models import Empresa
    base = f'{Empresa.actual().ruc}-{venta.tipo_comprobante}-{venta.numero_completo}'  # nombre según SUNAT
    archivos = [_descargar(venta.enlace_pdf, f'{base}.pdf', 'application/pdf'),
                _descargar(venta.enlace_xml, f'{base}.xml', 'application/xml'),
                _descargar(venta.enlace_cdr, f'R-{base}.zip', 'application/zip')]
    return [a for a in archivos if a]


@login_required
def enviar_correo(request, pk):
    venta = get_object_or_404(Venta.objects.select_related('tercero'), pk=pk)
    if request.method != 'POST':
        return redirect('ventas:detalle', pk)
    destinos = [d.strip() for d in re.split(r'[,;\s]+', request.POST.get('para', '')) if d.strip()]
    invalidos = [d for d in destinos if not EMAIL.match(d)]
    if not destinos or invalidos:
        messages.error(request, 'Indique uno o más correos válidos' + (f': {", ".join(invalidos)}' if invalidos else '.'))
        return redirect('ventas:detalle', pk)
    if venta.estado == 'ANULADO':
        messages.error(request, 'El comprobante está anulado.')
        return redirect('ventas:detalle', pk)
    archivos = adjuntos(venta)
    try:
        correo.enviar(destinos, f'{venta.get_tipo_comprobante_display()} {venta.numero_completo}',
                      'ventas/correo_comprobante.html',
                      {'v': venta, 'mensaje': request.POST.get('mensaje', '').strip()[:1000],
                       'adjuntos': [a[0] for a in archivos]}, adjuntos=archivos)
    except correo.ErrorCorreo as exc:
        messages.error(request, str(exc))
        return redirect('ventas:detalle', pk)
    registrar('ENVIAR', venta, {'Correo': ', '.join(destinos), 'Adjuntos': ', '.join(a[0] for a in archivos)})
    messages.success(request, f'Comprobante enviado a {", ".join(destinos)}' +
                     (f' con {", ".join(a[0] for a in archivos)}.' if archivos else
                      '. No se adjuntó PDF/XML: el comprobante aún no tiene archivos del OSE.'))
    return redirect('ventas:detalle', pk)


@login_required
def whatsapp(request, pk):
    """Abre WhatsApp con el mensaje y el enlace al PDF; queda registrado en la auditoría."""
    venta = get_object_or_404(Venta.objects.select_related('tercero'), pk=pk)
    telefono = re.sub(r'\D', '', request.GET.get('telefono') or venta.tercero.telefono or '')
    if len(telefono) == 9:  # celular peruano sin código de país
        telefono = f'51{telefono}'
    texto = (f'Hola {venta.tercero.nombre}, le enviamos su {venta.get_tipo_comprobante_display().lower()} '
             f'{venta.numero_completo} por {"US$" if venta.moneda == "USD" else "S/"} {venta.total:,.2f}.')
    if venta.enlace_pdf:
        texto += f' PDF: {venta.enlace_pdf}'
    registrar('ENVIAR', venta, {'WhatsApp': telefono or '(sin número)'})
    return redirect(f'https://wa.me/{telefono}?text={quote(texto)}')
