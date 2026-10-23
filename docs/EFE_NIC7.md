# Estado de Flujos de Efectivo (NIC 7) en Ceiba ERP — guía de configuración y desarrollo

> Documento de diseño para el equipo de desarrollo. Base: NIC 7, PCGE 2019 y la práctica de presentación de la SMV.
> Criterio: **lo percibido** (solo cuenta el efectivo que realmente entró o salió).
> Estado: **implementado en Ceiba v1.73.0** (`contabilidad/flujo_efectivo.py`, semilla en `contabilidad/flujo_semilla.py`;
> pantallas en Contabilidad › Reportes › Estado de flujos de efectivo y Contabilidad › Configuración). Ver la sección 8
> para lo que cambió respecto de esta propuesta al probarlo con datos reales.
> Las referencias a la SMV son de práctica general: antes de cerrar los rubros, contrastarlos con el formato de EEFF
> vigente de la SMV.

---

## 0. Principios de diseño

| # | Principio | Consecuencia en Ceiba |
|---|---|---|
| P1 | **Una sola fuente de verdad: los asientos que tocan efectivo.** Cada cobro/pago de Finanzas ya genera su asiento (`contabilidad.Asiento` con `movimiento`), y cada caja/banco tiene su subcuenta 10xxx. | El EFE se calcula leyendo `AsientoLinea` de las cuentas de efectivo, no con un registro paralelo. Así cuadra siempre con la cuenta 10. |
| P2 | **Clasificar por la contrapartida, no por la caja.** El rubro lo define *contra qué* se movió el efectivo (12, 42, 33, 45, 50…). | Tabla de reglas por prefijo PCGE + excepciones por documento (compra de activo fijo, préstamo, leasing). |
| P3 | **Una sola clasificación para ambos métodos.** | El flujo de operación del método directo debe ser **igual** al del indirecto (validación V4). |
| P4 | **Políticas contables explícitas.** NIC 7 deja opciones (intereses, dividendos, fondos restringidos). | Parámetros por empresa, aplicados de forma uniforme entre periodos (NIC 7.31). |
| P5 | **Las transacciones que no son efectivo se excluyen** (NIC 7.43): compras al crédito, leasing al inicio, canjes, aplicación de anticipos, detracciones aplicadas a tributos si el BN no es efectivo. | No aparecen en el directo; en el indirecto entran solo por variación de saldos. |

---

## 1. Método directo: actividades y mapeo de conceptos

### 1.1 Catálogo de rubros (semilla de la tabla `RubroFlujo`)

Signo: **(+)** entrada, **(−)** salida. «Contrapartida PCGE» es el prefijo de la cuenta contra la que se movió el efectivo.
«Origen en Ceiba» indica de dónde sale hoy el movimiento.

#### A) Actividades de operación

| Código | Rubro | Signo | Contrapartida PCGE | Origen en Ceiba |
|---|---|---|---|---|
| OP-E01 | Cobranza de ventas de bienes y prestación de servicios | + | 121, 123 (letras), 122 (anticipos de clientes), 13 (comerciales de relacionadas) | Cobranza (`COBRANZA`), cobranza de detracción en el BN (`DETRACCION` ingreso), letras por cobrar |
| OP-E02 | Otros cobros de efectivo de operación | + | 16 (excepto 165), 75, 18 | Movimientos `OTRO` de ingreso |
| OP-E03 | Intereses y rendimientos cobrados *(si la política los pone en operación)* | + | 772, 1681 | `OTRO` contra 77x |
| OP-S01 | Pagos a proveedores de bienes y servicios | − | 421, 423 (letras), 422 (anticipos a proveedores), 43 (comerciales a relacionadas) — **salvo activo fijo e intangibles** (ver R2) | Pago (`PAGO`), pago múltiple / masivo, depósito de detracción al proveedor (`DETRACCION` egreso) |
| OP-S02 | Pagos de gastos operativos y administrativos sin factura de proveedor (luz, agua, internet, alquileres pagados directo) | − | 62–65 (salvo 62 remuneraciones), 46 diversos operativos | `SERVICIOS`, rendiciones de caja chica |
| OP-S03 | Pagos a y por cuenta de los empleados (planillas, gratificaciones, CTS, AFP retenida) | − | 41, 1411 (préstamos al personal), 4031–4032 *(si se agrupan como cargas sociales)*, 407 | `PLANILLA`, entregas a rendir rendidas |
| OP-S04 | Pago de tributos (IGV, retenciones, EsSalud, ONP, otros) | − | 401 (excepto 4017), 403, 405 | `TRIBUTOS` |
| OP-S05 | Impuesto a las ganancias pagado (renta mensual y regularización) | − | 4017 | `TRIBUTOS` contra 4017 — NIC 7.35 exige mostrarlo separado |
| OP-S06 | Otros pagos de operación (gastos bancarios, ITF, comisiones) | − | 639, 641, 659 | `GASTO_BANCARIO`, `OTRO` |
| OP-S07 | Intereses pagados *(si la política los pone en operación)* | − | 673, 455 (costos de financiación por pagar), parte de interés de la cuota | Cuotas de préstamo (parte de interés) |

> **IGV**: los cobros a clientes y los pagos a proveedores van **con IGV incluido** (es lo percibido); el IGV neto pagado a
> SUNAT va en OP-S04. **Detracciones**: el depósito en la cuenta del BN del proveedor es parte de OP-S01; lo que el
> cliente depositó en nuestra cuenta del BN es OP-E01 (si el BN es efectivo, ver política POL-4).

#### B) Actividades de inversión

| Código | Rubro | Signo | Contrapartida PCGE | Origen en Ceiba |
|---|---|---|---|---|
| IN-S01 | Compra de propiedad, planta y equipo (maquinaria, laptops, vehículos) | − | 33, 465 (pasivo por compra de inmovilizado), 421 **de compras con clasificación `ACTIVO_FIJO`**, anticipos y obras en curso de activo (339) | Pago de una compra `ACTIVO_FIJO` (regla R2) |
| IN-S02 | Compra de intangibles (licencias de software, marcas) | − | 34, 421 de compras cuyas líneas van a 34 | Pago de compra con líneas en 34 (R2) |
| IN-E01 | Venta de propiedad, planta y equipo e intangibles | + | 165 (venta de activo inmovilizado), 1653, ventas de activos registradas en Activos fijos | Cobranza de una venta de activo (R2) |
| IN-S03 | Préstamos otorgados a terceros y vinculadas | − | 1422 (préstamos a accionistas), 168 (otras cuentas por cobrar – préstamos), 17 (diversas de relacionadas: préstamos) | `PRESTAMO` egreso contra 14/16/17 |
| IN-E02 | Cobranza de préstamos otorgados | + | mismas que IN-S03 | `PRESTAMO` ingreso contra 14/16/17 |
| IN-S04 | Compra de inversiones financieras (no equivalentes de efectivo) | − | 30, 31 | `OTRO` contra 30/31 |
| IN-E03 | Venta de inversiones financieras | + | 30, 31 | |
| IN-E04 | Intereses y dividendos recibidos *(si la política los pone en inversión)* | + | 772, 773 | |

#### C) Actividades de financiamiento

| Código | Rubro | Signo | Contrapartida PCGE | Origen en Ceiba |
|---|---|---|---|---|
| FI-E01 | Aportes de capital en efectivo | + | 50, 52, 1421 (suscripciones por cobrar) | `OTRO` ingreso contra 50/52/1421 |
| FI-E02 | Préstamos bancarios recibidos (pagarés, líneas de crédito) y obligaciones emitidas | + | 451, 453, 456 | Desembolso de `Prestamo` tipo `PRESTAMO` |
| FI-E03 | Préstamos recibidos de accionistas y relacionadas | + | 4411 (préstamos de accionistas), 47 (diversas a relacionadas) | `PRESTAMO` ingreso contra 44/47 |
| FI-S01 | Amortización (capital) de préstamos | − | 451, 453, 456, 4411, 47 (parte de capital) | Pago de `CuotaPrestamo` (capital) |
| FI-S02 | Cuotas de arrendamiento financiero (leasing) — capital | − | 452 | Pago de `CuotaPrestamo` de `Prestamo` tipo `LEASING` (capital) |
| FI-S03 | Dividendos pagados | − | 4412 (dividendos por pagar) | `OTRO` egreso contra 4412 |
| FI-S04 | Intereses pagados *(si la política los pone en financiamiento)* | − | 673, 455, interés de la cuota | Pago de `CuotaPrestamo` (interés) |
| FI-S05 | Devolución de capital / recompra de acciones | − | 50, 52 | |

#### D) Fuera de las actividades

| Código | Concepto | Tratamiento |
|---|---|---|
| NO-01 | Transferencias entre cuentas de efectivo (banco ↔ banco, banco ↔ caja chica, depósitos en tránsito) | Se excluyen: el asiento solo tiene cuentas de efectivo. |
| NO-02 | Efecto de las variaciones en el tipo de cambio sobre el efectivo | Línea propia de la conciliación (NIC 7.28): ajuste de cierre de bancos en US$ contra 676/776. |
| NO-03 | Transacciones no monetarias | Excluidas (NIC 7.43); se revelan en nota. |

### 1.2 Políticas contables configurables (por empresa)

| Código | Política | Opciones NIC 7 | Valor sugerido |
|---|---|---|---|
| POL-1 | Intereses pagados | Operación (NIC 7.33) o financiamiento | **Financiamiento** (separa el costo de la deuda del giro) |
| POL-2 | Intereses y dividendos recibidos | Operación o inversión | **Inversión** |
| POL-3 | Dividendos pagados | Financiamiento (NIC 7.34) u operación | **Financiamiento** |
| POL-4 | Cuenta de detracciones del BN (1042/107) | Equivalente de efectivo (con revelación de la restricción) o fondo restringido fuera del efectivo | **Dentro del efectivo + nota**. Si se excluye: los depósitos de clientes en el BN y los tributos pagados con esos fondos pasan a ser no monetarios. |
| POL-5 | Sobregiros bancarios exigibles a la vista | Componente del efectivo (NIC 7.8) o financiamiento | Financiamiento salvo que formen parte de la gestión diaria del efectivo |
| POL-6 | Depósitos a plazo | Equivalente si vencen en ≤ 3 meses desde la adquisición (NIC 7.7) | Por cuenta: marca «equivalente de efectivo» |

---

## 2. Motor de clasificación (método directo)

### 2.1 Algoritmo

Para un rango de fechas (`desde`, `hasta`) y la empresa activa:

1. **Universo**: `AsientoLinea` de asientos con `norma in ('AMBOS', 'NIIF')`, excepto la apertura, cuyas cuentas sean
   **cuentas de efectivo** (ver 2.3). El efectivo del asiento es `Σ(debe − haber)` de esas líneas.
2. **Transferencia interna (NO-01)**: si todas las líneas del asiento son cuentas de efectivo, el flujo es 0: se omite.
3. **Ajuste de diferencia de cambio de cierre (NO-02)**: si las líneas que no son de efectivo son solo 676/776 (asiento de
   ajuste de saldos en US$ de la centralización), va a la línea «Efecto de las variaciones en el tipo de cambio».
4. **Reparto**: el efectivo del asiento se reparte entre las líneas que no son de efectivo **del lado opuesto** en proporción
   a su importe. Las líneas 676/776 y de redondeo dentro de un cobro o pago se **absorben** en la contrapartida principal
   (la diferencia de cambio realizada no es un flujo aparte: el flujo es el importe en soles efectivamente cobrado o pagado).
5. **Rubro de cada porción**, por prioridad (la primera regla que aplica gana):

| Prioridad | Regla | Ejemplo |
|---|---|---|
| R1 | **Rubro manual** en el movimiento de Finanzas (`Movimiento.rubro_flujo`) | Un ingreso `OTRO` que es un aporte de capital |
| R2 | **Por documento de origen** del asiento | Pago de compra `ACTIVO_FIJO` → IN-S01; compra con líneas en 34 → IN-S02 (prorrata por líneas); cuota de préstamo → capital FI-S01/FI-S02 e interés según POL-1; desembolso de préstamo → FI-E02 |
| R3 | **Por cuenta contable de contrapartida** (`ReglaFlujoCuenta`, prefijo PCGE más largo) | 1421 → FI-E01 aunque 142 → IN-S03 |
| R4 | **Por concepto del movimiento** (`Movimiento.concepto` + tipo) | `PLANILLA` → OP-S03 |
| R5 | **Sin clasificar** → OP-E02 / OP-S06 y alerta V3 | |

6. **Moneda**: se usa el importe en soles del asiento (`debe`/`haber`), que ya está al tipo de cambio del día de la operación.
7. **Resultado**: `{rubro: importe}` y el detalle por asiento, para auditar cada cifra (enlace al voucher y al documento).

### 2.2 Reglas por cuenta (semilla de `ReglaFlujoCuenta`)

| Prefijo | Rubro (entrada) | Rubro (salida) |
|---|---|---|
| 12, 123 | OP-E01 | OP-E01 (devoluciones a clientes, con signo −) |
| 122 | OP-E01 | OP-E01 |
| 13 | OP-E01 | OP-E01 |
| 1411 | OP-S03 | OP-S03 |
| 1421 | FI-E01 | FI-S05 |
| 1422 | IN-E02 | IN-S03 |
| 16 | OP-E02 | OP-S06 |
| 165 | IN-E01 | — |
| 168, 17 | IN-E02 | IN-S03 |
| 30, 31 | IN-E03 | IN-S04 |
| 33 | IN-E01 | IN-S01 |
| 34 | IN-E01 | IN-S02 |
| 40 | OP-E02 (devoluciones de tributos) | OP-S04 |
| 4017 | OP-S05 (devolución, +) | OP-S05 |
| 41, 407 | — | OP-S03 |
| 42, 43 | OP-S01 (devoluciones de proveedores, +) | OP-S01 |
| 4411 | FI-E03 | FI-S01 |
| 4412 | — | FI-S03 |
| 451, 453, 456 | FI-E02 | FI-S01 |
| 452 | — | FI-S02 |
| 455 | — | POL-1 → FI-S04 u OP-S07 |
| 46 | OP-E02 | OP-S02 |
| 465 | — | IN-S01 |
| 47 | FI-E03 | FI-S01 |
| 50, 52 | FI-E01 | FI-S05 |
| 6 (62–65) | — | OP-S02 |
| 639, 641 | — | OP-S06 |
| 673 | — | POL-1 → FI-S04 u OP-S07 |
| 7 | OP-E02 | — |
| 772, 773 | POL-2 → IN-E04 u OP-E03 | — |

### 2.3 Cuentas de efectivo

Las cuentas de efectivo son las subcuentas 10 de las cajas y bancos de Finanzas con `incluir_en_efe = True`, más las
cuentas contables 10 marcadas como equivalentes (101 caja, 102 fondos fijos, 103 en tránsito, 104 cuentas corrientes, 105
y 106 equivalentes, 107 fondos restringidos solo según POL-4).

---

## 3. Método indirecto (conciliación de la utilidad neta)

### 3.1 Fórmula

```
Flujo de operación = Utilidad (pérdida) neta del ejercicio                      [resultado del periodo, devengado]
                   + Ajustes por partidas que no son efectivo                   [3.2]
                   − Ganancias (+ pérdidas) que pertenecen a inversión o financiamiento [3.3]
                   ± Variación del capital de trabajo operativo                  [3.4]
                   ± Diferencia de cambio no realizada                           [3.5]
```

La utilidad neta sale de `contabilidad/reportes.py::estado_resultados(desde, hasta)` (la misma cifra del Estado de
Resultados). Cada ajuste se calcula con **movimientos del periodo de las cuentas de resultado** o con la **variación de
saldos** de las cuentas de balance (`saldo(hasta) − saldo(desde − 1 día)`).

### 3.2 Sumar gastos y restar ingresos que no son efectivo

| Ajuste | Cuentas PCGE | Signo | Cálculo |
|---|---|---|---|
| Depreciación de PPE y propiedades de inversión | 681 | + | Debe − haber del periodo |
| Amortización de intangibles | 6821, 682 | + | |
| Estimación de cobranza dudosa | 6841 | + | |
| Desvalorización de existencias e inversiones | 6842, 6843 | + | |
| Deterioro del valor de los activos | 685 | + | |
| Otras provisiones (litigios, garantías) | 686 | + | |
| Recuperación de estimaciones y de deterioro | 755, 757 | − | Haber − debe del periodo |
| Ingresos por medición a valor razonable | 76 (ganancias por medición no realizadas) | − | |
| Pérdidas por medición a valor razonable | 66 | + | |
| Participaciones / impuesto diferido | 88 diferido, 37/49 | ± | Solo la parte diferida |

### 3.3 Partidas de resultado que pertenecen a otras actividades

| Partida | Cuentas | Signo | Por qué |
|---|---|---|---|
| Ganancia en venta de activo inmovilizado | 756 | − | El cobro completo va en IN-E01 |
| Pérdida en venta / baja de activo | 655 | + | |
| Intereses pagados (si POL-1 = financiamiento) | 673 | + | Se muestran en FI-S04 |
| Intereses y dividendos recibidos (si POL-2 = inversión) | 772, 773 | − | Se muestran en IN-E04 |

### 3.4 Variación del capital de trabajo operativo

`Δ = saldo final − saldo inicial` (en soles, de la contabilidad). Activos: **aumento resta**, disminución suma. Pasivos:
**aumento suma**, disminución resta.

| Rubro de conciliación | Cuentas | Exclusiones (van a inversión/financiamiento) |
|---|---|---|
| (Aumento) disminución de cuentas por cobrar comerciales | 12 (incluye estimación 19 neta) | — |
| (Aumento) disminución de cuentas por cobrar comerciales a relacionadas | 13 | — |
| (Aumento) disminución de otras cuentas por cobrar | 14, 16, 17 | 1421, 1422, 165, 168, préstamos en 17 |
| (Aumento) disminución de inventarios | 20–28 (neto de 29) | — |
| (Aumento) disminución de gastos pagados por anticipado | 18 | — |
| Aumento (disminución) de cuentas por pagar comerciales | 42, 43 | Saldo por pagar de compras `ACTIVO_FIJO` e intangibles (se resta al calcular Δ) |
| Aumento (disminución) de tributos, remuneraciones y otras cuentas por pagar | 40, 41, 46 | 465 (compra de inmovilizado), 4017 si se presenta el impuesto aparte |
| Aumento (disminución) de provisiones | 48 | La dotación del periodo ya se sumó en 3.2: solo el uso |

> Las compras de activo fijo **al crédito** no son efectivo: aumentan 33 y 42/465 sin movimiento de caja. Por eso el
> saldo por pagar de esas compras se excluye de la variación de 42 (si no, el indirecto mostraría un flujo de operación
> inflado).

### 3.5 Diferencias de cambio

| Tipo | Dónde está | Tratamiento en el EFE |
|---|---|---|
| **Realizada en cobros y pagos** | Línea 676/776 dentro del asiento del cobro/pago | Directo: se absorbe en la contrapartida (paso 4). Indirecto: ya está en la utilidad y en la variación de 12/42; no requiere ajuste. |
| **No realizada en cuentas por cobrar y pagar** (ajuste de cierre de 12/42 en US$) | Asiento de centralización 12/42 contra 676/776 | Indirecto: restar la ganancia neta no realizada / sumar la pérdida (no es efectivo). La variación de 12/42 ya la incluye: se calcula la variación **sin** ese ajuste. |
| **No realizada en efectivo** (ajuste de saldos de bancos en US$) | Asiento 10 contra 676/776 | Ambos métodos: línea separada «Efecto de las variaciones en el tipo de cambio sobre el efectivo y equivalentes» (NIC 7.28). En el indirecto se resta de la utilidad. |

---

## 4. Reglas de validación del ERP

| Código | Regla | Si falla |
|---|---|---|
| **V1** | `Saldo inicial cuenta 10 + Aumento (disminución) neto + Efecto del tipo de cambio = Saldo final cuenta 10` (solo cuentas de efectivo de 2.3). | **Bloquea** la emisión del EFE y muestra la diferencia por cuenta. |
| V2 | Saldo de tesorería (Finanzas › Caja y bancos) = saldo contable de su subcuenta 10, por cuenta y fecha. | Alerta: periodo sin centralizar o asiento manual en 10. |
| V3 | Porciones sin clasificar (R5) = 0. | Alerta con la lista de asientos y enlace para asignar el rubro. |
| V4 | Flujo de operación directo = flujo de operación indirecto. | Alerta con la diferencia por partida de conciliación. |
| V5 | Las transferencias internas suman 0 (cada salida tiene su entrada). | Alerta: transferencia sin par o anulada a medias. |
| V6 | Periodos del rango centralizados y sin asientos descuadrados. | Bloquea. |
| V7 | Política sin cambio respecto del periodo comparativo (NIC 7.31). | Alerta: el comparativo se recalcula con la política actual y se marca. |

---

## 5. Modelo de datos

### 5.1 Tablas nuevas (app `contabilidad`)

```python
class RubroFlujo(models.Model):
    """Rubro del Estado de Flujos de Efectivo (NIC 7). Semilla: tabla 1.1; el usuario puede crear subrubros."""
    ACTIVIDADES = [('OPERACION', 'Operación'), ('INVERSION', 'Inversión'), ('FINANCIAMIENTO', 'Financiamiento'),
                   ('EFECTO_TC', 'Efecto del tipo de cambio'), ('EXCLUIDO', 'No es flujo (transferencia / no monetario)')]
    SENTIDOS = [('ENTRADA', 'Entrada (+)'), ('SALIDA', 'Salida (−)'), ('AMBOS', 'Ambos')]
    codigo = models.CharField(max_length=10, unique=True)          # OP-E01
    nombre = models.CharField(max_length=150)
    actividad = models.CharField(max_length=15, choices=ACTIVIDADES)
    sentido = models.CharField(max_length=7, choices=SENTIDOS)
    rubro_smv = models.CharField('Rubro del formato SMV', max_length=20, blank=True)
    padre = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)  # subrubros propios
    orden = models.PositiveSmallIntegerField(default=0)
    activo = models.BooleanField(default=True)


class ReglaFlujoCuenta(models.Model):
    """Rubro por cuenta de contrapartida (prefijo PCGE; gana el más largo)."""
    prefijo = models.CharField(max_length=10)                       # '1421'
    rubro_entrada = models.ForeignKey(RubroFlujo, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    rubro_salida = models.ForeignKey(RubroFlujo, null=True, blank=True, on_delete=models.PROTECT, related_name='+')

    class Meta:
        unique_together = [('prefijo',)]


class PoliticaFlujo(models.Model):
    """Políticas NIC 7 de la empresa (una fila por base de empresa)."""
    intereses_pagados = models.CharField(max_length=15, default='FINANCIAMIENTO')   # POL-1
    intereses_cobrados = models.CharField(max_length=15, default='INVERSION')       # POL-2
    dividendos_pagados = models.CharField(max_length=15, default='FINANCIAMIENTO')  # POL-3
    detracciones_en_efectivo = models.BooleanField(default=True)                    # POL-4
    sobregiros_en_efectivo = models.BooleanField(default=False)                     # POL-5
    metodo_principal = models.CharField(max_length=10, default='DIRECTO')            # DIRECTO / INDIRECTO
```

### 5.2 Campos nuevos en tablas existentes

| Tabla | Campo | Uso |
|---|---|---|
| `finanzas.Cuenta` | `incluir_en_efe` (bool, por defecto `True`; `False` para el BN si POL-4 lo excluye) y `tipo_efectivo` (`EFECTIVO`, `EQUIVALENTE`, `RESTRINGIDO`) | Define el universo de 2.3 y la nota de efectivo restringido |
| `finanzas.Movimiento` | `rubro_flujo` (FK a `RubroFlujo`, opcional) | Regla R1: el usuario elige la «Actividad NIC 7» al registrar ingresos/egresos varios; para cobros y pagos de comprobantes se deja vacío (lo decide R2/R3) |
| `contabilidad.CuentaContable` | `rubro_flujo_entrada`, `rubro_flujo_salida` (opcionales) | Alternativa a `ReglaFlujoCuenta` para fijar el rubro en una subcuenta concreta |

### 5.3 Pantallas

1. **Contabilidad › Configuración › Estado de flujos de efectivo**: rubros (tabla 1.1), reglas por cuenta (tabla 2.2) y
   políticas (tabla 1.2), con la semilla cargada por una migración de datos.
2. **Finanzas › Movimiento (ingreso/egreso vario)**: selector «Actividad NIC 7 / rubro» que propone el de la regla R3/R4 y
   deja cambiarlo.
3. **Contabilidad › Estados financieros › Estado de flujos de efectivo**: rango, método (directo / indirecto / ambos),
   comparativo con el periodo anterior, detalle auditable por rubro (asientos y documentos), panel de validaciones V1–V7 y
   Excel en el orden de la SMV.

---

## 6. Plan de desarrollo paso a paso

1. **Migración de esquema y datos**: tablas 5.1, campos 5.2 y semilla de rubros, reglas y políticas (tablas 1.1, 1.2 y 2.2).
2. **Servicio `contabilidad/flujo_efectivo.py`**:
   - `cuentas_efectivo(politica)` → ids de `CuentaContable` (2.3).
   - `flujos_directo(desde, hasta)` → algoritmo 2.1; devuelve totales por rubro y detalle por asiento.
   - `conciliacion_indirecta(desde, hasta)` → 3.1–3.5 con el desglose por partida.
   - `validar(desde, hasta)` → V1–V7.
3. **R2 por documento**: usar `Asiento.movimiento.compra.clasificacion`, las cuentas de las líneas de la compra (33/34) y
   `Movimiento.cuota` / `Movimiento.prestamo` para separar capital, interés, comisión e IGV de cada cuota.
4. **Selector R1** en el formulario de movimientos varios y en la importación de extractos.
5. **Reporte** (pantalla y Excel), con los dos métodos y el comparativo.
6. **Pruebas** (sección 7) con datos que cubran cada rubro, la diferencia de cambio y las transferencias.
7. **Documentación de usuario** en `Manuales/` y nota de la versión en `CHANGELOG.md`.

---

## 7. Casos de aceptación

| # | Escenario | Resultado esperado |
|---|---|---|
| 1 | Cobro de factura de S/ 1,180 (incluye IGV) | OP-E01 +1,180 |
| 2 | Cliente deposita la detracción S/ 142 en el BN (POL-4 = dentro del efectivo) | OP-E01 +142 |
| 3 | Pago de factura de mantenimiento S/ 1,038 + detracción S/ 142 al BN del proveedor | OP-S01 −1,180 |
| 4 | Pago de factura de laptop (compra `ACTIVO_FIJO`) S/ 4,720 | IN-S01 −4,720 (no OP-S01) |
| 5 | Cuota de préstamo: capital 900, interés 100, comisión 10 | FI-S01 −900; interés según POL-1; OP-S06 −10 |
| 6 | Desembolso de préstamo bancario S/ 50,000 | FI-E02 +50,000 |
| 7 | Transferencia BCP → caja chica S/ 500 | No aparece (NO-01) |
| 8 | Cobro de factura en US$ 1,000 emitida a TC 3.70 y cobrada a TC 3.75 | OP-E01 +3,750 (el 776 de 50 se absorbe) |
| 9 | Ajuste de cierre del banco en US$: ganancia S/ 120 | Línea «Efecto del tipo de cambio» +120; el indirecto resta 120 de la utilidad |
| 10 | Compra de maquinaria al crédito sin pago en el periodo | Ningún flujo; el indirecto excluye su saldo de la variación de 42 |
| 11 | Depreciación del mes S/ 2,000 | Indirecto +2,000; directo no cambia |
| 12 | Aporte de capital S/ 100,000 (`OTRO` contra 1421 / 50) | FI-E01 +100,000 |
| 13 | Periodo completo | V1 cuadra al céntimo; V4: directo = indirecto en operación |

---

## 8. Notas de la implementación (v1.73.0)

1. **Reparto línea por línea.** En cada asiento con efectivo, cada línea que no es efectivo aporta `−(debe − haber)` a su
   propio rubro (no un prorrateo): así la cuota de un préstamo sale separada en capital, interés, comisión e IGV sin una
   regla especial. La diferencia de cambio (676/776) va con la contrapartida principal del asiento.
2. **Indirecto con las mismas líneas.** Se suma a la utilidad lo que el directo dejó fuera de operación (líneas de
   resultado clasificadas en inversión o financiamiento) y se resta la variación del capital de trabajo **solo de las
   líneas de operación**. Un asiento sin efectivo que toca cuentas de inversión o financiamiento (depreciación, compra
   de activo al crédito con su IGV, devengo de intereses, documentos de saldo inicial contra la 59) no es de operación.
   Así V4 cuadra por construcción; si no cuadra, hay datos fuera de las reglas.
3. **Estimaciones y provisiones** (6841, 6842, 686, 755) se muestran como ajuste aparte y se descuentan de su variación
   de capital de trabajo (no se suman dos veces). Solo en el PCGE 2019; en el 2026 quedan dentro de la variación.
4. **Datos del sistema anterior** (probado con 52 mil asientos migrados de Odoo):
   - el asiento de saldos iniciales importado (glosa «saldos iniciales al aperturar el ejercicio», «asiento de
     apertura») es **saldo inicial**, no flujo; el reporte indica cuál tomó;
   - los **ajustes de migración** (origen `MIGRACION`) no son flujos: su efecto en el efectivo va en una línea aparte;
   - gastos registrados **solo en la clase 9** (sin cuenta 6x): la parte de 9/79 que no cuadra dentro del asiento se
     trata como gasto/ingreso (regla de la clase 9) y en el indirecto va en su propia línea, porque el estado de
     resultados por naturaleza no la incluye.
5. **No implementado** de la propuesta: comparativo con el periodo anterior (V7), la política de sobregiros (POL-5) y
   los campos de rubro en `CuentaContable` (se usa solo `ReglaFlujoCuenta`, que admite subcuentas completas como prefijo).
   La venta de un activo fijo registrada como venta normal sale como cobranza (operación): use el rubro manual (R1) en su
   cobro o registre el cobro contra la 165.
