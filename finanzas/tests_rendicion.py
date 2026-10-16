"""Rendición de gastos (v1.27): flujo con estados y bitácora, políticas, aprobación por niveles con segregación,
contabilización (registro de compras con crédito fiscal y gastos rendidos), reembolso, devolución, caja chica,
reportes y pantallas. Un escenario por clase."""
import io
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from compras.models import Compra
from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import AsientoLinea, CentroCosto, CuentaContable
from core.models import PerfilUsuario, Tercero

from . import operaciones as op
from . import rendiciones as rd
from .models import (CategoriaGasto, Cuenta, EntregaRendir, LineaGasto, PoliticaGasto, ReglaAprobacion,
                     Rendicion)

D = Decimal
HOY = date.today()
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
       b'\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82')


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('seed_demo', verbosity=0)
        cls.admin = User.objects.create_superuser('admin_r', 'a@a.com', 'x')

    def setUp(self):
        p = patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450')))
        p.start()
        self.addCleanup(p.stop)
        self.banco = Cuenta.objects.create(nombre='BCP Rendiciones', banco='BCP', tipo='BANCO', saldo_inicial=D('50000'))
        self.trabajador = Tercero.objects.create(tipo='AMBOS', tipo_doc='1', numero_doc='45678912', nombre='JUAN PEREZ')
        grupo = Group.objects.get_or_create(name='Rendición de gastos')[0]
        self.rendidor = self.usuario('rendidor', ['rendiciones.rendir'], grupo, trabajador=self.trabajador)
        self.jefe = self.usuario('jefe', ['rendiciones.rendir'], grupo)
        self.gerente = self.usuario('gerente', ['rendiciones.aprobar'], grupo)
        self.contador = self.usuario('contador', ['rendiciones.contabilizar', 'rendiciones.tesoreria'], grupo)
        PerfilUsuario.objects.filter(usuario=self.rendidor).update(jefe=self.jefe)
        self.cc = CentroCosto.objects.create(codigo='ADM9', nombre='Administración rendiciones', tipo='ADMINISTRACION')
        cuenta = CuentaContable.objects.filter(codigo__startswith='63', imputable=True).order_by('codigo').first()
        self.cat = CategoriaGasto.objects.create(codigo='VIA', nombre='Viáticos', cuenta_contable=cuenta)
        self.movilidad = CategoriaGasto.objects.create(codigo='MOV', nombre='Movilidad', cuenta_contable=cuenta,
                                                       exige_comprobante=False, es_movilidad=True)

    def usuario(self, nombre, acciones, grupo, trabajador=None):
        u = User.objects.create_user(nombre, f'{nombre}@ejemplo.pe', 'x', first_name=nombre.capitalize())
        u.groups.add(grupo)
        PerfilUsuario.objects.create(usuario=u, acciones=acciones, trabajador=trabajador)
        return u

    def entrega(self, monto='1000', tipo='ENTREGA'):
        e = EntregaRendir(tipo=tipo, responsable=self.trabajador, motivo='Viaje a Arequipa', centro_costo=self.cc)
        return op.crear_entrega(e, self.banco, D(monto), '', 'TRANSFERENCIA', self.admin)

    def rendicion(self, entrega=None):
        return rd.crear(entrega or self.entrega(), self.rendidor, 'Viaje a Arequipa')

    def linea(self, r, total, tipo='12', categoria=None, **extra):
        total = D(total)
        indicador = extra.pop('indicador', None) or rd.sugerir_indicador(tipo, extra.get('a_nombre_empresa', True))
        base = (total / (1 + indicador.tasa / 100)).quantize(D('0.01')) if indicador.tasa else total
        datos = dict(fecha=HOY, tipo_comprobante=tipo, total=total, base=base, igv=total - base, indicador=indicador,
                     categoria=categoria or self.cat, centro_costo=self.cc, glosa='Gasto de viaje',
                     numero=extra.pop('numero', '123'))
        datos.update(extra)
        return rd.guardar_linea(r, LineaGasto(**datos), self.rendidor)

    def factura(self, r, numero='501', total='590'):
        total = D(total)
        base = (total / D('1.18')).quantize(D('0.01'))
        return self.linea(r, total, tipo='01', ruc='20100047218', razon_social='HOTEL SUR SAC', serie='F001',
                          numero=numero, base=base, igv=total - base)


class FlujoCompletoTest(Base):
    def test_dos_niveles_contabiliza_y_devuelve(self):
        ReglaAprobacion.objects.create(nivel=1, tipo='JEFE')
        ReglaAprobacion.objects.create(nivel=2, tipo='USUARIO', aprobador=self.gerente, monto_desde=D('500'))
        r = self.rendicion()
        self.factura(r)
        self.linea(r, '100', numero='777')
        rd.enviar(r, self.rendidor)
        self.assertEqual([n['usuario'] for n in r.niveles], [self.jefe.pk, self.gerente.pk])
        self.assertFalse(rd.puede_aprobar(self.gerente, r))  # le toca primero al jefe
        rd.aprobar(r, self.jefe)
        self.assertEqual(r.estado, 'EN_REVISION')
        rd.aprobar(r, self.gerente)
        self.assertEqual(r.estado, 'APROBADA')
        rd.contabilizar(r, self.contador, HOY)
        self.assertEqual(r.estado, 'CONTABILIZADA')
        compra = Compra.objects.get(serie='F001', numero='501')
        self.assertEqual((compra.clasificacion, compra.igv, compra.total, compra.periodo),
                         ('GASTO', D('90.00'), D('590.00'), HOY.strftime('%Y%m')))
        e = EntregaRendir.objects.get(pk=r.entrega_id)
        self.assertEqual((e.rendido, e.saldo), (D('690.00'), D('310.00')))
        rd.devolver(r, self.contador, self.banco, HOY)
        self.assertEqual(r.estado, 'DEVUELTA')
        self.assertEqual(EntregaRendir.objects.get(pk=e.pk).saldo, D('0'))
        estados = list(r.historial.values_list('a_estado', flat=True))
        self.assertEqual(estados, ['BORRADOR', 'ENVIADA', 'EN_REVISION', 'APROBADA', 'CONTABILIZADA', 'DEVUELTA'])
        # contabilidad: cuadra, IGV con crédito fiscal y la entrega a rendir queda en cero
        res = centralizar_periodo(HOY.strftime('%Y%m'))
        self.assertEqual(res['errores'], [])
        tot = AsientoLinea.objects.aggregate(d=Sum('debe'), h=Sum('haber'))
        self.assertEqual(tot['d'], tot['h'])
        igv = AsientoLinea.objects.filter(cuenta__codigo__startswith='4011').aggregate(d=Sum('debe'))['d']
        self.assertGreaterEqual(igv, D('90.00'))
        er = AsientoLinea.objects.filter(cuenta__codigo='1413', tercero=self.trabajador).aggregate(
            d=Sum('debe'), h=Sum('haber'))
        self.assertEqual(er['d'] - er['h'], D('0'))


class PoliticaTest(Base):
    def test_topes_justificacion_y_plazo(self):
        CategoriaGasto.objects.filter(pk=self.cat.pk).update(tope=D('50'))
        pol = PoliticaGasto.objects.create(nombre='General', tope_por_rendicion=D('100'), dias_para_rendir=5)
        r = self.rendicion()
        l1 = self.linea(r, '80')
        self.linea(r, '30', tipo='SC', numero='')  # sin comprobante en categoría que lo exige
        generales = rd.evaluar(r)
        l1.refresh_from_db()
        self.assertIn('tope por gasto', l1.alertas)
        self.assertIn('sin comprobante', LineaGasto.objects.get(tipo_comprobante='SC').alertas)
        self.assertTrue(generales and 'tope por rendición' in generales[0])
        with self.assertRaisesMessage(rd.ErrorRendicion, 'justificación'):
            rd.enviar(r, self.rendidor)
        LineaGasto.objects.filter(rendicion=r).update(justificacion='Cliente pidió reunión en hotel')
        with self.assertRaisesMessage(rd.ErrorRendicion, 'Justifíquelo'):
            rd.enviar(r, self.rendidor)
        rd.enviar(r, self.rendidor, 'Viaje con dos noches')
        self.assertEqual(r.estado, 'ENVIADA')
        # fuera de plazo: entrega antigua
        EntregaRendir.objects.filter(pk=r.entrega_id).update(fecha=HOY - timedelta(days=20))
        r2_ = self.rendicion(EntregaRendir.objects.get(pk=r.entrega_id))
        self.linea(r2_, '10', fecha=HOY - timedelta(days=20), numero='999')
        rd.evaluar(r2_)
        self.assertIn('fuera de plazo', r2_.lineas.get().alertas)
        self.assertTrue(pol.pk)


class MovilidadTest(Base):
    def test_tope_diario_4_por_ciento_rmv(self):
        from planillas.models import Parametro
        Parametro.objects.update_or_create(anio=HOY.year, defaults={'rmv': D('1130'), 'uit': D('5350')})
        r = self.rendicion()
        self.linea(r, '30', tipo='PM', categoria=self.movilidad, numero='')
        self.linea(r, '20', tipo='PM', categoria=self.movilidad, numero='')
        rd.evaluar(r)
        self.assertTrue(all('4% de la RMV' in l.alertas for l in r.lineas.all()))  # 50 > 45.20


class SegregacionTest(Base):
    def test_nadie_aprueba_su_propia_rendicion(self):
        PerfilUsuario.objects.filter(usuario=self.rendidor).update(acciones=['rendiciones.rendir',
                                                                             'rendiciones.aprobar'])
        r = self.rendicion()
        self.linea(r, '40')
        rd.enviar(r, self.rendidor)
        self.assertEqual(r.niveles[0]['usuario'], None)  # sin reglas: cualquier aprobador
        rendidor = User.objects.get(pk=self.rendidor.pk)
        self.assertFalse(rd.puede_aprobar(rendidor, r))
        with self.assertRaises(rd.ErrorRendicion):
            rd.aprobar(r, rendidor)
        self.assertTrue(rd.puede_aprobar(self.gerente, r))
        # si su jefe es él mismo (regla de jefe), ese nivel se salta
        ReglaAprobacion.objects.create(nivel=1, tipo='JEFE')
        PerfilUsuario.objects.filter(usuario=self.rendidor).update(jefe=self.rendidor)
        r2_ = self.rendicion()
        self.linea(r2_, '40', numero='55')
        rd.enviar(r2_, self.rendidor)
        self.assertEqual(r2_.niveles[0]['usuario'], None)


class ObservarTest(Base):
    def test_linea_observada_vuelve_al_rendidor(self):
        r = self.rendicion()
        l1 = self.linea(r, '40')
        l2 = self.linea(r, '60', numero='124')
        rd.enviar(r, self.rendidor)
        with self.assertRaisesMessage(rd.ErrorRendicion, 'motivo'):
            rd.aprobar(r, self.gerente, decisiones={l1.pk: ('OBSERVADA', '')})
        rd.aprobar(r, self.gerente, decisiones={l1.pk: ('OBSERVADA', 'Foto ilegible'), l2.pk: ('RECHAZADA', 'No corresponde')})
        self.assertEqual(r.estado, 'OBSERVADA')
        l1.refresh_from_db()
        self.assertEqual((l1.estado, l1.comentario), ('OBSERVADA', 'Foto ilegible'))
        rd.guardar_linea(r, l1, self.rendidor)
        self.assertEqual(LineaGasto.objects.get(pk=l1.pk).estado, 'PENDIENTE')
        rd.enviar(r, self.rendidor)
        rd.aprobar(r, self.gerente)
        self.assertEqual(r.estado, 'APROBADA')
        self.assertEqual(r.total, D('40'))  # la rechazada no suma
        self.assertEqual(LineaGasto.objects.get(pk=l2.pk).estado, 'RECHAZADA')


class DuplicadoTest(Base):
    def test_mismo_comprobante_no_se_rinde_dos_veces(self):
        r = self.rendicion()
        self.factura(r, numero='900')
        rd.enviar(r, self.rendidor)
        otra = self.rendicion()
        with self.assertRaisesMessage(rd.ErrorRendicion, 'ya fue rendido'):  # se bloquea al registrarlo
            self.factura(otra, numero='900')
        self.assertFalse(otra.lineas.exists())


class ReembolsoTest(Base):
    def test_gasto_mayor_a_lo_entregado_se_reembolsa(self):
        r = self.rendicion(self.entrega('100'))
        self.linea(r, '150')
        rd.enviar(r, self.rendidor)
        rd.aprobar(r, self.gerente)
        rd.contabilizar(r, self.contador)
        self.assertEqual(r.entrega.saldo, D('-50'))
        with self.assertRaises(rd.ErrorRendicion):
            rd.devolver(r, self.contador, self.banco, HOY)
        rd.reembolsar(r, self.contador, self.banco, HOY)
        self.assertEqual((r.estado, EntregaRendir.objects.get(pk=r.entrega_id).saldo), ('REEMBOLSADA', D('0')))


class CajaChicaTest(Base):
    def test_liquidar_y_reponer_en_un_paso(self):
        caja = self.entrega('500', tipo='CAJA_CHICA')
        r = self.rendicion(caja)
        self.linea(r, '120')
        self.factura(r, numero='321', total='118')
        rd.enviar(r, self.rendidor)
        rd.aprobar(r, self.gerente)
        m, monto, n = rd.reponer_caja(caja, self.contador, self.banco, HOY)
        self.assertEqual((monto, n), (D('238.00'), 1))
        r.refresh_from_db()
        caja.refresh_from_db()
        self.assertEqual((r.estado, caja.saldo, caja.por_reponer), ('REEMBOLSADA', D('500.00'), D('0')))


class VistasTest(Base):
    def test_pantallas_y_carga_de_categorias(self):
        self.client.force_login(self.rendidor)
        e = self.entrega()
        r = self.client.post(reverse('rendiciones:nueva'), {'entrega': e.pk, 'descripcion': 'Visita a cliente',
                                                            'fecha': HOY.isoformat()})
        rend = Rendicion.objects.get(descripcion='Visita a cliente')
        self.assertRedirects(r, reverse('rendiciones:detalle', args=[rend.pk]))
        url = reverse('rendiciones:detalle', args=[rend.pk])
        foto = SimpleUploadedFile('ticket.png', PNG, content_type='image/png')
        self.client.post(url, {'accion': 'linea', 'g-fecha': HOY.isoformat(), 'g-tipo_comprobante': '01',
                               'g-ruc': '20100047218', 'g-razon_social': 'HOTEL SUR SAC', 'g-serie': 'f001',
                               'g-numero': '88', 'g-a_nombre_empresa': 'on', 'g-moneda': 'PEN', 'g-total': '236',
                               'g-categoria': self.cat.pk, 'g-centro_costo': self.cc.pk, 'g-glosa': 'Hospedaje',
                               'g-indicador': rd.sugerir_indicador('01').pk,
                               'g-fotos': [foto]})
        linea = rend.lineas.get()
        self.assertEqual((linea.serie, linea.base, linea.igv), ('F001', D('200.00'), D('36.00')))
        from core.sustentos import de
        self.assertEqual(de(linea).count(), 1)
        self.assertContains(self.client.get(url), 'Enviar para aprobación')
        self.client.post(url, {'accion': 'enviar'})
        self.assertEqual(Rendicion.objects.get(pk=rend.pk).estado, 'ENVIADA')
        # el rendidor no ve la configuración ni aprueba
        self.assertNotContains(self.client.get(url), 'Aprobar (según decisión por gasto)')
        self.client.force_login(self.gerente)
        self.assertContains(self.client.get(reverse('rendiciones:lista') + '?vista=aprobar'), rend.numero)
        self.client.post(url, {'accion': 'aprobar', f'decision_{linea.pk}': 'APROBADA'})
        self.assertEqual(Rendicion.objects.get(pk=rend.pk).estado, 'APROBADA')
        self.client.force_login(self.admin)
        for nombre in ('lista', 'tablero', 'antiguedad', 'configuracion', 'pagos', 'cajas'):
            self.assertEqual(self.client.get(reverse(f'rendiciones:{nombre}')).status_code, 200, nombre)
        self.assertContains(self.client.get(reverse('rendiciones:imprimir', args=[rend.pk])), 'RENDICIÓN DE GASTOS')
        self.assertEqual(self.client.get(url + '?formato=excel').status_code, 200)
        self.assertContains(self.client.get(reverse('segregacion')), 'Nadie aprueba su propia rendición')
        # carga masiva de categorías
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.append(['codigo', 'nombre', 'cuenta', 'exige_comprobante', 'tope'])
        wb.active.append(['PEA', 'Peajes', self.cat.cuenta_contable.codigo, 'No', '20'])
        buf = io.BytesIO()
        wb.save(buf)
        carga = reverse('carga_masiva')
        r = self.client.post(carga, {'tipo': 'categorias_gasto', 'accion': 'validar',
                                     'archivo': SimpleUploadedFile('c.xlsx', buf.getvalue())})
        self.assertNotContains(r, 'con error')
        self.client.post(carga, {'tipo': 'categorias_gasto', 'accion': 'confirmar'})
        peaje = CategoriaGasto.objects.get(codigo='PEA')
        self.assertEqual((peaje.exige_comprobante, peaje.tope), (False, D('20')))
