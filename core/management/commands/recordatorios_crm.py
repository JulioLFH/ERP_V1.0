"""Recordatorios del CRM por correo (CRM-04): a cada responsable, sus actividades con «Recordarme» de hoy y las
vencidas sin avisar. Programarlo una vez al día (Programador de tareas de Windows o cron), en cada empresa."""
from django.core.management.base import BaseCommand
from django.utils import timezone

from erp import empresas as emp


class Command(BaseCommand):
    help = 'Envía por correo los recordatorios de las actividades del CRM (todas las empresas).'

    def handle(self, *args, **opciones):
        from core.correo import ErrorCorreo, enviar_simple
        from ventas.models import ActividadCRM
        emp.sincronizar(forzar=True)
        for alias in emp.empresas():
            token = emp.activar(alias)
            try:
                hoy = timezone.localdate()
                qs = (ActividadCRM.objects.filter(hecha=False, recordatorio=True, avisada=False, fecha__lte=hoy)
                      .select_related('responsable', 'usuario', 'oportunidad', 'tercero'))
                por_usuario = {}
                for a in qs:
                    u = a.a_cargo
                    if u is not None and u.is_active and u.email:
                        por_usuario.setdefault(u, []).append(a)
                enviados = 0
                for u, lista in por_usuario.items():
                    texto = '\n'.join(f'• {a.fecha:%d/%m}{a.hora and a.hora.strftime(" %H:%M") or ""} '
                                      f'{a.get_tipo_display()}: {a.descripcion} ({a.cliente})' for a in lista)
                    try:
                        enviar_simple([u.email], f'CRM: {len(lista)} actividad(es) para hoy', texto)
                    except ErrorCorreo as exc:
                        self.stderr.write(f'{alias}: {exc}')
                        break
                    ActividadCRM.objects.filter(pk__in=[a.pk for a in lista]).update(avisada=True)
                    enviados += 1
                self.stdout.write(f'{alias}: {enviados} correo(s) de recordatorio.')
            finally:
                emp.restaurar(token)
