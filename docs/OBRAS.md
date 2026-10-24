# Módulo de Obras en Ceiba ERP — diseño funcional y técnico

> Documento de diseño para el equipo de desarrollo. Objetivo: que una **constructora peruana** (obra pública y privada)
> gestione en Ceiba lo que hoy hace en S10 —presupuesto con análisis de precios unitarios, cronograma, valorizaciones,
> control de obra y planilla de construcción civil— **conectado** a Compras, Inventario, Planillas, Flota, Ventas,
> Finanzas y Contabilidad, que ya existen.
> Estado: **fase 1 implementada en Ceiba v1.74.0** (app `obras`: obras, recursos, presupuestos por versiones con
> partidas y APU, precios por presupuesto, biblioteca de partidas tipo, explosión de insumos, reportes e importación
> desde Excel de S10 o la plantilla de Ceiba).
> **v1.75.0**: subpresupuestos y gastos generales detallados, sub-análisis, importación del reporte del visor del S10
> (.xls sin títulos) y de su CSV de análisis, recursos por código, vínculo recurso → producto (código alterno),
> requerimiento de obra desde la Meta (solicitud de pedido con el centro de costo de la obra), control meta vs.
> pedido / recibido / consumido por insumo y **reparto de gastos generales** configurable (Contabilidad › Reparto de
> gastos). De la fase 3 queda la **partida** en SolPed, OC y salidas de almacén (hoy el control es por insumo), el avance
> diario, el parte de equipos y los subcontratos. Fases 2, 4 y 5: pendientes.
> Las cifras legales son referenciales y **se parametrizan por vigencia**; antes de usarlas en un caso real, contrastarlas
> con la norma publicada (El Peruano / OECE / MTPE) y con el acta del convenio de construcción civil del año.

---

## 0. Principios

| # | Principio | Consecuencia |
|---|---|---|
| P1 | **La obra es un centro de costo** (hijo del centro «OBRAS») y tiene su **almacén de obra** | Todo lo que ya registra Ceiba con centro de costo (compras, OC, solicitudes de pedido, salidas de almacén, planilla, combustible, rendiciones) cae en la obra sin cambiar esos módulos. |
| P2 | **La partida es la unidad de control** | Compras, salidas de almacén, tareo, parte de equipos y subcontratos llevan una **partida de control** opcional; sin ella el costo va a «gastos no asignados» de la obra. |
| P3 | **Presupuestos versionados** | Oferta → Contractual → Meta (presupuesto de ejecución). Se controla contra la **Meta**; se factura contra el **Contractual** + adicionales − deductivos. |
| P4 | **El análisis de precios unitarios (APU) manda** | El precio de una partida sale de su APU (mano de obra, materiales, equipos, subcontratos); el metrado × precio da el parcial; los insumos se explotan desde los APU. |
| P5 | **Una valorización = un comprobante** | La valorización aprobada genera la factura de venta (detracción 030, anticipos, retenciones) con lo que Ceiba ya sabe hacer. |
| P6 | **Todo legal es tabla con vigencia** | Jornales y beneficios de construcción civil, índices unificados (INEI), límites de adelantos y garantías: se cargan y actualizan sin tocar código. |
| P7 | **Importar antes que digitar** | Importación de presupuestos desde Excel (exportado de S10 u otro) y de cronogramas desde MS Project: la adopción depende de no volver a digitar. |

---

## 1. Mapa del módulo (menú «Obras»)

| Grupo | Pantallas |
|---|---|
| **Obras** | Obras (ficha y tablero) · Nueva obra · Contrato, garantías y adelantos |
| **Presupuestos** | Presupuestos y versiones · Partidas y APU · Biblioteca de partidas (APU tipo) · Recursos (insumos) y precios · Explosión de insumos · Importar presupuesto (Excel) |
| **Planificación** | Cronograma (Gantt) · Cronograma valorizado y curva S · Importar cronograma (MS Project) · Fórmula polinómica |
| **Ejecución** | Avance diario (metrados ejecutados) · Tareo de mano de obra · Parte diario de equipos · Requerimientos de obra (sobre Solicitudes de pedido) · Consumos de almacén por partida |
| **Subcontratos** | Subcontratos · Valorizaciones de subcontratistas |
| **Valorizaciones** | Valorizaciones · Adicionales y deductivos · Reajustes (índices) · Liquidación de obra |
| **Control** | Meta vs. real por partida e insumo · Valor ganado (CPI, SPI, EAC) · Resultado de la obra · Flujo de caja de la obra · Cartas fianza por vencer |
| **Configuración** | Índices unificados (INEI) · Tabla de jornales y beneficios de construcción civil · Parámetros de la obra pública (límites) · Cuentas contables de obras |

---

## 2. Modelo de datos (app `obras`)

### 2.1 Maestros

| Modelo | Campos clave | Notas |
|---|---|---|
| `Obra` | codigo, nombre, cliente (Tercero), tipo (`PUBLICA`/`PRIVADA`), sistema de contratación (`SUMA_ALZADA`, `PRECIOS_UNITARIOS`, `LLAVE_EN_MANO`, `MIXTO`), contrato N°, moneda, monto contratado, fecha de contrato, inicio, plazo (días), fin programado/real, ubigeo y dirección, **centro_costo** (FK, se crea solo), **almacen** (FK, se crea solo), residente, supervisor/inspector, estado (`LICITACION`, `ADJUDICADA`, `EJECUCION`, `PARALIZADA`, `RECEPCION`, `LIQUIDADA`, `CERRADA`), % gastos generales, % utilidad, % adelanto directo y de materiales, % retención de garantía, fórmula polinómica (FK) | Una obra en estado `CERRADA` no acepta movimientos. |
| `Recurso` (insumo) | codigo, nombre, unidad, tipo (`MO`, `MAT`, `EQ`, `SUB`), **producto** (FK a `core.Producto`, para materiales que pasan por almacén), **equipo** (FK a `produccion.Equipo`), categoría de MO (`OPERARIO`, `OFICIAL`, `PEON`, `CAPATAZ`, `OPERADOR`…), índice unificado (código INEI), precio vigente | Catálogo único de la empresa; los materiales se vinculan a productos para que la explosión de insumos alimente Compras. |
| `PrecioRecurso` | recurso, obra (o general), vigencia, precio, moneda, fuente | Lista de precios por obra (zona, flete). |
| `IndiceUnificado` | codigo (01..80), periodo AAAAMM, área geográfica (1..6), valor | Se importa la publicación mensual del INEI (Excel). |
| `PartidaBase` | codigo, descripcion, unidad, rendimiento, jornada, recursos (como el APU) | Biblioteca de APU tipo para armar presupuestos rápido. |

### 2.2 Presupuesto

| Modelo | Campos clave | Notas |
|---|---|---|
| `Presupuesto` | obra, tipo (`OFERTA`, `CONTRACTUAL`, `META`, `ADICIONAL`, `DEDUCTIVO`), versión, fecha de precios, estado (`BORRADOR`, `APROBADO`), moneda, notas | Aprobar exige el flujo de `core.aprobaciones`. Copiar una versión duplica partidas y APU. |
| `Partida` | presupuesto, padre (árbol), item (`01.02.03`), descripcion, unidad, metrado, precio unitario (calculado), parcial, es título, partida_base, **partida de control** (FK a sí misma en la Meta, para enlazar Contractual↔Meta) | El item define el orden; los títulos suman a sus hijos. |
| `AnalisisPU` | partida (1:1), rendimiento (unid/jornada), jornada (h), costo unitario MO/MAT/EQ/SUB | Se recalcula al cambiar un recurso o precio. |
| `RecursoAPU` | analisis, recurso, cuadrilla (para MO y EQ), cantidad (aporte por unidad), precio, parcial | MO/EQ: `cantidad = cuadrilla × jornada / rendimiento`. Herramientas manuales: % de la MO. |
| `GastoGeneral` | presupuesto, tipo (`FIJO`, `VARIABLE`), descripción, unidad, cantidad, meses, precio | Pie del presupuesto (si no se usa el % simple). |

**Pie del presupuesto**: costo directo (Σ parciales) + gastos generales (% o detallados) + utilidad (%) = subtotal; IGV;
total. Tres vistas: por partidas, por insumos (explosión) y resumen.

### 2.3 Cronograma y reajustes

| Modelo | Campos clave | Notas |
|---|---|---|
| `ProgramaPartida` | partida, inicio, fin, predecesoras, avance programado por periodo (JSON {AAAAMM: metrado}) | Gantt simple en pantalla; importación desde MS Project (XML/CSV). |
| `FormulaPolinomica` | obra, nombre | Hasta 8 monomios (práctica del D.S. 011-79-VC; confirmar la norma vigente). |
| `Monomio` | formula, coeficiente, índices (uno o más con su peso), área geográfica | Σ coeficientes = 1.000 (validación). |

**Factor de reajuste**: `K = Σ coefᵢ × (Irᵢ / Ioᵢ)`, con `Io` = índice a la fecha del presupuesto base y `Ir` = índice del
mes valorizado. Redondeo a 3 decimales.

### 2.4 Contrato, garantías y adelantos

| Modelo | Campos clave | Notas |
|---|---|---|
| `CartaFianza` | obra, tipo (`FIEL_CUMPLIMIENTO`, `ADELANTO_DIRECTO`, `ADELANTO_MATERIALES`, `SERIEDAD`), modalidad (carta fianza, póliza de caución, fideicomiso, retención), banco/aseguradora, número, monto, emisión, vencimiento, renovaciones, estado | Alerta 30 y 15 días antes del vencimiento (tablero y correo). La garantía de adelanto se **reduce** con la amortización. |
| `Adelanto` | obra, tipo (`DIRECTO`, `MATERIALES`, `AVANCE`), monto, fecha, factura de anticipo (FK `ventas.Venta`), carta fianza, monto amortizado | Límites de la obra pública (Ley 32069 y su reglamento: directo ≤ 10 %, materiales ≤ 20 %, avance ≤ 10 % del monto original en obras de solo construcción) en `ParametroObraPublica` con vigencia. |

### 2.5 Ejecución

| Modelo | Campos clave | Notas |
|---|---|---|
| `AvanceDiario` | obra, fecha, partida (Meta), metrado ejecutado, observaciones, foto | Base del avance físico y de la valorización. Desde tablet (como el «Reporte de planta»). |
| `Tareo` | obra, fecha, trabajador (`planillas.Trabajador`), partida, horas normales, horas extra 60 %/100 %, asistencia, bonificaciones del día (altura, agua…) | Alimenta la **planilla semanal de construcción civil** y el costo de MO por partida. |
| `ParteEquipo` | obra, fecha, equipo (`produccion.Equipo`), operador, partida, horómetro inicial/final (crea `flota.LecturaEquipo`), horas, combustible | Costo: horas × tarifa del equipo (propio) o del alquiler (subcontrato/OC). |
| *Consumos de almacén* | **se reutiliza** `inventario.Operacion` (salida) con `centro_costo` = obra + campo nuevo `partida` en `OperacionItem` | El material sale del almacén de obra al costo promedio del kardex. |
| *Requerimientos* | **se reutiliza** `compras.SolicitudPedido` + campo `obra` y `partida` en `LineaSolicitud` | Sugerencia: «requerir lo que falta» desde la explosión de insumos de la Meta menos lo comprado/consumido. |

### 2.6 Subcontratos

| Modelo | Campos clave | Notas |
|---|---|---|
| `Subcontrato` | obra, proveedor, número, alcance, moneda, % retención de garantía, % adelanto, estado | Sobre `compras.ContratoMarco` (mismo proveedor, vigencia, monto). |
| `LineaSubcontrato` | subcontrato, partida (Meta), unidad, metrado, precio | |
| `ValorizacionSubcontrato` | subcontrato, periodo, líneas (metrado del periodo), amortización de adelanto, retención, neto | Aprobada → genera la **conformidad de servicio (HES)** y la compra se registra contra ella (flujo que ya existe). Detracción 030 del proveedor. |

### 2.7 Valorizaciones al cliente

| Modelo | Campos clave | Notas |
|---|---|---|
| `Valorizacion` | obra, número, periodo, fecha de corte, tipo (`CONTRACTUAL`, `ADICIONAL`, `REINTEGRO`, `MAYORES_GASTOS`), estado (`BORRADOR`, `PRESENTADA`, `OBSERVADA`, `APROBADA`, `FACTURADA`, `COBRADA`), K del mes, venta (FK a la factura) | Una por obra y periodo y tipo. |
| `LineaValorizacion` | valorizacion, partida (Contractual), metrado anterior, del periodo, acumulado, % avance, monto del periodo | Valida: acumulado ≤ metrado contratado (+ adicionales), salvo partidas a precios unitarios con mayor metrado aprobado. |
| `Adicional` / `Deductivo` | obra, número, resolución/aprobación, presupuesto (tipo ADICIONAL/DEDUCTIVO), % de incidencia acumulado | Alerta cuando la incidencia acumulada supera el límite de la norma vigente. |

**Cálculo de la valorización** (por periodo):

```
Valorizado del periodo (CD)           = Σ metrado del periodo × precio unitario contractual
+ Gastos generales y utilidad         = CD × (%GG + %U)   (o según el pie del contractual)
= Valorización bruta (VB)
+ Reintegro por reajuste              = VB × (K − 1)
− Deducción del reintegro por adelantos (cuando hubo adelantos; fórmula de la norma de reajustes)
− Amortización del adelanto directo   = VB × (adelanto directo / monto contratado)
− Amortización del adelanto materiales (según el uso de los materiales o la proporción pactada)
= Subtotal
+ IGV (18 %)
= Total de la valorización (lo que se factura)
− Retención de garantía (si aplica, sobre el total sin IGV o según contrato)
− Detracción 030 (4 %), que el cliente deposita en la cuenta del BN
= Neto a cobrar
```

La factura sale con **regularización de anticipos** (la factura electrónica ya trae los campos
`anticipo_regularizacion`, hoy en `false`) y detracción **030 Contratos de construcción**.

### 2.8 Planilla de construcción civil (extensión de `planillas`)

| Cambio | Detalle |
|---|---|
| `Trabajador.regimen` | nuevo valor `CONSTRUCCION_CIVIL` + `categoria_cc` (`OPERARIO`, `OFICIAL`, `PEON`) y obra asignada. |
| `Planilla.tipo` | nuevo `SEMANAL` (la construcción civil se paga por semana, con su periodo AAAAMM y número de semana). |
| `TablaConstruccionCivil` (vigencia) | jornal básico por categoría (2026: operario S/ 89.30, oficial S/ 69.75, peón S/ 62.80, según la publicación del convenio CAPECO–FTCCP), % BUC por categoría, movilidad por día, bonificación por altura (2026: 8 %), feriado, escolaridad, Conafovicer (% de descuento, sujeto a la autorización del trabajador según la norma vigente), gratificación, vacaciones, CTS (% de jornales), SCTR (tasa de la póliza). |
| Cálculo | desde el **tareo** de la semana: días y horas × jornal + BUC + movilidad + horas extra + bonificaciones; descuentos (AFP/ONP, Conafovicer, renta de 5.ª); aportes (EsSalud, SCTR). Gratificación, vacaciones y CTS del régimen se calculan sobre jornales devengados. PLAME/T-Registro con los códigos de régimen correspondientes. |
| Costo por partida | el costo de la planilla de la semana se reparte a las partidas según las horas del tareo (asiento de planilla por centro de costo = obra). |

---

## 3. Integración con lo que Ceiba ya tiene

| Módulo existente | Qué se usa | Cambio necesario |
|---|---|---|
| Contabilidad | `CentroCosto` por obra (hijo de OBRAS), destinos 9x, presupuesto contable | Crear el centro al crear la obra; reporte de resultado por obra. |
| Compras | `SolicitudPedido`, `OrdenCompra`, `Compra` con `centro_costo`; licitaciones (cuadro comparativo); `ContratoMarco`; `ConformidadServicio` (HES) | Campo `partida` (opcional) en líneas de SolPed, OC y compra; subcontratos sobre contratos marco + HES. |
| Inventario | `Almacen` por obra, `Operacion` de salida con centro de costo, kardex | Campo `partida` en `OperacionItem`; tipo de operación «Consumo en obra». |
| Planillas | `Trabajador`, `Planilla`, conceptos PLAME, centro de costo | Régimen de construcción civil, planilla semanal, tareo (2.8). |
| Producción / Flota | `produccion.Equipo`, horómetro (`LecturaEquipo`), combustible por equipo y centro de costo | Parte diario de equipo por obra y partida; tarifa horaria del equipo. |
| Ventas | `Venta` con detracción (030 ya existe), cuotas, anticipos en la factura electrónica | Generar la factura desde la valorización; facturas de adelanto y su regularización. |
| Finanzas | cobranza con detracción, cobranza múltiple, constancias SUNAT, letras, préstamos | Retención de garantía por cobrar y su devolución al liquidar; flujo de caja de la obra. |
| Rendiciones | caja chica y entregas a rendir con centro de costo | Caja chica de obra (centro de costo = obra). |
| Aprobaciones | `ReglaAprobacion` por documento y monto | Documentos nuevos: presupuesto, valorización, adicional, subcontrato. |
| EFE (NIC 7) | rubros por cuenta | Sin cambios: cobranzas de valorizaciones y pagos a subcontratistas son operación; equipos propios comprados son inversión. |

---

## 4. Contabilidad de obras (cuentas sugeridas, configurables en «Cuentas por operación»)

| Hecho | Debe | Haber |
|---|---|---|
| Factura de adelanto | 121 (cliente) | 122 anticipos de clientes + 40111 IGV |
| Valorización facturada | 121 | 704 prestación de servicios (construcción) + 40111 |
| Regularización del adelanto en la valorización | 122 | 121 |
| Retención de garantía del cliente | 1213 *retenciones de garantía por cobrar* (divisionaria propia) | 121 |
| Valorizado y no facturado al cierre (NIIF 15, activo del contrato) | 1214 *valorizaciones por facturar* | 704 |
| Costos de la obra | 60-65, 68 por naturaleza | 79 / destino 9x por **centro de costo = obra** |
| Costo de ventas de la obra (cierre) | 69 costo de servicios | 9x / 2x según la política |
| Subcontratista (retención de garantía) | 42 | 4691 *retenciones de garantía por pagar* (divisionaria propia) |

Ingreso según **NIIF 15** (obligación satisfecha a lo largo del tiempo): por defecto, método del **producto** (avance
valorizado); opción del método del **insumo** (costo incurrido / costo total estimado) con su ajuste a 1214 o al pasivo del
contrato. Política por empresa.

---

## 5. Aspectos tributarios y legales

| Tema | Regla en el sistema |
|---|---|
| Detracción | Servicio de construcción **030** (4 %): en la factura de la valorización y en las de subcontratistas. Ya soportado por Ceiba (código y TXT masivo). |
| Anticipos | Factura de anticipo y su deducción en las valorizaciones (campos de anticipo de la factura electrónica). |
| Retención del IGV | Si el cliente es agente de retención: 3 % (ya soportado en la venta). |
| Obra pública | Ley 32069 y su reglamento (DS 009-2025-EF, modificado por DS 001-2026-EF): límites de adelantos, garantías de adelanto renovables y reducibles, valorizaciones mensuales, adicionales con su límite. Todo en `ParametroObraPublica` con vigencia. |
| Reajustes | Fórmula polinómica con índices unificados del INEI (2.3). |
| Construcción civil | Convenio CAPECO–FTCCP del año, SCTR obligatorio, Conafovicer según la norma vigente (2.8). |

---

## 6. Reportes (formatos tipo S10)

1. **Presupuesto** (partidas con metrado, precio y parcial; pie con GG, utilidad e IGV).
2. **Análisis de precios unitarios** por partida (rendimiento, cuadrilla, aportes).
3. **Relación de insumos** (explosión: cantidad total por recurso y monto) e **índice unificado** por insumo.
4. **Cronograma valorizado** y **curva S** (programado vs. ejecutado vs. valorizado).
5. **Valorización** (formato de la entidad: metrado anterior, actual, acumulado, saldo, %; resumen con reajuste,
   deducciones, amortizaciones, IGV, retención).
6. **Control meta vs. real** por partida e insumo: cantidad y monto presupuestado (Meta) contra lo consumido, comprado,
   tareado y parte de equipo; rendimiento real vs. del APU.
7. **Valor ganado**: PV, EV, AC, CPI, SPI, EAC, ETC, VAC por obra y por partida.
8. **Resultado de la obra**: ingresos valorizados, costos reales, margen; proyección al término.
9. **Flujo de caja de la obra** (cobros y pagos reales + proyección por cronograma).
10. **Cartas fianza** por vencer y garantías por devolver.

---

## 7. Importación

| Origen | Formato | Qué trae |
|---|---|---|
| S10 u otro software de presupuestos | Excel exportado: hoja de **presupuesto** (item, descripción, unidad, metrado, precio, parcial), hoja de **APU** (partida, rendimiento, recursos con cuadrilla, cantidad y precio) y hoja de **insumos** | Partidas, APU y recursos (se emparejan por código o descripción; los nuevos se crean). Vista previa con diferencias antes de grabar. |
| MS Project | XML (o CSV) | Tareas → programa de partidas (enlace por item o nombre), fechas y predecesoras. |
| INEI | Excel de índices unificados | `IndiceUnificado` del mes por área. |

---

## 8. Reglas y validaciones

1. El precio unitario de la partida **siempre** sale del APU; no se edita a mano (salvo partidas «global» marcadas).
2. Σ coeficientes de la fórmula polinómica = 1.000.
3. En la valorización, el acumulado no supera lo contratado + adicionales aprobados (aviso si hay mayor metrado).
4. Un adelanto no supera su límite (obra pública) y exige una carta fianza vigente por el saldo por amortizar.
5. No se valoriza un periodo con la valorización anterior sin aprobar.
6. Un consumo o compra con partida exige que la partida sea de la **Meta** de esa obra.
7. Al cambiar de versión de presupuesto, el control se recalcula contra la nueva Meta (con historial).
8. Obra `LIQUIDADA`/`CERRADA`: no acepta tareo, consumos ni compras; sí la devolución de garantías.

---

## 9. Roles

| Rol | Puede |
|---|---|
| Oficina técnica | Presupuestos, APU, cronograma, adicionales/deductivos, valorizaciones (borrador). |
| Residente de obra | Avance diario, tareo, partes de equipo, requerimientos; aprueba la valorización interna. |
| Almacenero de obra | Recepciones y salidas por partida del almacén de obra. |
| Administrador de obra | Subcontratos y sus valorizaciones, caja chica de obra, cartas fianza. |
| Gerencia de proyectos | Aprueba presupuestos, adicionales y valorizaciones; ve control y valor ganado. |

Se implementan como permisos del sistema actual (`core.permisos`) con el módulo «Obras» en `core/modulos.py`.

---

## 10. Plan por fases

| Fase | Entregable | Depende de |
|---|---|---|
| **F1 Presupuesto** | Obras, recursos, biblioteca de APU, presupuesto por versiones con APU y pie, explosión de insumos, reportes 1-3, **importación desde Excel (S10)** | — |
| **F2 Contrato y valorizaciones** | Cartas fianza, adelantos, fórmula polinómica e índices INEI, cronograma (manual + MS Project) y curva S, valorizaciones con reajuste/amortizaciones/retención y **factura automática** (detracción 030, anticipos), adicionales y deductivos | F1 |
| **F3 Control de obra** | Partida en SolPed/OC/compra/salidas de almacén, avance diario, parte de equipos, subcontratos y sus valorizaciones (HES), control meta vs. real, valor ganado, resultado y flujo de caja de la obra | F1, F2 |
| **F4 Construcción civil** | Régimen, tabla con vigencia, tareo, planilla semanal, beneficios del régimen, PLAME, costo de MO por partida | F3 (tareo) |
| **F5 Opcional** | Obra pública avanzada (formatos de la entidad, liquidación), app de campo sin conexión, inmobiliario (unidades, separaciones, cronograma de pagos) | F2-F4 |

---

## 11. Casos de aceptación (muestra)

| # | Escenario | Esperado |
|---|---|---|
| 1 | APU: concreto f'c 210, rendimiento 25 m³/día, cuadrilla 2 op + 2 of + 8 pe, cemento 9.73 bls/m³ | Aporte de MO = cuadrilla × 8 h / 25; precio unitario = Σ parciales; al subir el precio del cemento cambia el parcial de la partida y el total. |
| 2 | Importar un presupuesto de S10 en Excel (200 partidas, 150 insumos) | Mismo total al céntimo; vista previa de recursos nuevos. |
| 3 | Fórmula polinómica de 5 monomios, Io (mes base) e Ir (mes 3) | K a 3 decimales; reintegro = VB × (K − 1). |
| 4 | Valorización 2 con adelanto directo del 10 % | Amortización = 10 % de la valorización bruta; la garantía de adelanto se puede reducir al saldo. |
| 5 | Valorización aprobada | Factura 01 con detracción 030 (4 %), regularización del anticipo y retención de garantía; asiento a 121/122/704/1213. |
| 6 | Salida de 100 bolsas de cemento a la partida 02.01 | Costo real de la partida = 100 × costo promedio; aparece en meta vs. real. |
| 7 | Tareo de una semana de 10 obreros | Planilla semanal con jornal, BUC, movilidad, descuentos y aportes; costo repartido a las partidas por horas. |
| 8 | Carta fianza que vence en 15 días | Alerta en el tablero y por correo. |
| 9 | Obra con EV < PV y AC > EV | SPI < 1 y CPI < 1 en rojo; EAC = BAC / CPI. |
| 10 | Licitación ganada con oferta de S/ 141 600 | Obra Adjudicada, monto contratado = oferta, presupuesto contractual copiado, centro de costo de Propuestas a Obras, oportunidad Ganada. |
| 11 | Departamento de S/ 327 000 (terreno 50 %), separación de S/ 5 000 | Boleta de anticipo: construcción 2 293.58 + IGV 412.84 + terreno inafecto 2 293.58; en la entrega, ingreso 300 000 y costo por área techada. |

---

## 12. Comercial: licitaciones (CRM de constructoras) — v1.77.0

Cada propuesta es una **obra en «Propuesta / licitación»** con su `Licitacion` (1 a 1) y su **oportunidad del CRM** (Ventas):

- Se crea desde **Obras › Comercial › Nueva licitación**, desde el CRM («Convertir en obra / licitación» en la oportunidad) o desde una obra en licitación («Registrar la licitación»).
- Mientras se licita, el centro de costo de la obra cuelga de **PROPUESTAS** (gasto de ventas): lo que cuesta preparar la oferta (bases, visitas, cartas fianza, personal) se ve como **costo de la propuesta**.
- Datos del proceso: procedimiento (LP, CP, AS, SIE, CD, privada), nomenclatura SEACE, valor referencial, consorcio y participación. La **oferta** es el presupuesto de oferta vigente (aprobado o el último); se muestra su % del valor referencial y el monto de la oportunidad se mantiene igual a la oferta.
- **Cronograma** (convocatoria, consultas, absolución, integración, presentación, buena pro, consentimiento, firma) y **requisitos del postor** (experiencia, personal clave, equipamiento, RNP, garantía) con documentos adjuntos (sustentos) y vencimiento. Alertas: hitos en 3 días o vencidos sin marcar; requisitos vencidos o por vencer.
- **Ganar**: obra Adjudicada, monto contratado = oferta, la oferta se copia como **contractual** (si no hay), el centro pasa a **OBRAS** (producción) y la oportunidad queda Ganada. **Perder**: motivo obligatorio (precio, experiencia, plazo, técnica, descalificación), ganador y su precio; obra «No adjudicada» y oportunidad Perdida con el motivo. **Desierta** también se registra.
- El embudo del CRM no marca como ganada o perdida una oportunidad con licitación en curso: el resultado se registra en la licitación.
- Reportes: cartera en licitación, **tasa de éxito** por tipo de obra, procedimiento y entidad, motivos de pérdida y **cartera contratada por ejecutar** (contratado menos lo facturado con el centro de la obra).
- Permiso «obras.licitar» (quien tenía «presupuestar» lo recibe en la migración).

## 13. Proyecto inmobiliario — v1.77.0 (fase 1)

Obra de tipo **Proyecto inmobiliario** con sus **unidades** (departamentos, estacionamientos, depósitos, locales, lotes…): código, piso, área techada y libre, dormitorios, vista y precio de lista. Se registran una a una o pegando desde Excel.

- **Separación y venta** de una o más unidades con su **cronograma de pagos**: separación, cuota inicial, bono o subsidio (BBP, BFH), cuotas mensuales con la empresa (financiamiento directo), desembolso del banco (hipotecario, MiVivienda, Techo Propio) o saldo al contado. Estados: separada → vendida (minuta) → entregada; anulación con motivo y sin cobros.
- **Cobro de cada cuota** (requiere el permiso de Tesorería): emite su **comprobante de anticipo** (factura si el comprador tiene RUC; si no, boleta). Primera venta de inmuebles: el IGV grava solo la construcción y el **terreno** (el % de la obra; el Reglamento de la Ley del IGV lo presume en el 50 % del valor de venta) va como línea inafecta. Con importe cobrado M y p = % del terreno: construcción = M × (1 − p) / ((1 − p) × 1.18 + p). El cobro entra a la **cuenta recaudadora** del proyecto (banco que financia o fideicomiso) como cobranza del comprobante.
- **Entrega**: con la minuta firmada y todo cobrado. El ingreso de la venta es el valor sin IGV de sus anticipos; el **costo de cada unidad** = costo del proyecto a la fecha × su participación (por área techada o por precio de lista).
- **Contabilidad** (centralización, origen «Proyectos inmobiliarios»):
  - Cada mes los costos del proyecto (clase 6 con el centro de costo de la obra, sin el reparto de gastos generales) se **activan**: 2341 Inmuebles en construcción / 7134 Variación de productos inmuebles en proceso (PCGE 2026: 2312 / 7134).
  - Cada cobro: 1212 / 40111 IGV de la construcción y 1221 Anticipos (construcción + terreno), y su cobranza 104 / 1212.
  - En la entrega: 1221 / 7024 Ventas de inmuebles (PCGE 2026: 70221) y 6924 Costo de ventas / 2341 (PCGE 2026: 69221).
  - Las cuentas son propias para que el ajuste del kardex de la 2311 (manufactura) no las toque; se cambian en «Cuentas por defecto».
- **Tablero** de unidades por piso y estado, costo por m², costo asignado y margen por unidad, ventas, saldo por cobrar; **cobranza inmobiliaria** con cuotas vencidas y por vencer y separaciones por vencer (alertas).
- Permiso «obras.vender» (lo asigna el administrador).

### 13.1 Fase 2 — v1.78.0

- **Terminación (conformidad de obra)**: el saldo de 2341 pasa a **2141 inmuebles terminados** (PCGE 2026: 2112); desde entonces no se activan costos (intereses, predial, mantenimiento van a resultados) y las entregas sacan el costo de 2141.
- **Intereses (NIC 23)**: el préstamo que financia el proyecto lleva el centro de costo de la obra; sus intereses (y comisiones) se activan con los demás costos hasta la terminación.
- **Costo por unidad**: área techada, precio de lista o **mixto** (% del área); las **áreas comunes** son unidades que no se venden y no llevan costo propio. **Inventario permanente valorizado por unidad** (Excel desde el tablero).
- **Exoneración de vivienda**: si el parámetro INM_EXONERACION_UIT está vigente, la venta de vivienda (departamento o casa con su estacionamiento o depósito, sin locales) hasta ese límite va exonerada del IGV.
- **Arras de retractación**: hasta INM_ARRAS_PCT % del precio, sin IGV ni comprobante (4699); con la minuta se emite su comprobante de anticipo y se aplican. Si el comprador desiste, las pierde (otros ingresos).
- **Desistimiento**: penalidad (sugerida con INM_PENALIDAD_PCT), primero de las arras; de lo demás se emite **nota de crédito** por lo que se devuelve y se registra la devolución (Tesorería); lo retenido de los anticipos pasa de 122 a otros ingresos (su IGV ya declarado no se revierte).
- **Garantías postventa (NIC 37)**: en la entrega, INM_GARANTIA_PCT % del ingreso: 6866 / 486 (PCGE 2026: 6896 / 486).
- **Controles**: bancarización (BANCARIZACION_SOLES: sin efectivo y con N° de operación) y **ficha del comprador** (ocupación, origen de los fondos, PEP, declaración jurada) con alerta desde el umbral del registro de operaciones de la UIF.
- **Reportes inmobiliarios**: anticipos por aplicar, cobranza por comprador, flujo de caja del proyecto, margen por unidad y proyecto, registro de operaciones (UIF) y unidades en propiedad (predial).
- **Prorrata del crédito fiscal**: ver Contabilidad › Reportes; las compras del proyecto indican su destino del IGV.
- Pendiente: factura final de la venta con la deducción de los anticipos (SUNAT), independización y escritura, supervisión del fideicomiso y post-venta (reclamos y garantías atendidas).

## 14. Cierre de la obra — v1.78.0

Al pasar una obra a **Liquidada** o **Cerrada** su almacén debe estar en cero (Obras › Sobrantes en almacenes de obra, valorizado): los sobrantes se trasladan con guía al almacén central, se devuelven o se dan de baja con aprobación. Entonces el almacén se **cierra** (solo admite ajustes de inventario) y el centro de costo se **desactiva**. Reabrirla exige el permiso «Reabrir obras liquidadas» y un motivo (queda en la auditoría).

## 15. Ejecución de la obra — v1.79.0
Ceiba no compite con el S10 en presupuestos: el presupuesto (oferta, contractual y meta) se hace en el S10 y se importa; Ceiba gestiona la ejecución (Obras › obra › Ejecución).

| Qué | Dónde | Contabilidad |
|---|---|---|
| Adelanto directo o para materiales | Factura de adelanto (servicio «Adelanto de obra») o reclasificar una factura emitida | Debe 1212 / Haber 122 e IGV; detracción 030 |
| Valorización mensual | Sobre el contractual vigente: metrado del periodo (propuesto del avance), GG y utilidad en la proporción del presupuesto, amortización de adelantos (adelanto ÷ contrato; la última amortiza el saldo), reintegro y retención | Factura: valorizado y reintegro a ventas de servicios; deducción del anticipo como línea negativa con `anticipo_de` (Debe 122); fondo de garantía: aplicación sobre la factura (Debe 1683 / Haber 1212) |
| Fórmula polinómica | K = Σ coeficiente × Ir / Io (índices INEI del mes y del mes base, por área); R = V × (K − 1); D = A × (Kr / Ka − 1) | En la factura de la valorización |
| Cronograma y avance | Excel (meses o inicio/fin) o XML de MS Project; avance por partida con foto | Curva S en costo directo |
| Tareo y planilla civil | Tareo por cuadrilla y partida; planilla semanal con parámetros «Construcción civil» | 6211 / 6271 / 6273 / provisiones con el centro de la obra; retenciones (ONP 4032, AFP 417/4071, CONAFOVICER 4699); neto 4111 |
| Partes de equipo | Horómetro, horas, espera, combustible, tarifa | Propios: regla de reparto «Horas de equipo»; alquilados: la factura del proveedor con el centro de la obra |
| Subcontratos | Orden de servicio, valorización del subcontratista, HES generadas | Fondo de garantía retenido: aplicación sobre su factura (Debe 4212 / Haber 467) |

- Las partidas se unen entre versiones y entre contractual y meta por **subpresupuesto e ítem** (`Partida.clave`). Al aprobar una versión nueva, lo registrado contra la anterior pasa a la nueva (`obras/versiones.py`).
- **Stock antes de comprar** (`compras/stock.py`): disponible = stock − requerimientos aprobados por atender − traslados por aprobar − lo que la meta de la obra dueña aún consume (parámetro STOCK_RESERVA_META); STOCK_CONTROL_COMPRA: 0 no controla, 1 pide justificación, 2 bloquea. El traslado usa «Traslado a tránsito» y «Recepción de tránsito» (no es gasto).
- Tasas de construcción civil, detracción de construcción y control de stock: Ajustes › Parámetros (con vigencia). Confirmarlas con el especialista laboral y el contador.
