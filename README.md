# ERP V1.0 — Mini ERP en Django

Módulos (inspirados en AdSoft): **Registro de Compras**, **Registro de Ventas** y **Finanzas (Caja y Bancos)**.

## Funcionalidades

### Compras
- Órdenes de compra con correlativo automático → se convierten en factura de compra.
- Registro de comprobantes: factura, boleta, NC, ND, recibo por honorarios (retención 8% automática > S/ 1,500), tickets, servicios públicos.
- Clasificación: mercadería, gasto, activo fijo, servicio, honorarios.
- Operaciones gravadas / exoneradas / inafectas / exportación / gratuitas, ICBPER, detracción, retención y percepción.
- Ingreso a almacén (kardex y costo promedio), edición con reversión de stock, anulación, eliminación, traslado de periodo.
- **Registro de Compras 8.1** en pantalla, Excel y **TXT PLE**.
- Cuentas por pagar con cronograma de vencimientos (antigüedad de deuda).
- Reportes por proveedor, producto, clasificación, periodo y tipo.
- Importación desde Excel (con plantilla descargable).

### Ventas
- Cotizaciones / proformas y órdenes de pedido → se convierten en comprobante.
- Emisión de factura, boleta, ticket, nota de venta, **notas de crédito y débito** (con motivo SUNAT) y series/correlativos automáticos.
- Descarga de stock automática, impresión del comprobante (PDF desde el navegador).
- **Registro de Ventas 14.1** en pantalla, Excel y **TXT PLE**.
- Cuentas por cobrar, reportes por cliente, producto, vendedor, zona y periodo. Importación desde Excel.

### Finanzas (Caja y Bancos)
- Cuentas de caja y bancos (BCP, BBVA, Scotiabank, Interbank, BN detracciones), soles y dólares.
- Cobranzas y pagos **individuales o grupales**, incluidos depósitos de detracción.
- Ingresos/egresos varios: planillas, tributos, servicios, préstamos, gastos bancarios, caja chica, anticipos.
- Transferencias entre cuentas, vouchers imprimibles con correlativo.
- **Conciliación bancaria** con saldo según estado de cuenta.
- Importación de estados de cuenta desde Excel y flujo de caja por concepto.

### Otros
Dashboard con KPIs y gráficos, maestros de clientes/proveedores/productos, correlativos, datos de empresa e IGV configurable, panel `/admin`.

> Simplificaciones de la v1.0: no envía comprobantes electrónicos a SUNAT/OSE y los cobros/pagos se aplican en la moneda del documento. Valide los TXT del PLE con el aplicativo de SUNAT antes de presentarlos.

## Ejecutar en Windows (local)

Doble clic en `iniciar.bat`, o en una terminal:

```bash
.venv\Scripts\python manage.py runserver 8000
```

Abrir http://127.0.0.1:8000 — usuario de demostración: `admin` / `ErpDemo2026!` (cámbielo en `/admin`).

Comandos útiles:

```bash
.venv\Scripts\python manage.py seed_demo        # datos de ejemplo (solo si la BD está vacía)
.venv\Scripts\python manage.py createsuperuser  # crear otro usuario
.venv\Scripts\python manage.py test core        # pruebas
```

## Despliegue en la nube

El proyecto ya está preparado: `requirements.txt`, `build.sh`, `Procfile`, `render.yaml`, `Dockerfile`, WhiteNoise para estáticos y `DATABASE_URL` para PostgreSQL.

### Opción A — Render (recomendada, tiene plan gratuito)
1. Suba esta carpeta a un repositorio de GitHub (ya es un repositorio git con el primer commit):
   `git remote add origin https://github.com/<usuario>/erp-v1.git` y `git push -u origin main`.
2. En https://dashboard.render.com → **New + → Blueprint** → elija el repositorio. Render lee `render.yaml` y crea la base PostgreSQL y el servicio web.
3. Cuando lo pida, defina `DJANGO_SUPERUSER_PASSWORD` (contraseña del usuario `admin` de producción).
4. Al terminar, su ERP queda en `https://erp-v1.onrender.com` (o el nombre que asigne Render).

### Opción B — Railway / Heroku / cualquier PaaS
Usa el `Procfile`. Agregue una base PostgreSQL y defina las variables de `.env.example`
(`SECRET_KEY`, `DEBUG=0`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`).

### Opción C — VPS / Docker (AWS, Azure, Google Cloud, DigitalOcean)
```bash
docker build -t erp-v1 .
docker run -p 8000:8000 --env-file .env erp-v1
```

## Estructura
```
erp/        configuración (settings, urls, wsgi)
core/       empresa, clientes/proveedores, productos, kardex, correlativos, dashboard y lógica común de comprobantes
compras/    órdenes de compra y registro de compras
ventas/     cotizaciones/pedidos y comprobantes de venta
finanzas/   cuentas de caja/bancos, movimientos, cobranzas, pagos, conciliación
templates/  interfaz (Bootstrap 5)
```
