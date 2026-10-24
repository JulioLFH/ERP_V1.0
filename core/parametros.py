"""Parámetros legales y de política con vigencia (UIT, porcentajes, umbrales): el valor vigente a la fecha de cada
operación. Los valores por defecto están aquí con su norma; Ajustes › Parámetros tributarios registra valores nuevos
desde una fecha (quedan en la historia). Confirme los vigentes con su contador: las normas cambian."""
from datetime import date
from decimal import Decimal

from django.utils import timezone

# clave: (grupo, descripción, unidad, [(vigente desde, valor)], norma o sustento)
DEFECTOS = {
    'UIT': ('Generales', 'Unidad impositiva tributaria', 'S/',
            [('2024-01-01', '5150'), ('2025-01-01', '5350'), ('2026-01-01', '5500')],
            'Decreto supremo anual del MEF'),
    'INM_TERRENO_PCT': ('Inmobiliario', 'Terreno en el valor de la primera venta de inmuebles (no gravado)', '%',
                        [('2000-01-01', '50')], 'Reglamento de la Ley del IGV, art. 5 num. 9'),
    'INM_EXONERACION_UIT': ('Inmobiliario', 'Exoneración del IGV: primera venta de vivienda hasta (UIT); 0 = sin '
                                            'exoneración', 'UIT',
                            [('2016-01-01', '35'), ('2026-01-01', '0')],
                            'Apéndice I de la Ley del IGV (vigente hasta el 31/12/2025 salvo prórroga: si se prorrogó, '
                            'registre 35 desde la fecha)'),
    'INM_ARRAS_PCT': ('Inmobiliario', 'Arras de retractación sin IGV hasta (% del valor del inmueble)', '%',
                      [('2000-01-01', '3')], 'Reglamento de la Ley del IGV, art. 3'),
    'INM_PENALIDAD_PCT': ('Inmobiliario', 'Penalidad por desistimiento del comprador (% del precio, sugerida)', '%',
                          [('2000-01-01', '0')], 'Política de la empresa (contrato de compraventa)'),
    'INM_GARANTIA_PCT': ('Inmobiliario', 'Provisión de garantías postventa (% del ingreso de cada entrega)', '%',
                         [('2000-01-01', '0')], 'NIC 37: política de la empresa según su experiencia'),
    'BANCARIZACION_SOLES': ('Bancarización y lavado de activos', 'Bancarización: monto desde el que se usa un medio '
                                                                 'de pago (no efectivo)', 'S/',
                            [('2000-01-01', '2000')], 'Ley 28194 y modificatorias'),
    'UIF_RO_USD': ('Bancarización y lavado de activos', 'Registro de operaciones (UIF-SBS): operación individual '
                                                        'desde', 'US$', [('2000-01-01', '10000')],
                   'Normas de la SBS para sujetos obligados (actividad inmobiliaria)'),
    'UIF_RO_MULTIPLE_USD': ('Bancarización y lavado de activos', 'Registro de operaciones: operaciones múltiples de '
                                                                 'un cliente en el mes desde', 'US$',
                            [('2000-01-01', '50000')], 'Normas de la SBS para sujetos obligados'),
    'TC_REFERENCIAL': ('Bancarización y lavado de activos', 'Tipo de cambio referencial (si no hay el del día)', 'S/',
                       [('2000-01-01', '3.75')], 'Para comparar con los umbrales en dólares'),
    'RENTA_PAGO_CUENTA_PCT': ('Renta y libros', 'Pago a cuenta del Régimen General y MYPE sobre 300 UIT (o '
                                                'coeficiente)', '%', [('2000-01-01', '1.5')],
                              'Ley del Impuesto a la Renta, art. 85'),
    'RENTA_PAGO_CUENTA_MYPE_PCT': ('Renta y libros', 'Pago a cuenta MYPE Tributario hasta el límite de ingresos', '%',
                                   [('2017-01-01', '1')], 'D. Leg. 1269, art. 6'),
    'MYPE_LIMITE_UIT': ('Renta y libros', 'MYPE Tributario: ingresos hasta los que se paga a cuenta el 1 %', 'UIT',
                        [('2017-01-01', '300')], 'D. Leg. 1269, art. 6'),
    'RER_CUOTA_PCT': ('Renta y libros', 'Régimen Especial: cuota mensual sobre los ingresos netos', '%',
                      [('2000-01-01', '1.5')], 'Ley del Impuesto a la Renta, art. 120'),
    'LIBROS_SIMPLIFICADO_UIT': ('Renta y libros', 'Libros: registro de ventas, de compras y diario simplificado '
                                                  'hasta (ingresos anuales)', 'UIT', [('2017-01-01', '300')],
                                'Ley del Impuesto a la Renta, art. 65'),
    'LIBROS_MAYOR_UIT': ('Renta y libros', 'Libros: más diario y mayor hasta', 'UIT', [('2017-01-01', '500')],
                         'Ley del Impuesto a la Renta, art. 65'),
    'LIBROS_COMPLETA_UIT': ('Renta y libros', 'Libros: más inventarios y balances hasta (luego contabilidad '
                                              'completa)', 'UIT', [('2017-01-01', '1700')],
                            'Ley del Impuesto a la Renta, art. 65'),
    'SST_NOTIF_MORTAL_HORAS': ('Seguridad y salud en el trabajo', 'Notificar accidente mortal o incidente peligroso '
                                                                   'dentro de (horas)', 'h', [('2012-01-01', '24')],
                               'D.S. 005-2012-TR, art. 110 (confirmar con el especialista)'),
    'SST_NOTIF_ENFERMEDAD_DIAS': ('Seguridad y salud en el trabajo', 'Notificar enfermedad ocupacional dentro de '
                                                                      '(días hábiles)', 'días', [('2012-01-01', '5')],
                                  'D.S. 005-2012-TR, art. 110 (confirmar con el especialista)'),
    'SST_HORAS_MES': ('Seguridad y salud en el trabajo', 'Horas por trabajador al mes (si no se registran las '
                                                         'horas-hombre)', 'h', [('2000-01-01', '200')],
                      'Para estimar las horas-hombre de los índices'),
    'IGV_PRORRATA_EXCLUYE_TERRENO': ('IGV', 'Prorrata del crédito fiscal: no contar el terreno de la primera venta '
                                            'de inmuebles como operación no gravada (1 = sí, 0 = no)', '',
                                     [('2000-01-01', '1')], 'Reglamento de la Ley del IGV, art. 6 num. 6.2 '
                                                             '(confirmar con el contador)'),
}
GRUPOS = ['Generales', 'Inmobiliario', 'Bancarización y lavado de activos', 'Renta y libros', 'IGV',
          'Seguridad y salud en el trabajo']


def _fecha(f):
    return date.fromisoformat(f) if isinstance(f, str) else f


def historia(clave):
    """[(desde, valor, nota, propio)] del parámetro, del más reciente al más antiguo (propio = registrado en
    Ajustes; los otros son los valores por defecto)."""
    from .models import ParametroTributario
    _, _, _, defectos, norma = DEFECTOS[clave]
    propios = {p.desde: p for p in ParametroTributario.objects.filter(clave=clave)}
    filas = [(p.desde, p.valor, p.nota or norma, True) for p in propios.values()]
    filas += [(_fecha(d), Decimal(v), norma, False) for d, v in defectos if _fecha(d) not in propios]
    return sorted(filas, key=lambda f: f[0], reverse=True)


def valor(clave, fecha=None):
    """Valor vigente a la fecha (hoy si no se indica)."""
    fecha = fecha or timezone.localdate()
    for desde, v, _, _ in historia(clave):
        if desde <= fecha:
            return v
    return Decimal('0')


def uit(fecha=None):
    return valor('UIT', fecha)


def texto(v):
    """5500.0000 → '5500'; 1.5000 → '1.5'."""
    v = Decimal(v).normalize()
    return f'{v:f}'


def tabla(fecha=None):
    """Para la pantalla: por grupo, cada parámetro con su valor vigente y su historia."""
    fecha = fecha or timezone.localdate()
    salida = []
    for grupo in GRUPOS:
        filas = []
        for clave, (g, descripcion, unidad, _, norma) in DEFECTOS.items():
            if g != grupo:
                continue
            h = [(desde, texto(v), nota, propio) for desde, v, nota, propio in historia(clave)]
            vigente = next((f for f in h if f[0] <= fecha), None)
            filas.append({'clave': clave, 'descripcion': descripcion, 'unidad': unidad,
                          'norma': vigente[2] if vigente else norma, 'valor': vigente[1] if vigente else None,
                          'desde': vigente[0] if vigente else None, 'historia': h})
        salida.append((grupo, filas))
    return salida
