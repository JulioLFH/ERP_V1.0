"""Checklist v1.74.0 (OBR-01 a OBR-10): relación de insumos como S10, importación del reporte y del CSV del S10,
subpresupuestos y gastos generales detallados, recursos por código, sub-análisis, descargas, vínculo con productos,
requerimiento desde la Meta y control meta vs. real."""
import io
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from compras.models import SolicitudPedido
from core.models import Almacen, Kardex, Producto

from . import calculos, control, vinculos
from . import importar as imp
from .models import ComponenteRecurso, Obra, Partida, PrecioPresupuesto, Presupuesto, Recurso, RecursoAPU


def excel(hojas, nombre='presupuesto.xlsx'):
    from openpyxl import Workbook
    libro = Workbook()
    libro.remove(libro.active)
    for titulo, filas in hojas:
        h = libro.create_sheet(titulo)
        for f in filas:
            h.append(f)
    salida = io.BytesIO()
    libro.save(salida)
    return SimpleUploadedFile(nombre, salida.getvalue())


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('t', 't@t.com', 'x')
        cls.obra = Obra.objects.create(nombre='Puente en carretera', gastos_generales_pct=D('10'), utilidad_pct=D('10'))
        R = lambda codigo, nombre, unidad, tipo, precio: Recurso.objects.create(
            codigo=codigo, nombre=nombre, unidad=unidad, tipo=tipo, precio=D(precio))
        cls.topografo = R('0147010003', 'TOPOGRAFO', 'hh', 'MO', '25.50')
        cls.peon = R('0147010004', 'PEON', 'hh', 'MO', '19.85')
        cls.yeso = R('0229060005', 'YESO BOLSA 28 kg', 'bls', 'MAT', '9.50')
        cls.cemento = R('0221000001', 'CEMENTO PORTLAND TIPO I', 'bls', 'MAT', '28.50')
        cls.hormigon = R('0238000000', 'HORMIGON', 'm3', 'MAT', '65.00')

    def setUp(self):
        self.client.force_login(self.user)

    def presupuesto(self, tipo='OFERTA'):
        p = Presupuesto.objects.create(obra=self.obra, nombre='Prueba', tipo=tipo)
        x = Partida.objects.create(presupuesto=p, item='01.01', descripcion='TRAZO Y REPLANTEO', unidad='m2',
                                   metrado=D('250'), rendimiento=D('500'), jornada=D('8'))
        for n, (r, cuadrilla, cantidad) in enumerate([(self.topografo, '1', '0'), (self.peon, '2', '0'),
                                                      (self.yeso, None, '0.02')]):
            calculos.asegurar_precio(p, r)
            RecursoAPU.objects.create(partida=x, recurso=r, cuadrilla=D(cuadrilla) if cuadrilla else None,
                                      cantidad=D(cantidad), orden=n)
        return calculos.recalcular(p), x


class InsumosComoS10Test(Base):
    def test_parcial_es_cantidad_por_precio_y_diferencia_aparte(self):
        """OBR-01: TOPÓGRAFO 4.0000 hh × 25.50 = 102.00 (antes 102.50: sumaba los parciales redondeados por APU)."""
        p, _ = self.presupuesto()
        e = calculos.explosion(p)
        filas = {t['recurso'].nombre: t for t in e['filas']}
        self.assertEqual((filas['TOPOGRAFO']['cantidad'], filas['TOPOGRAFO']['monto']), (D('4.0000'), D('102.00')))
        self.assertEqual((filas['PEON']['cantidad'], filas['PEON']['monto']), (D('8.0000'), D('158.80')))
        for t in e['filas']:
            self.assertEqual(t['monto'], calculos.r2(t['cantidad'] * t['precio']))
        self.assertEqual(e['total'], p.costo_directo)
        self.assertEqual(e['insumos'] + e['diferencia'], p.costo_directo)
        self.assertNotEqual(e['diferencia'], 0)  # 0.41 + 0.64 + 0.19 por m2 × 250 frente a la cantidad × precio
        r = self.client.get(reverse('obras:insumos', args=[p.pk]))
        self.assertContains(r, 'Diferencia por redondeo')
        self.assertContains(r, '102.00')


class ImportacionTest(Base):
    def test_plantilla_sin_precios_no_avisa_diferencias(self):
        """OBR-02: la plantilla no trae precios unitarios: no se avisa «0.00 → 1.27»."""
        datos = imp.leer(SimpleUploadedFile('p.xlsx', imp.plantilla()))
        _, diferencias = imp.grabar(datos, self.obra, 'OFERTA', 'Plantilla')
        self.assertEqual(diferencias, [])
        datos['partidas'][1].update(precio='5.00', precio_archivo=True)  # con precio del archivo sí se compara
        _, diferencias = imp.grabar(datos, self.obra, 'OFERTA', 'Con precio')
        self.assertEqual([i for i, _, _ in diferencias], ['01.01'])

    def test_recursos_por_codigo_no_por_nombre(self):
        """OBR-07: PEON 0101010005 del S10 no se mezcla con el PEON 0147010004 que ya existía."""
        presupuesto = [['Ítem', 'Descripción', 'Und.', 'Metrado', 'Precio', 'Parcial'],
                       ['01', 'OBRAS PROVISIONALES', None, None, None, 1000], ['01.01', 'LIMPIEZA', 'm2', 100, 10, 1000]]
        apu = [['Ítem', 'Rendimiento', 'Jornada', 'Tipo', 'Código', 'Recurso', 'Unidad', 'Cuadrilla', 'Cantidad',
                'Precio'],
               ['01.01', 100, 8, 'MO', '0101010005', 'PEON', 'hh', 1, '', 19.85],
               ['01.01', 100, 8, 'MO', '0147010003', 'TOPOGRAFO', 'hh', 0.1, '', 25.50]]
        datos = imp.leer(excel([('Presupuesto', presupuesto), ('APU', apu)]))
        resumen = imp.resumen(datos)
        self.assertEqual((resumen['nuevos'], resumen['existentes']), (1, 1))
        self.assertEqual(resumen['mismo_nombre'], [('0101010005', 'PEON', '0147010004')])
        pres, _ = imp.grabar(datos, self.obra, 'OFERTA', 'S10')
        codigos = set(RecursoAPU.objects.filter(partida__presupuesto=pres).values_list('recurso__codigo', flat=True))
        self.assertEqual(codigos, {'0101010005', '0147010003'})
        self.assertEqual(Recurso.objects.filter(nombre='PEON').count(), 2)

    def test_reporte_del_s10_sin_titulos_con_subpresupuestos(self):
        """OBR-03 y OBR-04: reporte del visor (sin títulos, columnas por nivel) con dos subpresupuestos; el de gastos
        generales reemplaza al %."""
        v = [None] * 17

        def fila(**celdas):
            f = list(v)
            for col, valor in celdas.items():
                f['ABCDEFGHIJKLMNOPQ'.index(col)] = valor
            return f
        reporte = [
            fila(A='S10', N='Página :', P=1), fila(A='Presupuesto', D='0202001 PUENTE EN CARRETERA (meta)'),
            fila(A='Subpresupuesto', D='001 ESTRUCTURAS'), fila(A='Cliente', D='MUNICIPALIDAD'),
            fila(D='01', E='OBRAS PROVISIONALES', Q=5000), fila(C='01.01', D='CARTEL DE OBRA', G='und', H=1, K=2000, O=2000),
            fila(C='01.02', D='CAMPAMENTO', G='glb', H=1, K=3000, O=3000),
            fila(D='02', E='CONCRETO', Q=34556), fila(C='02.01', D="CONCRETO f'c=210", G='m3', H=100, K=345.56, O=34556),
            fila(D='COSTO DIRECTO', Q=39556),
            fila(A='Subpresupuesto', D='002 GASTOS GENERALES'),
            fila(D='01', E='GASTOS FIJOS', Q=1500), fila(C='01.01', D='MOVILIZACION', G='glb', H=1, K=1500, O=1500),
            fila(D='02', E='GASTOS VARIABLES', Q=6000), fila(C='02.01', D='INGENIERO RESIDENTE', G='mes', H=4, K=1500, O=6000)]
        datos = imp.leer(excel([('Hoja1', reporte)]))
        self.assertIn('sin títulos', datos['formato'])
        self.assertEqual([(s['codigo'], s['nombre'], s['gg']) for s in datos['subpresupuestos']],
                         [('001', 'ESTRUCTURAS', False), ('002', 'GASTOS GENERALES', True)])
        self.assertEqual([p['clave'] for p in datos['partidas']],
                         ['001|01', '001|01.01', '001|01.02', '001|02', '001|02.01',
                          '002|01', '002|01.01', '002|02', '002|02.01'])
        resumen = imp.resumen(datos)
        self.assertEqual((resumen['total_archivo'], resumen['gg_archivo']), (D('39556'), D('7500')))
        pres, _ = imp.grabar(datos, self.obra, 'META', 'Puente')
        self.assertEqual((pres.costo_directo, pres.gastos_generales), (D('39556.00'), D('7500.00')))
        self.assertEqual(pres.utilidad, D('3955.60'))  # la utilidad sigue en % del costo directo
        self.assertEqual(pres.subpresupuestos.get(codigo='002').parcial, D('7500.00'))
        r = self.client.get(reverse('obras:presupuesto', args=[pres.pk]))
        self.assertContains(r, 'SUBPRESUPUESTO GASTOS GENERALES')
        self.assertContains(r, 'GASTOS GENERALES (detallados)')
        # el ítem repetido sin encabezado también abre otro subpresupuesto
        sin_encabezado = [f for f in reporte if f[0] != 'Subpresupuesto']
        datos = imp.leer(excel([('Hoja1', sin_encabezado)]))
        self.assertEqual([s['codigo'] for s in datos['subpresupuestos']], ['001', '002'])

    def test_xls_y_csv_de_analisis_del_s10(self):
        """OBR-03: acepta .xls (Excel 97-2003) y lee el CSV de APU del S10 (cantidades a 2 decimales)."""
        with self.assertRaisesMessage(imp.ErrorImportacion, 'No se pudo leer el Excel (.xls)'):
            imp.leer(SimpleUploadedFile('reporte.xls', b'\xd0\xcf\x11\xe0' + b'\x00' * 600))
        presupuesto = [['Ítem', 'Descripción', 'Und.', 'Metrado', 'Precio', 'Parcial'],
                       ['01', 'OBRAS PRELIMINARES', None, None, None, None],
                       ['01.01', 'TRAZO Y REPLANTEO', 'm2', 250, 1.04, 260]]
        csv = ('Partida,Descripcion,Und,Rend MO,Rend EQ,Costo unitario,Codigo,Recurso,Und,Cantidad,Precio,Parcial\n'
               '01.01,TRAZO Y REPLANTEO,m2,500.0000,500.0000,1.04,0147010003,TOPOGRAFO,hh,0.02,25.50,0.41\n'
               '01.01,TRAZO Y REPLANTEO,m2,500.0000,500.0000,1.04,0229060005,YESO BOLSA 28 kg,bls,0.02,9.50,0.19\n'
               '01.01,TRAZO Y REPLANTEO,m2,500.0000,500.0000,1.04,0337010001,HERRAMIENTAS MANUALES,%mo,3.00,0.41,0.01\n')
        datos = imp.leer(excel([('Presupuesto', presupuesto)]),
                         SimpleUploadedFile('apu.chr', csv.encode('cp1252')))
        a = datos['apu']['01.01']
        self.assertEqual(a['rendimiento'], '500.0000')
        self.assertEqual([(r['codigo'], r['tipo'], r['cantidad']) for r in a['recursos']],
                         [('0147010003', 'MO', '0.0161'), ('0229060005', 'MAT', '0.0200'), ('0337010001', 'EQ', '3.00')])
        pres, _ = imp.grabar(datos, self.obra, 'OFERTA', 'CSV')
        self.assertEqual(pres.partidas.get(item='01.01').precio, D('0.61'))  # 0.41 + 0.19 + 3 % de 0.41

    def test_vista_de_importacion_con_csv_y_vinculo(self):
        presupuesto = [['Ítem', 'Descripción', 'Und.', 'Metrado', 'Precio', 'Parcial'],
                       ['01', 'CONCRETO', None, None, None, None], ['01.01', 'CONCRETO SIMPLE', 'm3', 10, 300, 3000]]
        apu = [['Ítem', 'Rendimiento', 'Jornada', 'Tipo', 'Código', 'Recurso', 'Unidad', 'Cuadrilla', 'Cantidad',
                'Precio'], ['01.01', 25, 8, 'MAT', '0221000099', 'CEMENTO SOL', 'bls', '', 9.73, 30]]
        r = self.client.post(reverse('obras:importar', args=[self.obra.pk]), {
            'archivo': excel([('Presupuesto', presupuesto), ('APU', apu)]), 'tipo': 'META', 'nombre': 'Meta'})
        self.assertContains(r, 'Vista previa')
        self.assertContains(r, 'Recursos (nuevos / existentes)')
        r = self.client.post(reverse('obras:importar', args=[self.obra.pk]), {'accion': 'grabar', 'vincular': '1'})
        self.assertEqual(r.status_code, 302)
        p = Producto.objects.get(codigo_alterno='0221000099')
        self.assertEqual((p.nombre, p.clase, p.unidad), ('CEMENTO SOL', 'SUMINISTRO', 'BG'))
        self.assertEqual(Recurso.objects.get(codigo='0221000099').producto, p)


class SubpresupuestosTest(Base):
    def test_subpresupuestos_desde_la_pantalla_y_copia(self):
        p, _ = self.presupuesto()
        url = reverse('obras:presupuesto', args=[p.pk])
        self.client.post(url, {'accion': 'subpresupuesto', 'codigo': '001', 'nombre': 'ESTRUCTURAS'})
        s1 = p.subpresupuestos.get()
        self.assertEqual(p.partidas.filter(subpresupuesto=s1).count(), 1)  # las existentes pasan al primero
        self.client.post(url, {'accion': 'subpresupuesto', 'codigo': '002', 'nombre': 'GASTOS GENERALES',
                               'es_gastos_generales': 'on'})
        s2 = p.subpresupuestos.get(codigo='002')
        # el mismo ítem 01.01 en otro subpresupuesto
        self.client.post(url, {'accion': 'agregar', 'subpresupuesto': s2.pk, 'item': '01.01',
                               'descripcion': 'RESIDENTE DE OBRA', 'unidad': 'mes', 'metrado': '3', 'precio_fijo': 'on',
                               'precio': '6000'})
        p.refresh_from_db()
        self.assertEqual(p.partidas.count(), 2)
        self.assertEqual(p.gastos_generales, D('18000.00'))
        self.assertEqual(p.costo_directo, s1.partidas.get().parcial)
        copia = calculos.copiar(p, 'META')
        self.assertEqual((copia.subpresupuestos.count(), copia.gastos_generales), (2, D('18000.00')))
        self.client.post(url, {'accion': 'eliminar_sub', 'sub': s2.pk})
        p.refresh_from_db()
        self.assertEqual((p.partidas.count(), p.gastos_generales), (1, calculos.r2(p.costo_directo / 10)))


class SubanalisisTest(Base):
    def concreto(self):
        c = Recurso.objects.create(codigo='9001010001', nombre="CONCRETO f'c=210", unidad='m3', tipo='MAT',
                                   es_subanalisis=True)
        ComponenteRecurso.objects.create(padre=c, recurso=self.cemento, cantidad=D('9.73'), orden=0)
        ComponenteRecurso.objects.create(padre=c, recurso=self.hormigon, cantidad=D('1.05'), orden=1)
        return c

    def test_subanalisis_en_el_apu_y_explotado_en_insumos(self):
        """OBR-08: el concreto como recurso de la partida, con su propio análisis, y explotado en los insumos."""
        concreto = self.concreto()
        p = Presupuesto.objects.create(obra=self.obra, nombre='Con sub-análisis')
        x = Partida.objects.create(presupuesto=p, item='01', descripcion='ZAPATAS', unidad='m3', metrado=D('10'))
        calculos.asegurar_precio(p, concreto)
        RecursoAPU.objects.create(partida=x, recurso=concreto, cantidad=D('1.05'))
        self.assertTrue(PrecioPresupuesto.objects.filter(presupuesto=p, recurso=self.cemento).exists())
        calculos.recalcular(p)
        x.refresh_from_db()
        self.assertEqual(calculos.precios_de(p)[concreto.pk], D('345.56'))  # 9.73 × 28.50 + 1.05 × 65
        self.assertEqual(x.precio, D('362.84'))  # 1.05 m3 × 345.56
        e = calculos.explosion(p)
        filas = {t['recurso'].nombre: t for t in e['filas']}
        self.assertNotIn("CONCRETO f'c=210", filas)
        self.assertEqual((filas['CEMENTO PORTLAND TIPO I']['cantidad'], filas['HORMIGON']['cantidad']),
                         (D('102.1650'), D('11.0250')))  # 10 × 1.05 × 9.73 y 10 × 1.05 × 1.05
        sin_explotar = calculos.explosion(p, explotar=False)
        self.assertEqual([t['recurso'] for t in sin_explotar['filas']], [concreto])
        # sube el cemento en el presupuesto: cambia el concreto y la partida
        PrecioPresupuesto.objects.filter(presupuesto=p, recurso=self.cemento).update(precio=D('30'))
        calculos.recalcular(p)
        x.refresh_from_db()
        self.assertEqual(x.precio, D('378.16'))  # (291.90 + 68.25) × 1.05 = 378.1575
        self.assertEqual(PrecioPresupuesto.objects.get(presupuesto=p, recurso=concreto).precio, D('360.15'))
        # la pantalla del sub-análisis agrega un componente y recalcula los presupuestos en borrador
        agua = Recurso.objects.create(codigo='0239050000', nombre='AGUA', unidad='m3', tipo='MAT', precio=D('5'))
        r = self.client.post(reverse('obras:subanalisis', args=[concreto.pk]), {
            'rendimiento': '0', 'jornada': '8', 'nuevo_recurso': f'{agua.codigo} · AGUA (m3)', 'nuevo_cantidad': '0.18'})
        self.assertRedirects(r, reverse('obras:subanalisis', args=[concreto.pk]))
        x.refresh_from_db()
        self.assertEqual(x.precio, D('379.10'))  # (360.15 + 0.18 × 5) × 1.05 = 379.1025
        self.assertContains(self.client.get(reverse('obras:subanalisis', args=[concreto.pk])), 'AGUA')

    def test_importa_subanalisis_de_la_plantilla(self):
        presupuesto = [['Ítem', 'Descripción', 'Unidad', 'Metrado', 'Precio', 'Parcial'],
                       ['01', 'CIMENTACION', '', '', '', ''], ['01.01', 'ZAPATAS', 'm3', 10, '', '']]
        apu = [['Ítem', 'Rendimiento', 'Jornada', 'Tipo', 'Código', 'Recurso', 'Unidad', 'Cuadrilla', 'Cantidad',
                'Precio'], ['01.01', 20, 8, 'MAT', '9001010009', "CONCRETO f'c=175", 'm3', '', 1.05, '']]
        sub = [['Sub-análisis', 'Descripción sub-análisis', 'Und. sub-análisis', 'Rendimiento', 'Jornada', 'Tipo',
                'Código', 'Recurso', 'Unidad', 'Cuadrilla', 'Cantidad', 'Precio'],
               ['9001010009', "CONCRETO f'c=175", 'm3', '', '', 'MAT', '0221000001', 'CEMENTO PORTLAND TIPO I', 'bls',
                '', 8.43, 28.50],
               ['9001010009', "CONCRETO f'c=175", 'm3', '', '', 'MAT', '0238000000', 'HORMIGON', 'm3', '', 1.05, 65]]
        datos = imp.leer(excel([('Presupuesto', presupuesto), ('APU', apu), ('Subanálisis', sub)]))
        self.assertEqual(list(datos['subanalisis']), ['9001010009'])
        pres, _ = imp.grabar(datos, self.obra, 'OFERTA', 'Sub')
        concreto = Recurso.objects.get(codigo='9001010009')
        self.assertTrue(concreto.es_subanalisis)
        self.assertEqual(concreto.componentes.count(), 2)
        self.assertEqual(pres.partidas.get(item='01.01').precio, D('323.94'))  # 1.05 × (240.26 + 68.25)
        e = calculos.explosion(pres)
        self.assertEqual({t['recurso'].codigo for t in e['filas']}, {'0221000001', '0238000000'})


class DescargasTest(Base):
    def test_las_descargas_ocultan_el_aviso_de_carga(self):
        """OBR-09: el servidor marca las descargas (cookie) para que la pantalla quite «Cargando…»."""
        r = self.client.get(reverse('obras:plantilla'))
        self.assertIn('attachment', r['Content-Disposition'])
        self.assertIn('ceiba_descarga', r.cookies)
        self.assertNotIn('ceiba_descarga', self.client.get(reverse('obras:lista')).cookies)


class VinculoYControlTest(Base):
    def meta(self):
        p = Presupuesto.objects.create(obra=self.obra, nombre='Meta', tipo='META')
        x = Partida.objects.create(presupuesto=p, item='01', descripcion='CONCRETO', unidad='m3', metrado=D('35.5'))
        for r, cantidad in ((self.cemento, '9.73'), (self.hormigon, '1.05')):
            calculos.asegurar_precio(p, r)
            RecursoAPU.objects.create(partida=x, recurso=r, cantidad=D(cantidad))
        return calculos.recalcular(p)

    def test_vincular_requerir_y_controlar(self):
        """OBR-10: materiales → productos (código del S10 como código alterno), requerimiento desde la Meta con aviso
        si se pide de más, y meta vs. pedido, recibido y consumido."""
        p = self.meta()
        propuesta = vinculos.proponer(vinculos.recursos_de(p))
        self.assertEqual({f['accion'] for f in propuesta}, {'CREAR'})
        c = vinculos.vincular(vinculos.recursos_de(p))
        self.assertEqual(c['creados'], 2)
        cemento = Producto.objects.get(codigo_alterno='0221000001')
        self.assertEqual((cemento.unidad, cemento.clase), ('BG', 'SUMINISTRO'))
        self.assertEqual(vinculos.buscar_producto(Recurso(codigo='0221000001', nombre='otro'))[0], cemento)
        self.assertEqual(vinculos.proponer(vinculos.recursos_de(p)), [])  # ya no hay nada que vincular
        filas, sin_producto = control.meta(p)
        self.assertEqual((filas[cemento.pk]['cantidad'], sin_producto), (D('345.4150'), []))
        # requerir: lo pedido de más exige justificación
        creadas = control.requerir(p, {cemento.pk: D('100')}, self.user)
        sp = creadas[0]
        self.assertEqual((sp.origen, sp.centro_costo, sp.tipo, sp.estado), ('OBRA', self.obra.centro_costo, 'MATERIAL',
                                                                           'BORRADOR'))
        linea = sp.lineas.get()
        self.assertEqual((linea.producto, linea.cantidad, linea.precio_estimado), (cemento, D('100.00'), D('28.50')))
        with self.assertRaisesMessage(control.ErrorRequerimiento, 'supera la Meta'):
            control.requerir(p, {cemento.pk: D('300')}, self.user)
        control.requerir(p, {cemento.pk: D('300')}, self.user, justificacion='Mayor metrado aprobado')
        self.assertIn('Mayor metrado', SolicitudPedido.objects.latest('id').motivo)
        # recibido en el almacén de obra y consumido con el centro de la obra
        hoy = timezone.localdate()
        Kardex.objects.create(producto=cemento, almacen=self.obra.almacen, fecha=hoy, tipo='ENTRADA',
                              cantidad=D('120'), saldo=D('120'), referencia='NI prueba', costo_unitario=D('29'))
        Kardex.objects.create(producto=cemento, almacen=self.obra.almacen, fecha=hoy, tipo='SALIDA',
                              cantidad=D('50'), saldo=D('70'), referencia='Consumo', costo_unitario=D('29'),
                              centro_costo=self.obra.centro_costo)
        Kardex.objects.create(producto=cemento, almacen=Almacen.principal(), fecha=hoy, tipo='SALIDA',
                              cantidad=D('999'), saldo=D('0'), referencia='Venta')  # de otro centro: no cuenta
        c = control.control(p)
        f = next(x for x in c['filas'] if x['producto'] == cemento)
        self.assertEqual((f['pedido'], f['recibido'], f['consumido'], f['costo_real']),
                         (D('400.00'), D('120'), D('50'), D('1450.00')))
        self.assertEqual(f['desviacion'], D('25.00'))  # 50 × (29 − 28.50)
        self.assertTrue(f['excede'])
        # pantallas
        r = self.client.get(reverse('obras:control', args=[p.pk]))
        self.assertContains(r, 'CEMENTO PORTLAND TIPO I')
        r = self.client.post(reverse('obras:control', args=[p.pk]), {f'pedir_{cemento.pk}': ''})
        self.assertRedirects(r, reverse('obras:control', args=[p.pk]))  # nada que pedir
        hormigon = Producto.objects.get(codigo_alterno='0238000000')
        r = self.client.post(reverse('obras:control', args=[p.pk]), {f'pedir_{hormigon.pk}': '10'})
        nueva = SolicitudPedido.objects.latest('id')
        self.assertRedirects(r, reverse('solicitudes:detalle', args=[nueva.pk]), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('obras:vincular') + f'?presupuesto={p.pk}').status_code, 200)
