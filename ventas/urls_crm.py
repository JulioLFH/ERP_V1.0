"""CRM como módulo propio (URL /crm/…). Las oportunidades y actividades siguen en la app ventas (sus tablas no cambian)."""
from django.urls import path

from . import crm, crm_gestion as g, crm_movil as movil, crm_reportes as rep

app_name = 'crm'

urlpatterns = [
    path('', crm.embudo, name='embudo'),
    path('lista/', crm.oportunidades, name='lista'),
    path('nueva/', crm.oportunidad_nueva, name='nueva'),
    path('<int:pk>/', crm.oportunidad, name='oportunidad'),
    path('<int:pk>/etapa/', crm.cambiar_etapa, name='etapa'),
    path('<int:pk>/convertir/', crm.convertir_cliente, name='convertir'),
    path('<int:pk>/whatsapp/', crm.whatsapp, name='whatsapp'),
    path('<int:pk>/proforma/', crm.proforma, name='proforma'),
    # seguimiento
    path('actividades/', crm.mis_actividades, name='actividades'),
    path('actividades/<int:pk>/hecha/', crm.actividad_hecha, name='actividad_hecha'),
    path('visitas/', crm.visitas, name='visitas'),
    path('movil/', movil.movil, name='movil'),
    path('movil/sincronizar/', movil.sincronizar, name='movil_sincronizar'),
    path('movil/sw.js', movil.service_worker, name='movil_sw'),
    # clientes
    path('clientes/', crm.clientes, name='clientes'),
    path('clientes/<int:pk>/', crm.cliente, name='cliente'),
    path('contactos/', g.contactos, name='contactos'),
    path('inactivos/', rep.inactivos, name='inactivos'),
    path('contratos/', g.contratos, name='contratos'),
    path('contratos/nuevo/', g.contrato, name='contrato_nuevo'),
    path('contratos/<int:pk>/', g.contrato, name='contrato'),
    # postventa
    path('postventa/', g.casos, name='casos'),
    path('postventa/nuevo/', g.caso, name='caso_nuevo'),
    path('postventa/<int:pk>/', g.caso, name='caso'),
    path('encuestas/', g.encuestas, name='encuestas'),
    path('encuestas/nueva/', g.encuesta, name='encuesta_nueva'),
    # marketing
    path('campanas/', g.campanas, name='campanas'),
    path('campanas/nueva/', g.campana, name='campana_nueva'),
    path('campanas/<int:pk>/', g.campana, name='campana'),
    path('segmentos/', g.segmentos, name='segmentos'),
    # reportes
    path('reportes/', rep.reportes, name='reportes'),
    path('metas/', rep.metas, name='metas'),
    path('metas/nueva/', g.meta, name='meta_nueva'),
    path('metas/<int:pk>/', g.meta, name='meta'),
    path('comisiones/', rep.comisiones, name='comisiones'),
    path('operaciones/', g.operaciones, name='operaciones'),
    # configuración
    path('equipos/', g.equipos, name='equipos'),
    path('equipos/nuevo/', g.equipo, name='equipo_nuevo'),
    path('equipos/<int:pk>/', g.equipo, name='equipo'),
    path('reasignaciones/', g.reasignaciones, name='reasignaciones'),
    path('datos-personales/', g.datos_personales, name='datos_personales'),
    path('configuracion/', g.configuracion, name='configuracion'),
]
