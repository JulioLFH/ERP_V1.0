"""Puente con Ceiba GO: el portal del cliente pide recojos, consulta sus solicitudes con el estado de las guías y sus
comprobantes, y manda su cola de sincronización. Todo con una clave de API del ERP."""
import json

from core.models import ApiToken, Empresa, Tercero

from . import servicios as srv
from .models import RegistroCeibaGO, SolicitudRecojo
from .tests import RUC_CLIENTE, Base


class PuenteCeibaGOTest(Base):
    def setUp(self):
        super().setUp()
        self.integracion = self.usuario('ceibago', modulos=['operador_logistico'],
                                        acciones=['operador_logistico.planificar'])
        _, self.clave = ApiToken.crear(self.integracion, 'Ceiba GO', solo_lectura=False)

    def get(self, ruta, clave=None, **params):
        return self.client.get(f'/api/ceibago/{ruta}', params,
                               HTTP_AUTHORIZATION=f'Bearer {clave or self.clave}')

    def post(self, ruta, datos, clave=None):
        return self.client.post(f'/api/ceibago/{ruta}', json.dumps(datos), content_type='application/json',
                                HTTP_AUTHORIZATION=f'Bearer {clave or self.clave}')

    def test_sin_clave_no_entra(self):
        r = self.client.get('/api/ceibago/cliente/', {'documento': RUC_CLIENTE})
        self.assertEqual(r.status_code, 401)

    def test_ficha_del_cliente_con_credito_y_almacen(self):
        r = self.get('cliente/', documento=RUC_CLIENTE)
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d['habilitado'])
        self.assertTrue(d['con_contrato'])
        self.assertEqual(d['almacen'], 'Almacén Callao, Av. Gambetta 5')
        self.assertEqual(d['credito']['dias'], 30)
        self.assertEqual(self.get('cliente/', documento='20999999999').json()['codigo'], 'no_registrado')

    def test_pide_recojo_y_queda_como_solicitud_del_canal_ceiba_go(self):
        r = self.post('solicitud/', {'documento': RUC_CLIENTE, 'bultos': 3, 'fecha': '2026-11-27',
                                     'contacto': 'Pedro', 'mensaje': 'Cajas en la puerta 2',
                                     'usuario': 'despachos@cliente.pe', 'referencia': 'cuenta-7'})
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        sol = SolicitudRecojo.objects.get(numero=d['numero'])
        self.assertEqual((sol.canal, sol.estado, sol.bultos, sol.contacto), ('CEIBAGO', 'ASIGNADO', 3, 'Pedro'))
        self.assertEqual(sol.referencia_externa, 'cuenta-7')
        self.assertEqual(str(sol.fecha_recojo), '2026-11-27')
        self.assertEqual(sol.almacen, 'Almacén Callao, Av. Gambetta 5')
        self.assertEqual(d['estado_nombre'], 'Asignado')
        self.assertEqual(d['bitacora'][0]['a'], 'Asignado')

    def test_datos_invalidos_y_cliente_no_habilitado(self):
        self.assertEqual(self.post('solicitud/', {'documento': RUC_CLIENTE, 'bultos': 'muchos'}).status_code, 422)
        self.assertEqual(self.post('solicitud/', {'documento': RUC_CLIENTE, 'fecha': '27/11'}).status_code, 422)
        # v1.117.14: con crédito pero sin contrato no pide recojos (proceso regular)
        from .models import ClienteLogistico
        ClienteLogistico.objects.filter(tercero=self.cliente).update(servicio_logistico=False)
        r = self.post('solicitud/', {'documento': RUC_CLIENTE})
        self.assertEqual((r.status_code, r.json()['codigo']), (422, 'no_habilitado'))
        self.assertFalse(SolicitudRecojo.objects.exists())

    def test_clave_de_solo_lectura_no_pide_recojos(self):
        _, lectura = ApiToken.crear(self.integracion, 'Solo lectura', solo_lectura=True)
        self.assertEqual(self.post('solicitud/', {'documento': RUC_CLIENTE}, clave=lectura).status_code, 403)
        self.assertEqual(self.get('cliente/', clave=lectura, documento=RUC_CLIENTE).status_code, 200)

    def test_el_cliente_ve_el_estado_de_sus_guias_al_recogerlas(self):
        sol = self.solicitud_con_guias({'numero': 11})
        operador = self.usuario('op', modulos=['operador_logistico'], acciones=['operador_logistico.operar'])
        guia = sol.guias.get()
        srv.registrar_recojo(operador, guia_id=guia.pk)
        d = self.get('solicitudes/', documento=RUC_CLIENTE).json()
        self.assertEqual(len(d['resultados']), 1)
        s = d['resultados'][0]
        self.assertEqual(s['numero'], sol.numero)
        g = s['guias'][0]
        self.assertEqual(g['numero'], 'EG01-11')
        self.assertEqual(g['estado']['codigo'], 'CONFIRMADO')
        self.assertEqual([b['a'] for b in g['bitacora']], ['Atendido', 'Confirmado'])
        self.assertEqual(g['destino'], 'AREQUIPA')
        # solo ve lo suyo
        otro = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc='20111111111', nombre='Otro SAC')
        self.assertEqual(self.get('solicitudes/', documento=otro.numero_doc).json()['resultados'], [])

    def test_comprobantes_del_cliente(self):
        d = self.get('comprobantes/', documento=RUC_CLIENTE).json()
        self.assertEqual(d['cliente'], RUC_CLIENTE)
        self.assertIsInstance(d['resultados'], list)

    def test_la_cola_de_ceiba_go_se_guarda(self):
        r = self.post('venta/', {'origen': 'ceiba_go', 'pedido': 'GO-000123', 'total': '118.00'})
        self.assertEqual(r.status_code, 201)
        reg = RegistroCeibaGO.objects.get()
        self.assertEqual((reg.tipo, reg.pedido, reg.payload['total']), ('venta', 'GO-000123', '118.00'))
        self.assertEqual(self.post('otra-cosa/', {}).status_code, 404)
        self.client.force_login(self.admin)
        pantalla = self.client.get('/operador/recibido-ceibago/')
        self.assertContains(pantalla, 'GO-000123')

    def test_sin_el_giro_no_responde(self):
        Empresa.objects.update(giros=['COMERCIAL'])
        from core import plataforma
        plataforma.invalidar()
        self.assertEqual(self.get('cliente/', documento=RUC_CLIENTE).status_code, 403)
