"""Servidor de producción de Ceiba ERP: varios procesos de waitress atendiendo el mismo puerto.

    .venv\\Scripts\\python.exe servir.py --puerto 8030 --settings=erp.settings_postgres [--procesos 4] [--hilos 8]

- Un proceso por núcleo (hasta --procesos): `runserver` usaba uno solo de los núcleos del equipo. El proceso
  principal abre el puerto y lo comparte con los procesos que atienden (Windows reparte las visitas entre ellos), así
  que IIS o el navegador siguen entrando por el mismo puerto.
- Si un proceso se cae, se levanta otro.
- Cambios en el código (.py, plantillas, estilos y scripts): se reinician los procesos de a uno — el nuevo empieza a
  atender antes de que el anterior deje de hacerlo y el anterior termina lo que tenía en curso —, así una
  actualización no corta a los usuarios. Si cambian los archivos de static/ primero se corre collectstatic.
  Las migraciones NO se aplican solas: se aplican con iniciar.bat (o manage.py migrate) antes de subir el código que
  las usa.
"""
import argparse
import multiprocessing as mp
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
VIGILAR = ('.py', '.html', '.txt', '.css', '.js', '.svg', '.json')
EXCLUIR = {'.venv', 'staticfiles', '__pycache__', '.git', 'media', 'logs', 'respaldos', 'node_modules', '.cache_ceiba',
           'migracion_local', 'docs'}
ESPERA_FIN = 60  # segundos que un proceso viejo tiene para terminar lo que estaba atendiendo


def log(texto):
    print(f'[{time.strftime("%H:%M:%S")}] {texto}', flush=True)


# ---------------------------------------------------------------- proceso que atiende
def atender(conexion, settings, hilos, n):
    """Recibe el puerto compartido y atiende con waitress hasta que el principal le pida terminar."""
    os.environ['DJANGO_SETTINGS_MODULE'] = settings
    sys.path.insert(0, str(BASE))
    os.chdir(BASE)
    sock = socket.fromshare(conexion.recv())
    import logging
    logging.getLogger('waitress.queue').setLevel(logging.ERROR)  # «Task queue depth»: normal con mucha carga
    import django
    django.setup()
    from django.core.wsgi import get_wsgi_application
    from waitress.server import create_server
    django_app = get_wsgi_application()
    cerrando = threading.Event()

    def app(environ, start_response):
        """Al terminar, cada respuesta cierra su conexión: el navegador (o IIS) abre la próxima y esa la atiende otro
        proceso. (WSGI no deja poner «Connection: close»; sin largo fijo, waitress la cierra al terminar de enviar.)"""
        if not cerrando.is_set():
            return django_app(environ, start_response)

        def sin_largo(status, headers, exc_info=None):
            return start_response(status, [(k, v) for k, v in headers if k.lower() != 'content-length'], exc_info)
        respuesta = django_app(environ, sin_largo)

        def partes():
            try:
                yield from respuesta
            finally:
                if hasattr(respuesta, 'close'):
                    respuesta.close()
        return partes()

    servidor = create_server(app, sockets=[sock], threads=hilos, ident='Ceiba ERP',
                             channel_timeout=300, connection_limit=1000, cleanup_interval=30,
                             clear_untrusted_proxy_headers=False,  # IIS manda X-Forwarded-*: llegan como con runserver
                             asyncore_use_poll=True)
    conexion.send('listo')

    def esperar_fin():
        try:
            conexion.recv()  # cualquier mensaje (o el cierre del principal) = terminar
        except (EOFError, OSError):
            pass
        cerrando.set()
        servidor.accepting = False  # no toma visitas nuevas (el puerto sigue abierto para los demás procesos)
        tareas = servidor.task_dispatcher
        inicio = time.time()
        libre_desde = None
        while time.time() - inicio < ESPERA_FIN:
            ocupado = tareas.active_count or tareas.queue
            conexiones = len(servidor._map) - 1  # sin contar el puerto
            if not ocupado and conexiones <= 0:
                break
            # sin nada en curso: un par de segundos para que las conexiones abiertas pidan algo y reciban el cierre
            libre_desde = None if ocupado else (libre_desde or time.time())
            if libre_desde and time.time() - libre_desde >= 3:
                break
            time.sleep(0.2)
        os._exit(0)
    threading.Thread(target=esperar_fin, daemon=True).start()
    servidor.run()


# ---------------------------------------------------------------- proceso principal
class Trabajador:
    def __init__(self, sock, settings, hilos, n):
        self.n = n
        self.conexion, otra = mp.Pipe()
        self.proceso = mp.Process(target=atender, args=(otra, settings, hilos, n), daemon=True,
                                  name=f'ceiba-{n}')
        self.proceso.start()
        self.conexion.send(sock.share(self.proceso.pid))
        try:  # si el código nuevo tiene un error, el proceso termina y la conexión se cierra
            self.listo = self.conexion.poll(180) and self.conexion.recv() == 'listo'
        except (EOFError, OSError):
            self.listo = False

    def terminar(self):
        try:
            self.conexion.send('fin')
        except OSError:
            pass

    def vivo(self):
        return self.proceso.is_alive()


def huella(carpeta):
    """La fecha de modificación más reciente del código y de static/ (para detectar cambios)."""
    codigo = estaticos = 0.0
    for raiz, dirs, archivos in os.walk(carpeta):
        dirs[:] = [d for d in dirs if d not in EXCLUIR and not d.startswith('.')]
        en_static = Path(raiz).relative_to(carpeta).parts[:1] == ('static',)
        for a in archivos:
            if a.endswith(VIGILAR):
                try:
                    m = os.stat(os.path.join(raiz, a)).st_mtime
                except OSError:
                    continue
                if en_static:
                    estaticos = max(estaticos, m)
                else:
                    codigo = max(codigo, m)
    return codigo, estaticos


def collectstatic(settings):
    r = subprocess.run([sys.executable, 'manage.py', 'collectstatic', '--no-input', f'--settings={settings}'],
                       cwd=BASE, capture_output=True, text=True)
    if r.returncode:
        log(f'collectstatic falló (se sigue con los archivos anteriores): {r.stderr.strip()[-500:]}')
    return r.returncode == 0


def principal():
    p = argparse.ArgumentParser(description='Servidor de producción de Ceiba ERP')
    p.add_argument('--puerto', type=int, default=int(os.environ.get('PUERTO', 8030)))
    p.add_argument('--host', default='0.0.0.0')
    p.add_argument('--procesos', type=int, default=int(os.environ.get('PROCESOS') or min(4, os.cpu_count() or 1)))
    p.add_argument('--hilos', type=int, default=int(os.environ.get('HILOS') or 8))
    p.add_argument('--settings', default=os.environ.get('DJANGO_SETTINGS_MODULE', 'erp.settings_postgres'))
    p.add_argument('--sin-recarga', action='store_true', help='no reiniciar los procesos al cambiar el código')
    a = p.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform != 'win32':
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((a.host, a.puerto))
    sock.listen(1024)
    sock.setblocking(False)

    log(f'Ceiba ERP: {a.procesos} procesos x {a.hilos} hilos en el puerto {a.puerto} ({a.settings})')
    trabajadores = []
    for n in range(1, a.procesos + 1):
        trabajadores.append(Trabajador(sock, a.settings, a.hilos, n))
    log(f'Listo: {sum(t.listo for t in trabajadores)} de {a.procesos} procesos atendiendo. No cierre esta ventana.')

    siguiente = a.procesos + 1
    ultima = huella(BASE)
    try:
        while True:
            time.sleep(2)
            for i, t in enumerate(trabajadores):  # proceso caído: se reemplaza
                if not t.vivo():
                    log(f'El proceso {t.n} se detuvo (código {t.proceso.exitcode}): se inicia otro.')
                    trabajadores[i] = Trabajador(sock, a.settings, a.hilos, siguiente)
                    siguiente += 1
            if a.sin_recarga:
                continue
            ahora = huella(BASE)
            if ahora == ultima:
                continue
            time.sleep(2)  # que termine de copiarse el cambio (git pull, varios archivos)
            ahora = huella(BASE)
            if ahora[1] != ultima[1]:
                log('Cambiaron los estilos o scripts: collectstatic…')
                collectstatic(a.settings)
            ultima = ahora
            log('Cambió el código: se reinician los procesos de a uno (sin cortar a los usuarios).')
            for i, viejo in enumerate(list(trabajadores)):
                nuevo = Trabajador(sock, a.settings, a.hilos, siguiente)
                siguiente += 1
                if not nuevo.listo:
                    log('El código nuevo no arrancó (revise el error de arriba): se mantienen los procesos actuales.')
                    nuevo.proceso.kill()
                    break
                trabajadores[i] = nuevo
                viejo.terminar()
            else:
                log('Procesos actualizados.')
    except KeyboardInterrupt:
        log('Deteniendo Ceiba ERP…')
        for t in trabajadores:
            t.terminar()
        for t in trabajadores:
            t.proceso.join(ESPERA_FIN)


if __name__ == '__main__':
    mp.freeze_support()
    principal()
