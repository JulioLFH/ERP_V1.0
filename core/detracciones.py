"""Detracciones (SPOT) de compras y ventas: constancia de depósito, depósito que hace la empresa cuando el cliente le
pagó el total y seguimiento de lo pendiente.

- Compra: la empresa (adquirente) deposita en la cuenta del BN del proveedor. Si le pagó el total, el proveedor debe
  depositarla y la empresa registra la constancia que él le entregue (la necesita el Registro de Compras 8.1).
- Venta: el cliente deposita en la cuenta del BN de la empresa. Si pagó el total, la empresa (proveedor) deposita
  la detracción en su propia cuenta del BN hasta el 5.° día hábil siguiente al cobro: se registra como transferencia
  de su banco a la cuenta de detracciones, ligada a la venta.
"""
from django import forms
from django.db import transaction

from .forms import BootstrapMixin, validar_periodo_abierto
from .sustentos import SustentoField


class ConstanciaForm(BootstrapMixin, forms.Form):
    numero = forms.CharField(label='N° de constancia', max_length=40)
    fecha = forms.DateField(label='Fecha del depósito')


class DepositoProveedorForm(BootstrapMixin, forms.Form):
    """La empresa deposita la detracción de una venta que el cliente le pagó por el total."""
    origen = forms.ModelChoiceField(None, label='Desde la cuenta')
    destino = forms.ModelChoiceField(None, label='Cuenta de detracciones (Banco de la Nación)')
    fecha = forms.DateField(label='Fecha del depósito')
    numero = forms.CharField(label='N° de constancia de depósito', max_length=40)
    sustento = SustentoField(required=False, label='Constancia de depósito (PDF o imagen)')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from finanzas.models import Cuenta
        self.fields['origen'].queryset = Cuenta.objects.filter(activo=True, moneda='PEN', es_detracciones=False)
        self.fields['destino'].queryset = Cuenta.objects.filter(activo=True, es_detracciones=True)

    def clean(self):
        data = super().clean()
        validar_periodo_abierto(self, 'fecha')
        return data


def depositar_como_proveedor(venta, datos, usuario):
    """Transferencia del banco de la empresa a su cuenta de detracciones por el monto de la detracción (en soles),
    ligada a la venta, que queda con su constancia. Devuelve (salida, entrada) o lanza ValueError."""
    from finanzas.models import Movimiento
    monto = venta.detr_pen
    glosa = f'Depósito de detracción de {venta} (el cliente pagó el total)'
    with transaction.atomic():
        salida = Movimiento.objects.create(
            cuenta=datos['origen'], fecha=datos['fecha'], tipo='EGRESO', concepto='TRANSFERENCIA',
            medio_pago='DEPOSITO', numero_operacion=datos['numero'], monto=monto, glosa=glosa, creado_por=usuario)
        entrada = Movimiento.objects.create(
            cuenta=datos['destino'], fecha=datos['fecha'], tipo='INGRESO', concepto='TRANSFERENCIA',
            medio_pago='DEPOSITO', numero_operacion=datos['numero'], monto=monto, glosa=glosa,
            transferencia_par=salida, creado_por=usuario)
        cuenta = datos['origen']
        negativo = None if cuenta.admite_negativo else cuenta.primer_negativo(desde=datos['fecha'])
        if negativo:
            raise ValueError(f'Saldo insuficiente en {cuenta}: con el depósito quedaría en {cuenta.simbolo} '
                             f'{negativo[1]:,.2f} el {negativo[0]:%d/%m/%Y}.')
        type(venta).objects.filter(pk=venta.pk).update(
            detraccion_deposito=entrada, detraccion_constancia=datos['numero'].strip(),
            detraccion_fecha=datos['fecha'])
        if datos.get('sustento'):
            from .sustentos import guardar_archivo, vincular
            archivo = guardar_archivo(datos['sustento'], usuario)
            for m in (salida, entrada, venta):
                vincular(m, archivo, usuario, glosa)
    return salida, entrada


def pendientes(modelo, periodo='', estado=''):
    """[(doc, estado, fecha del cobro/pago total, plazo)] de los comprobantes sujetos a detracción."""
    qs = (modelo.objects.filter(detraccion_monto__gt=0, tipo_comprobante='01', estado='REGISTRADO', es_historico=False)
          .select_related('tercero').order_by('-fecha_emision', '-id'))
    if periodo:
        qs = qs.filter(periodo=periodo)
    filas = []
    for d in qs:
        situacion, fecha = d.detraccion_estado
        if estado and situacion != estado:
            continue
        filas.append((d, situacion, fecha, d.detraccion_plazo))
    return filas
