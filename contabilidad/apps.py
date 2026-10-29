from django.apps import AppConfig


class ContabilidadConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'contabilidad'
    verbose_name = 'Contabilidad'

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from . import automatico, pcge
        automatico._conectar()
        for senal in (post_save, post_delete):  # cuentas de 6 dígitos del plan cargado (pcge.al_detalle)
            senal.connect(pcge.limpiar_detalle, sender='contabilidad.CuentaContable', weak=False)
