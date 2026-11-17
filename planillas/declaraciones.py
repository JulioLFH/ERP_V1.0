"""Archivos de importación para declarar el personal: T-Registro de la SUNAT (RP_<RUC>.ide/.tra/.per/.est/.edu)
y planilla de AFPnet («Declarar y pagar planillas»). PLA-05."""
import io
import zipfile
from decimal import Decimal

from .calculo import r2

D0 = Decimal('0')


def _f(fecha):
    return f'{fecha:%d/%m/%Y}' if fecha else ''


def _linea(campos):
    return '|'.join('' if c is None else str(c).replace('|', ' ') for c in campos) + '|'


# ---------------------------------------------------------------- T-Registro
# Estructuras de importación del T-Registro (SUNAT), campos separados por «|» (también al final), fechas dd/mm/aaaa.
# Las cinco empiezan con la identificación del trabajador: tipo de documento (tabla 3), número y país emisor del
# documento (tabla 26; solo pasaporte u otros documentos extranjeros: no se registra, va vacío).
#
# .ide · Datos de identificación (41 campos): 1-3 identificación | 4 fecha de nacimiento | 5 apellido paterno |
#   6 apellido materno | 7 nombres | 8 sexo (1 masculino / 2 femenino) | 9 nacionalidad (tabla 4: 9589 peruana) |
#   10 teléfono: código de larga distancia | 11 teléfono | 12 correo electrónico |
#   13-26 dirección 1: tipo de vía (tabla 5), nombre de vía, número, departamento, interior, manzana, lote,
#   kilómetro, block, etapa, tipo de zona (tabla 6), nombre de zona, referencia, ubigeo |
#   27-40 dirección 2 (mismos campos) | 41 dirección para el centro asistencial de EsSalud (1 = dirección 1)
# .tra · Datos del trabajador (23 campos): 1-3 identificación | 4 régimen laboral (tabla 33) |
#   5 situación educativa (tabla 9) | 6 ocupación (tabla 30) | 7 discapacidad (1/0) | 8 CUSPP |
#   9 SCTR pensión (0 no tiene) | 10 tipo de contrato (tabla 12) | 11 régimen alternativo, acumulativo o atípico
#   (1/0) | 12 jornada de trabajo máxima (1/0) | 13 horario nocturno (1/0) | 14 sindicalizado (1/0) |
#   15 periodicidad de la remuneración (tabla 13: 1 mensual, 3 semanal) | 16 remuneración básica inicial |
#   17 situación (tabla 15: 1 activo o subsidiado, 0 baja) | 18 rentas de 5ta exoneradas (1/0) |
#   19 situación especial (tabla 35: 0 ninguna) | 20 tipo de pago (tabla 16: 1 efectivo, 2 depósito en cuenta) |
#   21 categoría ocupacional (tabla 24: 02 obrero, 03 empleado) | 22 convenio para evitar la doble tributación
#   (0 ninguno) | 23 RUC (solo si además emite recibos por honorarios: vacío)
# .per · Periodos (9 campos, una línea por tipo de registro): 1-3 identificación | 4 categoría (1 trabajador) |
#   5 tipo de registro (1 periodo laboral, 2 tipo de trabajador, 3 régimen de salud, 4 régimen pensionario) |
#   6 fecha de inicio | 7 fecha de fin | 8 dato del tipo de registro: 1 → motivo de fin del periodo (tabla 17),
#   2 → tipo de trabajador (tabla 8), 3 → régimen de salud (tabla 32), 4 → régimen pensionario (tabla 11) |
#   9 EPS / servicios propios (solo régimen de salud con EPS: vacío)
# .est · Establecimiento donde labora (5 campos): 1-3 identificación | 4 RUC del empleador |
#   5 código del establecimiento (0000 = domicilio fiscal)
# .edu · Estudios concluidos (9 campos, solo trabajadores con educación superior completa): 1-3 identificación |
#   4 institución del Perú (1/0) | 5 régimen de la institución | 6 tipo de institución | 7 código de la
#   institución | 8 código de la carrera | 9 año de egreso. La ficha del trabajador no tiene estos datos: va sin
#   líneas y se completa en el T-Registro.
TR_DOC = {'01': '01', '04': '04', '07': '07'}
TR_REGIMEN = {'GENERAL': '01', 'MICRO': '16', 'PEQUENA': '17', 'CONSTRUCCION': '01'}  # tabla 33
TR_CONTRATO = {  # tabla 12 (las modalidades formativas no son contrato de trabajo: vacío)
    'INDETERMINADO': '01', 'PARCIAL': '02', 'INICIO_ACTIVIDAD': '03', 'NECESIDAD_MERCADO': '04',
    'RECONVERSION': '05', 'OCASIONAL': '06', 'SUPLENCIA': '07', 'EMERGENCIA': '08', 'OBRA': '09',
    'INTERMITENTE': '10', 'TEMPORADA': '11'}
TR_TIPO_TRABAJADOR = {'OBRERO': '20', 'EMPLEADO': '21'}  # tabla 8
TR_CATEGORIA = {'OBRERO': '02', 'EMPLEADO': '03'}  # tabla 24
TR_PENSION = {'ONP': '02', 'INTEGRA': '21', 'HORIZONTE': '22', 'PROFUTURO': '23', 'PRIMA': '24',
              'HABITAT': '25'}  # tabla 11
TR_SALUD_ESSALUD = '00'  # tabla 32: EsSalud regular
# tabla 17 (motivo de fin del periodo), por palabras del motivo de cese registrado; sin coincidencia: 01 renuncia
TR_MOTIVO_BAJA = [('INCENTIV', '02'), ('RENUNCIA', '01'), ('DESPID', '03'), ('DESTITU', '03'), ('COLECTIV', '04'),
                  ('JUBILA', '05'), ('INVALID', '06'), ('FALLEC', '07'), ('VENC', '08'), ('TERMIN', '08'),
                  ('MUTUO', '09')]
TR_VIA = [('AV', '01'), ('JR', '02'), ('JIRON', '02'), ('JIRÓN', '02'), ('CALLE', '03'), ('CAL', '03'),
          ('PSJE', '04'), ('PJE', '04'), ('PASAJE', '04'), ('ALAMEDA', '05'), ('MALECON', '06'), ('MALECÓN', '06'),
          ('OVALO', '07'), ('ÓVALO', '07'), ('PARQUE', '08'), ('PLAZA', '09'), ('CARRETERA', '10'),
          ('CARR', '10')]  # tabla 5; otro: 99


def _ident(t):
    return [TR_DOC.get(t.tipo_doc, '01'), t.numero_doc, '']


def _via(direccion):
    """(tipo de vía, nombre de vía) de la dirección en texto libre: «Av. Los Olivos 123» → (01, LOS OLIVOS 123)."""
    texto = ' '.join((direccion or '').split())
    if not texto:
        return '', ''
    primera, _, resto = texto.partition(' ')
    clave = primera.rstrip('.').upper()
    for prefijo, codigo in TR_VIA:
        if clave == prefijo:
            return codigo, resto.upper()[:50]
    return '99', texto.upper()[:50]


def _motivo_baja(t):
    if not t.fecha_cese:
        return ''
    motivo = (t.motivo_cese or '').upper()
    return next((codigo for palabra, codigo in TR_MOTIVO_BAJA if palabra in motivo), '01')


def _pension(t):
    if t.sistema_pensiones == 'ONP':
        return TR_PENSION['ONP']
    if t.sistema_pensiones == 'AFP' and t.afp_id:
        return TR_PENSION.get(t.afp.codigo.upper(), '')
    return ''


def lineas_t_registro(trabajador, contrato, alta, ruc):
    """{extensión: [líneas]} del trabajador. Las altas van en todos los archivos; las bajas solo en .per (con la
    fecha de fin del periodo y su motivo)."""
    t, ident = trabajador, _ident(trabajador)
    fin = _f(t.fecha_cese)
    lineas = {'per': []}
    inicio = _f(t.fecha_ingreso)
    lineas['per'].append(_linea(ident + ['1', '1', inicio, fin, _motivo_baja(t), '']))
    lineas['per'].append(_linea(ident + ['1', '2', inicio, fin, TR_TIPO_TRABAJADOR.get(t.tipo, '21'), '']))
    lineas['per'].append(_linea(ident + ['1', '3', inicio, fin, TR_SALUD_ESSALUD, '']))
    pension = _pension(t)
    if pension:
        lineas['per'].append(_linea(ident + ['1', '4', inicio, fin, pension, '']))
    if not alta:
        return lineas
    tipo_via, via = _via(t.direccion)
    direccion1 = [tipo_via, via] + [''] * 12  # número, interior, ubigeo… no están en la ficha
    telefono = ''.join(c for c in (t.telefono or '') if c.isdigit())
    lineas['ide'] = [_linea(ident + [
        _f(t.fecha_nacimiento), t.apellido_paterno.upper(), t.apellido_materno.upper(), t.nombres.upper(),
        {'M': '1', 'F': '2'}.get(t.sexo, ''), '9589' if t.tipo_doc == '01' else '', '', telefono[:9],
        t.email] + direccion1 + [''] * 14 + ['1' if via else ''])]
    lineas['tra'] = [_linea(ident + [
        TR_REGIMEN.get(t.regimen, '01'), '', '', '0', t.cuspp if t.sistema_pensiones == 'AFP' else '', '0',
        TR_CONTRATO.get(contrato.tipo, '') if contrato else '', '0', '1', '0', '0',
        '3' if t.regimen == 'CONSTRUCCION' else '1', f'{r2(t.sueldo):.2f}', '1' if not t.fecha_cese else '0', '0',
        '0', '2' if t.cuenta_sueldo else '1', TR_CATEGORIA.get(t.tipo, '03'), '0', ''])]
    lineas['est'] = [_linea(ident + [ruc, '0000'])]
    lineas['edu'] = []
    return lineas


def archivos_t_registro(desde, hasta, ruc):
    """(nombre del zip, bytes, n.° de trabajadores): RP_<RUC>.ide/.tra/.per/.est/.edu con las altas (ingreso en el
    rango) y las bajas (cese en el rango)."""
    from .models import Trabajador
    archivos = {'ide': [], 'tra': [], 'per': [], 'est': [], 'edu': []}
    n = 0
    for t in Trabajador.objects.filter(fecha_ingreso__isnull=False).select_related('afp').order_by(
            'apellido_paterno', 'apellido_materno', 'nombres'):
        alta = desde <= t.fecha_ingreso <= hasta
        baja = bool(t.fecha_cese and desde <= t.fecha_cese <= hasta)
        if not (alta or baja):
            continue
        n += 1
        for ext, lineas in lineas_t_registro(t, t.contratos.order_by('-inicio').first(), alta, ruc).items():
            archivos[ext] += lineas
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for ext, lineas in archivos.items():  # ANSI (Windows-1252): nombres con Ñ y tildes
            z.writestr(f'RP_{ruc}.{ext}', ('\r\n'.join(lineas) + ('\r\n' if lineas else '')).encode('cp1252',
                                                                                                    'replace'))
    return f'T-Registro_{desde:%Y%m%d}_{hasta:%Y%m%d}.zip', buf.getvalue(), n


# ---------------------------------------------------------------- AFPnet
# Planilla para «Declarar y pagar planillas» de AFPnet: Excel sin títulos ni encabezados, una fila por afiliado,
# 17 columnas: 1 secuencia | 2 CUSPP | 3 tipo de documento (0 DNI, 1 carné de extranjería, 4 pasaporte) |
# 4 número de documento | 5 apellido paterno | 6 apellido materno | 7 nombres | 8 relación laboral (S/N) |
# 9 inicio de la relación laboral en el mes (S/N) | 10 cese de la relación laboral en el mes (S/N) |
# 11 excepción de aportar (vacío si aporta; sin remuneración: U subsidio, L licencia sin goce) |
# 12 remuneración asegurable | 13 aporte voluntario con fin previsional | 14 aporte voluntario sin fin previsional |
# 15 aporte voluntario del empleador | 16 tipo de trabajo o rubro (N normal, C construcción civil) |
# 17 AFP del afiliado (código de la AFP en la configuración: HABITAT, INTEGRA, PRIMA, PROFUTURO)
AFPNET_DOC = {'01': '0', '04': '1', '07': '4'}


def filas_afpnet(periodo):
    from .calculo import rango
    from .models import FilaPlanilla
    desde, hasta = rango(periodo)
    por_trabajador = {}
    for f in (FilaPlanilla.objects.filter(planilla__periodo=periodo, planilla__tipo='MENSUAL',
                                          planilla__estado__in=('CALCULADA', 'CERRADA', 'PAGADA'),
                                          trabajador__sistema_pensiones='AFP')
              .select_related('trabajador__afp')):
        d = por_trabajador.setdefault(f.trabajador_id, {'t': f.trabajador, 'rem': D0, 'subsidio': D0})
        d['rem'] += f.remuneracion_afecta
        d['subsidio'] += f.dias_subsidio
    filas = []
    for i, d in enumerate(sorted(por_trabajador.values(), key=lambda x: (x['t'].apellido_paterno,
                                                                         x['t'].apellido_materno,
                                                                         x['t'].nombres)), 1):
        t = d['t']
        excepcion = '' if d['rem'] > 0 else ('U' if d['subsidio'] > 0 else 'L')
        filas.append([i, t.cuspp, AFPNET_DOC.get(t.tipo_doc, '0'), t.numero_doc, t.apellido_paterno.upper(),
                      t.apellido_materno.upper(), t.nombres.upper(), 'S',
                      'S' if t.fecha_ingreso and desde <= t.fecha_ingreso <= hasta else 'N',
                      'S' if t.fecha_cese and desde <= t.fecha_cese <= hasta else 'N', excepcion,
                      float(r2(d['rem'])), 0.0, 0.0, 0.0, 'C' if t.regimen == 'CONSTRUCCION' else 'N',
                      t.afp.codigo.upper() if t.afp_id else ''])
    return filas


def excel_afpnet(periodo):
    """Bytes del Excel (.xlsx) para cargar en AFPnet: solo las filas, sin títulos."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = 'AFPnet'
    for fila in filas_afpnet(periodo):
        ws.append(fila)
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = 's'  # documento y CUSPP como texto (conservan los ceros a la izquierda)
            elif isinstance(cell.value, float):
                cell.number_format = '0.00'
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
