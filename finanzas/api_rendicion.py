"""API v1: rendiciones de gastos (consultar rendiciones y registrar gastos desde integraciones o apps)."""
import base64
import binascii

from django.db.models import Q

from core.api import _cuerpo, _pagina, api, error, fecha_param, respuesta

from . import rendiciones as rd
from .forms_rendicion import LineaGastoForm


def _s_linea(l):
    return {'id': l.pk, 'fecha': l.fecha, 'tipo_comprobante': l.tipo_comprobante, 'ruc': l.ruc,
            'razon_social': l.razon_social, 'serie': l.serie, 'numero': l.numero, 'moneda': l.moneda,
            'tipo_cambio': l.tipo_cambio, 'base': l.base, 'igv': l.igv, 'total': l.total, 'importe': l.importe,
            'indicador_impuesto': l.indicador.codigo if l.indicador_id else None,
            'categoria': l.categoria.codigo, 'centro_costo': l.centro_costo.codigo if l.centro_costo_id else None,
            'distribucion': [{'centro_costo': d.centro_costo.codigo, 'porcentaje': d.porcentaje}
                             for d in l.distribucion.select_related('centro_costo')],
            'proyecto': l.proyecto.codigo if l.proyecto_id else None, 'glosa': l.glosa, 'estado': l.estado,
            'alertas': l.alertas, 'justificacion': l.justificacion, 'origen': l.origen,
            'validacion_sunat': l.estado_sunat, 'compra_id': l.compra_id}


def _s_rendicion(r, detalle=False):
    datos = {'id': r.pk, 'numero': r.numero, 'fecha': r.fecha, 'descripcion': r.descripcion, 'estado': r.estado,
             'entrega': r.entrega.numero, 'tipo_fondo': r.entrega.tipo, 'moneda': r.entrega.moneda,
             'responsable': {'numero_doc': r.entrega.responsable.numero_doc, 'nombre': r.entrega.responsable.nombre},
             'rendidor': r.rendidor.username, 'total': r.total, 'niveles': r.niveles,
             'nivel_aprobado': r.nivel_aprobado, 'enviada_en': r.enviada_en, 'aprobada_en': r.aprobada_en,
             'contabilizada_en': r.contabilizada_en}
    if detalle:
        datos['gastos'] = [_s_linea(l) for l in r.lineas.select_related('categoria', 'centro_costo', 'proyecto')]
        datos['historial'] = [{'fecha': h.fecha, 'usuario': h.usuario.username if h.usuario_id else None,
                               'de': h.de_estado, 'a': h.a_estado, 'comentario': h.comentario}
                              for h in r.historial.select_related('usuario')]
    return datos


@api('rendiciones')
def rendiciones(request):
    qs = rd.visibles(request.user).order_by('-fecha', '-id')
    desde, hasta = fecha_param(request, 'desde'), fecha_param(request, 'hasta')
    if desde:
        qs = qs.filter(fecha__gte=desde)
    if hasta:
        qs = qs.filter(fecha__lte=hasta)
    if request.GET.get('estado'):
        qs = qs.filter(estado=request.GET['estado'].upper())
    if request.GET.get('responsable'):
        v = request.GET['responsable']
        qs = qs.filter(Q(entrega__responsable__numero_doc=v) | Q(entrega__responsable__nombre__icontains=v))
    return respuesta(_pagina(request, qs, _s_rendicion))


@api('rendiciones')
def rendicion(request, pk):
    r = rd.visibles(request.user).filter(pk=pk).first()
    return respuesta(_s_rendicion(r, detalle=True)) if r else error('Rendición no encontrada.', 404)


@api('rendiciones')
def categorias(request):
    from .models import CategoriaGasto
    return respuesta(_pagina(request, CategoriaGasto.objects.filter(activo=True).select_related('cuenta_contable'),
                             lambda c: {'codigo': c.codigo, 'nombre': c.nombre, 'cuenta': c.cuenta_contable.codigo,
                                        'exige_comprobante': c.exige_comprobante, 'exige_centro': c.exige_centro,
                                        'tope': c.tope, 'tope_diario': c.tope_diario, 'tarifa_km': c.tarifa_km}))


@api('rendiciones', metodos=('POST',), escribir='rendiciones.rendir')
def gasto(request, pk):
    """Agrega un gasto a una rendición en borrador del usuario de la clave. Categoría, centro de costo y proyecto
    por su código; la foto opcional en base64 (foto_base64, foto_nombre)."""
    from contabilidad.models import CentroCosto
    from .models import CategoriaGasto, IndicadorImpuesto, Proyecto
    r = rd.visibles(request.user).filter(pk=pk).first()
    if r is None:
        return error('Rendición no encontrada.', 404)
    d = _cuerpo(request)
    if not isinstance(d, dict):
        return error('Envíe un objeto JSON.')
    datos = {k: d.get(k, '') for k in ('fecha', 'tipo_comprobante', 'ruc', 'razon_social', 'serie', 'numero', 'moneda',
                                         'tipo_cambio', 'base', 'igv', 'total', 'kilometros', 'destino', 'glosa',
                                         'justificacion')}
    datos['a_nombre_empresa'] = 'on' if d.get('a_nombre_empresa', True) else ''
    datos['moneda'] = datos['moneda'] or r.entrega.moneda
    datos['tipo_comprobante'] = datos['tipo_comprobante'] or '12'
    if not d.get('indicador_impuesto'):
        return error('indicador_impuesto es obligatorio (C1, S1, R1, R0, EX, IN, NG o los configurados).', 422)
    d['indicador'] = d['indicador_impuesto']
    for campo, modelo in (('categoria', CategoriaGasto), ('centro_costo', CentroCosto), ('proyecto', Proyecto),
                          ('indicador', IndicadorImpuesto)):
        codigo = d.get(campo)
        obj = modelo.objects.filter(codigo=codigo).first() if codigo else None
        if codigo and obj is None:
            return error(f'{campo} {codigo} no existe.', 422)
        datos[campo] = obj.pk if obj else ''
    archivos = {}
    if d.get('foto_base64'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        try:
            contenido = base64.b64decode(d['foto_base64'], validate=True)
        except (binascii.Error, ValueError):
            return error('foto_base64 no es base64 válido.', 422)
        nombre = d.get('foto_nombre') or 'comprobante.jpg'
        archivos['fotos'] = SimpleUploadedFile(nombre, contenido, content_type=d.get('foto_tipo') or 'image/jpeg')
    from django.utils.datastructures import MultiValueDict
    form = LineaGastoForm(datos, MultiValueDict({k: [v] for k, v in archivos.items()}))
    if not form.is_valid():
        return error('Datos inválidos.', 422, campos={k: v for k, v in form.errors.items()})
    try:
        linea = form.save(commit=False)
        linea.origen = 'API'
        linea = rd.guardar_linea(r, linea, request.user)
        from core.sustentos import adjuntar
        for archivo in form.cleaned_data.get('fotos') or []:
            adjuntar(linea, archivo, request.user, 'Comprobante (API)')
        rd.evaluar(r)
    except rd.ErrorRendicion as exc:
        return error(str(exc), 422)
    linea.refresh_from_db()
    return respuesta(_s_linea(linea), 201)
