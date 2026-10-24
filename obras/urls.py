from django.urls import path

from . import views, views_comercial as com

app_name = 'obras'

urlpatterns = [
    path('', views.obras_lista, name='lista'),
    path('nueva/', views.ObraNueva.as_view(), name='obra_nueva'),
    path('<int:pk>/', views.obra, name='obra'),
    path('<int:pk>/editar/', views.ObraEditar.as_view(), name='obra_editar'),
    path('<int:pk>/presupuestos/nuevo/', views.presupuesto_nuevo, name='presupuesto_nuevo'),
    path('<int:pk>/importar/', views.importar, name='importar'),
    path('presupuestos/<int:pk>/', views.presupuesto, name='presupuesto'),
    path('presupuestos/<int:pk>/precios/', views.precios, name='precios'),
    path('presupuestos/<int:pk>/insumos/', views.insumos, name='insumos'),
    path('presupuestos/<int:pk>/analisis/', views.apu_reporte, name='apu_reporte'),
    path('presupuestos/<int:pk>/control/', views.control_obra, name='control'),
    path('partidas/<int:pk>/analisis/', views.apu, name='apu'),
    path('recursos/', views.RecursoLista.as_view(), name='recursos'),
    path('recursos/nuevo/', views.RecursoNuevo.as_view(), name='recurso_nuevo'),
    path('recursos/<int:pk>/editar/', views.RecursoEditar.as_view(), name='recurso_editar'),
    path('recursos/<int:pk>/subanalisis/', views.subanalisis, name='subanalisis'),
    path('recursos/vincular/', views.vincular, name='vincular'),
    path('biblioteca/', views.biblioteca, name='biblioteca'),
    path('plantilla/', views.plantilla, name='plantilla'),
    # comercial: licitaciones (CRM de constructoras) y proyectos inmobiliarios
    path('licitaciones/', com.licitaciones, name='licitaciones'),
    path('licitaciones/nueva/', com.licitacion_nueva, name='licitacion_nueva'),
    path('licitaciones/desde-oportunidad/<int:pk>/', com.desde_oportunidad, name='desde_oportunidad'),
    path('<int:pk>/licitacion/', com.licitacion, name='licitacion'),
    path('<int:pk>/unidades/', com.unidades, name='unidades'),
    path('<int:pk>/separar/', com.separar, name='separar'),
    path('ventas-inmuebles/<int:pk>/', com.venta_inmueble, name='venta_inmueble'),
    path('cuotas/<int:pk>/cobrar/', com.cobrar, name='inm_cobrar'),
    path('cobranza-inmobiliaria/', com.cobranza, name='inm_cobranza'),
    path('reportes-inmobiliarios/', com.reportes_inmobiliarios, name='inm_reportes'),
    path('sobrantes/', com.sobrantes, name='sobrantes'),
]
