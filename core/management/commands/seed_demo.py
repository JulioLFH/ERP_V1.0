"""Carga datos de demostración: python manage.py seed_demo"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from compras.models import Compra, CompraItem
from core.models import Almacen, Empresa, Producto, Serie, Tercero
from finanzas.models import Cuenta, Movimiento
from logistica.models import Conductor, GuiaItem, GuiaRemision, Vehiculo
from ventas.models import Venta, VentaItem


class Command(BaseCommand):
    help = 'Crea empresa, maestros y comprobantes de ejemplo'

    @transaction.atomic
    def handle(self, *args, **opts):
        if Tercero.objects.exists():
            self.stdout.write('Ya existen datos; no se cargó la demo.')
            return
        emp = Empresa.actual()
        emp.ruc, emp.razon_social = '20601234565', 'COMERCIAL DEMO S.A.C.'
        emp.direccion = 'Av. Javier Prado Este 123, San Isidro, Lima'
        emp.save()

        for tipo, serie in (('01', 'F001'), ('03', 'B001'), ('07', 'FC01'), ('08', 'FD01')):
            Serie.objects.get_or_create(tipo=tipo, serie=serie)

        prov = [Tercero.objects.create(tipo='PROVEEDOR', numero_doc=r, nombre=n, dias_credito=30)
                for r, n in (('20100047218', 'DISTRIBUIDORA ANDINA S.A.'), ('20512345671', 'IMPORTACIONES DEL PACIFICO SAC'),
                             ('20131312955', 'SERVICIOS GENERALES LIMA EIRL'))]
        cli = [Tercero.objects.create(tipo='CLIENTE', numero_doc=r, nombre=n, zona=z, dias_credito=15)
               for r, n, z in (('20555555556', 'MINERA LOS ANDES S.A.', 'Norte'), ('20444444445', 'CONSTRUCTORA SUR SAC', 'Sur'),
                               ('20333333334', 'RETAIL CENTRO EIRL', 'Lima'))]
        cli.append(Tercero.objects.create(tipo='CLIENTE', tipo_doc='1', numero_doc='45678912', nombre='JUAN PEREZ ROJAS', zona='Lima'))

        prods = [Producto.objects.create(codigo=c, nombre=n, precio_venta=Decimal(p), stock_minimo=10)
                 for c, n, p in (('P001', 'Laptop Core i5 16GB', '2500'), ('P002', 'Monitor 24" Full HD', '520'),
                                 ('P003', 'Teclado + mouse inalámbrico', '85'), ('P004', 'Impresora multifuncional', '780'))]
        serv = Producto.objects.create(codigo='S001', nombre='Servicio de soporte técnico', clase='SERVICIO',
                                       unidad='ZZ', precio_venta=Decimal('350'))

        caja = Cuenta.objects.create(tipo='CAJA', nombre='Caja principal', saldo_inicial=Decimal('2000'))
        bcp = Cuenta.objects.create(tipo='BANCO', nombre='BCP Cta. Cte. Soles', banco='BCP', numero='191-1234567-0-12',
                                    saldo_inicial=Decimal('50000'))
        Cuenta.objects.create(tipo='BANCO', nombre='BCP Cta. Cte. Dólares', banco='BCP', moneda='USD',
                              numero='191-7654321-1-45', saldo_inicial=Decimal('8000'))
        Cuenta.objects.create(tipo='BANCO', nombre='Banco de la Nación - Detracciones', banco='BN',
                              es_detracciones=True, numero='00-000-123456')

        hoy = date.today()
        compras_data = [
            (prov[0], 'F001', '000451', 40, [(prods[0], 10, '1800'), (prods[1], 20, '350')]),
            (prov[1], 'F002', '001288', 25, [(prods[2], 50, '45'), (prods[3], 8, '520')]),
            (prov[0], 'F001', '000502', 5, [(prods[1], 10, '355')]),
        ]
        for tercero, serie, numero, dias, items in compras_data:
            c = Compra.objects.create(tercero=tercero, serie=serie, numero=numero, fecha_emision=hoy - timedelta(days=dias),
                                      fecha_vencimiento=hoy - timedelta(days=dias) + timedelta(days=30), forma_pago='CREDITO')
            for p, cant, precio in items:
                CompraItem.objects.create(documento=c, producto=p, descripcion=p.nombre, cantidad=cant, precio_unitario=Decimal(precio))
            c.calcular_totales()
            c.save()
            c.aplicar_stock()
        gasto = Compra.objects.create(tercero=prov[2], serie='E001', numero='315', clasificacion='SERVICIO',
                                      fecha_emision=hoy - timedelta(days=3), detraccion_pct=Decimal('12'),
                                      ingresar_almacen=False)
        CompraItem.objects.create(documento=gasto, descripcion='Mantenimiento de oficinas', cantidad=1, precio_unitario=Decimal('1500'))
        gasto.calcular_totales()
        gasto.save()

        primera = Compra.objects.order_by('fecha_emision').first()
        Movimiento.objects.create(cuenta=bcp, fecha=hoy - timedelta(days=10), tipo='EGRESO', concepto='PAGO',
                                  tercero=primera.tercero, compra=primera, monto=primera.total, glosa=f'Pago {primera}')

        ventas_data = [
            ('01', cli[0], 30, [(prods[0], 3, '2500'), (serv, 2, '350')], '10'),
            ('01', cli[1], 18, [(prods[1], 6, '520'), (prods[2], 10, '85')], '0'),
            ('03', cli[3], 2, [(prods[2], 2, '85')], '0'),
            ('01', cli[2], 1, [(prods[3], 2, '780'), (prods[0], 1, '2450')], '0'),
        ]
        for tipo, tercero, dias, items, detr in ventas_data:
            serie, numero = Serie.siguiente(tipo, 'F001' if tipo == '01' else 'B001')
            v = Venta.objects.create(tipo_comprobante=tipo, serie=serie, numero=numero, tercero=tercero, vendedor='Ana Torres',
                                     fecha_emision=hoy - timedelta(days=dias), forma_pago='CREDITO' if tipo == '01' else 'CONTADO',
                                     fecha_vencimiento=hoy - timedelta(days=dias) + timedelta(days=15), detraccion_pct=Decimal(detr))
            for p, cant, precio in items:
                VentaItem.objects.create(documento=v, producto=p, descripcion=p.nombre, cantidad=cant, precio_unitario=Decimal(precio))
            v.calcular_totales()
            v.save()
            v.aplicar_stock()
        boleta = Venta.objects.filter(tipo_comprobante='03').first()
        Movimiento.objects.create(cuenta=caja, fecha=boleta.fecha_emision, tipo='INGRESO', concepto='COBRANZA',
                                  medio_pago='EFECTIVO', tercero=boleta.tercero, venta=boleta, monto=boleta.total)
        Movimiento.objects.create(cuenta=bcp, fecha=hoy - timedelta(days=5), tipo='EGRESO', concepto='GASTO_BANCARIO',
                                  monto=Decimal('25.50'), glosa='Mantenimiento de cuenta')
        Movimiento.objects.create(cuenta=bcp, fecha=hoy - timedelta(days=4), tipo='EGRESO', concepto='SERVICIOS',
                                  monto=Decimal('480.00'), glosa='Luz del Sur / Sedapal')
        # ---- logística e inventario
        emp.ubigeo = '150131'
        emp.save()
        principal = Almacen.principal()
        principal.direccion, principal.ubigeo = emp.direccion, '150131'
        principal.save()
        Almacen.objects.create(codigo='ALM02', nombre='Almacén Callao', direccion='Av. Argentina 2450, Callao',
                               ubigeo='070101', codigo_sunat='0001')
        for t, ubi in zip(cli, ('060101', '040101', '150101', '150132')):
            t.ubigeo = ubi
            t.direccion = t.direccion or 'Av. Principal 100'
            t.save()
        Tercero.objects.create(tipo='PROVEEDOR', numero_doc='20601111111', nombre='TRANSPORTES RAPIDOS DEL PERU SAC',
                               registro_mtc='1554321CNG', direccion='Av. Colonial 1200, Lima', ubigeo='150101')
        vehiculo = Vehiculo.objects.create(placa='ABC123', marca='Hyundai', modelo='HD78')
        conductor = Conductor.objects.create(numero_doc='41234567', nombres='Carlos', apellidos='Ramos Quispe',
                                             licencia='Q41234567')
        venta = Venta.objects.filter(tipo_comprobante='01').order_by('fecha_emision').first()
        guia = GuiaRemision.objects.create(
            tipo='09', serie='T001', numero=Serie.siguiente('09', 'T001')[1], fecha_emision=venta.fecha_emision,
            fecha_traslado=venta.fecha_emision, motivo_traslado='01', modalidad='02', destinatario=venta.tercero,
            vehiculo=vehiculo, conductor=conductor, venta=venta, partida_ubigeo='150131',
            partida_direccion=emp.direccion, llegada_ubigeo=venta.tercero.ubigeo,
            llegada_direccion=venta.tercero.direccion or 'Av. Principal 100', peso_bruto=Decimal('25'),
            numero_bultos=4, efecto_stock='NINGUNO', almacen_origen=principal)
        for i in venta.items.select_related('producto'):
            if i.producto and i.producto.es_inventariable:
                GuiaItem.objects.create(documento=guia, producto=i.producto, descripcion=i.descripcion,
                                        cantidad=i.cantidad, unidad=i.producto.unidad)
        self.stdout.write(self.style.SUCCESS('Datos de demostración cargados.'))
