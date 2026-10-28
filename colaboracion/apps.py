from django.apps import AppConfig


class ColaboracionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'colaboracion'
    verbose_name = 'Conversaciones, calendario, actividades y proyectos'

    def ready(self):
        from django.db.models.signals import post_save

        from compras.models import OrdenCompra

        from .avisos import oc_registrada
        post_save.connect(oc_registrada, sender=OrdenCompra, dispatch_uid='aviso_oc_registrada')
