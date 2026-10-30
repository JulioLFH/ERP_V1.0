"""v1.102: el plan cargado con «Actualizar» queda solo con lo del Excel, las cuentas por operación van a la cuenta más
detallada y los centros de costo tienen vigencia (desde hoy hasta el 31/12/9999 por defecto)."""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import carga_masiva as cm
from core.models import Producto

from . import pcge
from .forms import CentroCostoForm
from .models import FIN_VIGENCIA, CentroCosto, CuentaContable, CuentaDefecto


def filas_plan(*cuentas, actualizar=True):
    """Filas validadas como las deja la carga masiva: (código, nombre[, moneda])."""
    cm._CONTEXTO['codigos'] = {c[0] for c in cuentas}
    filas = []
    for c in cuentas:
        d = {'codigo': c[0], 'nombre': c[1], 'moneda': c[2] if len(c) > 2 else ''}
        filas.append(cm._fila_cuenta(d, actualizar))
    return filas


class PlanDelExcelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')

    def setUp(self):
        self.addCleanup(pcge.limpiar_detalle)
        self.client.force_login(self.user)

    def plan(self):
        return filas_plan(('40', 'TRIBUTOS'), ('401', 'GOBIERNO CENTRAL'), ('4011', 'IGV'),
                          ('401111', 'IGV - CUENTA PROPIA'), ('42', 'CUENTAS POR PAGAR'), ('421', 'FACTURAS'),
                          ('4212', 'EMITIDAS'), ('421211', 'EMITIDAS MN'), ('421212', 'EMITIDAS ME', 'Dólares'))

    def test_actualizar_reemplaza_el_plan_por_el_del_excel(self):
        CuentaContable.objects.filter(codigo='4212').update(moneda='USD', nombre='Otro nombre',
                                                             destino_debe=CuentaContable.objects.get(codigo='401'))
        suelta = CuentaContable.objects.create(codigo='7777771', nombre='Sin uso', imputable=True)
        en_uso = CuentaContable.objects.create(codigo='6588881', nombre='La usa un producto', imputable=True)
        Producto.objects.create(nombre='Con cuenta', clase='SERVICIO', cuenta_compra=en_uso)
        resumen = cm.cargar('cuentas', self.plan(), self.user, actualizar=True)
        self.assertIn('Plan reemplazado', resumen)
        self.assertFalse(CuentaContable.objects.filter(pk=suelta.pk).exists())  # no estaba en el Excel: se elimina
        en_uso.refresh_from_db()
        self.assertFalse(en_uso.activo)  # en uso: queda inactiva, no se borra
        self.assertEqual(set(CuentaContable.objects.filter(activo=True).values_list('codigo', flat=True)),
                         {'40', '401', '4011', '401111', '42', '421', '4212', '421211', '421212'})
        cuenta = CuentaContable.objects.get(codigo='4212')  # como nueva: solo los datos del Excel
        self.assertEqual((cuenta.nombre, cuenta.moneda, cuenta.destino_debe_id), ('EMITIDAS', 'PEN', None))
        cta = CuentaDefecto.mapa()
        self.assertEqual(cta['proveedor'].codigo, '421211')  # la cuenta más detallada
        self.assertEqual(cta['igv'].codigo, '401111')
        self.assertIn('Cuentas por operación', resumen)

    def test_sin_actualizar_solo_agrega_y_detalla_la_configuracion(self):
        antes = CuentaContable.objects.count()
        filas = filas_plan(('421211', 'EMITIDAS MN'), ('401111', 'IGV - CUENTA PROPIA'), actualizar=False)
        cm.cargar('cuentas', filas, self.user)
        self.assertEqual(CuentaContable.objects.filter(activo=True).count(), antes + 2)
        self.assertEqual(CuentaDefecto.mapa()['proveedor'].codigo, '421211')

    def test_destino_fuera_del_excel_al_reemplazar(self):
        cm._CONTEXTO['codigos'] = {'631111'}
        with self.assertRaisesMessage(cm.ErrorFila, 'no está en el Excel'):
            cm._fila_cuenta({'codigo': '631111', 'nombre': 'Flete', 'destino_debe': '941'}, True)

    def test_la_mas_detallada_y_boton_de_la_configuracion(self):
        CuentaContable.objects.create(codigo='639710', nombre='Otros', imputable=True)
        siete = CuentaContable.objects.create(codigo='6397101', nombre='GPS', imputable=True)
        pcge.limpiar_detalle()
        self.assertEqual(pcge.al_detalle(CuentaContable.objects.get(codigo='639710')), siete)
        CuentaContable.objects.create(codigo='421211', nombre='EMITIDAS MN', imputable=True)
        r = self.client.post(reverse('contabilidad:configuracion'), {'accion': 'detallar'}, follow=True)
        self.assertContains(r, 'pasaron a la cuenta más detallada')
        self.assertEqual(CuentaDefecto.mapa()['proveedor'].codigo, '421211')


class VigenciaCentroCostoTest(TestCase):
    def test_por_defecto_desde_hoy_hasta_9999(self):
        c = CentroCosto.objects.create(codigo='ADM', nombre='Administración')
        self.assertEqual((c.valido_desde, c.valido_hasta), (timezone.localdate(), date(9999, 12, 31)))
        self.assertTrue(c.vigente())

    def test_vencido_no_se_ofrece_salvo_el_del_documento(self):
        hoy = timezone.localdate()
        vigente = CentroCosto.objects.create(codigo='A', nombre='Vigente')
        vencido = CentroCosto.objects.create(codigo='B', nombre='Vencido', valido_desde=hoy - timedelta(days=60),
                                             valido_hasta=hoy - timedelta(days=1))
        futuro = CentroCosto.objects.create(codigo='C', nombre='Futuro', valido_desde=hoy + timedelta(days=5))
        nuevos = {vigente, vencido, futuro}
        self.assertEqual(set(CentroCosto.vigentes()) & nuevos, {vigente})
        self.assertEqual(set(CentroCosto.vigentes(incluir=vencido.pk)) & nuevos, {vigente, vencido})
        self.assertFalse(vencido.vigente())

    def test_formulario_y_carga_masiva(self):
        f = CentroCostoForm(data={'codigo': 'X1', 'nombre': 'Centro', 'tipo': 'ADMINISTRACION',
                                  'valido_desde': '2026-10-10', 'valido_hasta': '2026-10-01', 'activo': 'on'})
        self.assertFalse(f.is_valid())
        self.assertIn('valido_hasta', f.errors)
        cm._CONTEXTO['codigos'] = {'PL'}
        fila = cm._fila_centro_costo({'codigo': 'PL', 'nombre': 'Planta', 'tipo': 'Producción'}, False)
        self.assertEqual(fila['datos']['valido_desde'], timezone.localdate().isoformat())
        self.assertEqual(fila['datos']['valido_hasta'], FIN_VIGENCIA.isoformat())
        cm.cargar('centros_costo', [fila], None)
        self.assertEqual(CentroCosto.objects.get(codigo='PL').valido_hasta, FIN_VIGENCIA)
