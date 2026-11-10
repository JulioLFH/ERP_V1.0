"""REQ-02: SUNAT › Cronograma de vencimientos. El administrador carga cada año las fechas de las obligaciones mensuales
(IGV-Renta y PLE / SIRE) por último dígito del RUC; el resto lo consulta. Es del grupo: lo usan todas las empresas."""
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from . import cronograma_sunat as cs
from .models import CronogramaSunat, Empresa

MESES = ('Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre',
         'Noviembre', 'Diciembre')
GRUPOS = (('d0', '0'), ('d1', '1'), ('d23', '2 y 3'), ('d45', '4 y 5'), ('d67', '6 y 7'), ('d89', '8 y 9'),
          ('bc', 'Buenos contrib.'))


def _anio(request):
    try:
        anio = int(request.GET.get('anio') or request.POST.get('anio') or timezone.localdate().year)
    except ValueError:
        anio = timezone.localdate().year
    return min(max(anio, 2020), 2100)


@login_required
def cronograma(request):
    anio = _anio(request)
    if request.method == 'POST':
        if not request.user.is_superuser:
            return render(request, 'core/sin_acceso.html',
                          {'motivo': 'Solo un administrador carga o modifica el cronograma de SUNAT.'}, status=403)
        guardados, borrados, errores = 0, 0, []
        for mes in range(1, 13):
            periodo = f'{anio}{mes:02d}'
            valores = {}
            for campo, _ in GRUPOS:
                texto = (request.POST.get(f'{periodo}_{campo}') or '').strip()
                try:
                    valores[campo] = date.fromisoformat(texto) if texto else None
                except ValueError:
                    valores[campo] = None
                    errores.append(f'{MESES[mes - 1]}: fecha no válida')
            llenos = [v for v in valores.values() if v]
            if not llenos:  # fila vacía: vuelve a la tabla del sistema (si la hay)
                borrados += CronogramaSunat.objects.filter(periodo=periodo).delete()[0]
                continue
            if len(llenos) < len(GRUPOS):
                errores.append(f'{MESES[mes - 1]}: complete las 7 fechas o deje la fila vacía')
                continue
            if any(v <= date(anio, mes, 1) for v in llenos):
                errores.append(f'{MESES[mes - 1]}: las fechas deben ser posteriores al periodo')
                continue
            CronogramaSunat.objects.update_or_create(periodo=periodo, defaults={
                **valores, 'modificado_por': request.user.username})
            guardados += 1
        if errores:
            messages.error(request, 'No se guardaron algunos meses: ' + '; '.join(dict.fromkeys(errores)) + '.')
        messages.success(request, f'Cronograma {anio}: {guardados} mes(es) guardados'
                                  + (f', {borrados} vuelto(s) a la tabla del sistema' if borrados else '') + '.')
        return redirect(f'{reverse("cronograma_sunat")}?anio={anio}')
    empresa = Empresa.actual()
    mi_grupo = cs.grupo(empresa.ruc, empresa.buen_contribuyente)
    campo_mio = dict(zip(cs._GRUPOS, (c for c, _ in GRUPOS))).get(mi_grupo)
    hoy = timezone.localdate()
    filas = []
    for mes in range(1, 13):
        periodo = f'{anio}{mes:02d}'
        lista, origen = cs.fechas(periodo)
        valores = dict(zip((c for c, _ in GRUPOS), lista)) if lista else {}
        mia = valores.get(campo_mio)
        filas.append({'periodo': periodo, 'mes': MESES[mes - 1], 'origen': origen,
                      'celdas': [{'campo': c, 'fecha': valores.get(c), 'mia': c == campo_mio} for c, _ in GRUPOS],
                      'mia': mia, 'dias': (mia - hoy).days if mia else None})
    return render(request, 'core/cronograma_sunat.html', {
        'anio': anio, 'filas': filas, 'grupos': GRUPOS, 'empresa': empresa, 'mi_grupo': mi_grupo,
        'admin': request.user.is_superuser, 'faltan': sum(1 for f in filas if not f['origen'])})
