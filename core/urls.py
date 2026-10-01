from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('empresa/', views.empresa_config, name='empresa'),
    path('terceros/', views.TerceroLista.as_view(), name='terceros'),
    path('terceros/nuevo/', views.TerceroNuevo.as_view(), name='tercero_nuevo'),
    path('terceros/<int:pk>/', views.TerceroEditar.as_view(), name='tercero_editar'),
    path('productos/', views.ProductoLista.as_view(), name='productos'),
    path('productos/nuevo/', views.ProductoNuevo.as_view(), name='producto_nuevo'),
    path('productos/<int:pk>/', views.ProductoEditar.as_view(), name='producto_editar'),
    path('productos/<int:pk>/kardex/', views.kardex, name='kardex'),
    path('series/', views.SerieLista.as_view(), name='series'),
    path('series/nueva/', views.SerieNueva.as_view(), name='serie_nueva'),
    path('series/<int:pk>/', views.SerieEditar.as_view(), name='serie_editar'),
]
