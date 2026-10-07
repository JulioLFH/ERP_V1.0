from django.urls import path

from . import views, views_asistencia

app_name = 'planillas'

urlpatterns = [
    path('asistencia/', views_asistencia.asistencia_mes, name='asistencia'),
    path('turnos/', views_asistencia.turnos, name='turnos'),
    path('turnos/<int:pk>/', views_asistencia.turnos, name='turno'),
    path('', views.planillas, name='lista'),
    path('nueva/', views.planilla_nueva, name='nueva'),
    path('<int:pk>/', views.planilla_detalle, name='detalle'),
    path('<int:pk>/boletas/', views.boletas, name='boletas'),
    path('plame/<str:periodo>/', views.plame, name='plame'),
    path('afp/<str:periodo>/', views.aportes_afp, name='afp'),
    path('trabajadores/', views.trabajadores, name='trabajadores'),
    path('trabajadores/nuevo/', views.trabajador_nuevo, name='trabajador_nuevo'),
    path('trabajadores/<int:pk>/', views.trabajador_editar, name='trabajador_editar'),
    path('configuracion/', views.configuracion, name='configuracion'),
    path('vacaciones/', views.vacaciones, name='vacaciones'),
    path('costo-centros/', views.costo_centros, name='costo_centros'),
]
