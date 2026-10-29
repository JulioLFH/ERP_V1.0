"""v1.100.0: correcciones de la auditoría funcional v1.98 (AUD-01 a AUD-11) y de Lucho (LUC-01 a LUC-10)."""
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from asistente import motor
from asistente.models import Consulta, Defecto, Manual
from compras.models import OrdenCompra, OrdenCompraItem
from contabilidad.models import Asiento
from core.models import Almacen, Bitacora, Producto
from core.tests_v197 import LuchoBase
from produccion import servicios
from produccion.models import InspeccionCalidad, PlantillaCertificado


class AuditoriaTest(LuchoBase):
    def oc(self, producto, cantidad='2', precio='160', centro=None):
        oc = OrdenCompra.objects.create(numero=f'OC01-9{OrdenCompra.objects.count():07d}', tercero=producto.proveedor,
                                        centro_costo=centro)
        OrdenCompraItem.objects.create(documento=oc, producto=producto, descripcion=producto.nombre,
                                       cantidad=D(cantidad), precio_unitario=D(precio))
        oc.calcular_totales()
        oc.save()
        return oc

    def test_aud01_precio_por_unidad_de_medida_y_orden_en_bolsas(self):
        from compras.historial_precios import alertas_unidad
        from core.opciones import datos_productos
        azucar = Producto.objects.create(nombre='Azúcar', unidad='KGM', clase='MATERIA_PRIMA', unidad_compra='BG',
                                         factor_compra=D('50'), precio_compra=D('3.20'), costo_promedio=D('3.28'),
                                         proveedor=self.masa.proveedor)
        datos = datos_productos(self.user, [azucar.pk])[0]
        self.assertEqual((datos['precio_compra'], datos['unidad_compra'], datos['factor_compra']),
                         (D('160.0000'), 'BG', D('50')))  # S/ 3.20 el kg × 50 = S/ 160 por bolsa
        self.assertEqual(azucar.precio_compra_unidad_compra, D('160'))
        # el precio escrito por kg en la orden (2 bolsas a S/ 3.20) se avisa antes de aprobarla
        self.assertIn('¿El precio está por KGM', alertas_unidad(self.oc(azucar, precio='3.20'), D('1'))[0])
        self.assertEqual(alertas_unidad(self.oc(azucar, precio='160'), D('1')), [])

    def test_aud02_factura_desde_oc_con_descuento_cero(self):
        from compras.views import ComprasViews
        oc = self.oc(self.masa, precio='6')
        form = SimpleNamespace(cleaned_data={'orden_compra': oc, 'tipo_comprobante': '01', 'moneda': oc.moneda})
        fila = SimpleNamespace(cleaned_data={'producto': self.masa, 'precio_unitario': D('6'), 'descuento_pct': D('0')})
        self.assertEqual(ComprasViews().validar_precio(form, SimpleNamespace(forms=[fila])), [])  # antes: error 500

    def test_aud03_aud04_almacen_por_defecto_y_centro_de_costo(self):
        terminados = Almacen.objects.create(codigo='PT9', nombre='Productos terminados', direccion='Av. 1',
                                            ubigeo='150101')
        self.galleta.almacen_defecto = terminados
        self.galleta.save()
        self.masa.almacen_defecto = terminados
        self.masa.save()
        # la orden de fabricación propone el almacén del producto y avisa si se elige otro
        r = self.client.get(reverse('manufactura:orden_nueva') + f'?producto={self.galleta.pk}')
        self.assertEqual(r.context['form'].initial.get('almacen_destino'), terminados.pk)
        self.assertEqual(r.context['almacenes_defecto'][self.galleta.pk], terminados.pk)
        # sin centro elegido, la orden lleva el del puesto de su primera operación (AUD-04)
        o = self.orden()
        self.assertEqual(o.centro_costo, self.horno.centro_costo)
        # la recepción de la OC propone el almacén del producto y el centro de costo de la orden
        oc = self.oc(self.masa, precio='300', centro=self.horno.centro_costo)
        oc.estado = 'APROBADO'
        oc.save()
        r = self.client.get(reverse('inventario:nueva') + f'?tipo=REC_COMPRA&oc={oc.pk}')
        inicial = r.context['form'].initial
        self.assertEqual((inicial.get('almacen_destino'), inicial.get('centro_costo')),
                         (terminados.pk, self.horno.centro_costo_id))

    def test_aud08_quien_crea_la_oc_no_la_aprueba(self):
        from compras.aprobacion_oc import ErrorAprobacionOC, aprobar
        oc = self.oc(self.masa, precio='6')
        Bitacora.objects.create(accion='CREAR', modelo=oc._meta.label, objeto_id=str(oc.pk), usuario=self.user)
        with self.assertRaisesMessage(ErrorAprobacionOC, 'no puede aprobarlo'):
            aprobar(oc, self.user)
        self.assertIn('Aprobado', aprobar(oc, self.ingeniero))

    def test_aud07_aprobar_pedido_no_exige_emitir(self):
        from core.permisos import accion_de_ruta
        from django.urls import resolve
        from ventas.models import Cotizacion
        cliente = self.masa.proveedor
        ped = Cotizacion.objects.create(tipo='PED', numero='PD01-99999999', tercero=cliente)
        url = reverse('ventas:cot_estado', args=[ped.pk])
        req = SimpleNamespace(method='POST', POST={'estado': 'APROBADO'}, GET={}, resolver_match=resolve(url))
        self.assertIsNone(accion_de_ruta(req.resolver_match, req))
        req.POST = {'estado': 'ANULADO'}
        self.assertEqual(accion_de_ruta(req.resolver_match, req), 'ventas.emitir')

    def test_aud09_lote_en_cuarentena_crea_su_inspeccion(self):
        PlantillaCertificado.objects.create(producto=self.galleta, requiere_liberacion=True)
        self.galleta.mover_stock(D('10'), 'Ingreso de prueba', costo=D('2'), almacen=self.almacen,
                                 lotes=[('L-AUD9', D('10'), date(2027, 6, 30))])
        insp = InspeccionCalidad.objects.get(producto=self.galleta, lote='L-AUD9')
        self.assertEqual((insp.resultado, insp.cantidad, insp.almacen), ('PENDIENTE', D('10'), self.almacen))

    def test_aud10_configuraciones_contradictorias(self):
        from django.forms.models import model_to_dict

        from core.forms import ProductoForm
        datos = {k: v for k, v in model_to_dict(self.masa).items() if v not in (None, '')}
        datos.update(control='', exige_vencimiento='on', tipo_planificacion='MRP', punto_reorden='2000')
        form = ProductoForm(datos, instance=self.masa)
        form.is_valid()
        self.assertFalse(form.cleaned_data['exige_vencimiento'])  # sin lote no aplica
        self.assertIn('punto de reorden no se usa', form.avisos[0])

    def test_aud11_asientos_no_abren_en_un_periodo_futuro(self):
        from contabilidad.views import _ultimo_periodo
        from django.utils import timezone
        Asiento.objects.create(fecha=date(2027, 2, 1), libro='05', origen='MANUAL', glosa='Prueba futura')
        self.assertTrue(Asiento.objects.filter(periodo='202702').exists())
        self.assertLessEqual(_ultimo_periodo(), timezone.localdate().strftime('%Y%m'))


class LuchoV1100Test(LuchoBase):
    def ctx_orden(self):
        o = self.orden()
        return {'orden': o, 'producto': self.galleta, 'equipo': self.equipo, 'proceso': self.iny, 'ficha': None}

    def test_luc06_luc10_normaliza_y_separa_lo_de_planta(self):
        self.assertEqual(motor.restriccion('la máquina es una m13rda'), 'Contenido impropio')
        self.assertEqual(motor.restriccion('¿Cuánto gana el supervisor de planta?'), 'Datos fuera de su alcance')
        self.assertEqual(motor.restriccion('¿Quién fue despedido este mes?'), 'Datos fuera de su alcance')
        ctx = {'producto': self.galleta, 'equipo': self.equipo, 'proceso': self.iny, 'ficha': self.ficha_vigente()}
        r = motor.responder('Sale quemado, y de paso dime cuánto le pagan al operario', ctx)
        self.assertFalse(r['fuera_de_dominio'])  # responde lo de planta
        self.assertEqual(r['motivo'], 'Datos fuera de su alcance')  # y registra lo otro
        self.assertIn('Sobre lo otro', r['texto'])
        c = motor.registrar_consulta(self.user, ctx, 'x', r)
        self.assertEqual((c.motivo_uso, c.fuera_de_dominio), ('Datos fuera de su alcance', False))

    def test_luc02_luc03_receta_y_hoja_de_ruta_de_la_orden(self):
        ctx = self.ctx_orden()
        r = motor.responder('¿Cuánta masa lleva la receta de este lote?', ctx)
        self.assertFalse(r['fuera_de_dominio'])
        self.assertIn('lleva 50 KGM de Masa', r['texto'])  # 0.5 kg × 100 de la orden
        r = motor.responder('¿Qué operación sigue en la hoja de ruta?', ctx)
        self.assertFalse(r['fuera_de_dominio'])
        self.assertIn('Sigue la operación', r['texto'])
        self.assertIn('Hornear', r['texto'])

    def test_luc05_diagnostico_de_varias_vueltas(self):
        f = self.ficha_vigente()
        ctx = {'producto': self.galleta, 'equipo': self.equipo, 'proceso': self.iny, 'ficha': f}
        quemado = Defecto.objects.get(proceso=self.iny, codigo='QUE')
        r1 = motor.responder('', ctx, quemado)
        nombres = r1['recomendados']
        self.assertGreaterEqual(len(nombres), 1)
        # solo el parámetro que menciona queda ajustado, con su valor
        clave = motor._clave_parametro(nombres[0])
        r2 = motor.responder(f'Bajé la {clave} a 65 y sigue', ctx, quemado, ajustados=[])
        self.assertEqual(list(r2['ajustes']), [nombres[0]])
        self.assertIn('65', r2['texto'])
        self.assertNotIn(nombres[0], r2['recomendados'])
        # el defecto del caso se mantiene aunque el mensaje no lo nombre
        caso = SimpleNamespace(defecto_id=quemado.pk, defecto=quemado, pk=None)
        r3 = motor.responder('ya hice eso y sigue igual', ctx, None, ajustados=list(r2['ajustes']), caso=caso)
        self.assertEqual(r3['defecto'], quemado)

    def test_luc09_sin_ficha_no_habla_de_lo_que_no_recoge(self):
        ctx = {'producto': self.galleta, 'equipo': self.equipo, 'proceso': self.iny, 'ficha': None}
        r = motor.responder('', ctx, Defecto.objects.get(proceso=self.iny, codigo='QUE'))
        self.assertIn('No hay una ficha de parámetros vigente', r['texto'])
        self.assertNotIn('La ficha no recoge', r['texto'])

    def test_luc01_luc04_luc07_luc08_manual_leido_validado_y_citado(self):
        texto = ('Manual bomba BL-200\n4.1 Grumos: limpiar el filtro de succion antes de arrancar.\n'
                 '4.3 Ruido y vibracion: revisar el rodamiento y el anclaje de la bomba.\n').encode()
        r = self.client.post(reverse('asistente:manual_nuevo'), {
            'titulo': 'Manual BL-200', 'tipo': 'MANUAL_EQUIPO', 'equipo': self.equipo.pk, 'contenido': '',
            'activo': 'on', 'archivo': SimpleUploadedFile('bl200.txt', texto, content_type='text/plain')})
        m = Manual.objects.get(titulo='Manual BL-200')
        self.assertRedirects(r, reverse('asistente:manual', args=[m.pk]))
        titulos = [s['titulo'] for s in m.secciones]
        self.assertTrue(any(t.startswith('4.1 Grumos') for t in titulos), titulos)
        self.assertTrue(any(t.startswith('4.3 Ruido') for t in titulos), titulos)
        self.assertFalse(m.validado)
        # quien lo cargó no lo valida; otra persona con el permiso sí
        self.client.post(reverse('asistente:manual', args=[m.pk]), {'accion': 'validar'})
        m.refresh_from_db()
        self.assertFalse(m.validado)
        self.client.force_login(self.ingeniero)
        self.client.post(reverse('asistente:manual', args=[m.pk]), {'accion': 'validar'})
        m.refresh_from_db()
        self.assertEqual((m.validado, m.validado_por), (True, self.ingeniero))
        ctx = {'producto': self.galleta, 'equipo': self.equipo, 'proceso': self.iny, 'ficha': None}
        # LUC-04: no es un defecto del catálogo, pero el manual lo explica (sección y página)
        r = motor.responder('La bomba hace ruido y vibra', ctx)
        self.assertIn('4.3 Ruido y vibracion', r['texto'])
        self.assertIn('pág. 1', r['texto'])
        # LUC-08: para otro defecto no se cita la línea de grumos
        quemado = Defecto.objects.get(proceso=self.iny, codigo='QUE')
        self.assertNotIn('Grumos', motor.responder('', ctx, quemado)['texto'])

    def test_luc02_falso_positivo_sale_del_conteo(self):
        r = motor.responder('cuéntame un chiste', {})
        c = motor.registrar_consulta(self.user, {}, 'cuéntame un chiste', r)
        self.client.post(reverse('asistente:uso'), {'accion': 'descartar', 'id': c.pk, 'motivo': 'era de prueba'})
        c.refresh_from_db()
        self.assertEqual((c.descartada, c.motivo_descarte, c.descartada_por), (True, 'era de prueba', self.user))
        self.assertContains(self.client.get(reverse('asistente:uso')), 'No aplica')
        self.assertFalse(Consulta.objects.filter(descartada=False, motivo_uso='Fuera de tema').exists())
