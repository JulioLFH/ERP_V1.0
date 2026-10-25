"""v1.83: módulos nuevos (TomaPedido, Portal de proveedores, Código de barras, Presupuestos, Proyectos, Empleados,
Tiempo personal, Distribución, Flota, SUNAT, Consulta RUC, Conversaciones, Calendario y Actividades pendientes) y su
asterisco de «nuevo» durante una semana."""
import importlib
import json
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.urls import resolve, reverse
from django.utils import timezone

from colaboracion import servicios as colab
from colaboracion.models import Canal, Evento, Pendiente, Proyecto, Tarea
from core.models import ConsultaRUC, Producto
from core.modulos import (DERIVADOS, GRUPOS, NUEVOS_DESDE, POR_CLAVE, menu_de, modulos_de_ruta, modulos_del_usuario,
                          nuevos)
from core.permisos import Permisos, puede
from distribucion import servicios as reparto
from distribucion.models import HojaReparto
from inventario import barras
from obras.tests_comercial import HOY, Base
from ventas.models import Cotizacion, CotizacionItem

from .tests_v181 import usuario

NUEVOS = ['toma_pedido', 'portal_proveedores', 'codigo_barras', 'presupuestos', 'proyectos', 'empleados',
          'tiempo_personal', 'distribucion', 'flota', 'sunat', 'consulta_ruc', 'conversaciones', 'calendario',
          'actividades']


class ModulosNuevosTest(Base):
    def test_estan_registrados_y_su_menu_abre(self):
        for clave in NUEVOS:
            self.assertIn(clave, POR_CLAVE, clave)
            for item in menu_de(POR_CLAVE[clave]):
                for enlace in item.get('hijos') or [item]:
                    r = self.client.get(enlace['url'])
                    self.assertIn(r.status_code, (200, 302), f'{clave}: {enlace["url"]}')

    def test_asterisco_una_semana(self):
        self.assertEqual(nuevos(date(2026, 10, 25)), set(NUEVOS) | {'planificacion', 'control_calidad',
                                                                       'mantenimiento'})
        self.assertEqual(nuevos(date(2026, 10, 31)), set(NUEVOS))  # los de v1.82 (24/10) ya cumplieron la semana
        self.assertEqual(nuevos(date(2026, 11, 1)), set())
        self.assertEqual(nuevos(date(2026, 10, 24)), {'planificacion', 'control_calidad', 'mantenimiento'})
        self.assertEqual(set(NUEVOS_DESDE) - set(POR_CLAVE), set())
        with patch('django.utils.timezone.localdate', return_value=date(2026, 10, 26)):
            r = self.client.get(reverse('home'))
        self.assertContains(r, 'class="app-nuevo"', count=17)
        self.assertContains(r, 'Módulo nuevo')
        with patch('django.utils.timezone.localdate', return_value=date(2026, 11, 2)):
            r = self.client.get(reverse('home'))
        self.assertNotContains(r, 'class="app-nuevo"')

    def test_derivados_y_grupos(self):
        vendedor = usuario('vende', ['ventas'])
        mods = modulos_del_usuario(vendedor)
        self.assertTrue({'consulta_ruc', 'conversaciones', 'calendario', 'actividades'} <= mods)
        self.assertNotIn('toma_pedido', mods)  # se asigna (la migración lo da a quien tenía Ventas)
        for clave in ('conversaciones', 'calendario', 'actividades', 'consulta_ruc'):
            self.assertNotIn(clave, GRUPOS)
        self.assertNotIn('cliente', DERIVADOS['conversaciones'])
        cliente = usuario('cli', ['cliente'])
        self.assertNotIn('conversaciones', modulos_del_usuario(cliente))

    def test_pantallas_separadas_por_modulo(self):
        self.assertEqual(modulos_de_ruta(resolve(reverse('planillas:trabajadores')))[0], 'empleados')
        self.assertEqual(modulos_de_ruta(resolve(reverse('planillas:vacaciones')))[0], 'tiempo_personal')
        self.assertEqual(modulos_de_ruta(resolve(reverse('planillas:lista'))), ['planillas'])
        self.assertEqual(modulos_de_ruta(resolve(reverse('compras:portal_tablero')))[0], 'portal_proveedores')
        self.assertEqual(modulos_de_ruta(resolve(reverse('contabilidad:presupuestos')))[0], 'presupuestos')
        self.assertIn('flota', modulos_de_ruta(resolve(reverse('flota:equipos'))))
        self.assertIn('sunat', modulos_de_ruta(resolve(reverse('ventas:registro'))))
        self.assertIn('sunat', modulos_de_ruta(resolve(reverse('finanzas:constancias_clientes'))))
        logistica = POR_CLAVE['logistica']['menu']
        self.assertNotIn('Flota', [e for e, _ in logistica])
        self.assertNotIn('Organización', [e for e, _ in POR_CLAVE['planillas']['menu']])

    def test_permiso_de_modulo_con_guion_bajo_en_plantillas(self):
        """Corrección: {% if puede.control_calidad_inspecciones %} solo funcionaba para el administrador."""
        u = usuario('inspector', ['control_calidad'], ['control_calidad.inspecciones'])
        p = Permisos(u)
        self.assertTrue(p.control_calidad_inspecciones)
        self.assertFalse(p.control_calidad_liberar_lotes)
        e = usuario('rrhh', ['empleados'], ['empleados.registrar'])
        self.assertTrue(Permisos(e).empleados_registrar)
        self.assertFalse(Permisos(e).tiempo_personal_registrar)

    def test_migracion_de_permisos(self):
        mig = importlib.import_module('core.migrations.0066_modulos_v183')
        self.assertEqual(mig._acciones(['compras.portal', 'planillas.calcular', 'logistica.flota', 'ventas.emitir']),
                         sorted(['portal_proveedores.aprobar', 'planillas.calcular', 'empleados.registrar',
                                 'tiempo_personal.registrar', 'flota.registrar', 'ventas.emitir',
                                 'toma_pedido.registrar']))

    def test_empleados_sin_planillas(self):
        """Un asistente de RR. HH. con Empleados registra trabajadores sin ver ni calcular planillas."""
        u = usuario('asistente', ['empleados'], ['empleados.registrar'])
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse('planillas:trabajadores')).status_code, 200)
        self.assertEqual(self.client.get(reverse('planillas:lista')).status_code, 403)
        self.assertTrue(puede(u, 'empleados.registrar'))
        self.assertFalse(puede(u, 'planillas.calcular'))


# ---------------------------------------------------------------- conversaciones
class ConversacionesTest(Base):
    def test_canal_mensajes_y_no_leidos(self):
        ana, luis, otro = usuario('ana', ['ventas']), usuario('luis', ['compras']), usuario('otro', ['ventas'])
        self.client.force_login(ana)
        self.client.post(reverse('conversaciones:canal_nuevo'), {'nombre': 'Gerencia', 'publico': '',
                                                                 'integrantes': [luis.pk]})
        canal = Canal.objects.get(nombre='Gerencia')
        self.assertFalse(colab.puede_ver(otro, canal))  # cerrado: solo sus miembros
        self.client.post(f'{reverse("conversaciones:inicio")}?canal={canal.pk}', {'texto': 'Reunión a las 4'})
        self.assertEqual(colab.no_leidos(luis), {canal.pk: 1})
        self.client.force_login(luis)
        r = self.client.get(reverse('conversaciones:nuevos', args=[canal.pk]) + '?desde=0')
        self.assertEqual(json.loads(r.content)['mensajes'][0]['texto'], 'Reunión a las 4')
        self.assertEqual(colab.no_leidos(luis), {})
        self.client.force_login(otro)
        self.assertEqual(self.client.get(reverse('conversaciones:nuevos', args=[canal.pk])).status_code, 404)

    def test_chat_directo_unico(self):
        ana, luis = usuario('ana2', ['ventas']), usuario('luis2', ['ventas'])
        c1, c2 = colab.directo(ana, luis), colab.directo(luis, ana)
        self.assertEqual(c1.pk, c2.pk)
        self.assertEqual(colab.titulo_canal(c1, ana), 'Luis2')


# ---------------------------------------------------------------- actividades, calendario y proyectos
class ColaboracionTest(Base):
    def test_actividad_encargada_y_calendario(self):
        jefe, asistente = usuario('jefe', ['ventas']), usuario('asis', ['ventas'])
        self.client.force_login(jefe)
        self.client.post(reverse('actividades:nueva'), {'titulo': 'Llamar al banco', 'fecha': HOY.isoformat(),
                                                        'prioridad': 'ALTA', 'asignado_a': asistente.pk})
        p = Pendiente.objects.get()
        self.assertEqual((p.asignado_a, p.creado_por), (asistente, jefe))
        filas = colab.por_hacer(asistente)
        self.assertEqual([f['titulo'] for f in filas], ['Llamar al banco'])
        self.assertEqual(colab.vencidas_y_hoy(asistente), 1)
        dias = colab.items(asistente, modulos_del_usuario(asistente), HOY, HOY, {'pendientes'})
        self.assertEqual(dias[HOY][0]['titulo'], 'Llamar al banco')
        self.client.force_login(asistente)
        self.client.post(reverse('actividades:hecha', args=[p.pk]), {'resultado': 'Listo'})
        p.refresh_from_db()
        self.assertTrue(p.hecho)
        r = self.client.get(reverse('home'))
        self.assertNotContains(r, 'app-insignia')

    def test_evento_privado_y_de_empresa(self):
        org, invitado, ajeno = usuario('org', ['ventas']), usuario('inv', ['ventas']), usuario('ajeno', ['ventas'])
        self.client.force_login(org)
        self.client.post(reverse('calendario:evento_nuevo'), {
            'titulo': 'Comité', 'fecha': HOY.isoformat(), 'hora_inicio': '09:00', 'hora_fin': '10:00',
            'visibilidad': 'PRIVADO', 'invitados': [invitado.pk]})
        e = Evento.objects.get()
        self.assertEqual(timezone.localtime(e.inicio).hour, 9)
        self.assertIn(e, colab.eventos_visibles(invitado))
        self.assertNotIn(e, colab.eventos_visibles(ajeno))
        self.assertEqual(self.client.get(reverse('calendario:mes')).status_code, 200)
        self.client.force_login(ajeno)
        self.assertEqual(self.client.get(reverse('calendario:evento', args=[e.pk])).status_code, 404)

    def test_vencimiento_sunat_en_el_calendario(self):
        contador = usuario('conta', ['contabilidad'])
        dias = colab.items(contador, modulos_del_usuario(contador), date(2026, 11, 1), date(2026, 11, 30), {'sunat'})
        titulos = [i['titulo'] for lista in dias.values() for i in lista]
        self.assertEqual(len(titulos), 1)
        self.assertIn('10/2026', titulos[0])

    def test_proyecto_tablero_de_tareas(self):
        jefe = usuario('jp', ['proyectos'], ['proyectos.gestionar'])
        miembro, ajeno = usuario('miembro', ['proyectos']), usuario('ajeno2', ['proyectos'])
        self.client.force_login(jefe)
        self.client.post(reverse('proyectos:nuevo'), {'nombre': 'Implementación ERP', 'estado': 'ACTIVO',
                                                      'miembros': [miembro.pk]})
        proy = Proyecto.objects.get()
        self.assertEqual(proy.responsable, jefe)
        self.client.post(reverse('proyectos:proyecto', args=[proy.pk]), {
            't-titulo': 'Cargar maestros', 't-responsable': miembro.pk, 't-etapa': 'POR_HACER', 't-prioridad': '1',
            't-horas_estimadas': '4', 't-fecha_limite': (HOY + timedelta(days=2)).isoformat()})
        t = Tarea.objects.get()
        self.assertEqual(colab.por_hacer(miembro)[0]['origen'], 'Tarea')
        self.client.force_login(miembro)
        self.client.post(reverse('proyectos:tarea_mover', args=[t.pk]), {'etapa': 'HECHA'})
        t.refresh_from_db()
        self.assertEqual(t.etapa, 'HECHA')
        self.assertEqual(colab.avance(proy), (100, 1))
        self.client.force_login(ajeno)
        self.assertEqual(self.client.get(reverse('proyectos:proyecto', args=[proy.pk])).status_code, 404)


# ---------------------------------------------------------------- distribución
class DistribucionTest(Base):
    def pedido(self, numero, total='100', condicion='Contado'):
        p = Cotizacion.objects.create(tipo='PED', numero=numero, tercero=self.entidad, estado='APROBADO',
                                      condicion_pago=condicion)
        CotizacionItem.objects.create(documento=p, descripcion='Producto', cantidad=1, precio_unitario=D(total))
        p.calcular_totales()
        p.save()
        return p

    def test_hoja_entregas_y_cierre(self):
        p1, p2 = self.pedido('PD01-00000501'), self.pedido('PD01-00000502', condicion='Crédito 30 días')
        self.assertEqual(set(reparto.pedidos_disponibles(HOY)), {p1, p2})
        r = self.client.post(reverse('distribucion:nueva'), {'fecha': HOY.isoformat(), 'pedido': [p1.pk, p2.pk]})
        hoja = HojaReparto.objects.get()
        self.assertRedirects(r, reverse('distribucion:hoja', args=[hoja.pk]))
        self.assertEqual(hoja.numero, f'HR-{hoja.pk:06d}')
        a, b = hoja.paradas.all()
        self.assertEqual((a.cobrar, b.cobrar), (D('118.00'), D('0')))  # contra entrega solo el contado
        self.assertEqual(list(reparto.pedidos_disponibles(HOY)), [])
        with self.assertRaisesMessage(reparto.ErrorReparto, 'motivo'):
            reparto.registrar_entrega(a, 'NO_ENTREGADO')
        reparto.registrar_entrega(a, 'ENTREGADO', recibido_por='Ana', cobrado='118', latitud='-12.0464',
                                  longitud='-77.0428')
        hoja.refresh_from_db()
        self.assertEqual(hoja.estado, 'EN_RUTA')  # la primera entrega marca la salida
        reparto.cerrar(hoja)
        b.refresh_from_db()
        self.assertEqual((b.estado, b.motivo), ('NO_ENTREGADO', 'TIEMPO'))
        self.assertEqual(list(reparto.pedidos_disponibles(HOY)), [p2])  # lo no entregado vuelve a programarse
        self.assertEqual(reparto.resumen(hoja)['efectividad'], 50)
        self.assertEqual(self.client.get(reverse('distribucion:reporte')).status_code, 200)
        self.assertEqual(self.client.get(reverse('distribucion:imprimir', args=[hoja.pk])).status_code, 200)


# ---------------------------------------------------------------- TomaPedido
class TomaPedidoTest(Base):
    def test_pedido_desde_el_celular(self):
        from core.models import Almacen
        Almacen.objects.get_or_create(codigo='PRI', defaults={'nombre': 'Principal', 'es_principal': True})
        p = Producto.objects.create(codigo='GAL', nombre='Galletas', tipo='BIEN', precio_venta=D('10'),
                                    codigo_barras='7750000000017')
        vendedor = usuario('vendedor', ['toma_pedido', 'ventas'], ['toma_pedido.registrar', 'ventas.emitir'])
        self.client.force_login(vendedor)
        r = self.client.get(reverse('toma_pedido:buscar') + f'?q=7750000000017&cliente={self.entidad.pk}')
        self.assertEqual(json.loads(r.content)['productos'][0]['id'], p.pk)
        r = self.client.post(reverse('toma_pedido:nuevo'), {
            'cliente': self.entidad.pk, 'producto': [p.pk], 'cantidad': ['3'], 'precio': ['10'], 'descuento': ['0'],
            'glosa': 'Entregar en la mañana'})
        ped = Cotizacion.objects.get(tipo='PED')
        self.assertRedirects(r, reverse('toma_pedido:pedido', args=[ped.pk]) + '?nuevo=1', fetch_redirect_response=False)
        self.assertEqual((ped.tomado_por, ped.total, ped.items.count()), (vendedor, D('35.40'), 1))
        r = self.client.get(reverse('toma_pedido:lista'))
        self.assertContains(r, ped.numero)
        otro = usuario('otro_vend', ['toma_pedido'], ['toma_pedido.registrar'])
        self.client.force_login(otro)
        self.assertEqual(self.client.get(reverse('toma_pedido:pedido', args=[ped.pk])).status_code, 404)

    def test_sin_productos_no_crea(self):
        from ventas.toma_pedido import crear_pedido
        pedido, errores = crear_pedido(self.user, self.entidad, [])
        self.assertIsNone(pedido)
        self.assertIn('Agregue al menos un producto.', errores)


# ---------------------------------------------------------------- código de barras
class CodigoBarrasTest(Base):
    def test_code128_y_ean13(self):
        self.assertTrue(all(sum(int(a) for a in pat) == (13 if i == 106 else 11)
                            for i, pat in enumerate(barras.PATRONES)))
        self.assertEqual(len(barras.PATRONES), 107)
        anchos = barras.code128('ABC-123')
        # inicio + 7 caracteres + control (6 elementos c/u) + parada (7)
        self.assertEqual(len(anchos), 9 * 6 + 7)
        self.assertIn('<svg', barras.svg('ABC-123'))
        with self.assertRaises(barras.ErrorBarras):
            barras.code128('ñ')
        codigo = barras.ean13_interno(15)
        self.assertEqual(codigo[:1], '2')
        self.assertTrue(barras.ean13_valido(codigo))
        self.assertTrue(barras.ean13_valido('4006381333931'))  # EAN-13 real (control 1)

    def test_asignar_y_escanear(self):
        a = Producto.objects.create(codigo='A1', nombre='Arroz', tipo='BIEN')
        b = Producto.objects.create(codigo='B1', nombre='Azúcar', tipo='BIEN')
        self.client.post(reverse('codigo_barras:asignar'), {f'codigo_{a.pk}': '7751234567892', 'generar': [b.pk],
                                                            f'codigo_{b.pk}': ''})
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.codigo_barras, '7751234567892')
        self.assertTrue(barras.ean13_valido(b.codigo_barras))
        c = Producto.objects.create(codigo='C1', nombre='Café', tipo='BIEN')
        r = self.client.post(reverse('codigo_barras:asignar'), {f'codigo_{c.pk}': '7751234567892'}, follow=True)
        self.assertContains(r, 'ya es el código de A1')
        r = self.client.get(reverse('codigo_barras:escanear') + '?codigo=7751234567892')
        self.assertContains(r, 'Arroz')
        r = self.client.get(reverse('codigo_barras:etiquetas') + f'?producto={a.pk}&cantidad=3&precio=1')
        self.assertContains(r, 'class="etiqueta"', count=3)


# ---------------------------------------------------------------- consulta RUC
class ConsultaRucTest(Base):
    def test_varios_ruc_con_alerta_e_historial(self):
        def falso(ruc, token=''):
            if ruc == '20100070970':
                return {'ruc': ruc, 'razon_social': 'SUPERMERCADOS PERUANOS', 'estado': 'ACTIVO', 'condicion': 'HABIDO',
                        'direccion': 'AV. MORRO SOLAR 1086', 'ubigeo': '150140', 'distrito': 'SURCO',
                        'provincia': 'LIMA', 'departamento': 'LIMA', 'tipo': 'S.A.', 'actividad_economica': '',
                        'agente_retencion': True, 'buen_contribuyente': False, 'tipo_facturacion': '',
                        'locales_anexos': [], 'n_locales': 0, 'alerta': ''}
            return {'ruc': ruc, 'razon_social': 'EMPRESA DE BAJA', 'estado': 'BAJA DE OFICIO', 'condicion': 'NO HABIDO',
                    'direccion': '-', 'ubigeo': '', 'locales_anexos': [], 'n_locales': 0, 'alerta': 'x'}
        with patch('core.padron_ruc.consultar', side_effect=falso):
            r = self.client.post(reverse('ruc_consulta'), {'rucs': '20100070970\n20131370645 20100070970'})
        self.assertEqual(ConsultaRUC.objects.count(), 2)  # sin repetidos
        self.assertContains(r, 'SUPERMERCADOS PERUANOS')
        self.assertContains(r, '1 RUC con estado o condición observada')
        self.assertContains(r, reverse('tercero_editar', args=[self.entidad.pk]))  # ya registrado
        r = self.client.get(reverse('tercero_nuevo') + '?ruc=20100070970&tipo=PROVEEDOR')
        self.assertEqual(r.context['form'].initial['nombre'], 'SUPERMERCADOS PERUANOS')
        r = self.client.get(reverse('ruc_historial'))
        self.assertContains(r, 'EMPRESA DE BAJA')


# ---------------------------------------------------------------- flota, portal y presupuestos
class SeparadosTest(Base):
    def test_flota_registra_con_su_propio_permiso(self):
        from flota.views import puede_flota
        u = usuario('flotero', ['flota'], ['flota.registrar'])
        self.assertTrue(puede_flota(u))
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse('flota:equipos')).status_code, 200)
        self.assertEqual(self.client.get(reverse('logistica:lista')).status_code, 403)

    def test_portal_y_presupuestos(self):
        comprador = usuario('comprador', ['compras', 'portal_proveedores'], ['compras.registrar'])
        self.client.force_login(comprador)
        self.assertEqual(self.client.get(reverse('compras:portal_facturas')).status_code, 200)
        self.assertEqual(self.client.get(reverse('compras:portal_tablero')).status_code, 403)  # aprobar es sensible
        analista = usuario('analista', ['presupuestos'], ['presupuestos.elaborar'])
        self.client.force_login(analista)
        self.assertEqual(self.client.get(reverse('contabilidad:presupuestos')).status_code, 200)
        self.assertEqual(self.client.get(reverse('contabilidad:asientos')).status_code, 403)
