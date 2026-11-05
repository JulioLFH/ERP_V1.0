"""v1.104: agentes con IA (piloto Ventas). La API de Claude se simula: se prueban las herramientas contra el ERP, los
permisos, el bucle de herramientas, el historial (solo crece al final), el tope de gasto y las pantallas."""
import copy
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import anthropic
from anthropic.types.beta import BetaMessage
from django.urls import get_resolver, reverse
from django.utils import timezone

from core.models import PerfilUsuario, Tercero
from core.modulos import RUTAS_COMPARTIDAS
from core.permisos import RUTAS
from core.tests_modulo_tesoreria import Base
from ventas.models import Cotizacion, Venta

from . import motor
from .herramientas import ejecutar
from .herramientas import ventas as hv
from .models import ConfigAgentes, Conversacion, LlamadaHerramienta, Mensaje

HOY = timezone.localdate()
D = Decimal


def respuesta(contenido, stop='end_turn', entrada=1000, salida=200):
    return BetaMessage.model_validate({
        'id': 'msg_x', 'type': 'message', 'role': 'assistant', 'model': 'claude-opus-5-5', 'content': contenido,
        'stop_reason': stop, 'stop_sequence': None,
        'usage': {'input_tokens': entrada, 'output_tokens': salida, 'cache_read_input_tokens': 0,
                  'cache_creation_input_tokens': 0}})


class ClienteFalso:
    """Devuelve las respuestas en orden y guarda lo que se le envió (copia: el historial sigue creciendo)."""
    def __init__(self, *respuestas, error=None):
        self.respuestas, self.error, self.enviados = list(respuestas), error, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._crear))

    def _crear(self, **kwargs):
        self.enviados.append(copy.deepcopy(kwargs))
        if self.error is not None:
            raise self.error
        return self.respuestas.pop(0)


class BaseAgentes(Base):
    def setUp(self):
        super().setUp()
        ConfigAgentes.objects.create(activo=True, api_key='sk-prueba', tope_mensual_usd=D('30'))
        self.cliente = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20100070970', nombre='UNICON',
                                              limite_credito=D('5000'), dias_credito=30)
        self.vencida = self.venta('00000001', 'ana', HOY - timedelta(days=60), HOY - timedelta(days=45), D('1180'))
        self.al_dia = self.venta('00000002', 'luis', HOY, HOY + timedelta(days=30), D('590'))
        self.vendedor_ana = self.usuario('ana', ['ventas'], acciones=['ventas.agente'])
        PerfilUsuario.objects.filter(usuario=self.vendedor_ana).update(vendedor='ana')
        self.gerente = self.usuario('gerente', ['ventas'], acciones=['ventas.agente'])

    def venta(self, numero, vendedor, emision, vence, total):
        base = (total / D('1.18')).quantize(D('0.01'))
        return Venta.objects.create(tercero=self.cliente, tipo_comprobante='01', serie='F001', numero=numero,
                                    forma_pago='CREDITO', fecha_emision=emision, fecha_vencimiento=vence,
                                    vendedor=vendedor, base_imponible=base, igv=total - base, total=total)

    def datos(self, nombre, usuario, **entrada):
        texto, ok = ejecutar(next(h for h in hv.HERRAMIENTAS if h.nombre == nombre), entrada, usuario)
        self.assertTrue(ok, texto)
        return json.loads(texto)


class HerramientasVentasTest(BaseAgentes):
    def test_deuda_del_cliente_vencida_y_disponible(self):
        d = self.datos('deuda_cliente', self.gerente, cliente_id=self.cliente.pk)
        self.assertEqual((d['deuda_total_soles'], d['deuda_vencida_soles']), (1770.0, 1180.0))
        self.assertEqual(d['credito_disponible_soles'], 3230.0)
        self.assertEqual([c['documento'] for c in d['comprobantes']], ['01 F001-00000001', '01 F001-00000002'])
        self.assertEqual(d['comprobantes'][0]['dias_vencido'], 45)

    def test_el_vendedor_ve_solo_lo_suyo(self):
        d = self.datos('deuda_cliente', self.vendedor_ana, cliente_id=self.cliente.pk)
        self.assertEqual((d['deuda_total_soles'], d['solo_ventas_del_vendedor']), (1180.0, True))
        texto, ok = ejecutar(hv.documento_de_venta, {'numero': 'F001-2'}, self.vendedor_ana)
        self.assertFalse(ok)
        self.assertIn('no lo ve', texto)
        self.assertEqual(self.datos('documento_de_venta', self.gerente, numero='f001-2')['total'], 590.0)

    def test_ventas_del_periodo_y_cobranza_vencida(self):
        periodo = HOY.strftime('%Y%m')
        d = self.datos('ventas_del_periodo', self.gerente, periodo=periodo, agrupar_por='vendedor')
        self.assertEqual(d['comprobantes'], 1 if self.vencida.periodo != periodo else 2)
        self.assertIn('luis', [g['grupo'] for g in d['grupos']])
        c = self.datos('cobranza_vencida', self.gerente, dias_minimos=30)
        self.assertEqual((c['clientes'], c['total_vencido_soles'], c['detalle'][0]['max_dias_vencido']),
                         (1, 1180.0, 45))
        self.assertEqual(self.datos('cobranza_vencida', self.gerente, dias_minimos=60)['clientes'], 0)

    def test_pedidos_por_aprobar_y_busqueda(self):
        Cotizacion.objects.create(tipo='PED', numero='PD01-00000007', tercero=self.cliente, total=D('100'),
                                  vendedor='luis', motivo_aprobacion='Bajo la tarifa: P001')
        p = self.datos('pedidos_de_venta', self.gerente, situacion='por_aprobar')['pedidos']
        self.assertEqual((p[0]['pedido'], p[0]['motivo_aprobacion']), ('PD01-00000007', 'Bajo la tarifa: P001'))
        self.assertEqual(self.datos('pedidos_de_venta', self.vendedor_ana, situacion='abiertos')['pedidos'], [])
        self.assertEqual(self.datos('buscar_cliente', self.gerente, texto='2010007')[0]['id'], self.cliente.pk)

    def test_sin_permiso_y_parametros_invalidos(self):
        sin = self.usuario('cajera', ['ventas'], acciones=['ventas.emitir'])
        self.assertEqual(ejecutar(hv.deuda_cliente, {'cliente_id': self.cliente.pk}, sin)[1], False)
        texto, ok = ejecutar(hv.deuda_cliente, {'cliente_id': 1, 'otro': 2}, self.gerente)
        self.assertFalse(ok)
        self.assertIn('sobran', texto)
        self.assertFalse(ejecutar(hv.ventas_del_periodo, {'periodo': 'octubre', 'agrupar_por': 'cliente'},
                                  self.gerente)[1])

    def test_definiciones_estrictas(self):
        for h in hv.HERRAMIENTAS:
            d = h.definicion()
            self.assertTrue(d['strict'])
            self.assertEqual(set(d['input_schema']['required']), set(d['input_schema']['properties']))
            self.assertFalse(d['input_schema']['additionalProperties'])


class MotorTest(BaseAgentes):
    def conversacion(self, usuario=None):
        return Conversacion.objects.create(agente='ventas', usuario=usuario or self.gerente)

    def test_usa_la_herramienta_y_responde(self):
        falso = ClienteFalso(
            respuesta([{'type': 'thinking', 'thinking': '', 'signature': 'firma'},
                       {'type': 'tool_use', 'id': 'tu_1', 'name': 'deuda_cliente',
                        'input': {'cliente_id': self.cliente.pk}}], stop='tool_use'),
            respuesta([{'type': 'text', 'text': 'UNICON debe S/ 1,770.00.'}]))
        c = self.conversacion()
        m = motor.responder(c, '¿Cuánto debe UNICON?', self.gerente, cliente=falso)
        self.assertEqual((m.texto, m.error), ('UNICON debe S/ 1,770.00.', False))
        self.assertEqual(m.costo_usd, D('0.0160'))  # 2 x (1000 x 4 + 200 x 20) / 1e6
        primera, segunda = falso.enviados
        self.assertEqual((primera['model'], primera['fallbacks'], primera['output_config']),
                         ('claude-opus-5-5', 'default', {'effort': 'medium'}))
        self.assertIn('UNICON', primera['system'] + str(segunda['messages'][-1]))
        resultado = segunda['messages'][-1]['content'][0]
        self.assertEqual((resultado['type'], resultado['tool_use_id']), ('tool_result', 'tu_1'))
        c.refresh_from_db()
        self.assertEqual([x['role'] for x in c.historial], ['user', 'assistant', 'user', 'assistant'])
        self.assertEqual(c.historial[1]['content'][0], {'type': 'thinking', 'thinking': '', 'signature': 'firma'})
        llamada = LlamadaHerramienta.objects.get()
        self.assertEqual((llamada.herramienta, llamada.ok, llamada.mensaje), ('deuda_cliente', True, m))
        # la siguiente pregunta reenvía el historial sin cambios y agrega al final
        falso2 = ClienteFalso(respuesta([{'type': 'text', 'text': 'Listo.'}]))
        motor.responder(c, 'Gracias', self.gerente, cliente=falso2)
        self.assertEqual(falso2.enviados[0]['messages'][:4], c.historial[:4])

    def test_tope_de_consultas_obliga_a_responder(self):
        ConfigAgentes.objects.update(max_herramientas=1, modelo='claude-haiku-5-5')
        uso = {'type': 'tool_use', 'id': 'tu_1', 'name': 'cobranza_vencida', 'input': {'dias_minimos': 0}}
        falso = ClienteFalso(respuesta([uso], stop='tool_use'), respuesta([{'type': 'text', 'text': 'Resumen.'}]))
        motor.responder(self.conversacion(), 'Vencidos', self.gerente, cliente=falso)
        self.assertNotIn('tool_choice', falso.enviados[0])
        self.assertEqual(falso.enviados[1]['tool_choice'], {'type': 'none'})
        self.assertNotIn('fallbacks', falso.enviados[0])  # Haiku no tiene respaldo del servidor

    def test_error_de_la_api_no_toca_el_historial(self):
        c = self.conversacion()
        error = anthropic.APIConnectionError(request=None)
        m = motor.responder(c, 'Hola', self.gerente, cliente=ClienteFalso(error=error))
        c.refresh_from_db()
        self.assertTrue(m.error)
        self.assertEqual(c.historial, [])

    def test_bloqueos(self):
        sin = self.usuario('cajera', ['ventas'], acciones=['ventas.emitir'])
        with self.assertRaisesMessage(motor.ErrorAgente, 'permiso'):
            motor.responder(self.conversacion(sin), 'Hola', sin, cliente=ClienteFalso())
        c = self.conversacion()
        Mensaje.objects.create(conversacion=c, rol='AGENTE', texto='x', costo_usd=D('30'))
        with self.assertRaisesMessage(motor.ErrorAgente, 'tope de gasto'):
            motor.responder(c, 'Hola', self.gerente, cliente=ClienteFalso())
        ConfigAgentes.objects.update(activo=False)
        self.assertIn('no están activos', motor.bloqueo(motor.AGENTES['ventas'], self.gerente))


class PantallasAgentesTest(BaseAgentes):
    def test_cada_pantalla_tiene_modulo_y_permiso(self):
        nombres = {f'agentes:{n}' for n in get_resolver().namespace_dict['agentes'][1].reverse_dict
                   if isinstance(n, str)}
        self.assertEqual(nombres - set(RUTAS_COMPARTIDAS), set())
        self.assertEqual(nombres - set(RUTAS), set())

    def test_chat_con_permiso(self):
        self.client.force_login(self.usuario('cajera', ['ventas'], acciones=['ventas.emitir']))
        self.assertEqual(self.client.get(reverse('agentes:ventas')).status_code, 403)
        self.client.force_login(self.gerente)
        r = self.client.get(reverse('agentes:ventas'))
        self.assertContains(r, '¿Qué pedidos están esperando aprobación?')
        falso = ClienteFalso(respuesta([{'type': 'text', 'text': 'Hay 2 comprobantes.'}]))
        with patch('agentes.motor._cliente', return_value=falso):
            r = self.client.post(reverse('agentes:ventas'), {'pregunta': '¿Cuántos comprobantes?'})
        c = Conversacion.objects.get()
        self.assertRedirects(r, f'{reverse("agentes:ventas")}?c={c.pk}#fin', fetch_redirect_response=False)
        self.assertContains(self.client.get(f'{reverse("agentes:ventas")}?c={c.pk}'), 'Hay 2 comprobantes.')
        self.client.force_login(self.vendedor_ana)  # otra persona no abre la conversación
        self.assertEqual(self.client.get(f'{reverse("agentes:ventas")}?c={c.pk}').status_code, 404)

    def test_configuracion_mantiene_la_clave(self):
        self.client.force_login(self.gerente)
        self.assertEqual(self.client.get(reverse('agentes:configuracion')).status_code, 403)
        admin = self.usuario('admin.ia', ['ajustes'], acciones=['ajustes.agentes'])
        self.client.force_login(admin)
        r = self.client.post(reverse('agentes:configuracion'), {
            'activo': 'on', 'api_key': '', 'modelo': 'claude-opus-5-5', 'esfuerzo': 'low',
            'tope_mensual_usd': '50', 'max_herramientas': '6'})
        self.assertRedirects(r, reverse('agentes:configuracion'))
        cfg = ConfigAgentes.actual()
        self.assertEqual((cfg.api_key, cfg.esfuerzo, cfg.tope_mensual_usd), ('sk-prueba', 'low', D('50')))
        self.assertNotContains(self.client.get(reverse('agentes:configuracion')), 'sk-prueba')
        self.assertEqual(self.client.get(reverse('agentes:conversaciones')).status_code, 200)
