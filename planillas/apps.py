from django.apps import AppConfig


class PlanillasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'planillas'
    verbose_name = 'Planillas'

    def ready(self):
        from django.db.models.signals import post_save

        from finanzas.models import Movimiento

        def pago_anulado(sender, instance, **kwargs):
            if instance.estado == 'ANULADO' and instance.concepto == 'PLANILLA':
                from .servicios import liberar_pago
                liberar_pago(instance)

        post_save.connect(pago_anulado, sender=Movimiento, dispatch_uid='planillas_pago_anulado')
