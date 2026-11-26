"""Qué trae el QR que escanea el operador.

- La hoja de despacho de un consolidado (la imprime Ceiba): «CEIBA-OL|CONSOLIDADO|CL-000001|TOKEN».
- La guía electrónica del cliente: PENDIENTE DE VALIDAR con guías reales (validaciones del requerimiento, bloque 2).
  Se cree que el QR de la GRE lleva a un PDF de SUNAT. Mientras no se pruebe con 5 a 10 guías reales, el lector
  reconoce estos formatos y nunca inventa datos:
    · texto con barras «RUC|09|SERIE|NÚMERO|…» (como el QR de los comprobantes): identifica la guía;
    · un enlace (http…): se guarda tal cual y la guía queda con «datos pendientes» hasta que llegue su XML por correo
      o se conecte la lectura del PDF de SUNAT (obtener_datos_de_enlace);
    · «SERIE-NÚMERO» suelto (ej. T001-123 o EG01-45): identifica la guía si ya se cargó.
- La guía transportista (31) del servicio de transporte: mismo formato con tipo 31."""
import re
from urllib.parse import parse_qs, urlparse

PATRON_SERIE = re.compile(r'\b([A-Z0-9]{4})-0*(\d{1,8})\b')


class Lectura:
    """Resultado de interpretar el QR."""

    def __init__(self, texto, clase, ruc='', tipo='', serie='', numero='', url='', token=''):
        self.texto, self.clase = texto, clase
        self.ruc, self.tipo, self.serie, self.numero, self.url, self.token = ruc, tipo, serie, numero, url, token

    @property
    def identifica_guia(self):
        return bool(self.serie and self.numero)

    def __repr__(self):
        return f'<Lectura {self.clase} {self.ruc} {self.tipo} {self.serie}-{self.numero}>'


def interpretar(texto):
    texto = (texto or '').strip()
    if not texto:
        return Lectura('', 'VACIO')
    partes = [p.strip() for p in texto.split('|')]
    if len(partes) >= 4 and partes[0] == 'CEIBA-OL' and partes[1] == 'CONSOLIDADO':
        return Lectura(texto, 'CONSOLIDADO', serie='CL', numero=partes[2], token=partes[3] if len(partes) > 3 else '')
    if len(partes) >= 4 and re.fullmatch(r'\d{11}', partes[0]) and re.fullmatch(r'\d{2}', partes[1]) \
            and re.fullmatch(r'[A-Z0-9]{4}', partes[2].upper()) and partes[3].lstrip('0').isdigit():
        return Lectura(texto, 'GUIA', ruc=partes[0], tipo=partes[1], serie=partes[2].upper(),
                       numero=str(int(partes[3])))
    if texto.lower().startswith(('http://', 'https://')):
        lectura = Lectura(texto, 'ENLACE', url=texto)
        _datos_del_enlace(lectura)
        return lectura
    m = PATRON_SERIE.search(texto.upper())
    if m:
        ruc = re.search(r'\b(\d{11})\b', texto)
        return Lectura(texto, 'GUIA', ruc=ruc.group(1) if ruc else '', serie=m.group(1), numero=str(int(m.group(2))))
    return Lectura(texto, 'DESCONOCIDO')


def _datos_del_enlace(lectura):
    """Algunos enlaces llevan la serie y el número en los parámetros: se aprovechan sin descargar nada."""
    try:
        q = parse_qs(urlparse(lectura.url).query)
    except ValueError:
        return
    serie = (q.get('serie') or [''])[0].upper()
    numero = (q.get('numero') or q.get('correlativo') or [''])[0].lstrip('0')
    if re.fullmatch(r'[A-Z0-9]{4}', serie) and numero.isdigit():
        lectura.serie, lectura.numero = serie, numero
        lectura.ruc = ((q.get('ruc') or q.get('numRuc') or [''])[0])[:11]
        lectura.tipo = ((q.get('tipo') or q.get('codCpe') or ['09'])[0])[:2]


def obtener_datos_de_enlace(url):
    """PENDIENTE (validación del bloque 2): descargar el PDF de SUNAT al que lleva el QR y leer su texto. Antes hay
    que confirmar con guías reales que el PDF se descarga sin iniciar sesión y que trae texto (no una imagen). Hasta
    entonces devuelve None y la guía espera su XML (plan B: el cliente lo envía por correo)."""
    return None
