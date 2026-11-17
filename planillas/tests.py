"""Planillas: cálculo mensual (ONP/AFP, quinta, EsSalud), gratificación, CTS, cierre, contabilidad, pago y PLAME."""
import io
import zipfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import AsientoLinea, CentroCosto
from finanzas.models import Cuenta

from . import calculo, servicios
from .models import AFP, Planilla, Trabajador

D = Decimal


class PlanillaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('rrhh', 'r@h.pe', 'x')

    def setUp(self):
        self.client.force_login(self.admin)
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.integra = AFP.objects.get(codigo='INTEGRA')
        self.planta = CentroCosto.objects.create(codigo='PL', nombre='Planta', tipo='PRODUCCION')
        self.ana = Trabajador.objects.create(
            numero_doc='45678912', apellido_paterno='QUISPE', apellido_materno='MAMANI', nombres='ANA',
            fecha_ingreso=date(2024, 3, 1), sueldo=D('3000'), asignacion_familiar=True, sistema_pensiones='AFP',
            afp=self.integra, cuspp='123456ABCDE1', centro_costo=self.planta)
        self.luis = Trabajador.objects.create(
            numero_doc='41234567', apellido_paterno='ROJAS', nombres='LUIS', fecha_ingreso=date(2025, 1, 16),
            sueldo=D('1500'), sistema_pensiones='ONP',
            centro_costo=CentroCosto.objects.create(codigo='AD', nombre='Administración', tipo='ADMINISTRACION'))

    def _planilla(self, tipo, periodo):
        p = Planilla.objects.create(tipo=tipo, periodo=periodo)
        calculo.generar_filas(p)
        calculo.calcular(p)
        return p

    def test_mensual_afp_onp_quinta_essalud_y_prorrateo(self):
        p = self._planilla('MENSUAL', '202501')
        ana = p.filas.get(trabajador=self.ana)
        # 3000 + asignación familiar 113 (10% de RMV 1130) = 3113
        self.assertEqual((ana.monto('BASICO'), ana.monto('ASIG_FAMILIAR'), ana.remuneracion_afecta),
                         (D('3000.00'), D('113.00'), D('3113.00')))
        self.assertEqual((ana.monto('AFP_APORTE'), ana.monto('AFP_PRIMA'), ana.monto('AFP_COMISION')),
                         (D('311.30'), D('42.65'), D('48.25')))
        self.assertEqual(ana.monto('ESSALUD'), D('280.17'))
        # quinta: 3113 × 12 + 2 gratificaciones × 3113 × 1.09 = 44,142.34 − 7 UIT (37,450) = 6,692.34 × 8% / 12
        self.assertEqual(ana.monto('QUINTA'), D('44.62'))
        self.assertEqual(ana.neto, D('3113.00') - D('311.30') - D('42.65') - D('48.25') - D('44.62'))
        # Luis ingresó el 16: 15 días; ONP 13%; EsSalud con base mínima la RMV
        luis = p.filas.get(trabajador=self.luis)
        self.assertEqual((luis.dias_laborados, luis.monto('BASICO'), luis.monto('ONP')),
                         (D('15'), D('750.00'), D('97.50')))
        self.assertEqual(luis.monto('ESSALUD'), D('101.70'))  # 9% de 1130
        self.assertEqual(luis.monto('QUINTA'), D('0'))

    def test_faltas_vacaciones_horas_extra(self):
        p = Planilla.objects.create(tipo='MENSUAL', periodo='202502')
        calculo.generar_filas(p)
        f = p.filas.get(trabajador=self.ana)
        f.dias_falta, f.dias_vacaciones, f.horas_extra_25 = D('2'), D('10'), D('4')
        f.save()
        calculo.calcular(p)
        f.refresh_from_db()
        self.assertEqual((f.monto('BASICO'), f.monto('VACACIONES')), (D('1800.00'), D('1000.00')))
        # valor hora (3000 + 113) / 30 / 8 = 12.9708 × 1.25 × 4 = 64.85
        self.assertEqual(f.monto('HORAS_EXTRA_25'), D('64.85'))

    def test_gratificacion_y_cts(self):
        grat = self._planilla('GRATIFICACION', '202507')
        ana = grat.filas.get(trabajador=self.ana)
        self.assertEqual((ana.monto('GRATIFICACION'), ana.monto('BONIF_EXTRA')), (D('3113.00'), D('280.17')))
        luis = grat.filas.get(trabajador=self.luis)  # ingresó el 16/01: 5 meses completos (feb-jun)
        self.assertEqual(luis.monto('GRATIFICACION'), D('1250.00'))
        cts = self._planilla('CTS', '202511')
        ana = cts.filas.get(trabajador=self.ana)
        # (3113 + 3113/6) / 12 × 6 meses = 1,815.92
        self.assertEqual(ana.monto('CTS'), D('1815.92'))
        # régimen de microempresa: sin gratificación ni CTS
        self.luis.regimen = 'MICRO'
        self.luis.save()
        grat2 = self._planilla('GRATIFICACION', '202512')
        self.assertEqual(grat2.filas.get(trabajador=self.luis).total_ingresos, D('0'))

    def test_quinta_resta_retenciones_previas(self):
        for mes in ('01', '02', '03', '04'):
            p = self._planilla('MENSUAL', f'2025{mes}')
            servicios.cerrar(p)
        abril = Planilla.objects.get(periodo='202504').filas.get(trabajador=self.ana)
        # (impuesto anual 535.39 − 3 × 44.62 retenido) / 9 = 44.61
        self.assertEqual(abril.monto('QUINTA'), D('44.61'))

    def test_quinta_con_datos_previos_al_sistema(self):
        # empieza a usar el sistema en setiembre: de enero a agosto percibió 8 × 3113 + gratificación de julio
        # (3113 + 9%) y le retuvieron 8 × 44.62
        self.ana.quinta_anio = 2025
        self.ana.quinta_remuneracion_previa = D('3113') * 8 + D('3393.17')
        self.ana.quinta_retencion_previa = D('356.96')
        self.ana.save()
        p = self._planilla('MENSUAL', '202509')
        # misma proyección anual (44,142.34 → impuesto 535.39); setiembre: (535.39 − 356.96) / 4
        self.assertEqual(p.filas.get(trabajador=self.ana).monto('QUINTA'), D('44.61'))

    def test_cierre_contabilidad_pago_y_plame(self):
        p = self._planilla('MENSUAL', '202509')
        servicios.cerrar(p)
        centralizar_periodo('202509')
        lineas = AsientoLinea.objects.filter(asiento__origen='PLANILLA', asiento__periodo='202509')
        self.assertTrue(lineas.exists())
        self.assertEqual(sum(l.debe for l in lineas), sum(l.haber for l in lineas))
        neto = lineas.get(cuenta__codigo='4111').haber
        self.assertEqual(neto, p.totales()['n'])
        self.assertEqual(lineas.get(cuenta__codigo='4031').haber, p.filas.get(trabajador=self.ana).monto('ESSALUD') +
                         p.filas.get(trabajador=self.luis).monto('ESSALUD'))
        self.assertTrue(lineas.filter(cuenta__codigo__startswith='90', es_destino=True).exists())  # planta → 90
        banco = Cuenta.objects.filter(tipo='BANCO', moneda='PEN').first()
        banco.permite_sobregiro = True
        banco.save()
        mov = servicios.pagar(p, banco, date(2025, 9, 30), self.admin)
        p.refresh_from_db()
        self.assertEqual((p.estado, mov.monto, mov.concepto), ('PAGADA', neto, 'PLANILLA'))
        with self.assertRaises(calculo.ErrorPlanilla):
            servicios.reabrir(p)
        mov.estado = 'ANULADO'
        mov.save()
        p.refresh_from_db()
        self.assertEqual(p.estado, 'CERRADA')
        nombre, datos = servicios.archivos_plame('202509', '20100000001')
        z = zipfile.ZipFile(io.BytesIO(datos))
        rem = z.read('0601202509' + '20100000001.rem').decode()
        self.assertIn('01|45678912|0121|3000.00|3000.00|', rem)
        # PLA-01: ONP (0607) y EsSalud (0804) los calcula el PDT: no van en el .rem; los de la AFP sí
        self.assertNotIn('|0607|', rem)
        self.assertNotIn('|0804|', rem)
        for codigo in ('0601', '0606', '0608'):
            self.assertIn(f'01|45678912|{codigo}|', rem)
        self.assertIn('01|45678912|0608|311.30|311.30|', rem)
        self.assertIn('01|45678912|240|0|0|0|', z.read('0601202509' + '20100000001.jor').decode())
        # sin recibos por honorarios pagados en el mes no van los archivos de 4ta
        self.assertFalse([n for n in z.namelist() if n.endswith(('.ps4', '.4ta'))])

    def test_plame_cuarta_categoria(self):
        """PLA-04: .ps4 y .4ta con los recibos por honorarios pagados en el periodo (con y sin retención); el
        recibo con saldo no va."""
        from compras.models import Compra, CompraItem
        from core.models import Tercero
        from finanzas.models import Movimiento
        self._planilla('MENSUAL', '202509')
        banco = Cuenta.objects.filter(tipo='BANCO', moneda='PEN').first()
        banco.permite_sobregiro = True
        banco.save()

        def recibo(tipo_doc, doc, nombre, numero, monto, emision):
            prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc=tipo_doc, numero_doc=doc, nombre=nombre)
            r = Compra.objects.create(tipo_comprobante='02', serie='E001', numero=numero, tercero=prov,
                                      fecha_emision=emision, periodo='202509', clasificacion='GASTO')
            CompraItem.objects.create(documento=r, descripcion='Servicio', cantidad=1, precio_unitario=D(monto))
            r.calcular_totales()
            r.save()
            return r

        def pagar(r, monto, fecha):
            Movimiento.objects.create(cuenta=banco, fecha=fecha, tipo='EGRESO', concepto='PAGO', monto=D(monto),
                                      compra=r)

        con_ret = recibo('6', '10406080901', 'PEREZ GOMEZ JUAN CARLOS', '1', '2000', date(2025, 9, 10))
        self.assertEqual(con_ret.retencion_monto, D('160.00'))  # 8 % (supera S/ 1,500)
        pagar(con_ret, '1000', date(2025, 9, 12))
        pagar(con_ret, '840', date(2025, 9, 15))  # completa el neto 1,840 el 15
        sin_ret = recibo('1', '40111222', 'RUIZ DIAZ, MARIA', '2', '1000', date(2025, 9, 12))
        self.assertEqual(sin_ret.retencion_monto, D('0'))
        pagar(sin_ret, '1000', date(2025, 9, 20))
        pendiente = recibo('1', '40999888', 'SALAS PINO ROSA', '3', '500', date(2025, 9, 5))
        pagar(pendiente, '100', date(2025, 9, 6))  # pago parcial: no va
        _, datos = servicios.archivos_plame('202509', '20100000001')
        z = zipfile.ZipFile(io.BytesIO(datos))
        ps4 = z.read('0601202509' + '20100000001.ps4').decode('cp1252').splitlines()
        cuarta = z.read('0601202509' + '20100000001.4ta').decode('cp1252').splitlines()
        self.assertEqual(ps4, ['01|40111222|RUIZ|DIAZ|MARIA|1|0|', '01|40608090|PEREZ|GOMEZ|JUAN CARLOS|1|0|'])
        self.assertEqual(cuarta, ['01|40608090|R|E001|1|2000.00|10/09/2025|15/09/2025|1|||',
                                  '01|40111222|R|E001|2|1000.00|12/09/2025|20/09/2025|0|||'])
        self.assertNotIn('40999888', ''.join(ps4 + cuarta))

    def test_t_registro_archivos(self):
        """PLA-05: RP_<RUC>.ide/.tra/.per/.est/.edu con las altas y bajas del rango."""
        from core.models import Empresa
        ruc = Empresa.actual().ruc
        self.luis.sexo, self.luis.direccion = 'M', 'Av. Los Olivos 123'
        self.luis.save()
        r = self.client.get(reverse('planillas:t_registro'), {'formato': 'zip', 'desde': '2025-01-01',
                                                              'hasta': '2025-01-31'})
        self.assertEqual(r['Content-Type'], 'application/zip')
        z = zipfile.ZipFile(io.BytesIO(r.content))
        self.assertEqual(sorted(z.namelist()), sorted(f'RP_{ruc}.{e}' for e in ('ide', 'tra', 'per', 'est', 'edu')))
        leer = lambda e: z.read(f'RP_{ruc}.{e}').decode('cp1252').splitlines()  # noqa: E731
        ide, tra, per, est = leer('ide'), leer('tra'), leer('per'), leer('est')
        # solo Luis ingresó en el rango (Ana ingresó en 2024)
        self.assertEqual(len(ide), 1)
        self.assertNotIn('45678912', ''.join(ide + tra + per + est))
        campos = ide[0].split('|')[:-1]
        self.assertEqual(len(campos), 41)
        self.assertEqual(campos[:9], ['01', '41234567', '', '', 'ROJAS', '', 'LUIS', '1', '9589'])
        self.assertEqual(campos[12:14], ['01', 'LOS OLIVOS 123'])
        campos = tra[0].split('|')[:-1]
        self.assertEqual(len(campos), 23)
        self.assertEqual((campos[3], campos[15], campos[20]), ('01', '1500.00', '03'))
        self.assertEqual(per, ['01|41234567||1|1|16/01/2025||||', '01|41234567||1|2|16/01/2025||21||',
                               '01|41234567||1|3|16/01/2025||00||', '01|41234567||1|4|16/01/2025||02||'])
        self.assertEqual(est, [f'01|41234567||{ruc}|0000|'])
        self.assertEqual(leer('edu'), [])
        # baja en el rango: solo sus periodos, con la fecha de fin y el motivo
        self.ana.fecha_cese, self.ana.motivo_cese = date(2025, 2, 28), 'Renuncia voluntaria'
        self.ana.save()
        r = self.client.get(reverse('planillas:t_registro'), {'formato': 'zip', 'desde': '2025-02-01',
                                                              'hasta': '2025-02-28'})
        z = zipfile.ZipFile(io.BytesIO(r.content))
        self.assertEqual(leer('ide'), [])
        self.assertIn('01|45678912||1|1|01/03/2024|28/02/2025|01||', leer('per'))
        self.assertIn('01|45678912||1|4|01/03/2024|28/02/2025|21||', leer('per'))  # AFP Integra
        # la pantalla sigue dando el Excel
        self.assertIn('spreadsheetml', self.client.get(reverse('planillas:t_registro'))['Content-Type'])

    def test_archivo_afpnet(self):
        """PLA-05: Excel de AFPnet sin títulos, 17 columnas y una fila por afiliado del mes."""
        from openpyxl import load_workbook
        Trabajador.objects.create(
            numero_doc='70123456', apellido_paterno='ALVA', apellido_materno='SOTO', nombres='PEDRO',
            fecha_ingreso=date(2025, 9, 10), sueldo=D('2000'), sistema_pensiones='AFP',
            afp=AFP.objects.get(codigo='PRIMA'), cuspp='654321ZYXWV9', centro_costo=self.planta)
        self._planilla('MENSUAL', '202509')
        r = self.client.get(reverse('planillas:afp', args=['202509']), {'formato': 'afpnet'})
        self.assertIn('spreadsheetml', r['Content-Type'])
        filas = [list(f) for f in load_workbook(io.BytesIO(r.content)).active.iter_rows(values_only=True)]
        self.assertEqual(len(filas), 2)  # Ana y Pedro (Luis es de la ONP); sin filas de título
        self.assertTrue(all(len(f) == 17 for f in filas))
        self.assertEqual([f[0] for f in filas], [1, 2])
        pedro, ana = filas
        self.assertEqual(ana[1:10], ['123456ABCDE1', '0', '45678912', 'QUISPE', 'MAMANI', 'ANA', 'S', 'N', 'N'])
        self.assertIn(ana[10], (None, ''))
        self.assertEqual((ana[11], ana[15], ana[16]), (3113.0, 'N', 'INTEGRA'))
        self.assertEqual((pedro[3], pedro[8], pedro[16]), ('70123456', 'S', 'PRIMA'))
        # el Excel de consulta se mantiene
        r = self.client.get(reverse('planillas:afp', args=['202509']))
        self.assertIn('spreadsheetml', r['Content-Type'])

    def test_pantallas(self):
        r = self.client.post(reverse('planillas:nueva'), {'tipo': 'MENSUAL', 'mes': '2025-10'})
        p = Planilla.objects.get(periodo='202510')
        self.assertRedirects(r, reverse('planillas:detalle', args=[p.pk]))
        f = p.filas.get(trabajador=self.ana)
        datos = {'filas-TOTAL_FORMS': '2', 'filas-INITIAL_FORMS': '2', 'accion': 'calcular'}
        for i, fila in enumerate(p.filas.all()):
            datos.update({f'filas-{i}-id': fila.pk, f'filas-{i}-dias_laborados': '30', f'filas-{i}-dias_falta': '0',
                          f'filas-{i}-dias_vacaciones': '0', f'filas-{i}-dias_subsidio': '0',
                          f'filas-{i}-horas_extra_25': '0', f'filas-{i}-horas_extra_35': '0',
                          f'filas-{i}-otros_ingresos': '100' if fila == f else '0',
                          f'filas-{i}-ingresos_no_afectos': '0', f'filas-{i}-adelantos': '200' if fila == f else '0',
                          f'filas-{i}-otros_descuentos': '0'})
        self.client.post(reverse('planillas:detalle', args=[p.pk]), datos)
        f.refresh_from_db()
        self.assertEqual((f.monto('OTROS_AFECTOS'), f.monto('ADELANTO'), f.remuneracion_afecta),
                         (D('100.00'), D('200.00'), D('3213.00')))
        self.assertContains(self.client.get(reverse('planillas:detalle', args=[p.pk])), 'QUISPE')
        self.assertContains(self.client.get(reverse('planillas:boletas', args=[p.pk])), 'Neto a pagar')
        self.assertIn('spreadsheetml', self.client.get(reverse('planillas:detalle', args=[p.pk]),
                                                       {'formato': 'excel'})['Content-Type'])
        self.assertContains(self.client.get(reverse('planillas:trabajadores')), 'QUISPE MAMANI, ANA')
        self.assertEqual(self.client.get(reverse('planillas:configuracion')).status_code, 200)
        # validación: gratificación solo en julio o diciembre
        r = self.client.post(reverse('planillas:nueva'), {'tipo': 'GRATIFICACION', 'mes': '2025-08'})
        self.assertContains(r, 'julio o diciembre')
