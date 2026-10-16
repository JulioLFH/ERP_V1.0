"""Rendición de gastos, lecciones de una implementación corporativa (v1.29): indicador de impuesto obligatorio,
motivos de observación estándar, historial de imputación, corte del mes, distribución en varios centros de costo,
trazabilidad de aprobación, sustento enlazado al asiento, exportación a un ERP externo y lo que faltaba ver en la app
del celular (duplicados al registrar, saldo en poder, aprobar desde el celular). Un escenario por clase."""
import io
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from compras.models import Compra
from contabilidad.centralizar import centralizar_periodo
from contabilidad.models import Asiento, AsientoLinea, CentroCosto, CuentaContable

from . import rendiciones as rd
from .models import (CategoriaCuentaHistorial, ConfigRendicion, EnvioERPDetalle, GastoRendicion, IndicadorImpuesto,
                     LineaGasto, MotivoObservacion, ObservacionLinea, PlantillaDistribucion, ReglaAprobacion)
from .tests_rendicion import PNG, Base

D = Decimal
HOY = date.today()


def ind(codigo):
    return IndicadorImpuesto.objects.get(codigo=codigo)


class IndicadorTest(Base):
    def test_indicador_obligatorio_y_tasa(self):
        from .forms_rendicion import LineaGastoForm
        r = self.rendicion()
        sin = self.linea(r, '50', numero='1')
        LineaGasto.objects.filter(pk=sin.pk).update(indicador=None)
        with self.assertRaisesMessage(rd.ErrorRendicion, 'falta el indicador de impuesto'):
            rd.enviar(r, self.rendidor)
        LineaGasto.objects.filter(pk=sin.pk).update(indicador=ind('S1'), base=D('42.37'), igv=D('5.00'))
        with self.assertRaisesMessage(rd.ErrorRendicion, 'no es base 42.37'):
            rd.enviar(r, self.rendidor)
        LineaGasto.objects.filter(pk=sin.pk).update(igv=D('7.63'))
        # tasa reducida (restaurantes MYPE): el formulario calcula base e IGV del total
        form = LineaGastoForm({'fecha': HOY.isoformat(), 'tipo_comprobante': '01', 'ruc': '20100047218',
                               'serie': 'F005', 'numero': '77', 'a_nombre_empresa': 'on', 'moneda': 'PEN',
                               'total': '110.50', 'indicador': ind('R1').pk, 'categoria': self.cat.pk,
                               'centro_costo': self.cc.pk, 'glosa': 'Almuerzo'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual((form.cleaned_data['base'], form.cleaned_data['igv']), (D('100.00'), D('10.50')))
        malo = LineaGastoForm({'fecha': HOY.isoformat(), 'tipo_comprobante': '03', 'moneda': 'PEN', 'total': '10',
                               'indicador': ind('C1').pk, 'categoria': self.cat.pk, 'glosa': 'x'})
        self.assertIn('indicador', malo.errors)  # una boleta no da crédito fiscal
        # factura gravada SIN crédito fiscal: va al gasto con el IGV incluido, no al registro de compras
        self.factura(r, numero='880')
        f2 = self.factura(r, numero='881')
        LineaGasto.objects.filter(pk=f2.pk).update(indicador=ind('S1'))
        rd.enviar(r, self.rendidor)
        rd.aprobar(r, self.gerente)
        rd.contabilizar(r, self.contador)
        self.assertTrue(Compra.objects.filter(numero='880').exists())
        self.assertFalse(Compra.objects.filter(numero='881').exists())
        self.assertEqual(GastoRendicion.objects.get(numero_documento='F001-881').monto, D('590.00'))


class MotivosTest(Base):
    def test_motivos_estandar_y_reporte(self):
        r = self.rendicion()
        l1 = self.linea(r, '40')
        l2 = self.linea(r, '60', numero='124')
        rd.enviar(r, self.rendidor)
        with self.assertRaisesMessage(rd.ErrorRendicion, 'Elija el motivo'):
            rd.aprobar(r, self.gerente, decisiones={l1.pk: ('OBSERVADA', '')})
        otro = MotivoObservacion.objects.get(codigo='OTRO')
        with self.assertRaisesMessage(rd.ErrorRendicion, 'escriba el comentario'):
            rd.aprobar(r, self.gerente, decisiones={l1.pk: ('OBSERVADA', '', otro.pk)})
        ilegible = MotivoObservacion.objects.get(codigo='ILEGIBLE')
        rd.aprobar(r, self.gerente, decisiones={l1.pk: ('OBSERVADA', '', ilegible.pk),
                                                 l2.pk: ('RECHAZADA', 'No corresponde', 'POLITICA')})
        l1.refresh_from_db()
        self.assertEqual((r.estado, l1.motivo), ('OBSERVADA', ilegible))
        datos = rd.errores_por(HOY, HOY)
        self.assertEqual(datos['total'], 2)
        self.assertEqual(dict(datos['por_usuario'])[self.rendidor.get_full_name()], 2)
        self.assertEqual(dict(datos['por_area'])[f'{self.cc.codigo} {self.cc.nombre}'], 2)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('rendiciones:errores')), 'Comprobante o foto ilegible')
        self.assertEqual(self.client.get(reverse('rendiciones:errores') + '?formato=excel').status_code, 200)


class ImputacionTest(Base):
    def test_cambio_de_cuenta_con_vigencia(self):
        vieja = self.cat.cuenta_contable
        nueva = CuentaContable.objects.filter(codigo__startswith='63', imputable=True).exclude(pk=vieja.pk).first()
        rd.cambiar_cuenta_categoria(self.cat, nueva, HOY, self.admin, 'Reclasificación de viáticos')
        self.cat.refresh_from_db()
        self.assertEqual((self.cat.cuenta_en(HOY - timedelta(days=1)), self.cat.cuenta_en(HOY)), (vieja, nueva))
        h = CategoriaCuentaHistorial.objects.get()
        self.assertEqual((h.cuenta_anterior, h.cuenta), (vieja, nueva))
        r = self.rendicion()
        self.linea(r, '30', fecha=HOY - timedelta(days=1), numero='501')
        self.linea(r, '20', numero='502')
        rd.enviar(r, self.rendidor)
        rd.aprobar(r, self.gerente)
        rd.contabilizar(r, self.contador)
        cuentas = dict(GastoRendicion.objects.values_list('numero_documento', 'cuenta_contable'))
        self.assertEqual((cuentas['501'], cuentas['502']), (vieja.pk, nueva.pk))
        # por la pantalla: se pide la vigencia y queda en el historial
        self.client.force_login(self.admin)
        url = reverse('rendiciones:config_editar', args=['categoria', self.cat.pk])
        self.client.post(url, {'codigo': 'VIA', 'nombre': 'Viáticos', 'cuenta_contable': vieja.pk, 'tope': '0',
                               'tope_diario': '0', 'tarifa_km': '0', 'exige_comprobante': 'on', 'activo': 'on',
                               'vigente_desde': (HOY + timedelta(days=5)).isoformat(), 'motivo_cambio': 'Vuelve'})
        self.assertEqual(CategoriaCuentaHistorial.objects.count(), 2)
        self.cat.refresh_from_db()
        self.assertEqual(self.cat.cuenta_en(HOY), nueva)  # el cambio rige recién en 5 días
        self.assertContains(self.client.get(reverse('rendiciones:configuracion')), 'Reclasificación de viáticos')


class CorteTest(Base):
    def test_corte_del_mes(self):
        ConfigRendicion.objects.filter(pk=ConfigRendicion.actual().pk).update(dia_corte=25, dias_aviso_corte=3)
        r = self.rendicion()
        tz = timezone.get_current_timezone()
        r.aprobada_en = datetime(2026, 9, 28, 10, 0, tzinfo=tz)  # después del corte de setiembre
        self.assertEqual(rd.fecha_contable_sugerida(r).strftime('%Y%m'), '202610')
        r.aprobada_en = datetime(2026, 9, 20, 10, 0, tzinfo=tz)  # antes del corte: su mes
        self.assertEqual(rd.fecha_contable_sugerida(r), date(2026, 9, 20))
        self.assertEqual(rd.fecha_corte(date(2026, 2, 10)), date(2026, 2, 25))
        self.linea(r, '30')
        corte, avisos = rd.avisos_de_corte(date(2026, 10, 23))
        self.assertEqual(corte, date(2026, 10, 25))
        self.assertIn(self.rendidor, dict(avisos))
        self.assertEqual(rd.avisos_de_corte(date(2026, 10, 10))[1], [])  # aún no toca avisar


class DistribucionTest(Base):
    def test_gasto_repartido_en_centros(self):
        ventas = CentroCosto.objects.create(codigo='VEN9', nombre='Ventas rendiciones', tipo='VENTAS')
        r = self.rendicion()
        boleta = self.linea(r, '100', tipo='03', numero='9001', serie='B001', ruc='20100047218')
        with self.assertRaisesMessage(rd.ErrorRendicion, 'sumar 100%'):
            rd.distribuir(boleta, [(self.cc, 60), (ventas, 30)])
        rd.distribuir(boleta, [(self.cc, 60), (ventas, 40)])
        factura = self.factura(r, numero='9002')
        plantilla = PlantillaDistribucion.objects.create(nombre='Común')
        plantilla.lineas.create(centro_costo=self.cc, porcentaje=D('50'))
        plantilla.lineas.create(centro_costo=ventas, porcentaje=D('50'))
        rd.aplicar_plantilla(r, plantilla, self.rendidor, lineas=[factura])
        rd.enviar(r, self.rendidor)
        rd.aprobar(r, self.gerente)
        rd.contabilizar(r, self.contador)
        gastos = dict(GastoRendicion.objects.filter(numero_documento='B001-9001').values_list('centro_costo', 'monto'))
        self.assertEqual(gastos, {self.cc.pk: D('60.00'), ventas.pk: D('40.00')})
        res = centralizar_periodo(HOY.strftime('%Y%m'))
        self.assertEqual(res['errores'], [])
        compra = Compra.objects.get(numero='9002')
        lineas = AsientoLinea.objects.filter(asiento__compra=compra, cuenta=self.cat.cuenta_contable, es_destino=False)
        self.assertEqual(dict(lineas.values_list('centro_costo', 'debe')), {self.cc.pk: D('250.00'), ventas.pk: D('250.00')})
        tot = AsientoLinea.objects.aggregate(d=Sum('debe'), h=Sum('haber'))
        self.assertEqual(tot['d'], tot['h'])


class TrazabilidadSustentoTest(Base):
    def test_trazabilidad_y_sustento_en_el_asiento(self):
        from core.sustentos import adjuntar
        ReglaAprobacion.objects.create(nivel=1, tipo='JEFE')
        ReglaAprobacion.objects.create(nivel=2, tipo='USUARIO', aprobador=self.gerente)
        r = self.rendicion()
        f = self.factura(r, numero='7701')
        adjuntar(f, SimpleUploadedFile('factura-7701.png', PNG, content_type='image/png'), self.rendidor)
        b = self.linea(r, '35', numero='7702')
        adjuntar(b, SimpleUploadedFile('ticket-7702.png', PNG, content_type='image/png'), self.rendidor)
        rd.enviar(r, self.rendidor)
        rd.aprobar(r, self.jefe)
        rd.aprobar(r, self.gerente)
        filas = rd.trazabilidad(HOY, HOY)
        self.assertEqual([p['h'].nivel for p in filas[0]['pasos']], [1, 2])
        self.assertEqual(filas[0]['ultimo'], self.gerente)
        rd.contabilizar(r, self.contador)
        centralizar_periodo(HOY.strftime('%Y%m'))
        compra = Compra.objects.get(numero='7701')
        asiento = Asiento.objects.get(compra=compra)
        self.assertEqual([s.archivo.nombre for s in rd.sustentos_de_asiento(asiento)], ['factura-7701.png'])
        aplicacion = Asiento.objects.filter(origen='APLICACION', glosa__startswith=f'Rendición {r.entrega.numero}')
        self.assertIn('ticket-7702.png', [s.archivo.nombre for a in aplicacion for s in rd.sustentos_de_asiento(a)])
        self.client.force_login(self.admin)
        self.client.get(reverse('contabilidad:asientos'))  # al abrir Contabilidad se centraliza lo pendiente
        asiento = Asiento.objects.get(compra=compra)
        self.assertContains(self.client.get(reverse('contabilidad:asiento_detalle', args=[asiento.pk])),
                            'factura-7701.png')
        self.assertContains(self.client.get(reverse('rendiciones:trazabilidad')), r.numero)
        self.assertEqual(self.client.get(reverse('rendiciones:trazabilidad') + '?formato=excel').status_code, 200)


class IntegracionERPTest(Base):
    def test_exportar_respuesta_reintento_y_conciliacion(self):
        from . import integracion_erp as erp
        r = self.rendicion()
        f = self.factura(r, numero='6601')
        b = self.linea(r, '40', numero='6602')
        rd.enviar(r, self.rendidor)
        rd.aprobar(r, self.gerente)
        rd.contabilizar(r, self.contador)
        envio = erp.generar(self.contador)
        filas = erp.filas(envio)
        por_doc = {x['documento']: x for x in filas}
        self.assertEqual((por_doc['F001-6601']['indicador_impuesto'], por_doc['6602']['indicador_impuesto']),
                         ('C1', 'C0'))
        self.assertEqual(por_doc['F001-6601']['centro_costo'], self.cc.codigo)
        with self.assertRaises(erp.ErrorIntegracion):
            erp.generar(self.contador)  # ya están en un lote sin respuesta
        d_f, d_b = (EnvioERPDetalle.objects.get(linea=f), EnvioERPDetalle.objects.get(linea=b))
        respuesta = f'id;estado;documento;mensaje\n{d_f.pk};OK;5100000123;\n{d_b.pk};ERROR;;Centro de costo bloqueado\n'
        ok, mal, _ = erp.registrar_respuesta(envio, SimpleUploadedFile('r.csv', respuesta.encode()))
        self.assertEqual((ok, mal), (1, 1))
        envio.refresh_from_db()
        self.assertEqual(envio.estado, 'PARCIAL')
        conc = erp.conciliacion()
        self.assertEqual(conc['ceiba'] - conc['erp'], D('40.00'))
        reintento = erp.generar(self.contador, solo_errores=True)
        self.assertEqual([d.linea_id for d in reintento.detalles.all()], [b.pk])
        self.assertEqual(reintento.detalles.get().intento, 2)
        self.client.force_login(self.admin)
        url = reverse('rendiciones:integracion')
        self.assertContains(self.client.get(url), 'Centro de costo bloqueado')
        csv = self.client.get(url, {'descargar': envio.pk})
        self.assertIn(b'F001-6601', csv.content)


class MovilCorporativoTest(Base):
    def test_duplicado_saldo_y_aprobar_desde_el_celular(self):
        r = self.rendicion()
        self.factura(r, numero='4401')
        self.client.force_login(self.rendidor)
        dup = self.client.get(reverse('rendiciones:duplicado'), {'ruc': '20100047218', 'tipo': '01', 'serie': 'F001',
                                                                 'numero': '0004401'}).json()
        self.assertIn('ya fue rendido', dup['duplicado'])
        pagina = self.client.get(reverse('rendiciones:movil'))
        self.assertContains(pagina, '"falta"')
        self.assertContains(pagina, 'indicadores')
        rd.enviar(r, self.rendidor)
        self.client.force_login(self.gerente)
        self.assertContains(self.client.get(reverse('rendiciones:movil_aprobar')), r.numero)
        detalle = self.client.get(reverse('rendiciones:movil_aprobar_detalle', args=[r.pk]))
        self.assertContains(detalle, 'Confirmar decisiones')
        linea = r.lineas.get()
        resp = self.client.post(reverse('rendiciones:detalle', args=[r.pk]), {
            'accion': 'aprobar', f'decision_{linea.pk}': 'OBSERVADA',
            f'motivo_{linea.pk}': MotivoObservacion.objects.get(codigo='ILEGIBLE').pk,
            'next': reverse('rendiciones:movil_aprobar')})
        self.assertRedirects(resp, reverse('rendiciones:movil_aprobar'))
        r.refresh_from_db()
        self.assertEqual(r.estado, 'OBSERVADA')
        self.assertEqual(ObservacionLinea.objects.get().motivo.codigo, 'ILEGIBLE')


class DemoTest(Base):
    def test_escenario_de_prueba(self):
        with patch('core.tipo_cambio._consultar', return_value=(D('3.441'), D('3.450'))):
            call_command('demo_rendiciones', '--clave', 'Demo-Clave-2026', '--confirmar', stdout=io.StringIO())
        from .models import Rendicion
        r = Rendicion.objects.get(rendidor__username='demo.rendidor')
        self.assertEqual(r.lineas.count(), 3)
        fuera = r.lineas.exclude(alertas='')
        self.assertEqual(fuera.count(), 1)  # el almuerzo supera el tope de alimentación: se justifica antes de enviar
        fuera.update(justificacion='Almuerzo con dos clientes')
        rd.enviar(r, r.rendidor)
        self.assertEqual(r.estado, 'ENVIADA')
