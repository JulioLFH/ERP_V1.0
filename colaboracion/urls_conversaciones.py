from django.urls import path

from . import views_conversaciones as v

app_name = 'conversaciones'

urlpatterns = [
    path('', v.inicio, name='inicio'),
    path('canales/nuevo/', v.canal_nuevo, name='canal_nuevo'),
    path('canales/<int:pk>/nuevos.json', v.nuevos, name='nuevos'),
    path('canales/<int:pk>/unirse/', v.unirse, name='unirse'),
    path('canales/<int:pk>/archivar/', v.archivar, name='archivar'),
    path('directo/', v.directo, name='directo'),
]
