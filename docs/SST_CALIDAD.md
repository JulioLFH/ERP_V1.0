# Seguridad y salud en el trabajo y Excelencia organizacional (v1.78.0)

## Seguridad y salud en el trabajo (módulo `sst`)

Registros por empresa, área (unidad organizativa de Planillas) u obra, con los trabajadores de Planillas:

| Registro | Qué guarda | Validación por defecto |
|---|---|---|
| Exámenes médicos ocupacionales | Trabajador, tipo (pre-ocupacional, periódico, retiro), resultado, restricciones y **vencimiento** | Médico ocupacional |
| Monitoreo de agentes | Agente físico, químico, biológico, disergonómico o psicosocial, resultado, límite y próximo monitoreo | Especialista en higiene ocupacional |
| Inspecciones | Hallazgos y **acciones correctivas** | No |
| Inducción, capacitación, entrenamiento y simulacros | Tema, horas, expositor y **asistentes** | No |
| Equipos de seguridad y emergencia | Extintores, botiquines… con su vencimiento | No |
| IPERC | Por proceso, puesto, área u obra: tarea, peligro, riesgo, **probabilidad × severidad**, controles y riesgo residual | Supervisor o jefe de SST |
| Entrega de EPP | Trabajador, almacén y EPP: **salida de almacén** al gasto con el centro de costo del trabajador (o de la obra), cargo firmado (sustento) y **fecha de reposición** | No |
| SCTR | Póliza de salud y pensión por trabajador y obra con su vigencia | — |
| Permisos de trabajo de alto riesgo | Altura, caliente, espacio confinado, eléctrico, excavación, izaje: responsable, trabajadores y controles | Supervisor de SST |
| Accidentes, incidentes y enfermedades | Investigación de causas, días de descanso, costo y centro de costo, **plazo de notificación** y acciones correctivas | Jefe o comité de SST |

**Índices** por mes, empresa u obra: IF = accidentes incapacitantes (y mortales) × 1 000 000 / HHT; IG = días perdidos × 1 000 000 / HHT; IA = IF × IG / 1 000. Las horas-hombre se registran o se estiman con los trabajadores de Planillas × SST_HORAS_MES.

**Plazos de notificación** (parámetros): accidente mortal e incidente peligroso, SST_NOTIF_MORTAL_HORAS; enfermedad ocupacional, SST_NOTIF_ENFERMEDAD_DIAS días hábiles; accidente incapacitante, hasta el último día del mes siguiente. Confírmelos con su especialista (Ley 29783, D.S. 005-2012-TR y G.050 en construcción).

**Alertas**: accidentes por notificar, vencimientos en 15 días (EMO, SCTR, equipos, reposición de EPP, monitoreos), permisos de trabajo abiertos, registros por validar y acciones vencidas.

## Excelencia organizacional (módulo `calidad`)

- **No conformidades** (auditoría, reclamo, inspección de SST, proveedor, producción) con análisis de causa (5 porqués o Ishikawa 6M), causa raíz y cierre cuando todas sus acciones están verificadas.
- **Acciones correctivas y preventivas** (también de inspecciones y accidentes de SST): responsable, plazo, ejecución y **verificación de la eficacia** (una no eficaz se reabre).
- **Auditorías internas** (ISO 9001, 14001, 45001, Ley 29783) con hallazgos que se convierten en no conformidades.
- **Documentos controlados** con código, versión y aprobación (validación) y próxima revisión.
- **Objetivos e indicadores por área** (BSC / OKR): meta, sentido, tolerancia (ámbar), frecuencia, responsable y semáforo. Automáticos: días promedio de cobro, no conformidades abiertas, acciones vencidas, IF, IG, IA, EMO vencidos y OEE; los manuales se ingresan con su evidencia.
- **Sugerencias de mejora (Kaizen)** con evaluación, ahorro estimado e implementación.

## Validación por especialista (`core.validacion`)

Cada tipo de registro indica si requiere validación y quién valida (Reglas de validación). Estados: Borrador → Por validar → Validado u Observado. Para validar se adjunta el **registro validado y firmado** (PDF o imagen) y se registra quién validó (nombre, cargo, N° de colegiatura o registro), la fecha y la versión. Un registro validado queda bloqueado; un cambio es una **versión nueva** (la anterior queda como historia). Todo queda en la auditoría. Permisos «registrar» y «validar» por módulo (validar lo asigna el administrador).
