"""v1.87: correcciones de la revisión de la v1.86 (OBR-25 a OBR-28, HR-01, HR-08, CON-14, SRV-15, SRV-05, PLAT-06,
INV-30, LIS-01, CRM-19) y la desviación lenta con miles de órdenes."""
import io
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from obras import ejecucion as ej
from obras import gestion as g
from obras.models import (AvancePartida, ParteEquipo, Partida, Presupuesto, ProgramacionPartida, Subpresupuesto)
from obras.tests_comercial import HOY
from obras.tests_ejecucion import EjecucionBase


def per(fecha):
    return f'{fecha:%Y%m}'


class ConGastosGenerales(EjecucionBase):
    """Contractual con subpresupuesto de estructuras (01 Excavación 5,000 y 02 Concreto 10,000) y uno de gastos
    generales detallados con el mismo ítem 01 (Residente 3,000)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from obras import calculos
        cls.est = Subpresupuesto.objects.create(presupuesto=cls.p, codigo='001', nombre='Estructuras')
        cls.gg = Subpresupuesto.objects.create(presupuesto=cls.p, codigo='002', nombre='Gastos generales',
                                               es_gastos_generales=True)
        Partida.objects.filter(pk__in=[cls.a.pk, cls.b.pk]).update(subpresupuesto=cls.est)
        cls.r = Partida.objects.create(presupuesto=cls.p, subpresupuesto=cls.gg, item='01', descripcion='Residente',
                                       unidad='mes', metrado=D('3'), precio_fijo=True, precio=D('1000'))
        calculos.recalcular(cls.p)
        cls.p.refresh_from_db()
        cls.a.refresh_from_db()


class ProyeccionTest(ConGastosGenerales):
    def test_gastos_generales_no_se_cuentan_dos_veces(self):
        """OBR-25: costo directo 15,000 (sin el subpresupuesto de GG), GG 3,000; venta 19,500 (CD + GG + 10 %)."""
        self.assertEqual((self.p.costo_directo, self.p.gastos_generales), (D('15000.00'), D('3000.00')))
        c = g.control_partidas(self.obra)
        self.assertEqual(c['totales']['bac'], D('15000.00'))
        self.assertNotIn(self.r.pk, [f['partida'].pk for f in c['filas']])
        self.assertEqual(c['margen_presupuestado'], self.p.subtotal - D('18000'))  # = la utilidad del presupuesto
        self.assertEqual(c['margen_presupuestado_pct'], D('7.7'))
        s = g.sensibilidad(self.p)
        self.assertEqual(s['margen'], c['margen_presupuestado'])  # igual que en la sensibilidad

    def test_una_sola_fuente_del_costo_y_del_eac(self):
        """OBR-26 y OBR-27: el costo real y el costo al cierre son los mismos en cartera, valor ganado y proyección;
        el gasto en la partida de GG no entra al costo directo."""
        AvancePartida.objects.create(obra=self.obra, partida=self.a, fecha=HOY, metrado=D('50'))
        ParteEquipo.objects.create(obra=self.obra, fecha=HOY, descripcion='Excavadora', propiedad='ALQUILADO',
                                   partida=self.a, horas=D('30'), tarifa=D('100'))
        ParteEquipo.objects.create(obra=self.obra, fecha=HOY, descripcion='Camioneta', propiedad='ALQUILADO',
                                   partida=self.r, horas=D('10'), tarifa=D('50'))
        ProgramacionPartida.objects.create(partida=self.a, periodo=per(HOY), metrado=D('100'))
        ProgramacionPartida.objects.create(partida=self.r, periodo=per(HOY), metrado=D('1'))
        c = g.control_partidas(self.obra)
        vg = g.valor_ganado(self.obra)
        fila = next(f for f in g.cartera()['filas'] if f['obra'].pk == self.obra.pk)
        self.assertEqual((c['totales']['ac'], c['gg_real'], c['costo_real']), (D('3000.00'), D('500.00'),
                                                                               D('3500.00')))
        self.assertEqual(vg['ac'], c['totales']['ac'])
        self.assertEqual(fila['costo'], c['costo_real'])
        self.assertEqual(vg['eac'], c['totales']['eac'])
        self.assertEqual((vg['pv'], vg['ev']), (D('5000.00'), D('2500.00')))  # sin la partida de GG
        self.assertEqual(vg['eac_indice'], D('18000.00'))  # 15,000 × 3,000 / 2,500 con el CPI sin redondear

    def test_cronograma_por_subpresupuesto(self):
        """OBR-28: el ítem 01 está en estructuras y en GG: va a la de costo directo; con el prefijo, al indicado."""
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(['Ítem', 'Descripción', HOY.strftime('%Y-%m')])
        ws.append(['01', '', 100])
        ws.append(['002.01', '', 1])
        buf = io.BytesIO()
        wb.save(buf)
        n, faltan = ej.importar_cronograma(self.p, SimpleUploadedFile('c.xlsx', buf.getvalue()))
        self.assertEqual((n, faltan), (2, []))
        self.assertEqual(ProgramacionPartida.objects.get(partida=self.a).metrado, D('100'))
        self.assertEqual(ProgramacionPartida.objects.get(partida=self.r).metrado, D('1'))
        # dos partidas de costo directo con el mismo ítem: no reemplaza nada sin confirmar
        otro = Subpresupuesto.objects.create(presupuesto=self.p, codigo='003', nombre='Obras de arte')
        Partida.objects.create(presupuesto=self.p, subpresupuesto=otro, item='01', descripcion='Encofrado',
                               unidad='m2', metrado=D('10'), precio_fijo=True, precio=D('10'))
        with self.assertRaisesMessage(ej.ErrorEjecucion, 'No se reemplazó el cronograma'):
            ej.importar_cronograma(self.p, SimpleUploadedFile('c.xlsx', buf.getvalue()))
        n, faltan = ej.importar_cronograma(self.p, SimpleUploadedFile('c.xlsx', buf.getvalue()), forzar=True)
        self.assertEqual((n, faltan), (1, ['01 (ambigua)']))


class ProductividadTest(EjecucionBase):
    def test_avance_sin_horas_no_infla_la_eficiencia(self):
        """HR-08: el avance de una semana sin tareo no se reparte entre las horas de otra semana."""
        from obras import construccion_civil as civil
        from obras.models import Recurso, RecursoAPU
        from planillas.models import Trabajador
        operario = Recurso.objects.create(nombre='Operario', unidad='hh', tipo='MO', precio=D('25'))
        RecursoAPU.objects.create(partida=self.a, recurso=operario, cantidad=D('0.5'))
        t = Trabajador.objects.create(numero_doc='40000077', apellido_paterno='RAMOS', nombres='ANA',
                                      fecha_ingreso=HOY - timedelta(days=60), regimen='CONSTRUCCION',
                                      categoria_civil='OPERARIO', sistema_pensiones='ONP')
        lunes = HOY - timedelta(days=HOY.weekday())
        civil.registrar_tareo(self.obra, lunes, 'Excavación', [(t, self.a, D('8'), D('0'), D('0'))], self.user)
        AvancePartida.objects.create(obra=self.obra, partida=self.a, fecha=lunes, metrado=D('10'))
        AvancePartida.objects.create(obra=self.obra, partida=self.a, fecha=lunes - timedelta(days=7),
                                     metrado=D('90'))  # semana anterior, sin horas
        r = g.productividad(self.obra)
        self.assertEqual(r['cuadrillas'][0]['eficiencia'], D('62.5'))  # 10 × 0.5 / 8
        self.assertEqual([x['metrado'] for x in r['sin_horas']], [D('90')])


class ConsorcioTest(EjecucionBase):
    def test_atribucion_por_socio(self):
        """SRV-05."""
        from obras.models import SocioConsorcio
        from ventas.models import Venta, VentaItem
        from core.models import Serie
        SocioConsorcio.objects.create(obra=self.obra, nombre='Nuestra empresa', participacion_pct=D('60'),
                                      es_la_empresa=True, operador=True)
        SocioConsorcio.objects.create(obra=self.obra, nombre='Socio Sur SAC', ruc='20600000022',
                                      participacion_pct=D('40'))
        serie, numero = Serie.siguiente('01', 'F001')
        v = Venta.objects.create(tipo_comprobante='01', serie=serie, numero=numero, tercero=self.entidad,
                                 fecha_emision=HOY, centro_costo=self.obra.centro_costo)
        VentaItem.objects.create(documento=v, descripcion='Valorización', cantidad=1, precio_unitario=D('10000'))
        v.calcular_totales()
        v.save()
        r = g.consorcio(self.obra)
        socio = next(f for f in r['filas'] if f['socio'].nombre == 'Socio Sur SAC')
        self.assertEqual((socio['ventas'], socio['igv_ventas']), (D('4000.00'), D('720.00')))
        self.assertEqual(r['suma'], D('100'))
        self.assertContains(self.client.get(reverse('obras:consorcio', args=[self.obra.pk])), 'Socio Sur SAC')


class CierreContableTest(TestCase):
    """CON-14: la compra de setiembre registrada en el periodo de octubre se contabiliza en octubre."""

    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.user = User.objects.create_superuser('t187', 't@t.com', 'x')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)

    def test_asiento_en_el_periodo_de_registro_y_no_en_el_cerrado(self):
        from compras.models import Compra
        from contabilidad.centralizar import Borrador, ErrorContable, centralizar_periodo, fecha_contable
        from contabilidad.models import Asiento, PeriodoContable
        c = Compra.objects.filter(estado='REGISTRADO', es_saldo_inicial=False).exclude(tipo_comprobante='07').first()
        emision = c.fecha_emision.replace(day=15)
        siguiente = (emision.replace(day=28) + timedelta(days=4)).replace(day=1)
        Compra.objects.filter(pk=c.pk).update(fecha_emision=emision, periodo=per(siguiente))
        c.refresh_from_db()
        PeriodoContable.objects.update_or_create(periodo=per(emision), defaults={'cerrado': True})
        antes = Asiento.objects.filter(periodo=per(emision)).count()
        self.assertEqual(fecha_contable(c).strftime('%Y%m'), per(siguiente))
        centralizar_periodo(per(siguiente))
        a = c.asientos.get()
        self.assertEqual(a.periodo, per(siguiente))
        self.assertEqual(Asiento.objects.filter(periodo=per(emision)).count(), antes)
        with self.assertRaisesMessage(ErrorContable, 'está cerrado'):  # nada se graba en un periodo cerrado
            b = Borrador(Asiento(fecha=emision, libro='05', origen='MANUAL', glosa='Prueba'), 'PEN', D('1'))
            cuenta = a.lineas.first().cuenta
            b.add(cuenta, debe=D('1'))
            b.add(cuenta, haber=D('1'))
            b.grabar()

    def test_recibo_por_honorarios_va_a_la_632(self):
        """SRV-15: el recibo por honorarios con la clasificación «Gasto» va a la cuenta de honorarios."""
        from compras.models import Compra, CompraItem
        from contabilidad.centralizar import centralizar_periodo
        from contabilidad.models import CuentaDefecto
        from core.models import Serie, Tercero
        prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='1', numero_doc='40111222', nombre='ARQ. RUIZ')
        r = Compra.objects.create(tipo_comprobante='02', serie='E001', numero='903', tercero=prov,
                                  fecha_emision=HOY, periodo=per(HOY), clasificacion='GASTO')
        CompraItem.objects.create(documento=r, descripcion='Diseño', cantidad=1, precio_unitario=D('100'))
        r.calcular_totales()
        r.save()
        centralizar_periodo(per(HOY))
        honorarios = CuentaDefecto.mapa()['compra_HONORARIOS']
        self.assertTrue(r.asientos.get().lineas.filter(cuenta=honorarios, debe=D('100')).exists())
        self.assertTrue(Serie)  # (import usado por otras pruebas del módulo)


class PlataformaTest(EjecucionBase):
    def test_tasa_de_exito_con_el_rango_propuesto(self):
        """HR-01: con Desde / Hasta del mes (lo que la pantalla propone) ya no da error 500."""
        r = self.client.get(reverse('obras:tasa_exito'), {'desde': HOY.replace(day=1).isoformat(),
                                                          'hasta': HOY.isoformat()})
        self.assertEqual(r.status_code, 200)

    def test_decimales_en_casillas(self):
        """PLAT-06: 5.0000 → 5.00 y 0.927000 → 0.927 en las casillas; lo guardado no cambia."""
        from core.formato import editable
        from obras.forms import ObraForm
        self.assertEqual((editable(D('5.0000')), editable(D('0.927000')), editable(D('1234.5'))),
                         ('5.00', '0.927', '1234.50'))
        f = ObraForm(instance=self.obra)
        self.assertIn('value="10.00"', str(f['gastos_generales_pct']))

    def test_trazabilidad_muestra_todos_los_movimientos(self):
        """INV-30: hacia adelante también los traslados y consumos internos del lote."""
        from core.models import Almacen, Kardex, Lote, Producto
        p = Producto.objects.create(codigo='LQ-01', nombre='Leche', unidad='LTR')
        lote = Lote.objects.create(producto=p, codigo='L-QC-01')
        alm = Almacen.principal()
        for tipo, ref in (('ENTRADA', 'Compra NI01-1'), ('SALIDA', 'Traslado a tránsito TR01-1'),
                          ('SALIDA', 'Consumo interno RQ-1')):
            Kardex.objects.create(producto=p, almacen=alm, fecha=HOY, tipo=tipo, cantidad=D('5'), saldo=D('0'),
                                  referencia=ref, lote=lote)
        r = self.client.get(reverse('inventario:trazabilidad'), {'lote': 'L-QC-01'})
        self.assertContains(r, 'Traslado a tránsito TR01-1')
        self.assertContains(r, 'Consumo interno RQ-1')

    def test_listas_con_columnas_y_filtros(self):
        """LIS-01: las listas marcadas guardan columnas ocultas y filtros por usuario."""
        url = reverse('lista_auto')
        self.client.post(url, {'lista': 'contabilidad:asientos', 'accion': 'ocultas', 'col': ['Glosa']})
        self.client.post(url, {'lista': 'contabilidad:asientos', 'accion': 'guardar_filtro', 'nombre': 'Ventas',
                               'query': '?libro=14'})
        d = self.client.get(url, {'lista': 'contabilidad:asientos'}).json()
        self.assertEqual((d['ocultas'], d['filtros']), (['Glosa'], [{'nombre': 'Ventas', 'query': 'libro=14'}]))
        for nombre in ('contabilidad:asientos', 'finanzas:movimientos', 'compras:lista', 'ventas:lista',
                       'productos', 'terceros', 'requerimientos:lista', 'rendiciones:lista'):
            self.assertContains(self.client.get(reverse(nombre)), 'data-lista=', msg_prefix=nombre)

    def test_crm_en_campo_abre_sin_senal(self):
        """CRM-19: la pantalla registra su service worker (copia para abrir sin señal)."""
        self.assertContains(self.client.get(reverse('crm:movil')), 'serviceWorker')
        r = self.client.get(reverse('crm:movil_sw'))
        self.assertEqual(r['Content-Type'], 'application/javascript')
        self.assertContains(r, 'caches.match')

    def test_ajuste_de_ingreso_usa_el_costo_escrito(self):
        """INV-31: el costo indicado (> 0) se usa; negativo se rechaza."""
        from core.models import Almacen, Producto
        from inventario import servicios
        from inventario.models import Operacion, TipoOperacion
        p = Producto.objects.create(codigo='MP-002', nombre='Azúcar', unidad='KGM')
        p.mover_stock(D('10'), 'Saldo', costo=D('3.28'), almacen=Almacen.principal())
        tipo = TipoOperacion.objects.get(codigo='AJ_ING')
        op = Operacion.objects.create(tipo=tipo, fecha=HOY, almacen_destino=Almacen.principal(), glosa='Sobrante',
                                      creado_por=self.user)
        op.items.create(producto=p, cantidad=D('2'), costo_unitario=D('10'))
        with patch('inventario.servicios.errores_confirmacion', return_value=[]):
            servicios.confirmar(op, self.user)
        k = p.kardex.order_by('-id').first()
        self.assertEqual(k.costo_unitario, D('10'))
        from inventario.forms import OperacionItemForm
        f = OperacionItemForm({'producto': p.pk, 'cantidad': '1', 'costo_unitario': '-50', 'rol': 'INSUMO'})
        self.assertIn('costo_unitario', f.errors)

    def test_desviacion_con_buscador_de_ordenes(self):
        """La nueva desviación no carga todas las órdenes de producción en la página (tardaba 22 s con 66 mil)."""
        r = self.client.get(reverse('manufactura:desviacion_nueva'))
        self.assertContains(r, reverse('opciones', args=['ordenes_produccion']))
        self.assertEqual(self.client.get(reverse('opciones', args=['ordenes_produccion'])).status_code, 200)
