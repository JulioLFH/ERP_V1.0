"""Archivos del SIRE armados con lo registrado en el ERP (SIR-01, SIR-02 y SIR-03).

- 14.4 RVIE y 8.4 RCE: reemplazo de la propuesta (oportunidad 02) y ajustes posteriores (03, 04 y 05).
- 8.5 RCE: compras con sujetos no domiciliados (comprobante 91), que no van al 8.1 / 8.4.

Nombre del archivo (RS 112-2021/SUNAT modificada por la RS 040-2022/SUNAT, Anexo N.° 1, y manuales API del SIRE):
    LE + RUC + AAAAMM + 00 + código del registro (140400 / 080400 / 080500) + CC (oportunidad) + O (indicador de
    operaciones: 1 con operaciones, 0 sin) + I (indicador de contenido: 1 con información, 0 vacío) + M (moneda:
    1 soles, 2 dólares) + 2 (generado por el contribuyente y enviado por servicio web) [+ NN correlativo del ajuste
    posterior] + .txt
y se envía comprimido en un .zip con el mismo nombre. Ej. de los manuales: LE2010017645020230200140400021112.zip para el
reemplazo y LE201000174912023110014040003111201.zip para el primer ajuste posterior.

Las filas son TXT separado por «|» (cada línea termina en «|», como el PLE) con las MISMAS columnas que la propuesta
que entrega SUNAT (encabezados que lee compras/sire.py leer_completo). Las columnas están en las constantes
COLUMNAS_*: si SUNAT cambia la estructura (anexos 3, 9 y 11 de la RS 112-2021), se corrige solo ahí.

Importes en la moneda del comprobante (con su tipo de cambio). En el TXT las notas de crédito van en NEGATIVO (como
las entrega SUNAT), aunque en el ERP se guardan en positivo (el signo lo da el tipo 07).
"""
import io
import zipfile
from datetime import date
from decimal import Decimal

from .models import Empresa
from .utils import fmt_fecha

D0 = Decimal('0')
TOLERANCIA = Decimal('0.01')
REGISTROS = {'140400': 'RVIE', '080400': 'RCE', '080500': 'RCE'}
NOMBRES = {'140400': 'Registro de Ventas e Ingresos 14.4', '080400': 'Registro de Compras 8.4',
           '080500': 'Compras con sujetos no domiciliados 8.5'}
AJUSTES = ('03', '04', '05')
GENERADO_POR_SIRE = '2'  # último indicador del nombre: archivo del contribuyente enviado por servicio web

# ---------------------------------------------------------------- estructuras (una constante por registro)
# 14.4 RVIE: Anexo N.° 3 (archivo plano para comparar o reemplazar la propuesta). Del 34 al 40 los completa SUNAT si
# vienen vacíos (nota 4 del anexo).
COLUMNAS_RVIE = [
    'Ruc', 'Apellidos y Nombres o Razón social', 'Periodo', 'CAR SUNAT', 'Fecha de emisión', 'Fecha Vcto/Pago',
    'Tipo CP/Doc.', 'Serie del CDP', 'Nro CP o Doc. Nro Inicial (Rango)', 'Nro Final (Rango)', 'Tipo Doc Identidad',
    'Nro Doc Identidad', 'Apellidos Nombres/ Razón Social', 'Valor Facturado Exportación', 'BI Gravada', 'Dscto BI',
    'IGV / IPM', 'Dscto IGV / IPM', 'Mto Exonerado', 'Mto Inafecto', 'ISC', 'BI Grav IVAP', 'IVAP', 'ICBPER',
    'Otros Tributos', 'Total CP', 'Moneda', 'Tipo Cambio', 'Fecha Emisión Doc Modificado', 'Tipo CP Modificado',
    'Serie CP Modificado', 'Nro CP Modificado', 'ID Proyecto Operadores Atribución', 'Tipo de Nota', 'Est. Comp',
    'Valor FOB Embarcado', 'Valor OP Gratuitas', 'Tipo Operación', 'DAM / CP', 'CLU']
# 8.4 RCE: Anexo N.° 11 (archivo plano para comparar o reemplazar la propuesta del RCE), 41 campos y 39 libres (CLU).
COLUMNAS_RCE = [
    'RUC', 'Apellidos y Nombres o Razón social', 'Periodo', 'CAR SUNAT', 'Fecha de emisión', 'Fecha Vcto/Pago',
    'Tipo CP/Doc.', 'Serie del CDP', 'Año', 'Nro CP o Doc. Nro Inicial (Rango)', 'Nro Final (Rango)',
    'Tipo Doc Identidad', 'Nro Doc Identidad', 'Apellidos Nombres/ Razón Social', 'BI Gravado DG', 'IGV / IPM DG',
    'BI Gravado DGNG', 'IGV / IPM DGNG', 'BI Gravado DNG', 'IGV / IPM DNG', 'Valor Adq. NG', 'ISC', 'ICBPER',
    'Otros Trib/ Cargos', 'Total CP', 'Moneda', 'Tipo de Cambio', 'Fecha Emisión Doc Modificado',
    'Tipo CP Modificado', 'Serie CP Modificado', 'COD. DAM O DSI', 'Nro CP Modificado', 'Clasif de Bss y Sss',
    'ID Proyecto Operadores', 'PorcPart', 'IMB', 'CAR Orig/ Ind E o I', 'Detracción', 'Tipo de Nota', 'Est. Comp.',
    'Incal'] + [f'CLU{i}' for i in range(1, 40)]
# 8.5 RCE no domiciliados: Anexo N.° 9 (mismos datos que el PLE 8.2, precedidos del RUC, la razón social y el periodo).
COLUMNAS_ND = [
    'RUC', 'Razón social', 'Periodo', 'CAR SUNAT', 'Fecha de emisión', 'Tipo CP/Doc.', 'Serie del CDP', 'Nro CP o Doc.',
    'Valor de las adquisiciones', 'Otros conceptos adicionales', 'Importe total', 'Tipo CP crédito fiscal',
    'Serie CP crédito fiscal', 'Año DUA o DSI', 'Nro CP crédito fiscal', 'Monto de retención del IGV', 'Moneda',
    'Tipo de Cambio', 'País de residencia del no domiciliado', 'Apellidos y nombres o razón social del no domiciliado',
    'Domicilio en el extranjero', 'Nro identificación del no domiciliado', 'Nro identificación del beneficiario',
    'Apellidos y nombres o razón social del beneficiario', 'País de residencia del beneficiario',
    'Vínculo entre el contribuyente y el residente en el extranjero', 'Renta bruta',
    'Deducción / costo de enajenación de bienes de capital', 'Renta neta', 'Tasa de retención', 'Impuesto retenido',
    'Convenio para evitar la doble imposición', 'Exoneración aplicada', 'Tipo de renta', 'Modalidad del servicio',
    'Aplicación del penúltimo párrafo del art. 76 de la LIR', 'CAR Orig']
COLUMNAS = {'140400': COLUMNAS_RVIE, '080400': COLUMNAS_RCE, '080500': COLUMNAS_ND}


def num(valor):
    return f'{Decimal(valor or 0):.2f}'


def _t(texto):
    """Texto sin «|» ni saltos de línea (romperían las columnas del TXT)."""
    return ' '.join(str(texto or '').replace('|', ' ').split())


def _tc(d):
    return f'{(d.tipo_cambio if d.moneda == "USD" else Decimal("1")):.3f}'


def _referencia(d):
    ref = getattr(d, 'doc_referencia', None) if d.tipo_comprobante in ('07', '08') else None
    return ref


def car(d):
    """Código de Anotación de Registro del comprobante de compra: RUC del emisor + tipo + serie (4) + número (10)."""
    if d.tercero.tipo_doc != '6' or not d.tercero.numero_doc:
        return ''
    return f'{d.tercero.numero_doc}{d.tipo_comprobante}{(d.serie or "")[:4].rjust(4, "0")}{(d.numero or "").zfill(10)}'


def _fila_rvie(empresa, periodo, d):
    s = 0 if d.estado == 'ANULADO' else d.signo  # anulados y con baja: en cero (nota 3 del Anexo N.° 3)
    ref = _referencia(d)
    return {
        'Ruc': empresa.ruc, 'Apellidos y Nombres o Razón social': _t(empresa.razon_social), 'Periodo': periodo,
        'Fecha de emisión': fmt_fecha(d.fecha_emision),
        'Fecha Vcto/Pago': fmt_fecha(d.fecha_vencimiento) if d.tipo_comprobante == '14' else '',
        'Tipo CP/Doc.': d.tipo_comprobante, 'Serie del CDP': d.serie, 'Nro CP o Doc. Nro Inicial (Rango)': d.numero,
        'Tipo Doc Identidad': d.tercero.tipo_doc, 'Nro Doc Identidad': d.tercero.numero_doc,
        'Apellidos Nombres/ Razón Social': _t(d.tercero.nombre),
        'Valor Facturado Exportación': num(s * d.exportacion), 'BI Gravada': num(s * d.base_imponible),
        'Dscto BI': num(0), 'IGV / IPM': num(s * d.igv), 'Dscto IGV / IPM': num(0),
        'Mto Exonerado': num(s * d.exonerado), 'Mto Inafecto': num(s * d.inafecto), 'ISC': num(0),
        'BI Grav IVAP': num(0), 'IVAP': num(0), 'ICBPER': num(s * d.icbper), 'Otros Tributos': num(0),
        'Total CP': num(s * d.total), 'Moneda': d.moneda, 'Tipo Cambio': _tc(d),
        'Fecha Emisión Doc Modificado': fmt_fecha(ref.fecha_emision) if ref else '',
        'Tipo CP Modificado': ref.tipo_comprobante if ref else '', 'Serie CP Modificado': ref.serie if ref else '',
        'Nro CP Modificado': ref.numero if ref else '',
        'Tipo de Nota': getattr(d, 'motivo_nota', '') if ref else '',
    }


def _fila_rce(empresa, periodo, d):
    s = d.signo
    ref = _referencia(d)
    # DUA (50): la base del IGV de importación es el valor en aduana más los tributos (como el PLE 8.1)
    base = s * (d.base_imponible + (d.valor_aduana + d.no_gravado if d.es_dua else 0))
    igv = s * d.igv
    columnas = {'GRAVADA': ('BI Gravado DG', 'IGV / IPM DG'), 'COMUN': ('BI Gravado DGNG', 'IGV / IPM DGNG'),
                'NO_GRAVADA': ('BI Gravado DNG', 'IGV / IPM DNG')}.get(d.destino_igv, ('BI Gravado DG', 'IGV / IPM DG'))
    fila = {
        'RUC': empresa.ruc, 'Apellidos y Nombres o Razón social': _t(empresa.razon_social), 'Periodo': periodo,
        'CAR SUNAT': car(d), 'Fecha de emisión': fmt_fecha(d.fecha_emision),
        'Fecha Vcto/Pago': fmt_fecha(d.fecha_vencimiento) if d.tipo_comprobante == '14' else '',
        'Tipo CP/Doc.': d.tipo_comprobante, 'Serie del CDP': d.serie,
        'Año': str(d.fecha_emision.year) if d.es_dua else '', 'Nro CP o Doc. Nro Inicial (Rango)': d.numero,
        'Tipo Doc Identidad': d.tercero.tipo_doc, 'Nro Doc Identidad': d.tercero.numero_doc,
        'Apellidos Nombres/ Razón Social': _t(d.tercero.nombre),
        'BI Gravado DG': num(0), 'IGV / IPM DG': num(0), 'BI Gravado DGNG': num(0), 'IGV / IPM DGNG': num(0),
        'BI Gravado DNG': num(0), 'IGV / IPM DNG': num(0),
        'Valor Adq. NG': num(0 if d.es_dua else s * d.no_gravado), 'ISC': num(0), 'ICBPER': num(s * d.icbper),
        'Otros Trib/ Cargos': num(0), 'Total CP': num(s * d.total), 'Moneda': d.moneda, 'Tipo de Cambio': _tc(d),
        'Fecha Emisión Doc Modificado': fmt_fecha(ref.fecha_emision) if ref else '',
        'Tipo CP Modificado': ref.tipo_comprobante if ref else '', 'Serie CP Modificado': ref.serie if ref else '',
        'Nro CP Modificado': ref.numero if ref else '',
    }
    fila[columnas[0]], fila[columnas[1]] = num(base), num(igv)
    return fila


def _fila_nd(empresa, periodo, d):
    valor = d.base_imponible + d.no_gravado
    retenido = d.retencion_monto
    return {
        'RUC': empresa.ruc, 'Razón social': _t(empresa.razon_social), 'Periodo': periodo,
        'Fecha de emisión': fmt_fecha(d.fecha_emision), 'Tipo CP/Doc.': d.tipo_comprobante, 'Serie del CDP': d.serie,
        'Nro CP o Doc.': d.numero, 'Valor de las adquisiciones': num(valor),
        'Otros conceptos adicionales': num(d.total - valor - d.igv), 'Importe total': num(d.total),
        'Monto de retención del IGV': num(d.igv), 'Moneda': d.moneda, 'Tipo de Cambio': _tc(d),
        'País de residencia del no domiciliado': pais_sunat(d.tercero.pais),
        'Apellidos y nombres o razón social del no domiciliado': _t(d.tercero.nombre),
        'Domicilio en el extranjero': _t(d.tercero.direccion), 'Nro identificación del no domiciliado':
            d.tercero.numero_doc, 'Renta bruta': num(valor), 'Deducción / costo de enajenación de bienes de capital':
            num(0), 'Renta neta': num(valor), 'Impuesto retenido': num(retenido),
        'Tasa de retención': num(retenido * 100 / valor) if retenido and valor else '',
    }


FILAS = {'140400': _fila_rvie, '080400': _fila_rce, '080500': _fila_nd}

# Tabla 35 del Anexo N.° 3 de la RS 286-2009 (países): solo los más usados; en el maestro del proveedor se puede
# escribir el código de 4 dígitos directamente.
PAISES = {'ESTADOS UNIDOS': '9249', 'EEUU': '9249', 'USA': '9249', 'CHINA': '9215', 'ALEMANIA': '9023',
          'ESPAÑA': '9245', 'ESPANA': '9245', 'MEXICO': '9493', 'MÉXICO': '9493', 'BRASIL': '9105', 'CHILE': '9211',
          'COLOMBIA': '9169', 'ARGENTINA': '9063', 'CANADA': '9149', 'CANADÁ': '9149', 'REINO UNIDO': '9628',
          'FRANCIA': '9275', 'ITALIA': '9386', 'JAPON': '9399', 'JAPÓN': '9399', 'INDIA': '9361',
          'ECUADOR': '9239', 'BOLIVIA': '9097', 'PAISES BAJOS': '9573', 'IRLANDA': '9375'}


def pais_sunat(pais):
    texto = (pais or '').strip().upper()
    if texto.isdigit() and len(texto) == 4:
        return texto
    return PAISES.get(texto, '')


def linea(registro, empresa, periodo, d):
    fila = FILAS[registro](empresa, periodo, d)
    return '|'.join(str(fila.get(c, '')) for c in COLUMNAS[registro]) + '|'


# ---------------------------------------------------------------- nombre, datos y archivo
def nombre_archivo(ruc, periodo, registro, oportunidad='02', operaciones='1', contenido='1', moneda='1',
                   correlativo=None):
    """Nombre del TXT (sin el .zip). El ajuste posterior agrega su correlativo NN (01, 02…) del periodo."""
    nn = f'{int(correlativo or 1):02d}' if oportunidad in AJUSTES else ''
    return f'LE{ruc}{periodo}00{registro}{oportunidad}{operaciones}{contenido}{moneda}{GENERADO_POR_SIRE}{nn}.txt'


def documentos(registro, periodo, desde=None):
    """Comprobantes del periodo como en el registro PLE (sin saldos iniciales ni históricos migrados). Ventas anuladas
    van en cero; en compras la anulada no se anota. El 91 (no domiciliado) solo va al 8.5."""
    from compras.views import compras_views
    from ventas.views import ventas_views

    from .comprobantes import ComprobanteViews
    if registro == '140400':
        qs = ventas_views._registro_qs(periodo)
    elif registro == '080400':
        qs = compras_views._registro_qs(periodo).exclude(estado='ANULADO')
    else:
        qs = ComprobanteViews._registro_qs(compras_views, periodo).filter(tipo_comprobante='91') \
            .exclude(estado='ANULADO')
    if desde is not None:
        # >=: en Windows dos registros guardados en el mismo instante pueden tener la misma hora exacta; repetir un
        # comprobante en el ajuste es inocuo (SUNAT lo reemplaza), omitirlo no
        qs = qs.filter(creado__gte=desde)
    return list(qs)


def _fin_periodo(periodo):
    anio, mes = int(periodo[:4]), int(periodo[4:])
    return date(anio + mes // 12, mes % 12 + 1, 1)


def validar(registro, periodo, docs):
    """(errores, avisos) antes de enviar: campos obligatorios, totales, notas con su comprobante y periodo."""
    errores, avisos = [], []
    fin = _fin_periodo(periodo)
    for d in docs:
        nombre = f'{d.tipo_comprobante} {d.serie}-{d.numero}'.replace(' -', ' ')
        e = []
        if not d.numero:
            e.append('falta el número')
        if not d.serie and d.tipo_comprobante not in ('50', '91', '00'):
            e.append('falta la serie')
        if not d.fecha_emision:
            e.append('falta la fecha de emisión')
        elif d.fecha_emision >= fin and d.tipo_comprobante != '14':
            e.append(f'la fecha de emisión {fmt_fecha(d.fecha_emision)} es posterior al periodo')
        t = d.tercero
        if registro == '080500':
            if not t.nombre:
                e.append('falta el nombre del no domiciliado')
            if not t.numero_doc:
                e.append('falta el número de identificación del no domiciliado')
            if not pais_sunat(t.pais):
                avisos.append(f'{nombre}: el país del proveedor ({t.pais or "vacío"}) no tiene código SUNAT (tabla '
                              f'35); escriba el código de 4 dígitos en el maestro del proveedor.')
        elif d.tipo_comprobante == '01' or registro == '080400' and d.tipo_comprobante not in ('00', '12', '14', '50'):
            if t.tipo_doc != '6' or len(t.numero_doc or '') != 11 or not (t.numero_doc or '').isdigit():
                e.append(f'el {"cliente" if registro == "140400" else "proveedor"} debe tener RUC de 11 dígitos '
                         f'({t.tipo_doc} {t.numero_doc})')
            if not t.nombre:
                e.append('falta la razón social del tercero')
        if d.tipo_comprobante in ('07', '08') and not d.doc_referencia_id:
            e.append('la nota no indica el comprobante que modifica')
        if d.moneda == 'USD' and not d.tipo_cambio:
            e.append('falta el tipo de cambio')
        if d.estado != 'ANULADO':
            esperado = d.base_imponible + d.no_gravado + d.igv + d.icbper + getattr(d, 'ajuste_redondeo', D0)
            if abs(d.total - esperado) > TOLERANCIA:
                e.append(f'el total {d.total:,.2f} no es base + IGV + otros ({esperado:,.2f})')
            if d.total <= 0 and d.tipo_comprobante not in ('07',):
                e.append('el total debe ser mayor a cero')
        if e:
            errores.append(f'{nombre} ({t.nombre}): {"; ".join(e)}.')
    return errores, avisos


def generar(registro, periodo, oportunidad='02', operaciones='1', moneda='1', correlativo=None, docs=None,
            empresa=None):
    """{'nombre_txt', 'nombre_zip', 'txt', 'zip', 'filas', 'docs'} del registro del periodo."""
    empresa = empresa or Empresa.actual()
    docs = documentos(registro, periodo) if docs is None else docs
    lineas = [linea(registro, empresa, periodo, d) for d in docs]
    nombre = nombre_archivo(empresa.ruc, periodo, registro, oportunidad, operaciones, '1' if lineas else '0', moneda,
                            correlativo)
    txt = ''.join(f'{x}\r\n' for x in lineas)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(nombre, a_bytes(txt))
    return {'nombre_txt': nombre, 'nombre_zip': nombre[:-4] + '.zip', 'txt': txt, 'zip': buf.getvalue(),
            'filas': len(lineas), 'docs': docs}


def a_bytes(txt):
    """v1.115.1: el TXT del SIRE va en ISO-8859-1 (texto de Windows), como lo lee SUNAT. En UTF-8, una letra con
    tilde (la «Ú» de «BANCO BBVA PERÚ») hacía que SUNAT rechazara la fila: «número de campos incorrecto»."""
    return txt.encode('iso-8859-1', errors='replace')


# ---------------------------------------------------------------- SIR-03: exportar el registro en formato SIRE
def archivos_registro(codigo, periodo, empresa=None):
    """[(nombre_txt, txt)] del registro del periodo en el formato del SIRE: ventas 14.4; compras 8.4 y, si hay
    comprobantes de no domiciliados, también el 8.5."""
    codigos = [codigo] + (['080500'] if codigo == '080400' and documentos('080500', periodo) else [])
    return [(a['nombre_txt'], a['txt']) for a in (generar(c, periodo, empresa=empresa) for c in codigos)]


def respuesta_registro(codigo, periodo):
    """ZIP con los TXT del SIRE para descargar (como se suben al SIRE)."""
    from django.http import HttpResponse
    archivos = archivos_registro(codigo, periodo)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for nombre, txt in archivos:
            z.writestr(nombre, a_bytes(txt))
    resp = HttpResponse(buf.getvalue(), content_type='application/zip')
    resp['Content-Disposition'] = f'attachment; filename="{archivos[0][0][:-4]}.zip"'
    return resp


def leer_respuesta(contenido):
    """Líneas del reporte que SUNAT devuelve con el ticket (inconsistencias): TXT/CSV dentro de un ZIP o suelto."""
    datos = contenido or b''
    if datos[:2] == b'PK':
        try:
            with zipfile.ZipFile(io.BytesIO(datos)) as z:
                datos = b'\n'.join(z.read(n) for n in z.namelist() if not n.endswith('/'))
        except zipfile.BadZipFile:
            return []
    for codificacion in ('utf-8', 'latin-1'):
        try:
            texto = datos.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    return [x.strip() for x in texto.splitlines() if x.strip()][:500]
