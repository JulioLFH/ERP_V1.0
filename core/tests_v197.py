"""v1.97: asistente de planta «Lucho»: fichas de parámetros con aprobación de otra persona, motor que responde solo con
fuentes validadas (ficha, casos, manuales), casos enganchados a la orden (merma, parada, no conformidad, aviso y
propuesta de ficha), importación desde Excel, uso inadecuado y permisos."""
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from asistente import motor, plantillas
from asistente import servicios as srv
from asistente.models import CasoPlanta, Consulta, Defecto, FichaParametros, Manual, Proceso
from core.models import PerfilUsuario
from core.modulos import GRUPOS
from produccion.tests_reportes_produccion import Base as BaseProduccion

SETPOINTS = [{'nombre': 'Velocidad de inyección', 'unidad': '%', 'minimo': '60', 'objetivo': '70', 'maximo': '80'},
             {'nombre': 'Temperatura de barril', 'unidad': '°C', 'minimo': '210', 'objetivo': '220',
              'maximo': '230'},
             {'nombre': 'Presión de mantenimiento', 'unidad': 'bar', 'minimo': '550', 'objetivo': '575',
              'maximo': '600'}]


class LuchoBase(BaseProduccion):
    def setUp(self):
        super().setUp()
        plantillas.cargar()
        self.iny = Proceso.objects.get(codigo='INY')
        self.iny.puestos.add(self.horno)
        self.ingeniero = User.objects.create_superuser('ingeniero', 'i@i.com', 'x')

    def ficha_vigente(self, filas=SETPOINTS):
        f = srv.guardar_ficha(FichaParametros(producto=self.galleta, equipo=self.equipo, proceso=self.iny),
                              filas, self.user)
        srv.proponer(f, self.user)
        return srv.aprobar(f, self.ingeniero)


class FichasTest(LuchoBase):
    def test_validacion_revision_y_aprobacion_de_otra_persona(self):
        base = FichaParametros(producto=self.galleta, equipo=self.equipo, proceso=self.iny)
        with self.assertRaisesMessage(srv.ErrorAsistente, 'mínimo ≤ objetivo ≤ máximo'):
            srv.guardar_ficha(base, [{'nombre': 'Velocidad', 'unidad': '%', 'minimo': '80', 'objetivo': '70',
                                      'maximo': '90'}], self.user)
        with self.assertRaisesMessage(srv.ErrorAsistente, 'la unidad es obligatoria'):
            srv.guardar_ficha(base, [{'nombre': 'Velocidad', 'minimo': '1', 'objetivo': '2', 'maximo': '3'}],
                              self.user)
        f = srv.guardar_ficha(base, SETPOINTS, self.user)
        self.assertEqual((f.revision, f.estado, f.setpoints.count()), (1, 'BORRADOR', 3))
        srv.proponer(f, self.user)
        with self.assertRaisesMessage(srv.ErrorAsistente, 'no aprueba su propio estándar'):
            srv.aprobar(f, self.user)
        srv.aprobar(f, self.ingeniero)
        self.assertEqual(srv.vigente(self.galleta, self.equipo), f)
        rev2 = srv.nueva_revision(f, self.user, {'Velocidad de inyección': D('85')})
        self.assertEqual((rev2.numero, rev2.revision), (f.numero, 2))
        v = rev2.setpoints.get(nombre='Velocidad de inyección')
        self.assertEqual((v.objetivo, v.maximo), (D('85'), D('85')))  # el rango se amplía al valor real
        srv.proponer(rev2, self.user)
        srv.aprobar(rev2, self.ingeniero)
        f.refresh_from_db()
        self.assertEqual(f.estado, 'RETIRADA')  # una sola vigente por combinación
        self.assertEqual(srv.vigente(self.galleta, self.equipo), rev2)

    def test_importar_desde_excel_todo_o_nada(self):
        fila = {'Producto (código)': self.galleta.codigo, 'Equipo (código)': self.equipo.codigo,
                'Molde (código, opcional)': '', 'Proceso (código)': 'INY', 'Unidad': '%'}
        malas = [(2, {**fila, 'Parámetro': 'Velocidad de inyección', 'Mínimo': 60, 'Objetivo': 70, 'Máximo': 80}),
                 (3, {**fila, 'Parámetro': 'Contrapresión', 'Mínimo': 9, 'Objetivo': 5, 'Máximo': 8})]
        with self.assertRaisesMessage(srv.ErrorAsistente, 'Fila 3'):
            srv.importar(malas, self.user)
        self.assertFalse(FichaParametros.objects.exists())
        buenas = [malas[0], (3, {**fila, 'Parámetro': 'Contrapresión', 'Mínimo': 5, 'Objetivo': 7, 'Máximo': 8})]
        creadas = srv.importar(buenas, self.user)
        self.assertEqual(len(creadas), 1)
        self.assertEqual((creadas[0].estado, creadas[0].setpoints.count()), ('POR_APROBAR', 2))


class MotorTest(LuchoBase):
    def test_responde_con_la_ficha_y_no_repite_lo_ajustado(self):
        f = self.ficha_vigente()
        ctx = {'producto': self.galleta, 'equipo': self.equipo, 'proceso': self.iny, 'ficha': f}
        quemado = Defecto.objects.get(proceso=self.iny, codigo='QUE')
        r = motor.responder('', ctx, quemado)
        self.assertIn('Baje velocidad de inyección: la ficha marca 70 % (rango 60 a 80 %); no pase de 60 %.', r['texto'])
        self.assertEqual(r['cobertura'], 'PRECEDENTE')
        self.assertEqual(r['fuentes'][0]['tipo'], 'Ficha')
        self.assertIn(motor.AVISO, r['texto'])
        # segunda vuelta: ya bajó la velocidad; no la repite y avisa no revertirla
        r2 = motor.responder('ahora sale rechupado', ctx, None, ajustados=r['recomendados'])
        self.assertEqual(r2['defecto'].codigo, 'RCH')  # lo reconoce por el texto
        self.assertIn('Mantenga como los dejó: temperatura de barril, velocidad de inyección (no los revierta)',
                      r2['texto'])
        self.assertIn('Suba presión de mantenimiento: la ficha marca 575 bar (rango 550 a 600 bar); no pase de 600 '
                      'bar. Compacta mejor la masa.', r2['texto'])
        self.assertIn('La ficha no recoge tiempo de mantenimiento', r2['texto'])  # sin valor validado

    def test_sin_fuente_no_inventa_y_fuera_de_dominio(self):
        ctx = {'producto': self.galleta, 'equipo': self.equipo, 'proceso': self.iny, 'ficha': None}
        r = motor.responder('', ctx, Defecto.objects.get(proceso=self.iny, codigo='REB'))
        self.assertEqual(r['cobertura'], 'SIN')
        self.assertIn('No tengo una fuente validada', r['texto'])
        self.assertIn('no le doy valores', r['texto'])
        r = motor.responder('quién ganó el partido de fútbol ayer', ctx)
        self.assertTrue(r['fuera_de_dominio'])
        self.assertEqual(motor.responder('cuánto es el sueldo de mi jefe', ctx)['motivo'], 'Datos fuera de su alcance')
        for _ in range(3):  # umbral por defecto: 3 en 7 días
            motor.registrar_consulta(self.user, ctx, 'cuéntame un chiste', motor.responder('cuéntame un chiste', ctx))
        self.assertTrue(Consulta.objects.filter(supervisor_avisado=True).exists())

    def test_cita_casos_y_manuales_validados(self):
        quemado = Defecto.objects.get(proceso=self.iny, codigo='QUE')
        CasoPlanta.objects.create(estado='RESUELTO', producto=self.galleta, equipo=self.equipo, defecto=quemado,
                                  causa='Aire atrapado', accion='Bajé la velocidad a 62 %')
        Manual.objects.create(titulo='Manual de la inyectora', equipo=self.equipo, validado=True,
                              contenido='Arranque en frío: 30 min.\nQuemado: revise la ventilación del molde.')
        Manual.objects.create(titulo='Borrador sin validar', equipo=self.equipo, contenido='Quemado: x')
        r = motor.responder('', {'producto': self.galleta, 'equipo': self.equipo, 'proceso': self.iny}, quemado)
        self.assertIn('Bajé la velocidad a 62 %', r['texto'])
        self.assertIn('Quemado: revise la ventilación del molde.', r['texto'])
        self.assertNotIn('Borrador sin validar', r['texto'])
        self.assertEqual(r['cobertura'], 'PRECEDENTE')


class CasoIntegracionTest(LuchoBase):
    def test_caso_resuelto_vuelve_a_ceiba(self):
        from calidad.models import NoConformidad
        from produccion.models import AvisoMantenimiento, MotivoParada
        f = self.ficha_vigente()
        o = self.orden()
        ctx = srv.contexto_de_orden(o)
        self.assertEqual((ctx['producto'], ctx['equipo'], ctx['proceso'], ctx['ficha']),
                         (self.galleta, self.equipo, self.iny, f))
        caso = srv.abrir_caso(self.user, ctx, Defecto.objects.get(proceso=self.iny, codigo='QUE'), 'Pieza quemada')
        motivo = MotivoParada.objects.create(codigo='PR-AJ', nombre='Ajuste de proceso', tipo='PROCESO')
        caso, propuesta = srv.resolver(
            caso, self.user, True, 'Aire atrapado', 'Bajé la velocidad', piezas='5', minutos=20,
            parametros=[{'nombre': 'Velocidad de inyección', 'valor': '55'}],
            parada={'motivo': motivo, 'minutos': '15'}, abrir_nc=True, avisar_mantenimiento=True)
        self.assertEqual(caso.estado, 'RESUELTO')
        self.assertTrue(caso.merma_registrada)
        self.assertEqual(sum(a.cantidad_merma for a in o.avances.all()), D('5'))
        self.assertEqual(caso.costo, (D('5') * o.costo_estandar_unit).quantize(D('0.01')))
        self.assertTrue(NoConformidad.objects.filter(pk=caso.no_conformidad_id, origen='PRODUCCION').exists())
        self.assertTrue(AvisoMantenimiento.objects.filter(pk=caso.aviso_id, equipo=self.equipo).exists())
        self.assertEqual(caso.parada.motivo, motivo)
        # 55 % está fuera de la ficha (60 a 80): se propone la revisión 2 para aprobación
        self.assertEqual((propuesta.revision, propuesta.estado, propuesta.origen_caso), (2, 'POR_APROBAR', caso))
        self.assertEqual(propuesta.setpoints.get(nombre='Velocidad de inyección').minimo, D('55'))
        with self.assertRaisesMessage(srv.ErrorAsistente, 'ya está cerrado'):
            srv.resolver(caso, self.user, True, 'x', 'y')

    def test_resuelto_exige_causa_y_accion(self):
        caso = srv.abrir_caso(self.user, {'producto': self.galleta}, None)
        with self.assertRaisesMessage(srv.ErrorAsistente, 'el defecto, la causa y la acción'):
            srv.resolver(caso, self.user, True)
        with self.assertRaisesMessage(srv.ErrorAsistente, 'no pueden ser negativas'):
            srv.resolver(caso, self.user, False, piezas='-1')


class PantallasTest(LuchoBase):
    def test_chat_con_la_orden_y_ficha_en_la_declaracion(self):
        f = self.ficha_vigente()
        o = self.orden()
        r = self.client.get(reverse('asistente:chat') + f'?orden={o.pk}')
        self.assertContains(r, f'{f.numero}')
        quemado = Defecto.objects.get(proceso=self.iny, codigo='QUE')
        r = self.client.post(reverse('asistente:chat'), {'orden': o.pk, 'defecto': quemado.pk})
        caso = CasoPlanta.objects.get()
        self.assertRedirects(r, reverse('asistente:chat') + f'?caso={caso.pk}#fin', fetch_redirect_response=False)
        r = self.client.get(reverse('asistente:chat') + f'?caso={caso.pk}')
        self.assertContains(r, 'Baje velocidad de inyección')
        self.assertContains(r, '¿Se resolvió su consulta?')
        # fuera de tema: no abre caso
        self.client.post(reverse('asistente:chat'), {'orden': o.pk, 'texto': 'recomiéndame una película'})
        self.assertEqual(CasoPlanta.objects.count(), 1)
        self.assertTrue(Consulta.objects.filter(fuera_de_dominio=True).exists())
        # la ficha vigente se ve al declarar la producción de la orden
        r = self.client.get(reverse('manufactura:orden', args=[o.pk]))
        self.assertContains(r, 'Ficha de parámetros vigente')
        self.assertContains(r, 'Preguntar a Lucho')
        for nombre in ('casos', 'fichas', 'manuales', 'procesos', 'uso', 'configuracion', 'importar',
                       'ficha_nueva', 'manual_nuevo'):
            self.assertEqual(self.client.get(reverse(f'asistente:{nombre}')).status_code, 200, nombre)
        self.assertTrue(self.client.get(reverse('asistente:importar') + '?plantilla=1')['Content-Type']
                        .startswith('application/vnd.openxmlformats'))

    def test_permisos(self):
        u = User.objects.create_user('operario', password='x')
        u.groups.add(Group.objects.get_or_create(name=GRUPOS['asistente'])[0])
        PerfilUsuario.objects.create(usuario=u, acciones=['asistente.consultar'])
        self.client.force_login(u)
        r = self.client.get(reverse('asistente:fichas'))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, reverse('asistente:ficha_nueva'))
        self.assertEqual(self.client.get(reverse('asistente:ficha_nueva')).status_code, 403)
        self.assertEqual(self.client.get(reverse('asistente:procesos')).status_code, 403)
        f = srv.guardar_ficha(FichaParametros(producto=self.galleta, equipo=self.equipo, proceso=self.iny),
                              SETPOINTS, self.user)
        srv.proponer(f, self.user)
        self.assertEqual(self.client.post(reverse('asistente:ficha', args=[f.pk]), {'accion': 'aprobar'}).status_code,
                         403)
        f.refresh_from_db()
        self.assertEqual(f.estado, 'POR_APROBAR')
