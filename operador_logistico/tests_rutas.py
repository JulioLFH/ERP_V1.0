"""v1.117.15: ruta de recojo sugerida por zona y cercanía, ajuste manual, operador, celular y hoja de ruta."""
from datetime import timedelta
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from core.models import Tercero

from . import rutas as rr
from . import servicios as srv
from .models import ClienteLogistico, ConfigOperador, OperadorRecojo, RutaRecojo, SolicitudRecojo
from .tests import RUC_CLIENTE, Base, xml_guia


class BaseRutas(Base):
    def setUp(self):
        super().setUp()
        cfg = ConfigOperador.actual()
        cfg.base_direccion, cfg.base_latitud, cfg.base_longitud = 'Almacén Go, Cercado', D('-12.046400'), D('-77.042800')
        cfg.save()
        self.sol_callao = srv.crear_solicitud(self.cliente, 'MANUAL', mensaje='Bultos: 3')  # almacén en el Callao
        self.sol_olivos = self.otra('20100000011', 'Olivos SAC', '150117')
        self.sol_comas = self.otra('20100000012', 'Comas SAC', '150110')
        self.sol_surco = self.otra('20100000013', 'Surco SAC', '150140')
        self.hoy = timezone.localdate()

    def otra(self, ruc, nombre, ubigeo):
        t = Tercero.objects.create(tipo='CLIENTE', tipo_doc='6', numero_doc=ruc, nombre=nombre,
                                   direccion=f'Av. {nombre}', ubigeo=ubigeo)
        ClienteLogistico.objects.create(tercero=t, almacen_direccion=f'Almacén {nombre}', almacen_ubigeo=ubigeo,
                                        servicio_logistico=True)
        return srv.crear_solicitud(t, 'MANUAL', mensaje='Bultos: 2')

    def zonas(self):
        return {r.zona: [s.pk for s in r.paradas_en_orden()] for r in RutaRecojo.objects.all()}

    def sol(self, s):
        return SolicitudRecojo.objects.get(pk=s.pk)


class RutasTest(BaseRutas):
    def test_sugerir_agrupa_por_zona_y_ordena_por_cercania(self):
        creadas = rr.sugerir(self.hoy, self.admin)
        self.assertEqual(len(creadas), 3)
        z = self.zonas()
        self.assertEqual(z['Norte'], [self.sol_olivos.pk, self.sol_comas.pk])  # Los Olivos, más cerca del almacén
        self.assertEqual(z['Callao'], [self.sol_callao.pk])
        self.assertEqual(z['Sur'], [self.sol_surco.pk])
        self.assertFalse(rr.pendientes(self.hoy).exists())
        self.assertEqual(rr.sugerir(self.hoy), [])  # nada nuevo que sugerir

    def test_capacidad_abre_otra_ruta_y_la_fecha_futura_espera(self):
        ConfigOperador.objects.update(max_paradas=1)
        SolicitudRecojo.objects.filter(pk=self.sol_surco.pk).update(fecha_recojo=self.hoy + timedelta(days=2))
        rr.sugerir(self.hoy)
        z = self.zonas()
        self.assertEqual((z['Norte 1'], z['Norte 2']), ([self.sol_olivos.pk], [self.sol_comas.pk]))
        self.assertNotIn('Sur', z)  # la pidió para pasado mañana

    def test_ajuste_manual_y_rehacer(self):
        rr.sugerir(self.hoy)
        norte = self.sol(self.sol_olivos).ruta
        rr.mover(self.sol(self.sol_comas), -1)
        self.assertEqual(self.zonas()['Norte'], [self.sol_comas.pk, self.sol_olivos.pk])
        rr.ordenar_por_cercania(norte)
        self.assertEqual(self.zonas()['Norte'], [self.sol_olivos.pk, self.sol_comas.pk])
        rr.agregar(self.sol(self.sol_surco), norte)  # pasa de la ruta Sur a la Norte
        self.assertEqual(self.zonas()['Norte'][-1], self.sol_surco.pk)
        rr.quitar(self.sol(self.sol_olivos))
        self.assertEqual(self.zonas()['Norte'], [self.sol_comas.pk, self.sol_surco.pk])
        self.assertEqual(self.sol(self.sol_comas).orden_ruta, 1)
        rr.sugerir(self.hoy, rehacer=True)
        self.assertEqual(self.zonas()['Norte'], [self.sol_olivos.pk, self.sol_comas.pk])

    def test_operador_celular_recojo_aprende_ubicacion_y_cierra_la_ruta(self):
        operador = self.usuario('op', ['operador_logistico'], ['operador_logistico.operar'])
        SolicitudRecojo.objects.exclude(pk=self.sol_callao.pk).delete()
        srv.registrar_guia_xml(xml_guia(numero=50), self.cliente, self.sol_callao)
        ruta = rr.sugerir(self.hoy)[0]
        with self.assertRaises(rr.ErrorRuta):
            rr.iniciar(ruta)  # sin operador
        rr.asignar(ruta, operador, 'abc-123')
        rr.iniciar(ruta)
        ruta.refresh_from_db()
        self.assertEqual((ruta.estado, ruta.placa), ('EN_CURSO', 'ABC-123'))
        self.assertEqual(self.sol(self.sol_callao).operador, operador)
        self.assertIn('origin=-12.046400%2C-77.042800', rr.enlace_google(ruta))
        self.client.force_login(operador)
        r = self.client.get(reverse('operador_logistico:movil'))
        self.assertContains(r, 'Mi ruta de hoy')
        self.assertContains(r, 'Abrir la ruta en Google Maps')
        items = self.client.get(reverse('operador_logistico:movil_pendientes'), {'paso': 'RECOJO'}).json()['items']
        self.assertTrue(items[0]['detalle'].startswith('Parada 1'))
        srv.registrar_recojo(operador, f'{RUC_CLIENTE}|09|EG01|50', latitud='-12.051', longitud='-77.118')
        ruta.refresh_from_db()
        self.assertEqual(ruta.estado, 'CERRADA')
        self.assertEqual(ClienteLogistico.objects.get(pk=self.perfil.pk).almacen_latitud, D('-12.051000'))
        self.assertEqual(rr.punto(self.sol(self.sol_callao))[2], 'ficha')

    def test_pantalla_hoja_y_permisos(self):
        planificador = self.usuario('plan', ['operador_logistico'], ['operador_logistico.planificar'])
        mirador = self.usuario('mira', ['operador_logistico'])
        url = reverse('operador_logistico:rutas')
        self.client.force_login(mirador)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {'accion': 'sugerir'}).status_code, 403)
        self.client.force_login(planificador)
        r = self.client.post(url, {'accion': 'sugerir', 'fecha': self.hoy.isoformat()}, follow=True)
        self.assertContains(r, '3 ruta(s) sugerida(s)')
        self.assertContains(r, 'Google Maps')
        ruta = RutaRecojo.objects.get(zona='Norte')
        self.assertContains(self.client.get(reverse('operador_logistico:ruta_hoja', args=[ruta.pk])), 'Olivos SAC')
        self.assertContains(self.client.get(reverse('operador_logistico:solicitud', args=[self.sol_olivos.pk])),
                            ruta.numero)
        self.client.post(url, {'accion': 'eliminar', 'ruta': ruta.pk})
        self.assertFalse(RutaRecojo.objects.filter(pk=ruta.pk).exists())
        self.assertIsNone(self.sol(self.sol_olivos).ruta)

    def test_coordenadas_de_la_ficha(self):
        from .forms import ClienteLogisticoForm
        f = ClienteLogisticoForm({'almacen_latitud': '-12.05'}, instance=self.perfil)
        self.assertIn('almacen_longitud', f.errors)
        f = ClienteLogisticoForm({'almacen_latitud': '40.4', 'almacen_longitud': '-3.7'}, instance=self.perfil)
        self.assertIn('almacen_latitud', f.errors)


class RutaDesdeElOperadorTest(BaseRutas):
    """v1.117.16: la ruta se programa desde la dirección (casa) de los operadores."""

    def operador(self, nombre, **ficha):
        u = self.usuario(nombre, ['operador_logistico'], ['operador_logistico.operar'])
        if ficha:
            OperadorRecojo.objects.create(usuario=u, **ficha)
        return u

    def test_sugerir_da_a_cada_operador_la_ruta_mas_cercana_a_su_casa(self):
        ana = self.operador('ana', direccion='Av. Túpac Amaru 100', ubigeo='150110',
                            latitud=D('-11.940000'), longitud=D('-77.060000'))
        beto = self.operador('beto', ubigeo='150140', placa='xyz-1')  # solo el distrito
        self.operador('carla', latitud=D('-12.050000'), longitud=D('-77.120000'), activo=False)  # hoy no sale
        self.operador('dani')  # sin dirección
        rr.sugerir(self.hoy)
        norte, sur, callao = (RutaRecojo.objects.get(zona=z) for z in ('Norte', 'Sur', 'Callao'))
        self.assertEqual((norte.operador, sur.operador, callao.operador), (ana, beto, None))
        self.assertEqual(sur.placa, 'XYZ-1')
        # sale de la casa de Ana (Comas): primero Comas, luego Los Olivos
        self.assertEqual(self.zonas()['Norte'], [self.sol_comas.pk, self.sol_olivos.pk])
        self.assertEqual(self.sol(self.sol_olivos).operador, ana)
        g = rr.enlace_google(norte)
        self.assertIn('origin=-11.940000%2C-77.060000', g)
        self.assertIn('destination=-12.046400%2C-77.042800', g)  # termina en el almacén de Go
        self.assertIn('origin=Santiago%20de%20Surco%2C%20Lima%2C%20Per%C3%BA', rr.enlace_google(sur))
        self.assertEqual(rr.origen(callao)['fuente'], 'go')  # sin operador: sale del almacén de Go
        # un operador con ruta abierta ese día ya no recibe otra
        nueva = self.otra('20100000014', 'Comas Dos SAC', '150110')
        rr.sugerir(self.hoy)
        self.assertIsNone(self.sol(nueva).ruta.operador)

    def test_asignar_reordena_saliendo_de_la_casa_del_operador(self):
        rr.sugerir(self.hoy)
        norte = RutaRecojo.objects.get(zona='Norte')
        self.assertEqual(self.zonas()['Norte'], [self.sol_olivos.pk, self.sol_comas.pk])  # desde el almacén de Go
        ana = self.operador('ana', latitud=D('-11.940000'), longitud=D('-77.060000'), placa='ab-123')
        rr.asignar(norte, ana, '')
        norte.refresh_from_db()
        self.assertEqual(norte.placa, 'AB-123')  # la placa habitual
        self.assertEqual(self.zonas()['Norte'], [self.sol_comas.pk, self.sol_olivos.pk])

    def test_pantalla_de_operadores_y_ubicacion_desde_el_celular(self):
        ana = self.operador('ana')
        planificador = self.usuario('plan', ['operador_logistico'], ['operador_logistico.planificar'])
        mirador = self.usuario('mira', ['operador_logistico'])
        url = reverse('operador_logistico:operadores_recojo')
        datos = {'usuario': ana.pk, f'op{ana.pk}-direccion': 'Jr. Las Flores 123', f'op{ana.pk}-ubigeo': '150110',
                 f'op{ana.pk}-latitud': '', f'op{ana.pk}-longitud': '', f'op{ana.pk}-placa': 'abc-1',
                 f'op{ana.pk}-activo': 'on'}
        self.client.force_login(mirador)
        self.assertEqual(self.client.post(url, datos).status_code, 403)
        self.client.force_login(planificador)
        self.assertContains(self.client.get(url), 'sin punto')
        self.assertRedirects(self.client.post(url, datos), url)
        f = OperadorRecojo.objects.get(usuario=ana)
        self.assertEqual((f.direccion, f.ubigeo, f.placa), ('Jr. Las Flores 123', '150110', 'ABC-1'))
        self.assertContains(self.client.get(reverse('operador_logistico:rutas')), '1 operador(es) libre(s)')
        self.client.force_login(ana)
        self.assertContains(self.client.get(reverse('operador_logistico:movil')), 'Guardar mi ubicación')
        salida = reverse('operador_logistico:movil_salida')
        self.assertFalse(self.client.post(salida, {'latitud': '40.4', 'longitud': '-3.7'}).json()['ok'])
        self.assertTrue(self.client.post(salida, {'latitud': '-11.9401234', 'longitud': '-77.06'}).json()['ok'])
        f.refresh_from_db()
        self.assertEqual((f.latitud, f.direccion), (D('-11.940123'), 'Jr. Las Flores 123'))
        self.client.force_login(mirador)
        self.assertEqual(self.client.post(salida, {'latitud': '-12', 'longitud': '-77'}).status_code, 403)
