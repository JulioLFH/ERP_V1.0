from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'Núcleo'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from . import auditoria
        auditoria.conectar()

        def acceso(sender, request, user, **kwargs):
            from .models import Bitacora
            ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
                  or request.META.get('REMOTE_ADDR', ''))[:45] if request else ''
            Bitacora.objects.create(usuario=user, accion='ACCESO', modelo='auth.User', modelo_nombre='usuario',
                                    objeto_id=str(user.pk), objeto=user.get_username(), ip=ip)

        user_logged_in.connect(acceso, dispatch_uid='bitacora-acceso', weak=False)

        # multiempresa: mientras se migra una base, las migraciones de datos escriben en esa misma base
        from django.db.models.signals import post_migrate, pre_migrate

        from erp.empresas import activar

        def antes(sender, using='default', **kwargs):
            activar(using)

        def despues(sender, **kwargs):
            activar('default')

        pre_migrate.connect(antes, dispatch_uid='empresa-pre-migrate', weak=False)
        post_migrate.connect(despues, dispatch_uid='empresa-post-migrate', weak=False)
