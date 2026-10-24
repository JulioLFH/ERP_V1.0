"""CRM como módulo propio (URL /crm/…). Las oportunidades y actividades siguen en la app ventas (sus tablas no cambian)."""
from django.urls import path

from . import crm

app_name = 'crm'

urlpatterns = [
    path('', crm.embudo, name='embudo'),
    path('lista/', crm.oportunidades, name='lista'),
    path('nueva/', crm.oportunidad_nueva, name='nueva'),
    path('<int:pk>/', crm.oportunidad, name='oportunidad'),
    path('<int:pk>/etapa/', crm.cambiar_etapa, name='etapa'),
]
