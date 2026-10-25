"""v1.81: correcciones del checklist (VEN-12, ALM-11, ALM-14, OBR-23, CRM-13), CRM ampliado (CRM-02 a CRM-21) y
servicios profesionales por proyecto (EMP-02, SRV-01, SRV-02, SRV-06)."""
import json
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.urls import reverse

from compras.models import Compra, CompraItem
from core.models import PerfilUsuario, Tercero
from core.modulos import GRUPOS
from inventario import servicios as inv
from obras.models import AvancePartida, Obra, Partida, Presupuesto
from obras.tests_comercial import HOY, Base as BaseObras
from ventas import crm_servicios as srv
from ventas.models import (ActividadCRM, CambioEtapa, CasoPostventa, ConfigCRM, ConsentimientoDatos,
                           ContratoServicio, Cotizacion, CotizacionItem, EquipoVenta, MetaVenta, Oportunidad,
                           Reasignacion)

from .tests_v179 import Base as BaseAlmacen


def usuario(nombre, modulos=(), acciones=None):
    u = User.objects.create_user(nombre, password='Clave-segura-9', first_name=nombre.title())
    for m in modulos:
        u.groups.add(Group.objects.get_or_create(name=GRUPOS[m])[0])
    if acciones is not None:
        PerfilUsuario.objects.create(usuario=u, acciones=list(acciones))
    return u


# ---------------------------------------------------------------- correcciones
class PedidoEnAprobacionTest(BaseObras):
    def test_no_se_factura_ni_se_prepara(self):
        """VEN-12: el pedido en aprobación no se convierte en comprobante (botón bloqueado y validación)."""
        from core import aprobaciones
        from ventas.aprobacion_pedido import bloqueo
        ped = Cotizacion.objects.create(tipo='PED', numero='PD01-00000099', tercero=self.entidad)
        CotizacionItem.objects.create(documento=ped, descripcion='Servicio', cantidad=1, precio_unitario=D('472'))
        aprobaciones.registrar('PEDIDO', ped, self.user, 'ENVIADA', 'Bajo la tarifa')
        self.assertIn('no está aprobado', bloqueo(ped))
        r = self.client.get(reverse('ventas:cot_detalle', args=[ped.pk]))
        self.assertContains(r, 'bi-lock')
        r = self.client.get(reverse('ventas:nuevo') + f'?cot={ped.pk}', follow=True)
        self.assertContains(r, 'no está aprobado')
        from inventario.almacen import pedidos_pendientes
        self.assertNotIn(ped.pk, [p.pk for p in pedidos_pendientes()])
        Cotizacion.objects.filter(pk=ped.pk).update(estado='APROBADO')
        ped.refresh_from_db()
        self.assertEqual(bloqueo(ped), '')
        cot = Cotizacion.objects.create(tipo='COT', numero='CT01-00000099', tercero=self.entidad)
        self.assertEqual(bloqueo(cot), '')  # las cotizaciones no se bloquean


class CotizacionYPedidoTest(BaseObras):
    def test_cotizacion_la_acepta_el_cliente(self):
        """VEN-13: la cotización no entra a la ruta de pedidos: se acepta y queda aprobada (sin estado intermedio)."""
        cot = Cotizacion.objects.create(tipo='COT', numero='CT01-00000088', tercero=self.entidad,
                                        niveles=[{'nivel': 1, 'nombre': 'Gerente General'}])
        CotizacionItem.objects.create(documento=cot, descripcion='Obra', cantidad=1, precio_unitario=D('380000'))
        r = self.client.get(reverse('ventas:cot_detalle', args=[cot.pk]))
        self.assertContains(r, 'Aceptada por el cliente')
        self.client.post(reverse('ventas:cot_estado', args=[cot.pk]), {'estado': 'APROBADO'})
        cot.refresh_from_db()
        self.assertEqual((cot.estado, cot.niveles), ('APROBADO', []))

    def test_pedido_atendido_con_ruta_pendiente_y_factura_anulada(self):
        """VEN-14: manda la ruta, no el estado; al anular la única factura el pedido vuelve a su estado."""
        from ventas.aprobacion_pedido import bloqueo, restaurar_al_anular
        from ventas.models import Venta
        ped = Cotizacion.objects.create(tipo='PED', numero='PD01-00000066', tercero=self.entidad, estado='ATENDIDO',
                                        niveles=[{'nivel': 1, 'nombre': 'Gerente General', 'estado': 'PENDIENTE'}])
        self.assertIn('espera el nivel 1', bloqueo(ped))
        Venta.objects.create(tipo_comprobante='01', serie='F001', numero='66', tercero=self.entidad,
                             fecha_emision=HOY, cotizacion=ped, estado='ANULADO')
        restaurar_al_anular(ped)
        ped.refresh_from_db()
        self.assertEqual(ped.estado, 'PENDIENTE')


class CruceLiberarPagoTest(BaseAlmacen):
    def test_liberar_revisa_todo_y_factura_propone_lo_recibido(self):
        """ALM-14: al liberar se revisan precio y cantidad (la anulación agrega su motivo aunque ya estuviera
        retenida) y la factura desde la OC propone lo recibido y no facturado sin ingresar al almacén."""
        from compras.cruce import cantidad_sugerida, motivos_retencion, retener_si_corresponde
        from compras.tolerancias import liberar_pago
        oc = self.oc()
        op1 = self.recibir(oc, '50')
        inv.confirmar(op1, self.admin)
        r = self.client.get(reverse('compras:nuevo') + f'?oc={oc.pk}')
        self.assertFalse(r.context['form'].initial.get('ingresar_almacen', True))
        self.assertEqual(cantidad_sugerida(oc)[self.harina.pk], D('50'))
        c = Compra.objects.create(tipo_comprobante='01', serie='F001', numero='2102', tercero=self.prov,
                                  fecha_emision=HOY, orden_compra=oc, ingresar_almacen=False)
        CompraItem.objects.create(documento=c, producto=self.harina, descripcion='Harina', cantidad=D('50'),
                                  precio_unitario=D('6.50'))
        c.calcular_totales()
        c.save()
        self.assertTrue(retener_si_corresponde(c))
        inv.anular(op1, self.admin, 'Recepción errada')
        c.refresh_from_db()
        self.assertIn('Precio', c.motivo_bloqueo_pago)
        self.assertIn('Con diferencia', c.motivo_bloqueo_pago)
        tipos = [t for t, _ in motivos_retencion(c)]
        self.assertEqual(sorted(set(tipos)), ['CANTIDAD', 'PRECIO'])
        with self.assertRaisesMessage(ValueError, 'No se puede liberar'):
            liberar_pago(c, self.admin, 'Precio aceptado por gerencia')


class AvanceFisicoTest(BaseObras):
    def setUp(self):
        super().setUp()
        self.obra = Obra.objects.create(nombre='Losa deportiva', cliente=self.entidad, estado='EJECUCION')
        p = Presupuesto.objects.create(obra=self.obra, tipo='CONTRACTUAL', estado='APROBADO')
        self.partida = Partida.objects.create(presupuesto=p, item='01.01.01', descripcion='Losa', unidad='m2',
                                              metrado=D('264'), precio_fijo=True, precio=D('80'))

    def test_no_negativos_ni_excesos_y_se_anula_con_motivo(self):
        """OBR-23."""
        from obras import ejecucion as ej
        with self.assertRaisesMessage(ej.ErrorEjecucion, 'superaría'):
            ej.registrar_avance(self.obra, self.partida, HOY, D('999999'), self.user)
        with self.assertRaisesMessage(ej.ErrorEjecucion, 'mayor que cero'):
            ej.registrar_avance(self.obra, self.partida, HOY, D('-5'), self.user)
        a, _ = ej.registrar_avance(self.obra, self.partida, HOY, D('100'), self.user)
        r = self.client.post(reverse('obras:avance_anular', args=[a.pk]), {'motivo': 'Metrado mal digitado'})
        self.assertEqual(r.status_code, 302)
        a.refresh_from_db()
        self.assertTrue(a.anulado)
        self.assertEqual(AvancePartida.objects.filter(obra=self.obra).count(), 0)  # no cuenta
        self.assertEqual(AvancePartida.todos.filter(obra=self.obra).count(), 1)  # queda en la lista
        r = self.client.get(reverse('obras:avance', args=[self.obra.pk]) + f'?partida={self.partida.pk}')
        self.assertContains(r, 'Metrado mal digitado')


class SelectorSinRepetidosTest(BaseObras):
    def test_cliente_una_sola_vez(self):
        """CRM-13: el selector liviano no repite la opción elegida."""
        from ventas.crm import OportunidadForm
        f = OportunidadForm(data={'tercero': [str(self.entidad.pk)]})
        html = str(f['tercero'])
        self.assertEqual(html.count(f'value="{self.entidad.pk}"'), 1)


# ---------------------------------------------------------------- CRM ampliado
class CRMBase(BaseObras):
    def oportunidad(self, **datos):
        datos = {'nombre': 'Ampliación de almacén', 'tercero': self.entidad, 'monto': D('50000'),
                 'responsable': self.user, **datos}
        return Oportunidad.objects.create(**datos)


class CRMOportunidadTest(CRMBase):
    def test_prospecto_duplicado_y_conversion(self):
        """CRM-02: aviso de duplicado y «Convertir en cliente» con su documento."""
        datos = {'nombre': 'Cerco perimétrico', 'prospecto': 'CONSTRUCTORA NUEVA', 'telefono': '987654321',
                 'etapa': 'PROSPECTO', 'monto': '30000', 'responsable': self.user.pk, 'consentimiento': 'on'}
        self.client.post(reverse('crm:nueva'), datos)
        r = self.client.post(reverse('crm:nueva'), datos, follow=True)
        self.assertContains(r, 'Posible duplicado')
        o = Oportunidad.objects.filter(prospecto='CONSTRUCTORA NUEVA').first()
        self.assertTrue(ConsentimientoDatos.objects.filter(oportunidad=o, canal='VERBAL').exists())
        r = self.client.post(reverse('crm:convertir', args=[o.pk]), {'tipo_doc': '1', 'numero_doc': '40404040',
                                                                    'nombre': 'JUAN PEREZ'})
        self.assertRedirects(r, reverse('crm:oportunidad', args=[o.pk]))
        o.refresh_from_db()
        self.assertEqual((o.tercero.numero_doc, o.prospecto), ('40404040', ''))
        r = self.client.post(reverse('crm:convertir', args=[o.pk]), {'tipo_doc': '6', 'numero_doc': '123'})
        self.assertContains(r, '11 dígitos')

    def test_validaciones_y_motivo_de_perdida(self):
        """CRM-07: monto mayor que cero, cierre no anterior a hoy y motivo de pérdida del catálogo con competidor."""
        base = {'nombre': 'Mal registrada', 'tercero': self.entidad.pk, 'etapa': 'PROSPECTO',
                'responsable': self.user.pk}
        r = self.client.post(reverse('crm:nueva'), {**base, 'monto': '-500',
                                                    'fecha_cierre': (HOY - timedelta(days=400)).isoformat()})
        self.assertContains(r, 'mayor que cero')
        self.assertContains(r, 'no puede ser anterior a hoy')
        o = self.oportunidad()
        datos = {'accion': 'datos', 'nombre': o.nombre, 'tercero': self.entidad.pk, 'monto': '50000',
                 'etapa': 'PERDIDA', 'responsable': self.user.pk, 'motivo_perdida_tipo': 'COMPETIDOR'}
        r = self.client.post(reverse('crm:oportunidad', args=[o.pk]), datos)
        self.assertContains(r, 'competidor que ganó')
        ActividadCRM.objects.create(oportunidad=o, tipo='LLAMADA', fecha=HOY - timedelta(days=1), descripcion='x',
                                    usuario=self.user)
        r = self.client.post(reverse('crm:oportunidad', args=[o.pk]), {**datos, 'competidor': 'GyM'})
        self.assertFalse(o.actividades.filter(hecha=False).exists())  # CRM-22: no quedan vencidas
        o.refresh_from_db()
        self.assertEqual((o.etapa, o.competidor, o.cerrada_en), ('PERDIDA', 'GyM', HOY))
        self.assertTrue(CambioEtapa.objects.filter(oportunidad=o, a='PERDIDA').exists())

    def test_cotizacion_vuelve_a_la_oportunidad_y_la_gana(self):
        """CRM-03: la cotización nace de la oportunidad, le da su monto y al aprobarse la gana."""
        o = self.oportunidad()
        r = self.client.get(reverse('ventas:cot_nuevo') + f'?oportunidad={o.pk}')
        self.assertEqual(r.context['form'].initial.get('tercero'), self.entidad.pk)
        cot = Cotizacion.objects.create(tipo='COT', numero='CT01-00000077', tercero=self.entidad)
        CotizacionItem.objects.create(documento=cot, descripcion='Servicio', cantidad=1, precio_unitario=D('42000'))
        cot.calcular_totales()
        cot.save()
        srv.vincular_cotizacion(cot, o)
        o.refresh_from_db()
        self.assertEqual((o.monto, o.etapa, o.cotizacion_id), (D('42000.00'), 'PROPUESTA', cot.pk))
        cot.estado = 'APROBADO'
        cot.save()
        o.refresh_from_db()
        self.assertEqual(o.etapa, 'GANADA')

    def test_cotizacion_sin_oportunidad_crea_la_suya(self):
        """CRM-14: desde el monto del parámetro la cotización crea su oportunidad."""
        from core.models import ParametroTributario
        ParametroTributario.objects.create(clave='CRM_EXIGIR_OPORTUNIDAD_DESDE', desde=HOY - timedelta(days=1),
                                           valor=D('1000'))
        cot = Cotizacion.objects.create(tipo='COT', numero='CT01-00000078', tercero=self.entidad, total=D('5000'))
        o = srv.asegurar_oportunidad(cot, self.user)
        self.assertEqual((o.origen, o.etapa), ('COTIZACION', 'PROPUESTA'))
        chica = Cotizacion.objects.create(tipo='COT', numero='CT01-00000079', tercero=self.entidad, total=D('500'))
        self.assertIsNone(srv.asegurar_oportunidad(chica, self.user))

    def test_siguiente_actividad_obligatoria(self):
        """CRM-04: al marcar realizada la última actividad pendiente se exige la siguiente."""
        o = self.oportunidad()
        a = ActividadCRM.objects.create(oportunidad=o, tipo='LLAMADA', fecha=HOY - timedelta(days=1),
                                        descripcion='Llamar', usuario=self.user, responsable=self.user)
        r = self.client.post(reverse('crm:oportunidad', args=[o.pk]), {'accion': 'hecha', 'actividad': a.pk},
                             follow=True)
        self.assertContains(r, 'siguiente paso')
        a.refresh_from_db()
        self.assertFalse(a.hecha)
        self.client.post(reverse('crm:oportunidad', args=[o.pk]), {
            'accion': 'hecha', 'actividad': a.pk, 'resultado': 'Pidió propuesta', 'sig_tipo': 'REUNION',
            'sig_fecha': (HOY + timedelta(days=2)).isoformat(), 'sig_descripcion': 'Presentar propuesta'})
        a.refresh_from_db()
        self.assertTrue(a.hecha)
        self.assertTrue(o.actividades.filter(hecha=False, tipo='REUNION').exists())
        from core.alertas import calcular
        ActividadCRM.objects.create(oportunidad=o, tipo='CORREO', fecha=HOY, descripcion='Enviar', usuario=self.user)
        self.assertIn('Actividades del CRM para hoy', [x['titulo'] for x in calcular(self.user)])
        self.assertEqual(self.client.get(reverse('crm:actividades')).status_code, 200)

    def test_fecha_de_entrega_factible(self):
        """CRM-12 (manufactura): stock hoy; lo que falta se fabrica con la receta y el plazo de sus insumos."""
        from core.models import Almacen, Producto
        from produccion.models import ComponenteLista, ListaMateriales
        Almacen.objects.get_or_create(codigo='PRI', defaults={'nombre': 'Principal', 'es_principal': True})
        tela = Producto.objects.create(codigo='TELA', nombre='Tela', tipo='BIEN', tiempo_entrega=10)
        polo = Producto.objects.create(codigo='POLO', nombre='Polo', tipo='BIEN', tiempo_entrega=2)
        lista = ListaMateriales.objects.create(producto=polo, cantidad_base=D('1'))
        ComponenteLista.objects.create(lista=lista, producto=tela, cantidad=D('1.5'))
        fecha, detalle = srv.fecha_entrega([(polo, D('100'))], HOY)
        self.assertEqual(fecha, HOY + timedelta(days=12))  # 10 de la tela + 2 de fabricación
        self.assertIn('se fabrica', detalle[0])

    def test_credito_del_cliente(self):
        """CRM-16: límite, deuda, vencida y días de pago en la oportunidad."""
        Tercero.objects.filter(pk=self.entidad.pk).update(limite_credito=D('10000'))
        self.entidad.refresh_from_db()
        c = srv.credito(self.entidad)
        self.assertEqual((c['limite'], c['deuda'], c['en_mora']), (D('10000'), D('0'), False))
        o = self.oportunidad()
        self.assertContains(self.client.get(reverse('crm:oportunidad', args=[o.pk])), 'Crédito del cliente')


class CRMVisibilidadTest(CRMBase):
    def test_vendedor_jefe_y_gerente_y_reasignar_al_desactivar(self):
        """CRM-15."""
        ana = usuario('ana', ['crm'], ['crm.registrar'])
        luis = usuario('luis', ['crm'], ['crm.registrar'])
        jefe = usuario('jefa', ['crm'], ['crm.registrar'])
        equipo = EquipoVenta.objects.create(nombre='Lima', jefe=jefe)
        equipo.miembros.add(ana)
        oa = self.oportunidad(nombre='De Ana', responsable=ana)
        ol = self.oportunidad(nombre='De Luis', responsable=luis)
        self.client.force_login(ana)
        self.assertEqual(self.client.get(reverse('crm:oportunidad', args=[ol.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('crm:oportunidad', args=[oa.pk])).status_code, 200)
        self.client.force_login(jefe)
        nombres = [o.nombre for o in self.client.get(reverse('crm:lista')).context['oportunidades']]
        self.assertIn('De Ana', nombres)
        self.assertNotIn('De Luis', nombres)
        self.client.force_login(self.user)
        ActividadCRM.objects.create(oportunidad=oa, tipo='LLAMADA', fecha=HOY, descripcion='x', usuario=ana)
        datos = {'username': 'ana', 'first_name': 'Ana', 'last_name': '', 'email': '', 'modulos': ['crm'],
                 'acciones': ['crm.registrar']}
        r = self.client.post(reverse('usuario_editar', args=[ana.pk]), datos)
        self.assertContains(r, 'elija a quién pasarlas')
        self.client.post(reverse('usuario_editar', args=[ana.pk]), {**datos, 'reasignar_crm': luis.pk})
        oa.refresh_from_db()
        self.assertEqual(oa.responsable, luis)
        self.assertTrue(Reasignacion.objects.filter(de=ana, a=luis, oportunidades=1).exists())


class CRMReportesTest(CRMBase):
    def test_reportes_metas_comisiones_e_inactivos(self):
        """CRM-05, CRM-08 y CRM-12."""
        o = self.oportunidad(origen='WEB')
        srv.cambiar_etapa(o, 'GANADA', self.user)
        p = self.oportunidad(nombre='Perdida', motivo_perdida_tipo='PRECIO')
        srv.cambiar_etapa(p, 'PERDIDA', self.user)
        r = self.client.get(reverse('crm:reportes'))
        self.assertContains(r, 'Conversión por etapa')
        self.assertContains(r, 'Precio')
        r = self.client.get(reverse('crm:reportes') + '?formato=excel&seccion=3')
        self.assertIn('spreadsheet', r['Content-Type'])
        MetaVenta.objects.create(periodo=HOY.strftime('%Y%m'), vendedor=self.user, monto=D('100000'))
        self.assertContains(self.client.get(reverse('crm:metas')), '50.0%')
        self.assertEqual(self.client.get(reverse('crm:comisiones')).status_code, 200)
        self.assertEqual(self.client.get(reverse('crm:inactivos')).status_code, 200)
        self.assertEqual(self.client.get(reverse('crm:operaciones')).status_code, 200)


class CRMWebYMovilTest(CRMBase):
    def test_formulario_web(self):
        """CRM-09: crea la oportunidad web, agenda «Llamar hoy» y registra el consentimiento; filtra robots."""
        cfg = ConfigCRM.actual()
        ConfigCRM.objects.filter(pk=cfg.pk).update(web_activo=True, responsable_web=self.user)
        url = reverse('crm_web', args=['default', cfg.token_web])
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 200)
        datos = {'nombre': 'María Ríos', 'empresa': 'Clínica Sur', 'telefono': '999888777',
                 'mensaje': 'Quiero una cotización', 'consentimiento': '1', 't': '1'}
        self.assertContains(self.client.post(url, datos), 'Gracias')
        o = Oportunidad.objects.get(prospecto='Clínica Sur')
        self.assertEqual((o.origen, o.responsable), ('WEB', self.user))
        self.assertTrue(o.actividades.filter(descripcion__startswith='Llamar hoy').exists())
        self.assertTrue(ConsentimientoDatos.objects.filter(oportunidad=o, canal='WEB').exists())
        self.client.post(url, {**datos, 'empresa': 'Robot', 'sitio_web': 'spam'})
        self.assertFalse(Oportunidad.objects.filter(prospecto='Robot').exists())
        self.assertEqual(self.client.get(reverse('crm_web', args=['default', 'otra'])).status_code, 404)

    def test_correo_a_oportunidad(self):
        """CRM-09: cada correo del buzón comercial crea su oportunidad (origen «Correo»), sin consentimiento web."""
        from email.message import EmailMessage
        from ventas.crm_web import procesar_correo
        ConfigCRM.objects.filter(pk=ConfigCRM.actual().pk).update(responsable_web=self.user)
        m = EmailMessage()
        m['From'] = 'Pedro Soto <pedro@agricolasur.pe>'
        m['Subject'] = 'Cotización de riego tecnificado'
        m.set_content('Necesitamos 20 hectáreas.')
        o = procesar_correo(m)
        self.assertEqual((o.origen, o.email, o.prospecto, o.responsable), ('CORREO', 'pedro@agricolasur.pe',
                                                                           'AGRICOLASUR', self.user))
        self.assertIn('20 hectáreas', o.notas)
        self.assertFalse(ConsentimientoDatos.objects.filter(oportunidad=o).exists())

    def test_celular_sin_senal(self):
        """CRM-19: lo registrado en el celular llega una sola vez."""
        self.assertEqual(self.client.get(reverse('crm:movil')).status_code, 200)
        items = [{'tipo': 'oportunidad', 'uid': 'a1', 'nombre': 'Pedido en campo', 'cliente': self.entidad.pk,
                  'monto': '1500'},
                 {'tipo': 'visita', 'uid': 'a2', 'cliente': self.entidad.pk, 'nota': 'Visita', 'lat': '-12.04',
                  'lng': '-77.03'}]
        url = reverse('crm:movil_sincronizar')
        r = self.client.post(url, json.dumps(items), content_type='application/json')
        self.assertTrue(all(x['ok'] for x in r.json()['resultado'].values()))
        self.client.post(url, json.dumps(items), content_type='application/json')
        self.assertEqual(Oportunidad.objects.filter(nombre='Pedido en campo').count(), 1)
        self.assertEqual(ActividadCRM.objects.filter(tipo='VISITA', tercero=self.entidad).count(), 1)


class CRMGestionTest(CRMBase):
    def test_postventa_campanas_contratos_datos_y_ficha(self):
        """CRM-06, CRM-11, CRM-12, CRM-17, CRM-18, CRM-20 y CRM-21."""
        o = self.oportunidad()
        srv.cambiar_etapa(o, 'GANADA', self.user)
        r = self.client.post(reverse('crm:caso_nuevo'), {'tercero': self.entidad.pk, 'tipo': 'RECLAMO',
                                                         'asunto': 'Fisura en losa', 'estado': 'ABIERTO',
                                                         'responsable': self.user.pk})
        self.assertEqual(r.status_code, 302)
        caso = CasoPostventa.objects.get()
        self.assertTrue(caso.numero.startswith('PV-'))
        self.client.post(reverse('crm:encuesta_nueva'), {'tercero': self.entidad.pk, 'motivo': 'Cierre de obra',
                                                         'fecha': HOY.isoformat(), 'satisfaccion': 4,
                                                         'recomendaria': 9})
        self.assertContains(self.client.get(reverse('crm:encuestas')), 'NPS')
        self.client.post(reverse('crm:campana_nueva'), {'nombre': 'Feria Expo', 'canal': 'FERIA',
                                                        'inicio': HOY.isoformat(), 'costo': '1000', 'activa': 'on'})
        self.assertContains(self.client.get(reverse('crm:campanas')), 'Feria Expo')
        c = ContratoServicio.objects.create(tercero=self.entidad, descripcion='Mantenimiento', inicio=HOY,
                                            fin=HOY + timedelta(days=10), valor=D('12000'))
        r = self.client.post(reverse('crm:contratos'), {'renovar': c.pk})
        c.refresh_from_db()
        self.assertEqual(c.oportunidad_renovacion.origen, 'RENOVACION')
        r = self.client.post(reverse('crm:cliente', args=[self.entidad.pk]), {
            'accion': 'contacto', 'nombre': 'Ing. Rosa Díaz', 'rol': 'DECIDE', 'cargo': 'Gerente',
            'email': 'rosa@cliente.pe', 'consentimiento': 'on'})
        r = self.client.get(reverse('crm:cliente', args=[self.entidad.pk]))
        self.assertContains(r, 'Ing. Rosa Díaz')
        self.assertContains(r, 'Fisura en losa')
        cons = ConsentimientoDatos.objects.get(titular='Ing. Rosa Díaz')
        r = self.client.post(reverse('crm:datos_personales'), {'id': cons.pk, 'accion': 'exportar'})
        self.assertIn('rosa@cliente.pe', r.content.decode())
        self.client.post(reverse('crm:datos_personales'), {'id': cons.pk, 'accion': 'suprimir'})
        cons.refresh_from_db()
        self.assertEqual(cons.estado, 'SUPRIMIDO')
        self.assertFalse(self.entidad.contactos_crm.filter(nombre='Ing. Rosa Díaz').exists())
        html = self.client.get(reverse('crm:configuracion')).content.decode()  # SEG-06
        self.assertIn('autocomplete="new-password"', html)
        self.assertRegex(html, r'name="correo_usuario"[^>]*autocomplete="off"|autocomplete="off"[^>]*name="correo_usuario"')
        for nombre in ('crm:clientes', 'crm:contactos', 'crm:segmentos', 'crm:visitas', 'crm:equipos',
                       'crm:reasignaciones', 'crm:datos_personales', 'crm:configuracion', 'crm:casos'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, nombre)

    def test_proforma_inmobiliaria(self):
        """CRM-11: la oportunidad se liga a la unidad y emite la proforma con cronograma."""
        from obras.models import Unidad
        proyecto = Obra.objects.create(nombre='Residencial Sol', tipo='INMOBILIARIA')
        u = Unidad.objects.create(obra=proyecto, codigo='D-301', area_techada=D('80'), precio_lista=D('350000'))
        o = self.oportunidad(unidad=u)
        r = self.client.get(reverse('crm:proforma', args=[o.pk]) + '?cuotas=12&inicial=10')
        self.assertContains(r, 'Cuota 12')
        self.assertContains(r, 'D-301')


# ---------------------------------------------------------------- servicios por proyecto
class ServiciosProyectoTest(BaseObras):
    def test_giro_entregables_horas_y_cierre(self):
        """EMP-02, SRV-01, SRV-02 y SRV-06."""
        from core import giro
        from core.models import Empresa
        from obras import servicios as s
        from planillas.models import Trabajador
        self.assertIn('inventario', giro.sugerir_ocultos(['SERVICIOS_PROYECTO']))
        self.assertNotIn('obras', giro.sugerir_ocultos(['SERVICIOS_PROYECTO']))
        Empresa.objects.update_or_create(pk=Empresa.objects.values_list('pk', flat=True).first() or 1, defaults={
            'ruc': '20600000001', 'razon_social': 'ARQUITECTOS SAC', 'giros': ['SERVICIOS_PROYECTO']})
        self.assertEqual(self.client.get(reverse('obras:obra_nueva')).context['form'].fields['tipo'].initial,
                         'SERVICIO')
        p = Obra.objects.create(nombre='Hospital Regional (expediente)', tipo='SERVICIO', cliente=self.entidad,
                                monto_contratado=D('118000'), residente=self.user, horas_presupuestadas=D('100'))
        self.assertIsNone(p.almacen_id)  # sin almacén de obra
        self.assertIsNotNone(p.centro_costo_id)
        e = s.nuevo_entregable(p, nombre='Anteproyecto', porcentaje=D('40'),
                               fecha_comprometida=HOY - timedelta(days=5), horas_presupuestadas=D('40'))
        self.assertEqual(e.valor, D('40000.00'))
        s.entregar(e, HOY)
        self.assertEqual(e.dias_atraso, 5)
        self.assertEqual(e.penalidad, D('200.00'))  # 0.1 % diario × 5 días
        with self.assertRaises(s.ErrorServicio):
            s.facturar(e, self.user)  # sin conformidad no se factura
        s.conformidad(e, 'Ing. Pérez, jefe de proyectos del cliente')
        comp = s.facturar(e, self.user)
        self.assertEqual((comp.base_imponible, comp.detraccion_codigo), (D('40000.00'), '022'))
        t = Trabajador.objects.create(nombres='Luis', apellido_paterno='Rojas', numero_doc='41414141',
                                      sueldo=D('5760'), fecha_ingreso=HOY - timedelta(days=100))
        self.assertEqual(s.costo_hora(t), D('43.50'))  # 5760 × 145 % / 192
        # v1.84 (SRV-13): horas generales del proyecto (sin entregable): se reparten por el % facturado del contrato
        parte = s.registrar_parte(p, HOY, D('30'), self.user, trabajador=t)
        with self.assertRaisesMessage(s.ErrorServicio, 'máximo'):
            s.registrar_parte(p, HOY, D('60'), self.user, trabajador=t)
        r = self.client.post(reverse('obras:partes_aprobar'), {'parte': parte.pk, 'accion': 'aprobar'})
        parte.refresh_from_db()
        self.assertEqual(parte.estado, 'APROBADO')
        h = s.horas_proyecto(p)
        self.assertEqual((h['horas'], h['avance_horas']), (D('30'), D('30.0')))
        self.assertContains(self.client.get(reverse('obras:horas', args=[p.pk])), 'Anteproyecto')
        periodo = HOY.strftime('%Y%m')
        sit = s.situacion(p, periodo)
        self.assertEqual(sit['costo'], D('1305.00'))
        self.assertEqual(sit['costo_proceso'], D('783.00'))  # 60 % no facturado del contrato
        asiento = s.registrar_cierre(periodo, self.user)
        self.assertTrue(asiento.cuadrado)
        with self.assertRaisesMessage(s.ErrorServicio, 'ya se registró'):
            s.registrar_cierre(periodo, self.user)
        for nombre in ('obras:partes_horas', 'obras:garantias', 'obras:servicios_proceso'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, nombre)
        self.assertContains(self.client.get(reverse('obras:entregables', args=[p.pk])), 'Facturado')
