"""v1.91 (checklist del 26/10/2026 en v1.89): validaciones de proyectos (OBR-31 a OBR-42, HR-21), CRM (CRM-23),
finanzas y contabilidad (FIN-12, FIN-13, CON-21), entregas y saldos de compras y ventas (COM-21 a COM-27, VEN-21,
VEN-22) y seguridad (SEG-10 a SEG-12, AJ-06/07, SU-01/02, TE-01, PL-05, PLAT-17, ALM-15, DI-01)."""
import io
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import PerfilUsuario, Tercero
from core.modulos import GRUPOS
from obras.tests_ejecucion import EjecucionBase

from .tests_v179 import Base as BaseCompras

HOY = timezone.localdate()
P = HOY.strftime('%Y%m')


def _mes(fecha, n):
    a, m = fecha.year, fecha.month + n
    while m > 12:
        a, m = a + 1, m - 12
    while m < 1:
        a, m = a - 1, m + 12
    return f'{a}{m:02d}'


def _usuario(nombre, modulos, acciones=None):
    u = User.objects.create_user(nombre, password='x')
    for m in modulos:
        u.groups.add(Group.objects.get_or_create(name=GRUPOS[m])[0])
    if acciones is not None:
        PerfilUsuario.objects.create(usuario=u, acciones=acciones)
    return u


def _excel(filas):
    from openpyxl import Workbook
    wb = Workbook()
    for f in filas:
        wb.active.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile('crono.xlsx', buf.getvalue())


class NumeroUsuarioTest(TestCase):
    def test_coma_o_punto_decimal(self):
        """OBR-32: «10,5» es 10.5 (antes 105) y «1,500» es ambiguo."""
        from core.formato import NumeroAmbiguo, numero_usuario
        for texto, valor in (('10,5', '10.5'), ('10.5', '10.5'), ('1,234.50', '1234.50'), ('1.234,50', '1234.50'),
                             ('1,234,567', '1234567'), (' 7 ', '7'), ('0,25', '0.25')):
            self.assertEqual(numero_usuario(texto), D(valor), texto)
        self.assertIsNone(numero_usuario(''))
        with self.assertRaises(NumeroAmbiguo):
            numero_usuario('1,500')
        for malo in ('abc', 'NaN', 'Infinity', '1.2.3'):
            with self.assertRaises(ValueError, msg=malo):
                numero_usuario(malo)


class CronogramaTest(EjecucionBase):
    def test_valida_todo_el_archivo_antes_de_grabar(self):
        """OBR-31 y OBR-35: negativos, más del 100 %, ítem repetido, texto y valores enormes; no se graba nada."""
        from obras import ejecucion as ej
        from obras.models import ProgramacionPartida
        ant, act = _mes(HOY, -1), P
        cab = ['Ítem', 'Descripción', f'{ant[:4]}-{ant[4:]}', f'{act[:4]}-{act[4:]}']
        casos = [([['01', '', -5, 10]], 'negativo'), ([['01', '', 60, 70]], 'supera'),
                 ([['01', '', 10, 10], ['01', '', 5, 5]], 'más de una vez'), ([['01', '', 'abc', 10]], 'no es un número'),
                 ([['01', '', 1000000000000, 0]], 'supera')]
        for filas, texto in casos:
            with self.assertRaisesMessage(ej.ErrorEjecucion, texto):
                ej.importar_cronograma(self.p, _excel([cab] + filas))
            self.assertFalse(ProgramacionPartida.objects.exists(), texto)
        with self.assertRaisesMessage(ej.ErrorEjecucion, 'supera'):  # en %: 150 % para una partida
            ej.importar_cronograma(self.p, _excel([cab, ['01', '', 80, 70]]), en_porcentaje=True)
        avisos = []
        n, _ = ej.importar_cronograma(self.p, _excel([cab + ['13/2026'], ['01', '', 60, 40, 5]]), avisos=avisos)
        self.assertEqual(n, 1)
        self.assertTrue(any('13/2026' in a for a in avisos))  # columna que no es un mes: se avisa
        # meses anteriores al inicio de la obra
        type(self.obra).objects.filter(pk=self.obra.pk).update(fecha_inicio=HOY.replace(day=1))
        p = type(self.p).objects.get(pk=self.p.pk)
        with self.assertRaisesMessage(ej.ErrorEjecucion, 'anterior al inicio'):
            ej.importar_cronograma(p, _excel([cab, ['01', '', 60, 40]]))


class AvanceTest(EjecucionBase):
    def test_fechas_partidas_fotos_y_nota(self):
        """OBR-36 y OBR-34."""
        from obras import ejecucion as ej
        from obras.models import Partida, Presupuesto
        type(self.obra).objects.filter(pk=self.obra.pk).update(fecha_inicio=HOY - timedelta(days=5))
        self.obra.refresh_from_db()
        with self.assertRaisesMessage(ej.ErrorEjecucion, 'anterior al inicio'):
            ej.registrar_avance(self.obra, self.a, HOY - timedelta(days=10), D('1'), self.user)
        meta = Presupuesto.objects.create(obra=self.obra, tipo='META')
        x = Partida.objects.create(presupuesto=meta, item='01', descripcion='Excavación', unidad='m3',
                                   metrado=D('100'), precio_fijo=True, precio=D('40'))
        with self.assertRaisesMessage(ej.ErrorEjecucion, 'el avance se registra en las partidas'):
            ej.registrar_avance(self.obra, x, HOY, D('1'), self.user)
        with self.assertRaisesMessage(ej.ErrorEjecucion, 'Formato no admitido'):
            ej.registrar_avance(self.obra, self.a, HOY, D('1'), self.user,
                                foto=SimpleUploadedFile('virus.exe', b'MZ\x90\x00'))
        with self.assertRaisesMessage(ej.ErrorEjecucion, 'no es una imagen'):
            ej.registrar_avance(self.obra, self.a, HOY, D('1'), self.user,
                                foto=SimpleUploadedFile('foto.jpg', b'no soy una imagen'))
        with self.assertRaisesMessage(ej.ErrorEjecucion, 'máximo es 250'):
            ej.registrar_avance(self.obra, self.a, HOY, D('1'), self.user, nota='x' * 300)
        r = self.client.post(reverse('obras:avance', args=[self.obra.pk]), {'partida': self.a.pk, 'metrado': '10,5'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.obra.avances.get().metrado, D('10.5'))  # antes 105

    def test_avance_posterior_a_la_valorizacion_se_anula(self):
        """OBR-33: el avance registrado después de aprobar la valorización del mes pasa a la siguiente."""
        from obras import ejecucion as ej
        from obras import valorizaciones as val
        primero, _ = ej.registrar_avance(self.obra, self.a, HOY, D('40'), self.user)
        v = val.nueva(self.obra, P, self.user)
        val.enviar(v, self.user)
        val.aprobar(v, self.user, 'Ing. Supervisor', HOY)
        nuevo, aviso = ej.registrar_avance(self.obra, self.a, HOY, D('10'), self.user)
        self.assertIn('pasa a la siguiente', aviso)
        ej.anular_avance(nuevo, self.user, 'Se digitó dos veces')
        with self.assertRaisesMessage(ej.ErrorEjecucion, 'ya se valorizó'):
            ej.anular_avance(primero, self.user, 'Metrado errado')

    def test_ids_y_periodos_invalidos_no_dan_500(self):
        """OBR-34: «abc» en la dirección o el formulario es 404 o un aviso; «202613» no es un mes."""
        from obras import valorizaciones as val
        self.assertEqual(self.client.get(reverse('obras:versiones', args=[self.obra.pk]) + '?nuevo=abc')
                         .status_code, 404)
        self.assertFalse(val.periodo_valido('202613'))
        r = self.client.post(reverse('obras:formula', args=[self.obra.pk]), {'numero': '-1', 'texto': 'J 1 47'})
        self.assertEqual(r.status_code, 302)
        r = self.client.post(reverse('obras:servicios_proceso'), {'accion': 'distribuir', 'periodo': 'abc'})
        self.assertEqual(r.status_code, 302)


class ValorizacionYFormulaTest(EjecucionBase):
    def test_periodos_y_formula(self):
        """OBR-40."""
        from obras import valorizaciones as val
        for periodo, texto in ((_mes(HOY, 1), 'futuro'), ('190001', 'año'), ('202613', 'mes válido')):
            with self.assertRaisesMessage(val.ErrorValorizacion, texto):
                val.nueva(self.obra, periodo, self.user)
        base = _mes(HOY, -2)
        for filas, kw, texto in (
                ([('J', D('-0.4'), '47', ''), ('M', D('1.4'), '21', '')], {}, 'mayor que cero'),
                ([('J', D('1'), '99', '')], {}, 'no existe'),
                ([('J', D('0.5'), '47', ''), ('J', D('0.5'), '47', '')], {}, 'dos veces'),
                ([('J', D('1'), '47', '')], {'area': '9'}, 'área'),
                ([('J', D('1'), '47', '')], {'numero': -1}, 'N°')):
            with self.assertRaisesMessage(val.ErrorValorizacion, texto):
                val.guardar_formula(self.obra, filas, periodo_base=kw.pop('periodo_base', base), **kw)
        with self.assertRaisesMessage(val.ErrorValorizacion, 'futuro'):
            val.guardar_formula(self.obra, [('J', D('1'), '47', '')], periodo_base=_mes(HOY, 3))
        val.guardar_formula(self.obra, [('M', D('0.6'), '21', ''), ('M', D('0.4'), '43', '')], periodo_base=base)
        with self.assertRaisesMessage(val.ErrorValorizacion, 'mes futuro'):
            val.guardar_indices(_mes(HOY, 2), {('47', '1'): D('100')})
        with self.assertRaisesMessage(val.ErrorValorizacion, 'mayores que cero'):
            val.guardar_indices(base, {('47', '1'): D('-10')})


class ManoDeObraEquiposTest(EjecucionBase):
    def setUp(self):
        super().setUp()
        from planillas.models import Trabajador
        self.t = Trabajador.objects.create(numero_doc='40000101', apellido_paterno='QUISPE', nombres='LUIS',
                                           fecha_ingreso=HOY - timedelta(days=60), regimen='CONSTRUCCION',
                                           categoria_civil='PEON', sistema_pensiones='ONP')

    def test_jornada_de_8_horas_y_planillas(self):
        """HR-21: no más de 8 horas normales al día; ni semanas futuras ni planillas sin tareos."""
        from obras import construccion_civil as civil
        civil.registrar_tareo(self.obra, HOY, 'A', [(self.t, self.a, D('6'), D('0'), D('0'))], self.user)
        with self.assertRaisesMessage(civil.ErrorCivil, 'jornada es de 8'):
            civil.registrar_tareo(self.obra, HOY, 'B', [(self.t, self.a, D('4'), D('0'), D('0'))], self.user)
        t = civil.registrar_tareo(self.obra, HOY, 'B', [(self.t, self.a, D('2'), D('2'), D('0'))], self.user)
        self.assertTrue(t.avisos)  # ya tenía otro tareo ese día
        with self.assertRaisesMessage(civil.ErrorCivil, 'todavía no empieza'):
            civil.nueva_planilla(self.obra, HOY + timedelta(days=14))
        with self.assertRaisesMessage(civil.ErrorCivil, 'no tiene tareos'):
            civil.nueva_planilla(self.obra, HOY - timedelta(days=21))

    def test_partes_de_equipo_validados_y_anulables(self):
        """OBR-37."""
        from obras import equipos as eq
        from obras.models import ParteEquipo
        base = dict(obra=self.obra, fecha=HOY, descripcion='Excavadora', horas=D('2'), tarifa=D('100'))
        with self.assertRaisesMessage(eq.ErrorParte, 'partida'):
            eq.registrar(ParteEquipo(**base), self.user)
        with self.assertRaisesMessage(eq.ErrorParte, 'negativ'):
            eq.registrar(ParteEquipo(**{**base, 'tarifa': D('-200')}, partida=self.a), self.user)
        with self.assertRaisesMessage(eq.ErrorParte, 'superan las del horómetro'):
            eq.registrar(ParteEquipo(**{**base, 'horas': D('12')}, partida=self.a, horometro_inicio=D('100'),
                                     horometro_fin=D('110')), self.user)
        with self.assertRaisesMessage(eq.ErrorParte, 'propio o alquilado'):
            eq.registrar(ParteEquipo(**base, partida=self.a, propiedad='XX'), self.user)
        parte = eq.registrar(ParteEquipo(**base, partida=self.a, horometro_inicio=D('100'),
                                         horometro_fin=D('110')), self.user)
        self.assertTrue(parte.avisos)  # 10 h en el horómetro y 2 h trabajadas
        eq.anular(parte, self.user, 'Parte duplicado')
        self.assertEqual(eq.resumen(self.obra)['total'], D('0'))
        self.assertEqual(ParteEquipo.todos.filter(obra=self.obra).count(), 1)
        r = self.client.post(reverse('obras:partes', args=[self.obra.pk]), {
            'descripcion': 'Rodillo', 'partida': self.a.pk, 'horas': '4', 'tarifa': 'abc'})
        self.assertEqual(ParteEquipo.todos.filter(obra=self.obra).count(), 1)  # tarifa «abc» no es 0
        self.assertEqual(r.status_code, 302)


class SubcontratoTest(EjecucionBase):
    def test_porcentajes_precios_saldo_y_anulacion(self):
        """OBR-38."""
        from obras import subcontratos as sub
        prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100070970', nombre='ENCOFRADOS SAC')
        for kw, lineas, texto in (({'adelanto_pct': D('150')}, [(self.a, '', '', D('10'), D('5'))], 'adelanto'),
                                  ({'garantia_pct': D('101')}, [(self.a, '', '', D('10'), D('5'))], 'garantía'),
                                  ({}, [(self.a, '', '', D('10'), D('-500'))], 'mayor que cero'),
                                  ({}, [(self.a, '', '', D('300'), D('5'))], 'sin subcontratar'),
                                  ({'modalidad': 'XX'}, [(self.a, '', '', D('10'), D('5'))], 'modalidad')):
            with self.assertRaisesMessage(sub.ErrorSubcontrato, texto):
                sub.crear(self.obra, prov, 'Excavación', lineas, self.user, **kw)
        sc = sub.crear(self.obra, prov, 'Excavación', [(self.a, '', '', D('80'), D('5'))], self.user)
        with self.assertRaisesMessage(sub.ErrorSubcontrato, 'sin subcontratar'):  # 80 + 30 > 100
            sub.crear(self.obra, prov, 'Más excavación', [(self.a, '', '', D('30'), D('5'))], self.user)
        sub.anular_subcontrato(sc, self.user, 'Se contrató a otro')
        sc.refresh_from_db()
        self.assertEqual(sc.estado, 'ANULADO')
        sub.crear(self.obra, prov, 'Excavación', [(self.a, '', '', D('30'), D('5'))], self.user)  # el saldo se libera


class ContratoYPresupuestoTest(EjecucionBase):
    def test_adendas_penalidades_y_escenarios(self):
        """OBR-39 y OBR-41."""
        from obras import gestion as g
        from obras.views_gestion import EventoForm
        with self.assertRaisesMessage(g.ErrorGestion, 'no puede ser negativa'):
            g.registrar_adenda(self.obra, 'PLAZO', 0, -30, 'Reducción', self.user)
        with self.assertRaisesMessage(g.ErrorGestion, 'futura'):
            g.registrar_adenda(self.obra, 'PLAZO', 0, 30, 'Ampliación', self.user, fecha=HOY + timedelta(days=30))
        type(self.obra).objects.filter(pk=self.obra.pk).update(estado='LICITACION', monto_contratado=D('10000'))
        self.obra.refresh_from_db()
        with self.assertRaisesMessage(g.ErrorGestion, 'adjudicadas'):
            g.registrar_adenda(self.obra, 'PLAZO', 0, 30, 'Ampliación', self.user)
        f = EventoForm({'fecha': HOY.isoformat(), 'tipo': 'PENALIDAD', 'gravedad': 'MEDIA', 'monto': '5000',
                        'descripcion': 'Atraso'}, obra=self.obra)
        self.assertFalse(f.is_valid())
        self.assertIn('tope', str(f.errors['monto']))
        with self.assertRaisesMessage(g.ErrorGestion, 'máximo es 120'):
            g.guardar_escenario(self.obra, 'x' * 200, self.user)

    def test_presupuesto_vacio_o_con_negativos_no_se_aprueba(self):
        """OBR-42."""
        from obras.forms import PartidaForm, PieForm
        from obras.models import Presupuesto
        p = Presupuesto.objects.create(obra=self.obra, tipo='OFERTA')
        self.client.post(reverse('obras:presupuesto', args=[p.pk]), {'accion': 'aprobar'})
        p.refresh_from_db()
        self.assertEqual(p.estado, 'BORRADOR')
        pie = PieForm({'nombre': 'x', 'fecha_precios': '1900-01-01', 'gastos_generales_pct': '-10',
                       'utilidad_pct': '500', 'igv_pct': '50'}, instance=p)
        self.assertFalse(pie.is_valid())
        self.assertEqual(set(pie.errors), {'fecha_precios', 'gastos_generales_pct', 'utilidad_pct', 'igv_pct'})
        f = PartidaForm({'item': '09.09.09', 'descripcion': 'Huérfana', 'unidad': 'm2', 'metrado': '-10'},
                        presupuesto=p)
        self.assertFalse(f.is_valid())
        self.assertIn('título', str(f.errors['item']))
        self.assertIn('negativo', str(f.errors['metrado']))


class CrmTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('crm', 'c@c.com', 'x')

    def test_reglas_de_la_oportunidad_y_la_licitacion(self):
        """CRM-23."""
        from obras.forms import HitoForm, RequisitoForm
        from ventas.crm import OportunidadForm
        f = OportunidadForm({'nombre': 'Casa de playa', 'prospecto': 'Juan Pérez', 'telefono': 'abc',
                             'etapa': 'PROSPECTO', 'monto': '1000', 'responsable': self.user.pk,
                             'fecha_inicio': HOY.isoformat(), 'fecha_cierre': (HOY - timedelta(days=5)).isoformat()})
        self.assertFalse(f.is_valid())
        self.assertTrue({'telefono', 'consentimiento', 'fecha_cierre'} <= set(f.errors))
        empresa = OportunidadForm({'nombre': 'Local', 'prospecto': 'CONSTRUCTORA ANDES S.A.C.', 'etapa': 'PROSPECTO',
                                   'monto': '1000', 'responsable': self.user.pk})
        self.assertNotIn('consentimiento', empresa.errors)  # una empresa no es persona natural
        self.assertFalse(HitoForm({'tipo': 'CONVOCATORIA', 'fecha': '1900-01-01'}).is_valid())
        self.assertFalse(RequisitoForm({'tipo': 'GARANTIA', 'descripcion': 'Carta', 'monto': '-1000'}).is_valid())


class FinanzasTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from finanzas.models import Cuenta
        cls.user = User.objects.create_superuser('fin', 'f@f.com', 'x')
        cls.banco = Cuenta.objects.create(tipo='BANCO', nombre='BCP soles', banco='BCP', saldo_inicial=D('1000'))

    def setUp(self):
        self.client.force_login(self.user)

    def test_propuesta_de_pagos_y_redireccion(self):
        """FIN-12."""
        from finanzas import propuesta_pagos as pp
        with self.assertRaisesMessage(pp.ErrorPropuesta, 'anterior a hoy'):
            pp.armar(self.banco, HOY - timedelta(days=30), HOY, D('0'), self.user)
        with self.assertRaisesMessage(pp.ErrorPropuesta, 'Vencimientos hasta'):
            pp.armar(self.banco, HOY + timedelta(days=5), HOY, D('0'), self.user)
        p = pp.armar(self.banco, HOY, HOY + timedelta(days=7), D('0'), self.user)
        pp.anular(p, self.user, 'Se armó por error')
        p.refresh_from_db()
        self.assertEqual(p.estado, 'ANULADA')
        self.assertRedirects(self.client.get('/finanzas/'), reverse('finanzas:movimientos'),
                             fetch_redirect_response=False)

    def test_egreso_manual_y_asiento_a_bancos(self):
        """FIN-13 y CON-21."""
        from contabilidad.models import CuentaContable
        from contabilidad.views import _bancos_ok
        from finanzas.forms import MovimientoForm
        tesorero = _usuario('tesorero', ['tesoreria'], ['tesoreria.registrar'])
        cuenta = CuentaContable.objects.filter(codigo__startswith='6011', imputable=True).first() or \
            CuentaContable.objects.create(codigo='601101', nombre='Mercaderías', imputable=True)
        f = MovimientoForm({'cuenta': self.banco.pk, 'fecha': HOY.isoformat(), 'tipo': 'EGRESO', 'concepto': 'OTRO',
                            'medio_pago': 'TRANSFERENCIA', 'monto': '5000', 'cuenta_contable': cuenta.pk,
                            'glosa': 'Pago'}, usuario=tesorero)
        f.is_valid()
        self.assertIn('existencias o compras', str(f.errors.get('cuenta_contable')))
        self.assertIn('gerencia', str(f.errors.get('monto')))
        contador = _usuario('contador', ['contabilidad'], ['contabilidad.asientos'])
        banco = CuentaContable.objects.filter(codigo__startswith='104', imputable=True).first() or \
            CuentaContable.objects.create(codigo='104101', nombre='Cta. cte.', imputable=True)
        errores = []
        form = SimpleNamespace(cleaned_data={'glosa': 'Ajuste'}, add_error=lambda c, e: errores.append(e))
        formset = SimpleNamespace(forms=[SimpleNamespace(cleaned_data={'cuenta': banco, 'DELETE': False})])
        request = RequestFactory().post('/')
        request.user = contador
        self.assertFalse(_bancos_ok(request, form, formset))
        self.assertIn('Tesorería', errores[0])


class ComprasSeguimientoTest(BaseCompras):
    def _factura(self, oc, cantidad, numero='3001', precio='5'):
        from compras.models import Compra, CompraItem
        c = Compra.objects.create(tipo_comprobante='01', serie='F001', numero=numero, tercero=self.prov,
                                  fecha_emision=HOY, orden_compra=oc, ingresar_almacen=False)
        CompraItem.objects.create(documento=c, producto=self.harina, descripcion='Harina', cantidad=D(cantidad),
                                  precio_unitario=D(precio))
        c.calcular_totales()
        c.save()
        return c

    def test_entregas_parciales_anulacion_y_cierre_de_saldo(self):
        """COM-22 a COM-25."""
        from compras import seguimiento
        from inventario import servicios
        oc = self.oc('10')
        servicios.confirmar(self.recibir(oc, '4'), self.admin)
        self._factura(oc, '4')
        self.assertEqual(seguimiento.actualizar_estado(oc), 'APROBADO')  # antes «Atendido» con 6 pendientes
        fila = seguimiento.avance(oc)[0]
        self.assertEqual((fila['recibido'], fila['por_recibir'], fila['facturado'], fila['por_facturar']),
                         (D('4'), D('6'), D('4'), D('6')))
        self.assertIn('parcial', seguimiento.situacion(oc))
        self.assertEqual(len(seguimiento.entregas(oc)), 1)
        with self.assertRaisesMessage(seguimiento.ErrorSeguimiento, 'no se anula'):
            seguimiento.anular(oc, self.admin, 'El proveedor no entrega')
        r = self.client.post(reverse('compras:oc_estado', args=[oc.pk]), {'estado': 'ATENDIDO'})
        self.assertEqual(r.status_code, 302)
        oc.refresh_from_db()
        self.assertEqual(oc.estado, 'APROBADO')  # COM-24: el POST no fuerza «Atendido»
        self.client.post(reverse('compras:oc_estado', args=[oc.pk]),
                         {'accion': 'cerrar_saldo', 'motivo_cierre': 'PROVEEDOR'})
        oc.refresh_from_db()
        self.assertEqual(oc.estado, 'ATENDIDO')
        self.assertIsNotNone(oc.saldo_cerrado_en)
        self.assertEqual(self.client.get(reverse('compras:oc_detalle', args=[oc.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('compras:oc_seguimiento')).status_code, 200)
        self.assertEqual(self.client.get(reverse('compras:oc_lista') + '?saldo=1').status_code, 200)

    def test_comprador_no_anula_facturas_ni_ve_registrar(self):
        """COM-21 y COM-26."""
        oc = self.oc('5')
        c = self._factura(oc, '5', numero='3002')
        comprador = _usuario('comprador', ['compras'], ['compras.oc', 'compras.anular', 'compras.anular_oc'])
        self.client.force_login(comprador)
        self.client.post(reverse('compras:anular', args=[c.pk]), {'motivo': 'Prueba de anulación'})
        c.refresh_from_db()
        self.assertEqual(c.estado, 'REGISTRADO')
        r = self.client.get(reverse('compras:oc_detalle', args=[oc.pk]))
        self.assertNotContains(r, 'Registrar factura de compra')


class VentasPedidoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from core.models import Producto
        cls.user = User.objects.create_superuser('ven', 'v@v.com', 'x')
        cls.cliente = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20131370645', nombre='CLIENTE')
        cls.clavo = Producto.objects.create(codigo='CLV', nombre='Clavo', clase='MERCADERIA', unidad='KGM')

    def test_saldo_del_pedido(self):
        """VEN-21: factura parcial, sobre-facturación bloqueada y pedido con facturas que no se anula."""
        from ventas import seguimiento_pedido as sp
        from ventas.models import Cotizacion, CotizacionItem, Venta, VentaItem
        ped = Cotizacion.objects.create(tipo='PED', numero='PD01-00000901', tercero=self.cliente, estado='APROBADO')
        CotizacionItem.objects.create(documento=ped, producto=self.clavo, descripcion='Clavo', cantidad=D('100'),
                                      precio_unitario=D('8.50'))
        v = Venta.objects.create(tipo_comprobante='01', serie='F001', numero='901', tercero=self.cliente,
                                 fecha_emision=HOY, cotizacion=ped)
        VentaItem.objects.create(documento=v, producto=self.clavo, descripcion='Clavo', cantidad=D('40'),
                                 precio_unitario=D('8.50'))
        self.assertEqual(sp.actualizar_estado(ped), 'APROBADO')
        self.assertEqual(sp.avance(ped)[0]['pendiente'], D('60'))
        self.assertEqual(sp.items_saldo(ped)[0]['cantidad'], D('60'))
        self.assertTrue(sp.errores_factura(ped, [(self.clavo, 'Clavo', D('70'))]))
        self.assertEqual(sp.errores_factura(ped, [(self.clavo, 'Clavo', D('60'))]), [])
        with self.assertRaisesMessage(sp.ErrorPedido, 'no se anula'):
            sp.anular(ped, self.user, 'Cliente desistió')
        sp.cerrar_saldo(ped, self.user, 'El cliente no comprará el resto')
        ped.refresh_from_db()
        self.assertEqual(ped.estado, 'ATENDIDO')

    def test_vendedor_es_un_usuario(self):
        """VEN-22: se llena con quien registra y sin permiso no se cambia."""
        from ventas.forms import CotizacionForm
        vendedor = _usuario('vendedor1', ['ventas'], ['ventas.emitir'])
        with patch('core.auditoria.usuario_actual', return_value=vendedor):
            f = CotizacionForm()
            self.assertEqual(f.initial['vendedor'], 'vendedor1')
            f = CotizacionForm({'tipo': 'PED', 'tercero': self.cliente.pk, 'fecha': HOY.isoformat(), 'validez_dias': 15,
                                'vendedor': 'admin', 'moneda': 'PEN', 'tipo_operacion': 'GRAVADA'})
            f.fields['vendedor'].choices = [('', '—'), ('admin', 'admin'), ('vendedor1', 'vendedor1')]
            self.assertFalse(f.is_valid())
            self.assertIn('vendedor', f.errors)


class ClaveSolSireTest(TestCase):
    def test_se_guarda_cifrada_y_no_se_vuelve_a_pedir(self):
        """La clave SOL del SIRE se registra una vez en Ajustes › Empresa y la API la usa sola."""
        from django.core.cache import cache

        from core import sire_api
        from core.cifrado import descifrar
        from core.forms import EmpresaForm
        from core.models import Empresa
        e = Empresa.actual()
        Empresa.objects.filter(pk=e.pk).update(ruc='20100070970', sire_client_id='cid', sire_client_secret='sec',
                                               sire_usuario_sol='SIRE01', direccion=e.direccion or 'Av. Lima 1',
                                               email=e.email or 'a@a.com')
        e.refresh_from_db()
        from django.forms.models import model_to_dict
        datos = {k: v for k, v in model_to_dict(e).items() if v is not None}
        datos['sire_clave_nueva'] = 'MiClaveSol'
        f = EmpresaForm(datos, instance=e)
        self.assertTrue(f.is_valid(), f.errors)
        f.save()
        e.refresh_from_db()
        self.assertNotIn('MiClaveSol', e.sire_clave_sol)
        self.assertEqual(descifrar(e.sire_clave_sol), 'MiClaveSol')
        cache.clear()
        self.assertTrue(sire_api.hay_token())  # la pantalla ya no pide la clave
        enviados = []

        def falso(metodo, url, datos=None, cabeceras=None, binario=False):
            enviados.append(datos)
            return {'access_token': 'tok', 'expires_in': 3600} if 'oauth2' in url else {'numTicket': '123'}
        with patch('core.sire_api._http', side_effect=falso):
            self.assertEqual(sire_api.solicitar_propuesta('RCE', '202609'), '123')
        self.assertIn(b'MiClaveSol', enviados[0])  # pidió el token con la clave guardada, sin preguntar


class SeguridadTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from core.models import Almacen, Producto
        cls.admin = User.objects.create_superuser('admin2', 'a@a.com', 'x')
        cls.otro = User.objects.create_superuser('admin3', 'b@b.com', 'x')
        cls.prov = Tercero.objects.create(tipo='PROVEEDOR', tipo_doc='6', numero_doc='20100070970', nombre='PROV',
                                          banco='BCP', cuenta_bancaria='1931234567012')
        Almacen.objects.create(codigo='PRI', nombre='Principal', es_principal=True, direccion='Av. Lima 1',
                               ubigeo='150101')
        cls.clavo = Producto.objects.create(codigo='CLV', nombre='Clavo', clase='MERCADERIA', unidad='KGM',
                                            precio_venta=D('8.50'))

    def test_cuenta_bancaria_con_doble_control(self):
        """SEG-10."""
        from django.forms.models import model_to_dict

        from core.forms import TerceroForm
        datos = {k: v for k, v in model_to_dict(self.prov).items() if v is not None}
        datos['cuenta_bancaria'] = '1939999999012'
        with patch('core.auditoria.usuario_actual', return_value=self.admin):
            f = TerceroForm(datos, instance=self.prov)
            self.assertTrue(f.is_valid(), f.errors)
            f.save()
        self.prov.refresh_from_db()
        self.assertEqual(self.prov.cuenta_bancaria, '1931234567012')  # todavía la anterior
        self.assertEqual(self.prov.cambio_bancario, {'cuenta_bancaria': '1939999999012'})
        self.client.force_login(self.admin)
        self.client.post(reverse('tercero_banco', args=[self.prov.pk]), {'accion': 'aprobar'})
        self.prov.refresh_from_db()
        self.assertEqual(self.prov.cuenta_bancaria, '1931234567012')  # quien lo pidió no lo aprueba
        self.client.force_login(self.otro)
        self.client.post(reverse('tercero_banco', args=[self.prov.pk]), {'accion': 'aprobar'})
        self.prov.refresh_from_db()
        self.assertEqual((self.prov.cuenta_bancaria, self.prov.cambio_bancario), ('1939999999012', {}))

    def test_campos_sensibles_y_rutas(self):
        """SEG-11, SEG-12, SU-01, PL-05, TE-01 y AJ-06."""
        from django.forms.models import model_to_dict

        from core.forms import ProductoForm
        from core.usuarios import UsuarioForm
        almacenero = _usuario('almacenero', ['inventario'], ['inventario.operar'])
        datos = {k: v for k, v in model_to_dict(self.clavo).items() if v is not None and k not in ('stock',
                                                                                                    'costo_promedio')}
        datos['precio_venta'] = '1.00'
        with patch('core.auditoria.usuario_actual', return_value=almacenero):
            f = ProductoForm(datos, instance=self.clavo)
            self.assertTrue(f.fields['precio_venta'].disabled)
            self.assertFalse(f.is_valid())
            self.assertIn('no cambia el precio', str(f.non_field_errors()))
        self.client.force_login(almacenero)
        self.assertEqual(self.client.get(reverse('inventario:nueva') + '?tipo=AJ_ING').status_code, 403)
        comprador = _usuario('comprador2', ['compras'], ['compras.oc'])
        self.client.force_login(comprador)
        self.assertEqual(self.client.get(reverse('sire_propuestas')).status_code, 403)
        r = self.client.post(reverse('tercero_nuevo'), {'tipo': 'PROVEEDOR', 'tipo_doc': '6',
                                                        'numero_doc': '20100047218', 'nombre': 'NUEVO'})
        self.assertIn(r.status_code, (302, 403))
        self.assertFalse(Tercero.objects.filter(nombre='NUEVO').exists())
        rrhh = _usuario('rrhh', ['planillas'], ['planillas.calcular'])
        self.client.force_login(rrhh)
        self.assertEqual(self.client.get(reverse('planillas:configuracion')).status_code, 403)
        jefe = _usuario('jefe_ti', ['ajustes'], ['ajustes.usuarios'])
        f = UsuarioForm({'username': 'jefe_ti', 'is_active': 'on', 'modulos': ['ajustes', 'contabilidad'],
                         'acciones': ['ajustes.usuarios']}, instance=jefe, editor=jefe)
        self.assertFalse(f.is_valid())
        self.assertIn('propios', str(f.non_field_errors()))
        f = UsuarioForm({'username': 'admin2', 'is_active': 'on', 'modulos': ['ajustes'], 'es_admin': 'on'},
                        instance=self.admin, editor=jefe)
        self.assertFalse(f.is_valid())

    def test_hoja_de_ruta_vacia_y_lote_como_texto(self):
        """DI-01 y ALM-15."""
        from distribucion.models import HojaReparto
        self.client.force_login(self.admin)
        self.client.post(reverse('distribucion:nueva'), {})
        self.assertFalse(HojaReparto.objects.exists())
        r = self.client.post(reverse('inventario:estado_stock'), {'que': 'lote', 'lote': 'L-E2', 'estado': 'BLOQUEADO',
                                                                  'motivo': 'Prueba'}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'No existe el lote')
