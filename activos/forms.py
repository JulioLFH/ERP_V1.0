from decimal import Decimal

from django import forms

from core.forms import BootstrapMixin
from core.models import Tercero
from core.sustentos import SustentoField

from .models import ActivoFijo, CategoriaActivo

BLOQUEABLES = ['categoria', 'origen', 'fecha_adquisicion', 'fecha_uso', 'valor', 'valor_residual', 'vida_util_meses',
               'dep_inicial', 'dep_inicial_hasta']


class CategoriaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CategoriaActivo
        fields = ['codigo', 'nombre', 'cuenta_activo', 'cuenta_depreciacion', 'cuenta_gasto', 'deprecia',
                  'tasa_anual', 'vida_util_meses', 'activo']

    def clean(self):
        datos = super().clean()
        if datos.get('deprecia') and not (datos.get('cuenta_depreciacion') and datos.get('cuenta_gasto')):
            self.add_error('cuenta_depreciacion', 'Indique las cuentas de depreciación acumulada y de gasto.')
        return datos


class ActivoForm(BootstrapMixin, forms.ModelForm):
    sustento = SustentoField(required=False, label='Documento de sustento',
                             help_text='Factura, contrato, acta de entrega o inventario valorizado (máx. 5 MB)')
    unidades = forms.IntegerField(label='Cantidad de activos a registrar', min_value=1, initial=1, required=False,
                                  help_text='Se crea un activo por unidad, cada uno con el valor indicado')

    class Meta:
        model = ActivoFijo
        fields = ['nombre', 'categoria', 'marca', 'modelo', 'serie', 'ubicacion', 'responsable', 'centro_costo',
                  'origen', 'proveedor', 'documento', 'fecha_adquisicion', 'fecha_uso', 'valor', 'valor_residual',
                  'vida_util_meses', 'dep_inicial', 'dep_inicial_hasta']

    def __init__(self, *args, compra=None, maximo=1, **kwargs):
        super().__init__(*args, **kwargs)
        self.compra = compra or self.instance.compra
        self.maximo = maximo
        self.fields['categoria'].queryset = CategoriaActivo.objects.filter(activo=True)
        self.fields['proveedor'].queryset = Tercero.objects.filter(tipo__in=['PROVEEDOR', 'AMBOS'])
        self.fields['proveedor'].required = False
        if self.compra:
            self.fields['origen'].choices = [('COMPRA', 'Compra registrada en el sistema')]
            for nombre in ('proveedor', 'documento'):
                self.fields.pop(nombre)
        else:
            self.fields['origen'].choices = [c for c in ActivoFijo.ORIGENES if c[0] != 'COMPRA']
        if self.instance.pk or not self.compra or maximo <= 1:
            self.fields.pop('unidades')
        else:
            self.fields['unidades'].max_value = maximo
            self.fields['unidades'].help_text = f'Hasta {maximo}; se crea un activo por unidad con el valor indicado'
        if self.instance.pk:
            self.fields['sustento'].help_text = 'Opcional: agregar otro documento'
            if self.instance.bloqueado:
                for nombre in BLOQUEABLES:
                    if nombre in self.fields:
                        self.fields[nombre].disabled = True
                        self.fields[nombre].help_text = 'Con depreciación registrada no se modifica'

    def clean(self):
        datos = super().clean()
        cat, valor = datos.get('categoria'), datos.get('valor')
        residual = datos.get('valor_residual') or Decimal('0')
        vida = datos.get('vida_util_meses')
        if valor is not None and valor <= 0:
            self.add_error('valor', 'Debe ser mayor a cero.')
        if valor and (residual < 0 or residual >= valor):
            self.add_error('valor_residual', 'Debe ser menor al valor de adquisición.')
        if cat and cat.deprecia and not vida:
            self.add_error('vida_util_meses', 'Indique la vida útil.')
        adq, uso = datos.get('fecha_adquisicion'), datos.get('fecha_uso')
        if adq and uso and uso < adq:
            self.add_error('fecha_uso', 'No puede ser anterior a la adquisición.')
        dep, hasta = datos.get('dep_inicial') or Decimal('0'), datos.get('dep_inicial_hasta')
        origen = datos.get('origen')
        if dep:
            if origen != 'SALDO_INICIAL':
                self.add_error('dep_inicial', 'Solo para activos existentes (saldo inicial).')
            elif not hasta:
                self.add_error('dep_inicial_hasta', 'Indique hasta qué fecha corresponde la depreciación inicial.')
            elif valor and dep > valor - residual:
                self.add_error('dep_inicial', 'No puede superar el valor depreciable.')
        if hasta and uso and hasta < uso:
            self.add_error('dep_inicial_hasta', 'No puede ser anterior al inicio de uso.')
        if hasta and not dep:
            datos['dep_inicial_hasta'] = None
        if not self.instance.pk and origen != 'COMPRA' and not datos.get('sustento'):
            self.add_error('sustento', 'Adjunte el documento que sustenta el activo (factura, contrato, acta).')
        return datos
