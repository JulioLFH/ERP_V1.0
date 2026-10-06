"""Vistas compartidas para Registro de Compras y Registro de Ventas.

Ambos módulos manejan comprobantes SUNAT con la misma lógica (alta con ítems, notas de
crédito/débito, anulación, registro formal, PLE, cuentas pendientes, importación Excel y
reportes estadísticos). Cada app instancia `ComprobanteViews` con su configuración.
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.decorators import method_decorator

from .forms import item_formset, periodo_cerrado
from .models import D0, Empresa, Tercero, r2
from .utils import (a_fecha, excel_response, faltantes_stock, fmt_fecha, guardar_documento, leer_excel,
                    lineas_formset, periodo_actual, rango_por_defecto, txt_response)


def normalizar_periodo(texto):
    """'202606', '2026-06', '06/2026' o '6/2026' -> '202606'; vacío o inválido -> ''."""
    import re
    texto = (texto or '').strip()
    m = re.fullmatch(r'(\d{4})-?(\d{1,2})', texto) or None
    if m:
        anio, mes = m.group(1), m.group(2)
    else:
        m = re.fullmatch(r'(\d{1,2})[/-](\d{4})', texto)
        if not m:
            return ''
        mes, anio = m.group(1), m.group(2)
    return f'{anio}{int(mes):02d}' if 1 <= int(mes) <= 12 else ''


def _dec(v):
    if v in (None, ''):
        return D0
    try:
        return Decimal(str(v).replace(',', ''))
    except InvalidOperation:
        raise ValueError(f'Monto inválido: {v}')


class ComprobanteViews:
    app = ''                 # 'compras' | 'ventas'
    modelo = None
    item_modelo = None
    form_class = None
    titulo = ''              # 'Compras'
    tipo_tercero = ''        # 'PROVEEDOR' | 'CLIENTE'
    etiqueta_tercero = ''    # 'Proveedor'
    libro = ''               # '8.1' | '14.1'
    codigo_ple = ''          # '080100' | '140100'
    agrupaciones = []        # [(clave, etiqueta)] para reportes

    # ------------------------------------------------------------ utilidades
    def _ctx(self, **extra):
        base = {'app': self.app, 'titulo_modulo': self.titulo, 'etiqueta_tercero': self.etiqueta_tercero,
                'libro': self.libro}
        base.update(extra)
        return base

    def _url(self, nombre, *args):
        return reverse(f'{self.app}:{nombre}', args=args)

    def al_guardar(self, doc):
        """Hook: mover almacén luego de guardar."""

    def puede_editar(self, doc):
        return (doc.estado == 'REGISTRADO' and not doc.es_historico and not doc.movimientos.exists() and not doc.notas.exists()
                and not periodo_cerrado(doc.periodo))

    def es_salida(self, tipo, mueve_stock):
        """True si el documento saca mercadería del almacén (se valida el stock)."""
        return False

    def validar_stock(self, form, formset):
        doc = form.instance
        if not self.es_salida(doc.tipo_comprobante, self.mueve_stock(doc)):
            return []
        devolver = {}
        if doc.pk:
            anterior = type(doc).objects.get(pk=doc.pk)
            if anterior.stock_aplicado and anterior.almacen_id == doc.almacen_id:
                for i in anterior.items.all():
                    devolver[i.producto_id] = devolver.get(i.producto_id, D0) + i.cantidad
        return faltantes_stock(lineas_formset(formset), doc.almacen, devolver)

    def mueve_stock(self, doc):
        return True

    @staticmethod
    def faltantes_al_revertir(doc):
        """Anular/eliminar una entrada (compra, NC de venta) saca stock: debe haber existencias."""
        if not doc.stock_aplicado or doc._signo_stock() < 0:
            return []
        return faltantes_stock([(i.producto, i.cantidad) for i in doc.items.select_related('producto')], doc.almacen)

    # ------------------------------------------------------------ vistas
    def lista(self, request):
        qs = self.modelo.objects.con_saldos().select_related('tercero')
        periodo = normalizar_periodo(request.GET.get('periodo', ''))
        if periodo:
            qs = qs.filter(periodo=periodo)
        for campo in ('tipo_comprobante', 'estado', 'moneda'):
            if request.GET.get(campo):
                qs = qs.filter(**{campo: request.GET[campo]})
        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(Q(tercero__nombre__icontains=q) | Q(tercero__numero_doc__icontains=q) |
                           Q(numero__icontains=q) | Q(serie__icontains=q) | Q(glosa__icontains=q))
        if request.GET.get('formato') == 'excel':  # la lista con los mismos filtros
            from .utils import excel_response
            datos = [[d.fecha_emision.strftime('%d/%m/%Y'), d.get_tipo_comprobante_display(), d.numero_completo,
                      d.tercero.numero_doc, d.tercero.nombre, d.moneda, d.total, d.total_pen, d.saldo,
                      d.get_estado_display(), d.periodo] for d in qs]
            return excel_response(f'{self.titulo}_{periodo or "todos"}', f'{self.titulo} {periodo}'.strip(),
                                  ['Fecha', 'Tipo', 'Número', 'RUC / DNI', 'Razón social', 'Moneda', 'Total',
                                   'Total S/', 'Saldo', 'Estado', 'Periodo'], datos)
        pagina = Paginator(qs, 50).get_page(request.GET.get('page'))
        return render(request, 'core/comprobante_lista.html', self._ctx(
            page_obj=pagina, periodo=periodo, q=q, tipos=self.modelo._meta.get_field('tipo_comprobante').choices))

    def _form_ctx(self, titulo, doc=None):
        return self._ctx(titulo=titulo, doc=doc, igv_tasa=Empresa.actual().igv_tasa,
                         precio_campo='precio_compra' if self.app == 'compras' else 'precio_venta')

    def nuevo(self, request):
        initial, items = self.initial_desde(request)
        return guardar_documento(request, self.form_class, item_formset(self.modelo, self.item_modelo),
                                 self.modelo(), 'core/comprobante_form.html',
                                 self._form_ctx(f'Nuevo comprobante de {self.titulo.lower()}'),
                                 al_guardar=self.al_guardar, initial=initial, items_iniciales=items,
                                 validar=self.validar_stock)

    def initial_desde(self, request):
        """Prellenado al emitir NC/ND desde un comprobante (?ref=ID&tipo=07)."""
        ref_id = request.GET.get('ref')
        if not ref_id:
            return {'tipo_comprobante': request.GET.get('tipo', '01')}, None
        ref = get_object_or_404(self.modelo, pk=ref_id)
        initial = {
            'tipo_comprobante': request.GET.get('tipo', '07'), 'doc_referencia': ref.pk, 'tercero': ref.tercero_id,
            'moneda': ref.moneda, 'tipo_cambio': ref.tipo_cambio, 'tipo_operacion': ref.tipo_operacion,
            'motivo_nota': request.GET.get('motivo', ''), 'almacen': ref.almacen_id,
            'glosa': request.GET.get('sustento', ''),
        }
        items = [{'producto': i.producto_id, 'descripcion': i.descripcion, 'cantidad': i.cantidad,
                  'precio_unitario': i.precio_unitario, 'descuento_pct': i.descuento_pct} for i in ref.items.all()]
        if request.GET.get('op'):
            # NC de una devolución ya registrada en Inventario: el almacén ya se movió
            from inventario.models import Operacion
            op = get_object_or_404(Operacion, pk=request.GET['op'])
            initial.update(ingresar_almacen=False, descontar_stock=False,
                           glosa=initial['glosa'] or f'Devolución {op.numero}')
            precios = {i.producto_id: i for i in ref.items.all()}
            items = [{'producto': i.producto_id, 'descripcion': precios[i.producto_id].descripcion
                      if i.producto_id in precios else i.producto.nombre, 'cantidad': i.cantidad,
                      'precio_unitario': precios[i.producto_id].precio_unitario if i.producto_id in precios else D0}
                     for i in op.items.select_related('producto')]
        return initial, items

    def editar(self, request, pk):
        doc = get_object_or_404(self.modelo, pk=pk)
        if not self.puede_editar(doc):
            messages.error(request, 'No se puede editar: el comprobante ya fue emitido (corríjalo con nota de crédito '
                                    'o anúlelo), está anulado, tiene pagos/notas asociadas o su periodo contable '
                                    'está cerrado.' if self.modelo._meta.model_name == 'venta' else
                           'No se puede editar: el comprobante está anulado, tiene pagos/notas asociadas '
                           'o su periodo contable está cerrado.')
            return redirect(self._url('detalle', pk))
        return guardar_documento(request, self.form_class, item_formset(self.modelo, self.item_modelo, extra=0),
                                 doc, 'core/comprobante_form.html',
                                 self._form_ctx(f'Editar {doc}', doc), al_guardar=self.al_guardar,
                                 validar=self.validar_stock)

    def detalle(self, request, pk):
        from inventario.servicios import acciones_para, operaciones_de
        doc = get_object_or_404(self.modelo.objects.select_related('tercero', 'doc_referencia'), pk=pk)
        activos = {}
        if doc._meta.model_name == 'compra':  # activos fijos de la factura: registrados y por registrar
            from activos.servicios import items_compra
            activos = {'pendientes': items_compra(doc), 'registrados': doc.activos.exclude(estado='ANULADO')}
        return render(request, 'core/comprobante_detalle.html', self._ctx(
            doc=doc, items=doc.items.select_related('producto'), notas=doc.notas.all(),
            movimientos=doc.movimientos.select_related('cuenta'), puede_editar=self.puede_editar(doc),
            acciones_inv=acciones_para(doc), operaciones_inv=operaciones_de(doc), activos_doc=activos))

    def imprimir(self, request, pk):
        doc = get_object_or_404(self.modelo, pk=pk)
        return render(request, 'core/comprobante_imprimir.html', self._ctx(doc=doc, items=doc.items.all()))

    def motivo_bloqueo_anulacion(self, doc):
        if doc.estado == 'ANULADO':
            return 'El comprobante ya está anulado.'
        if doc.es_historico:
            return 'Es historial importado del sistema anterior: se anula allí.'
        if periodo_cerrado(doc.periodo):
            return f'El periodo contable {doc.periodo} está cerrado.'
        if doc.movimientos.exists():
            return 'Tiene cobros/pagos registrados en Finanzas. Elimínelos antes de anular.'
        if doc.notas.filter(estado='REGISTRADO').exists():
            return 'Tiene notas de crédito/débito registradas. Anúlelas primero.'
        if doc._meta.model_name == 'compra' and doc.activos.exclude(estado='ANULADO').exists():
            return 'Tiene activos fijos registrados con esta factura. Anúlelos o déles de baja primero.'
        faltan = self.faltantes_al_revertir(doc)
        if faltan:
            return 'No se puede anular: la mercadería ya salió del almacén. ' + ' '.join(faltan)
        from inventario.cierre import error_cierre
        if doc.stock_aplicado and error_cierre(doc.fecha_emision):
            return error_cierre(doc.fecha_emision)
        return ''

    def anular(self, request, pk):
        doc = get_object_or_404(self.modelo, pk=pk)
        if request.method == 'POST':
            motivo = request.POST.get('motivo', '').strip()
            bloqueo = self.motivo_bloqueo_anulacion(doc)
            if bloqueo:
                messages.error(request, bloqueo)
            elif len(motivo) < 5:
                messages.error(request, 'Indique el motivo de la anulación (mínimo 5 caracteres).')
            else:
                with transaction.atomic():
                    doc.anular(request.user, motivo)
                    if doc._meta.model_name == 'compra':
                        from compras.precios import liquidar
                        liquidar(doc)  # se revierte la diferencia de precio que hubiera liquidado
                messages.success(request, f'{doc} anulado.')
        return redirect(self._url('detalle', pk))

    def eliminar(self, request, pk):
        """Los comprobantes no se eliminan: se anulan con motivo (queda el registro y la auditoría)."""
        messages.error(request, 'Los comprobantes no se eliminan: use "Anular" e indique el motivo.')
        return redirect(self._url('detalle', pk))

    def trasladar(self, request, pk):
        """Traslada el comprobante a otro periodo de registro."""
        doc = get_object_or_404(self.modelo, pk=pk)
        nuevo = request.POST.get('periodo', '')
        if request.method == 'POST' and (periodo_cerrado(doc.periodo) or periodo_cerrado(nuevo)):
            messages.error(request, 'No se puede trasladar desde o hacia un periodo contable cerrado.')
        elif request.method == 'POST' and len(nuevo) == 6 and nuevo.isdigit():
            from contabilidad.automatico import marcar_pendiente
            marcar_pendiente(doc.periodo)
            doc.periodo = nuevo
            doc.save(update_fields=['periodo'])
            messages.success(request, f'Trasladado al periodo {nuevo}.')
        else:
            messages.error(request, 'Periodo inválido (formato AAAAMM).')
        return redirect(self._url('detalle', pk))

    # ------------------------------------------------------------ notas de crédito / débito
    def notas(self, request):
        """Flujo propio de NC/ND: lista de notas + asistente que parte del comprobante a modificar."""
        qs = (self.modelo.objects.filter(tipo_comprobante__in=['07', '08'])
              .select_related('tercero', 'doc_referencia'))
        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(Q(tercero__nombre__icontains=q) | Q(numero__icontains=q) |
                           Q(doc_referencia__numero__icontains=q))
        if request.method == 'POST':
            ref_id, tipo = request.POST.get('referencia'), request.POST.get('tipo')
            if not ref_id or tipo not in ('07', '08'):
                messages.error(request, 'Seleccione el comprobante y el tipo de nota.')
            else:
                from urllib.parse import urlencode
                params = urlencode({'ref': ref_id, 'tipo': tipo, 'motivo': request.POST.get('motivo', ''),
                                    'sustento': request.POST.get('sustento', '')})
                return redirect(f"{self._url('nuevo')}?{params}")
        candidatos = (self.modelo.objects.filter(estado='REGISTRADO').exclude(tipo_comprobante__in=['07', '08'])
                      .select_related('tercero').order_by('-fecha_emision')[:300])
        motivos = getattr(self.modelo, 'MOTIVOS_NC', [])
        return render(request, 'core/notas.html', self._ctx(
            page_obj=Paginator(qs, 50).get_page(request.GET.get('page')), q=q, candidatos=candidatos,
            motivos=[m for m in motivos if m[0]], ref_inicial=request.GET.get('ref', '')))

    # ------------------------------------------------------------ registro formal / PLE
    def _registro_qs(self, periodo):
        return (self.modelo.objects.filter(periodo=periodo, es_saldo_inicial=False, es_historico=False)
                .select_related('tercero', 'doc_referencia')
                .order_by('fecha_emision', 'tipo_comprobante', 'serie', 'numero'))

    def registro(self, request):
        periodo = periodo_actual(request, self.modelo.objects.all())
        docs = list(self._registro_qs(periodo))
        tot = {k: D0 for k in ('base', 'nograv', 'igv', 'total', 'detr', 'ret', 'perc')}
        for d in docs:
            if d.estado == 'ANULADO':
                continue
            # mismos importes en soles que la contabilidad (calculados una vez por documento)
            s = d.signo
            tot['base'] += s * d.base_pen
            tot['nograv'] += s * d.nograv_pen
            tot['igv'] += s * d.igv_pen
            tot['total'] += s * d.total_pen
            tot['detr'] += d.detr_pen
            tot['ret'] += d.ret_pen
            tot['perc'] += d.perc_pen
        if request.GET.get('formato') == 'excel':
            return self._registro_excel(periodo, docs)
        if request.GET.get('formato') == 'ple':
            return self._registro_ple(periodo, docs)
        return render(request, 'core/registro.html', self._ctx(docs=docs, periodo=periodo, tot=tot))

    def _registro_excel(self, periodo, docs):
        enc = ['N°', 'Fecha emisión', 'Fecha venc.', 'Tipo', 'Serie', 'Número', 'Tipo doc', 'N° doc',
               self.etiqueta_tercero, 'Moneda', 'T.C.', 'Base imponible', 'Exon./Inaf.', 'IGV', 'ICBPER', 'Total',
               'Total S/', 'Detracción', 'Retención', 'Percepción', 'Ref. fecha', 'Ref. tipo', 'Ref. serie-número',
               'Estado']
        filas = []
        for n, d in enumerate(docs, 1):
            s = d.signo
            ref = d.doc_referencia
            filas.append([n, fmt_fecha(d.fecha_emision), fmt_fecha(d.fecha_vencimiento), d.tipo_comprobante,
                          d.serie, d.numero, d.tercero.tipo_doc, d.tercero.numero_doc, d.tercero.nombre, d.moneda,
                          d.tipo_cambio, s * d.base_imponible, s * d.no_gravado, s * d.igv, d.icbper, s * d.total,
                          s * d.total_pen, d.detraccion_monto, d.retencion_monto, d.percepcion_monto,
                          fmt_fecha(ref.fecha_emision) if ref else '', ref.tipo_comprobante if ref else '',
                          ref.numero_completo if ref else '', d.get_estado_display()])
        empresa = Empresa.actual()
        titulo = f'REGISTRO DE {self.titulo.upper()} {self.libro} - {empresa.razon_social} - RUC {empresa.ruc} - PERIODO {periodo}'
        return excel_response(f'Registro_{self.titulo}_{periodo}', titulo, enc, filas)

    def _registro_ple(self, periodo, docs):
        empresa = Empresa.actual()
        lineas = [self.linea_ple(periodo, n, d) for n, d in enumerate(docs, 1)]
        indicador = '1' if lineas else '0'
        nombre = f'LE{empresa.ruc}{periodo}00{self.codigo_ple}00{indicador}11.txt'
        return txt_response(nombre, lineas)

    def linea_ple(self, periodo, n, d):
        raise NotImplementedError

    # ------------------------------------------------------------ cuentas pendientes
    def pendientes(self, request):
        qs = (self.modelo.objects.con_saldos().cobrables().filter(estado='REGISTRADO')
              .exclude(tipo_comprobante='07').select_related('tercero').order_by('fecha_vencimiento'))
        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(Q(tercero__nombre__icontains=q) | Q(tercero__numero_doc__icontains=q))
        docs = [d for d in qs if d.saldo > 0]
        tramos = {'Por vencer': D0, '1-30 días': D0, '31-60 días': D0, '61-90 días': D0, '+90 días': D0}
        for d in docs:
            dias, monto = d.dias_vencido, d.saldo_pen
            clave = ('Por vencer' if dias <= 0 else '1-30 días' if dias <= 30 else '31-60 días' if dias <= 60
                     else '61-90 días' if dias <= 90 else '+90 días')
            tramos[clave] += monto
        if request.GET.get('formato') == 'excel':
            filas = [[fmt_fecha(d.fecha_emision), fmt_fecha(d.fecha_vencimiento), d.dias_vencido,
                      d.get_tipo_comprobante_display(), d.numero_completo, d.tercero.numero_doc, d.tercero.nombre,
                      d.moneda, d.neto, d.pagado, d.saldo, d.saldo_pen, d.detraccion_monto] for d in docs]
            enc = ['Emisión', 'Vencimiento', 'Días vencido', 'Tipo', 'Número', 'RUC/DNI', self.etiqueta_tercero,
                   'Moneda', 'Neto', 'Pagado', 'Saldo', 'Saldo S/', 'Detracción']
            return excel_response(f'Pendientes_{self.titulo}', f'Cronograma de vencimientos - {self.titulo}', enc, filas)
        return render(request, 'core/pendientes.html', self._ctx(
            docs=docs, tramos=tramos, q=q, total=sum(tramos.values(), D0)))

    # ------------------------------------------------------------ reportes estadísticos
    def reportes(self, request):
        desde, hasta = rango_por_defecto(request, self.modelo.objects.filter(estado='REGISTRADO'), 'fecha_emision')
        agrupar = request.GET.get('agrupar') or self.agrupaciones[0][0]
        base = self.modelo.objects.de_gestion().filter(estado='REGISTRADO', fecha_emision__range=[desde, hasta])
        if agrupar == 'producto':
            filas = (self.item_modelo.objects.filter(documento__in=base.exclude(tipo_comprobante='07'))
                     .values('descripcion').annotate(cantidad=Sum('cantidad'), total=Sum('subtotal'),
                                                     docs=Count('documento', distinct=True)).order_by('-total'))
            filas = [{'clave': f['descripcion'], 'docs': f['docs'], 'cantidad': f['cantidad'], 'total': f['total']}
                     for f in filas]
        else:
            campo = {'tercero': 'tercero__nombre', 'zona': 'tercero__zona'}.get(agrupar, agrupar)
            filas = (base.exclude(tipo_comprobante='07').values(campo)
                     .annotate(docs=Count('id'), total=Sum('total'), igv=Sum('igv')).order_by('-total'))
            filas = [{'clave': f[campo] or '(sin dato)', 'docs': f['docs'], 'igv': f['igv'], 'total': f['total']}
                     for f in filas]
        total = sum((f['total'] or D0 for f in filas), D0)
        for f in filas:
            f['pct'] = round(float((f['total'] or 0) / total * 100), 1) if total else 0
        return render(request, 'core/reportes.html', self._ctx(
            filas=filas, desde=desde, hasta=hasta, agrupar=agrupar, agrupaciones=self.agrupaciones, total=total))

    # ------------------------------------------------------------ importación Excel
    COLUMNAS_IMPORT = ['tipo', 'serie', 'numero', 'fecha', 'vencimiento', 'tipo_doc', 'ruc', 'nombre',
                       'moneda', 'tc', 'base', 'no_gravado', 'igv', 'total', 'glosa']

    def importar(self, request):
        if request.GET.get('plantilla'):
            ejemplo = ['01', 'F001', '123', date.today().strftime('%d/%m/%Y'), '', '6', '20123456786',
                       'EMPRESA EJEMPLO SAC', 'PEN', 1, 100, 0, 18, 118, 'Importado']
            return excel_response(f'Plantilla_{self.titulo}', 'plantilla', self.COLUMNAS_IMPORT, [ejemplo])
        resultado = None
        if request.method == 'POST' and request.FILES.get('archivo'):
            resultado = self._procesar_import(request.FILES['archivo'])
            if resultado['ok']:
                messages.success(request, f"{resultado['ok']} comprobantes importados.")
        return render(request, 'core/importar.html', self._ctx(
            resultado=resultado, columnas=self.COLUMNAS_IMPORT,
            titulo=f'Importar registro de {self.titulo.lower()} desde Excel'))

    def _procesar_import(self, archivo):
        ok, errores = 0, []
        try:
            filas = leer_excel(archivo)
        except Exception as exc:  # archivo corrupto / formato no soportado
            return {'ok': 0, 'errores': [f'No se pudo leer el archivo: {exc}']}
        for n, f in enumerate(filas, 2):
            try:
                with transaction.atomic():
                    num_doc = str(f.get('ruc') or '').strip()
                    tercero, _ = Tercero.objects.get_or_create(
                        numero_doc=num_doc,
                        defaults={'nombre': f.get('nombre') or num_doc, 'tipo': self.tipo_tercero,
                                  'tipo_doc': str(f.get('tipo_doc') or ('6' if len(num_doc) == 11 else '1'))})
                    fecha = a_fecha(f.get('fecha'))
                    total = _dec(f.get('total'))
                    base, igv, nograv = _dec(f.get('base')), _dec(f.get('igv')), _dec(f.get('no_gravado'))
                    if not total:
                        total = base + igv + nograv
                    self.modelo.objects.create(
                        tipo_comprobante=str(f.get('tipo') or '01').zfill(2), serie=str(f.get('serie') or ''),
                        numero=str(f.get('numero') or '').strip(), fecha_emision=fecha,
                        fecha_vencimiento=a_fecha(f['vencimiento']) if f.get('vencimiento') else fecha,
                        tercero=tercero, moneda=(f.get('moneda') or 'PEN').upper(),
                        tipo_cambio=_dec(f.get('tc')) or Decimal('1'),
                        tipo_operacion='GRAVADA' if igv else 'EXONERADA',
                        base_imponible=base, no_gravado=nograv, exonerado=nograv, igv=igv, total=total,
                        glosa=f.get('glosa') or 'Importado desde Excel')
                ok += 1
            except IntegrityError:
                errores.append(f'Fila {n}: comprobante duplicado.')
            except Exception as exc:
                errores.append(f'Fila {n}: {exc}')
        return {'ok': ok, 'errores': errores}

    # ------------------------------------------------------------ rutas
    def urls(self):
        lr = login_required
        return [
            path('', lr(self.lista), name='lista'),
            path('nuevo/', lr(self.nuevo), name='nuevo'),
            path('<int:pk>/', lr(self.detalle), name='detalle'),
            path('<int:pk>/editar/', lr(self.editar), name='editar'),
            path('<int:pk>/imprimir/', lr(self.imprimir), name='imprimir'),
            path('<int:pk>/anular/', lr(self.anular), name='anular'),
            path('<int:pk>/eliminar/', lr(self.eliminar), name='eliminar'),
            path('<int:pk>/trasladar/', lr(self.trasladar), name='trasladar'),
            path('registro/', lr(self.registro), name='registro'),
            path('notas/', lr(self.notas), name='notas'),
            path('pendientes/', lr(self.pendientes), name='pendientes'),
            path('reportes/', lr(self.reportes), name='reportes'),
            path('importar/', lr(self.importar), name='importar'),
        ]


def ple_num(v):
    return f'{Decimal(v or 0):.2f}'
