"""Cronograma de obligaciones mensuales de SUNAT (IGV-Renta, PLE/SIRE, ITF) según el último dígito del RUC.

Fuente: SUNAT, «Cronograma de obligaciones mensuales – ejercicio 2026» (Anexo I de la R.S. N.º 281-2022/SUNAT),
https://www.sunat.gob.pe/orientacion/cronogramas/2026/cObligacionMensual2026.html
Para otro año se agrega su tabla aquí (SUNAT la publica cada diciembre)."""
from datetime import date

# periodo (AAAAMM) -> fechas por grupo de último dígito: 0, 1, 2-3, 4-5, 6-7, 8-9, buenos contribuyentes / UESP
_GRUPOS = ('0', '1', '23', '45', '67', '89', 'BC')
_TABLA = {
    '202601': ('2026-02-16', '2026-02-17', '2026-02-18', '2026-02-19', '2026-02-20', '2026-02-23', '2026-02-24'),
    '202602': ('2026-03-16', '2026-03-17', '2026-03-18', '2026-03-19', '2026-03-20', '2026-03-23', '2026-03-24'),
    '202603': ('2026-04-17', '2026-04-20', '2026-04-21', '2026-04-22', '2026-04-23', '2026-04-24', '2026-04-27'),
    '202604': ('2026-05-18', '2026-05-19', '2026-05-20', '2026-05-21', '2026-05-22', '2026-05-25', '2026-05-26'),
    '202605': ('2026-06-15', '2026-06-16', '2026-06-17', '2026-06-18', '2026-06-19', '2026-06-22', '2026-06-23'),
    '202606': ('2026-07-15', '2026-07-16', '2026-07-17', '2026-07-20', '2026-07-21', '2026-07-22', '2026-07-24'),
    '202607': ('2026-08-18', '2026-08-19', '2026-08-20', '2026-08-21', '2026-08-24', '2026-08-25', '2026-08-26'),
    '202608': ('2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18', '2026-09-21', '2026-09-22', '2026-09-23'),
    '202609': ('2026-10-16', '2026-10-19', '2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23', '2026-10-26'),
    '202610': ('2026-11-16', '2026-11-17', '2026-11-18', '2026-11-19', '2026-11-20', '2026-11-23', '2026-11-24'),
    '202611': ('2026-12-17', '2026-12-18', '2026-12-21', '2026-12-22', '2026-12-23', '2026-12-24', '2026-12-28'),
    '202612': ('2027-01-18', '2027-01-19', '2027-01-20', '2027-01-21', '2027-01-22', '2027-01-25', '2027-01-26'),
}


def grupo(ruc, buen_contribuyente=False):
    if buen_contribuyente:
        return 'BC'
    ultimo = (ruc or '')[-1:]
    return next((g for g in _GRUPOS if ultimo and ultimo in g), None)


def vencimiento(ruc, periodo, buen_contribuyente=False):
    """Fecha de vencimiento del periodo tributario (AAAAMM) para el RUC; None si el año no está cargado."""
    fechas = _TABLA.get(periodo)
    g = grupo(ruc, buen_contribuyente)
    if not fechas or g is None:
        return None
    return date.fromisoformat(fechas[_GRUPOS.index(g)])


def periodo_anterior(hoy):
    """Periodo tributario que se declara este mes (el mes anterior), AAAAMM."""
    return f'{hoy.year - 1}12' if hoy.month == 1 else f'{hoy.year}{hoy.month - 1:02d}'
