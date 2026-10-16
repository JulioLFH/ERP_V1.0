"""Prepara un escenario de prueba de la rendición de gastos en una empresa DE PRUEBA (no en la de datos reales).

    python manage.py demo_rendiciones --empresa empresa2 --clave "<contraseña de los usuarios demo>" --confirmar

Crea (si no existen): categorías de gasto, una política, reglas de aprobación de 2 niveles, los usuarios
demo.rendidor, demo.jefe y demo.contador (con la contraseña indicada), su trabajador, una caja con saldo, una entrega
a rendir de S/ 1,000 y una rendición en borrador con una factura, una boleta y una movilidad, lista para recorrer
el circuito: enviar (demo.rendidor) → aprobar (demo.jefe y luego un administrador) → contabilizar y devolver
(demo.contador)."""
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

D = Decimal


class Command(BaseCommand):
    help = 'Escenario de prueba de la rendición de gastos en una empresa de prueba.'

    def add_arguments(self, parser):
        parser.add_argument('--empresa', default='default', help='Alias de la empresa de prueba (multiempresa)')
        parser.add_argument('--clave', required=True, help='Contraseña de los usuarios demo')
        parser.add_argument('--confirmar', action='store_true', help='Graba (sin esto solo muestra la empresa)')

    def handle(self, *args, **o):
        from erp.empresas import activar, empresas
        if o['empresa'] not in empresas():
            raise CommandError(f'Empresa desconocida: {o["empresa"]}. Disponibles: {", ".join(empresas())}')
        activar(o['empresa'])
        from core.models import Empresa
        empresa = Empresa.actual()
        self.stdout.write(f'Empresa: {empresa.razon_social} (RUC {empresa.ruc}) · base «{o["empresa"]}»')
        if not o['confirmar']:
            self.stdout.write('Revise que sea la empresa DE PRUEBA y repita con --confirmar.')
            return
        with transaction.atomic():
            r = self._crear(o['clave'])
        self.stdout.write(self.style.SUCCESS(
            f'Listo: rendición {r.numero} en borrador de demo.rendidor. Ingrese como demo.rendidor, revise y envíe; '
            f'luego apruebe como demo.jefe y como administrador; contabilice y devuelva como demo.contador.'))

    def _crear(self, clave):
        from contabilidad.models import CentroCosto, CuentaContable
        from core.models import PerfilUsuario, Tercero
        from finanzas import operaciones as op
        from finanzas import rendiciones as rd
        from finanzas.models import (CategoriaGasto, Cuenta, EntregaRendir, IndicadorImpuesto, LineaGasto,
                                     PoliticaGasto, ReglaAprobacion)
        hoy = timezone.localdate()
        cc = CentroCosto.objects.filter(activo=True, tipo='ADMINISTRACION').first() or \
            CentroCosto.objects.create(codigo='DEMO-ADM', nombre='Administración (demo)', tipo='ADMINISTRACION')
        gasto = CuentaContable.objects.filter(codigo__startswith='63', imputable=True).order_by('codigo').first()
        if gasto is None:
            raise CommandError('No hay cuentas 63 imputables en el plan de cuentas.')
        cats = {}
        for codigo, nombre, extra in (('VIA', 'Viáticos y hospedaje', {}),
                                      ('ALI', 'Alimentación', {'tope': D('80')}),
                                      ('MOV', 'Movilidad', {'exige_comprobante': False, 'es_movilidad': True})):
            cats[codigo], _ = CategoriaGasto.objects.get_or_create(codigo=codigo, defaults={
                'nombre': nombre, 'cuenta_contable': gasto, **extra})
        PoliticaGasto.objects.get_or_create(nombre='Política general (demo)', defaults={
            'dias_para_rendir': 15, 'tope_por_rendicion': D('3000')})
        grupo = Group.objects.get_or_create(name='Rendición de gastos')[0]
        trabajador, _ = Tercero.objects.get_or_create(numero_doc='40000001', defaults={
            'tipo': 'AMBOS', 'tipo_doc': '1', 'nombre': 'TRABAJADOR DEMO RENDICIONES'})

        def usuario(nombre, acciones, **perfil):
            u, _ = User.objects.get_or_create(username=nombre, defaults={'first_name': nombre.split('.')[1].title()})
            u.set_password(clave)
            u.save()
            u.groups.add(grupo)
            PerfilUsuario.objects.update_or_create(usuario=u, defaults={'acciones': acciones, **perfil})
            return u
        jefe = usuario('demo.jefe', ['rendiciones.rendir'])
        usuario('demo.contador', ['rendiciones.contabilizar', 'rendiciones.tesoreria'])
        rendidor = usuario('demo.rendidor', ['rendiciones.rendir'], trabajador=trabajador, jefe=jefe)
        if not ReglaAprobacion.objects.exists():
            ReglaAprobacion.objects.create(nivel=1, tipo='JEFE')
            admin = User.objects.filter(is_superuser=True, is_active=True).first()
            if admin:
                ReglaAprobacion.objects.create(nivel=2, tipo='USUARIO', aprobador=admin, monto_desde=D('500'))
        caja = Cuenta.objects.filter(nombre='Caja demo rendiciones').first() or Cuenta.objects.create(
            nombre='Caja demo rendiciones', tipo='CAJA', saldo_inicial=D('5000'))
        entrega = op.crear_entrega(EntregaRendir(tipo='ENTREGA', responsable=trabajador, centro_costo=cc,
                                                 motivo='Viaje de prueba (demo)'), caja, D('1000'), '', 'EFECTIVO',
                                   rendidor)
        r = rd.crear(entrega, rendidor, 'Viaje de prueba (demo)')
        ind = {i.codigo: i for i in IndicadorImpuesto.objects.all()}
        for datos in (dict(tipo_comprobante='01', ruc='20100047218', razon_social='HOTEL DEMO SAC', serie='F001',
                           numero=str(hoy.toordinal())[-6:], base=D('500'), igv=D('90'), total=D('590'),
                           categoria=cats['VIA'], indicador=ind.get('C1'), glosa='Hospedaje 2 noches'),
                      dict(tipo_comprobante='03', ruc='20100070970', razon_social='RESTAURANTE DEMO', serie='B001',
                           numero=str(hoy.toordinal())[-5:], base=D('101.69'), igv=D('18.31'), total=D('120'),
                           categoria=cats['ALI'], indicador=ind.get('S1'), glosa='Almuerzo con cliente'),
                      dict(tipo_comprobante='PM', total=D('25'), base=D('25'), categoria=cats['MOV'],
                           indicador=ind.get('NG'), glosa='Taxi al cliente', destino='San Isidro')):
            rd.guardar_linea(r, LineaGasto(fecha=hoy, centro_costo=cc, **datos), rendidor)
        rd.evaluar(r)
        return r
