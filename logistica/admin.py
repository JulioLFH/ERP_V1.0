from django.contrib import admin

from .models import Conductor, GuiaItem, GuiaRemision, Vehiculo

admin.site.register(Vehiculo)
admin.site.register(Conductor)


class GuiaItemInline(admin.TabularInline):
    model = GuiaItem
    extra = 0


@admin.register(GuiaRemision)
class GuiaRemisionAdmin(admin.ModelAdmin):
    inlines = [GuiaItemInline]
    list_display = ('__str__', 'fecha_emision', 'destinatario', 'motivo_traslado', 'estado', 'estado_sunat')
    list_filter = ('tipo', 'estado', 'estado_sunat')
