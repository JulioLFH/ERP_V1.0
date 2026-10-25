"""Cobranza diaria (FIN-03), en todas las empresas: recordatorios por correo antes y después del vencimiento, revisión de
las promesas de pago vencidas y bloqueo o desbloqueo por mora. Programarlo una vez al día (Programador de tareas de
Windows o cron)."""
from django.core.management.base import BaseCommand

from erp import empresas as emp


class Command(BaseCommand):
    help = 'Recordatorios de cobranza, promesas vencidas y bloqueo por mora (todas las empresas).'

    def handle(self, *args, **opciones):
        from finanzas import cobranza
        emp.sincronizar(forzar=True)
        for alias in emp.empresas():
            token = emp.activar(alias)
            try:
                enviados, errores = cobranza.enviar_recordatorios()
                incumplidas = cobranza.revisar_promesas()
                bloqueados, desbloqueados = cobranza.actualizar_bloqueos()
                self.stdout.write(f'{alias}: {enviados} recordatorio(s), {errores} sin enviar, {len(incumplidas)} '
                                  f'promesa(s) incumplida(s), {bloqueados} cliente(s) bloqueado(s), {desbloqueados} '
                                  f'desbloqueado(s).')
            finally:
                emp.restaurar(token)
