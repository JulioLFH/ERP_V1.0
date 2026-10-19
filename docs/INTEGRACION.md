# Integración de Ceiba ERP

Cómo se conectan los módulos entre sí y con los servicios externos (versión 1.33.0). GitHub dibuja los diagramas.

## 1. Flujo de información entre módulos

Cada documento operativo mueve el inventario o el dinero y termina, sin digitar de nuevo, en un asiento contable.

```mermaid
flowchart TB
    VEN[Ventas<br/>cotizaciones, facturas, guías] --> INV
    COM[Compras<br/>órdenes, facturas, portal] --> INV
    MAN[Manufactura<br/>órdenes, MRP, planta] --> INV
    REQ[Requerimientos<br/>internos] --> INV
    LOG[Logística<br/>guías de remisión] --> INV
    INV[Inventario<br/>kardex valorizado, lotes, almacenes] --> CON
    VEN --> FIN
    COM --> FIN
    PLA[Planillas<br/>sueldos, AFP, EsSalud] --> FIN
    REN[Rendición de gastos<br/>celular, aprobación] --> FIN
    REN --> COM
    FIN[Finanzas<br/>cobros, pagos, bancos] --> CON
    PLA --> CON
    ACT[Activos fijos<br/>depreciación] --> CON
    MAN --> COS[Costos<br/>estándar, real, ABC]
    COS --> CON
    CON[Contabilidad<br/>centralización automática] --> EEFF[Estados financieros]
    CON --> PLE[Libros electrónicos PLE]
    CON --> REP[Rentabilidad y consolidación del grupo]
```

| Origen | Qué genera | Destino |
|---|---|---|
| Ventas | Salida de stock (kardex), cuenta por cobrar, asiento de venta | Inventario, Finanzas, Contabilidad |
| Compras | Ingreso de stock al costo de la factura, cuenta por pagar, asiento de compra | Inventario, Finanzas, Contabilidad |
| Manufactura | Consumo de insumos, ingreso del producto con su lote, costo de la orden | Inventario, Costos, Contabilidad |
| Requerimientos | Salida de almacén cargada al centro de costo del área | Inventario, Contabilidad |
| Logística | Guía electrónica que acompaña la salida | Inventario, SUNAT |
| Planillas | Pago de sueldos y obligaciones, asiento de planilla | Finanzas, Contabilidad |
| Rendición de gastos | Facturas al registro de compras, reembolso o devolución del saldo | Compras, Finanzas, Contabilidad |
| Activos fijos | Depreciación mensual | Contabilidad |
| Finanzas | Cobros, pagos, retenciones, conciliación bancaria | Contabilidad |
| Costos | Liquidación del costo real del periodo y variaciones | Contabilidad |

## 2. Integraciones externas

```mermaid
flowchart LR
    subgraph Fiscal
        NUB[Nubefact OSE<br/>facturas, boletas, notas y guías]
        SUN[API SUNAT<br/>validación de comprobantes]
        DEC[Decolecta / apis.net.pe<br/>RUC, DNI y tipo de cambio]
        ARC[Archivos PLE, PLAME y SIRE<br/>se suben a SUNAT]
    end
    subgraph Servicios
        IA[Claude de Anthropic<br/>lectura de comprobantes]
        S3[Almacenamiento S3<br/>fotos y sustentos]
        MAIL[Correo SMTP / IMAP<br/>avisos y comprobantes recibidos]
        WA[WhatsApp Cloud API<br/>comprobantes por chat]
    end
    subgraph Usuarios
        WEB[Navegador<br/>usuarios del ERP]
        CEL[App del celular<br/>rendiciones sin internet]
        TAB[Tablets de planta<br/>avances y paradas]
        API[API REST /api/v1<br/>Power BI, tienda, ERP externo]
        POR[Portal de proveedores]
    end
    ERP((Ceiba ERP))
    DB[(PostgreSQL<br/>una base por empresa)]
    NUB <--> ERP
    SUN <--> ERP
    DEC --> ERP
    ERP --> ARC
    IA <--> ERP
    S3 <--> ERP
    MAIL <--> ERP
    WA --> ERP
    WEB <--> ERP
    CEL <--> ERP
    TAB <--> ERP
    API <--> ERP
    POR <--> ERP
    ERP <--> DB
```

| Servicio | Para qué | Dónde se configura | Código |
|---|---|---|---|
| Nubefact (OSE) | Emitir facturas, boletas, notas y guías electrónicas; recibir CDR | Ajustes › Facturación electrónica | `core/sunat.py` |
| API de SUNAT | Validar que un comprobante recibido existe y está activo | Ajustes › Facturación electrónica (client id/secret) | `core/sunat_consulta.py` |
| Decolecta / apis.net.pe | Datos del RUC/DNI y tipo de cambio SUNAT/SBS | Token en Rendiciones › Integraciones | `finanzas/comprobantes.py`, `core/tipo_cambio.py` |
| PLE, PLAME, SIRE | Libros electrónicos, planilla y cruce de compras (archivos) | Contabilidad, Planillas, Compras | `contabilidad/`, `planillas/`, `compras/` |
| Claude (Anthropic) | Leer datos de una foto de comprobante | Rendiciones › Integraciones (clave de API) | `finanzas/comprobantes.py` |
| Almacenamiento S3 | Guardar fotos y sustentos fuera de la base | Variables `OBJETOS_*` | `core/almacen_objetos.py` |
| Correo SMTP / IMAP | Avisos, órdenes de compra, recordatorios; leer comprobantes enviados por correo | Ajustes › Correo saliente; Rendiciones › Integraciones | `core/correo.py`, `leer_correo_rendiciones` |
| WhatsApp Cloud API | Recibir comprobantes por chat | Rendiciones › Integraciones | `finanzas/views_rendicion_movil.py` |
| API REST | Consultar y registrar desde otros sistemas (Power BI, tienda, ERP externo) | Menú del usuario › Claves de API | `core/views_api.py` |
| Portal de proveedores | Proveedores cargan facturas y aceptan órdenes | Compras › Portal | `proveedores/` |

## 3. Plataforma

```mermaid
flowchart TB
    U[Usuarios: navegador, celular, tablets] -->|HTTPS| W[Servidor web: gunicorn + Django]
    W --> M[Middleware: empresa activa, acceso por módulo, auditoría]
    M --> V[Vistas de los 14 módulos]
    V --> R{Enrutador multiempresa}
    R --> P1[(ceiba_principal)]
    R --> P2[(ceiba_empresa2)]
    R --> P3[(ceiba_empresaN)]
    V --> L[Bitácora de auditoría y log de modificaciones]
    V --> I[Incidencias: errores y reportes de usuarios]
    S["Monitor de caídas: /salud/"] --> R
    B[Respaldo diario pg_dump] --> P1
    B --> P2
```

- **Multiempresa**: cada empresa tiene su propia base PostgreSQL (datos, usuarios y configuración separados); la sesión indica cuál se usa y el enrutador manda ahí cada consulta (`erp/empresas.py`).
- **Seguridad**: acceso por módulo y por acción (`core/permisos.py`), segregación de funciones, doble factor, bitácora de cada cambio.
- **Monitoreo**: `/salud/` para un monitor de caídas, errores registrados como incidencias con aviso por correo, `logs/errores.log`.
- **Respaldos**: `scripts/respaldar_postgres.ps1` (programable con `scripts/programar_respaldo.ps1`). Detalle en [POSTGRESQL.md](POSTGRESQL.md).
- **Pruebas**: en cada subida a GitHub, todas las pruebas corren sobre PostgreSQL 17 (`.github/workflows/pruebas.yml`).
