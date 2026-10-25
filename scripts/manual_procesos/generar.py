"""Genera los manuales de Ajustes › Manuales: un PDF por módulo y uno completo (procesos por módulo) y las
Novedades por versión (del CHANGELOG).

Las pantallas salen del menú (core/modulos.py) y las acciones de core/permisos.py, así que siempre coinciden con la
versión; los procesos e introducciones están en contenido.py (actualícelos cuando cambie un flujo). Imprime con
Chrome o Edge sin ventana.

Uso: python scripts/manual_procesos/generar.py [carpeta_salida]   (por defecto Manuales/Procesos por módulo)
"""
import html
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'erp.settings')
import django  # noqa: E402

django.setup()
from django.conf import settings  # noqa: E402

from contenido import COMUNES, M  # noqa: E402
from core.modulos import MODULOS, RUTAS_ADMIN, RUTAS_COSTOS  # noqa: E402
from core.permisos import ACCIONES  # noqa: E402

NAVEGADORES = [r'C:\Program Files\Google\Chrome\Application\chrome.exe',
               r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
               r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
               '/usr/bin/google-chrome', '/usr/bin/chromium']
CHROME = next((n for n in NAVEGADORES if Path(n).exists()), None)
VERSION = settings.ERP_VERSION
FECHA = date.today().strftime('%d/%m/%Y')
SALIDA = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / 'Manuales' / 'Procesos por módulo'
TRABAJO = Path(tempfile.mkdtemp(prefix='manual_procesos_'))
ISOTIPO = (RAIZ / 'static' / 'img' / 'ceiba-isotipo.svg').read_text(encoding='utf-8')
ISOTIPO = ISOTIPO[ISOTIPO.index('<svg'):]

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700&display=swap');
:root { --selva:#0F3D2E; --hoja:#1F7A52; --hoja-clara:#2E9E66; --sol:#E3A72F; --bruma:#F4F6F2; --tinta:#1d2b25;
        --gris:#5f6f67; --linea:#d9e1dc; }
@page { size: A4; margin: 16mm 15mm 18mm 15mm;
  @bottom-left { content: "Ceiba ERP v__V__ · Manual de procesos por módulo"; font: 8pt 'Sora', 'Segoe UI', sans-serif; color: #5f6f67; }
  @bottom-right { content: "Página " counter(page) " de " counter(pages); font: 8pt 'Sora', 'Segoe UI', sans-serif; color: #5f6f67; } }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; font-family: 'Sora', 'Segoe UI', Arial, sans-serif; font-size: 9.6pt; line-height: 1.5; color: var(--tinta); background: #fff; }
.portada { background: var(--selva); color: #fff; border-radius: 10px; padding: 18px 22px; display: flex; gap: 18px; align-items: center; }
.portada svg { width: 74px; height: 64px; background: #fff; border-radius: 8px; padding: 6px; flex: none; }
.portada .serie { font-size: 8.5pt; letter-spacing: .08em; text-transform: uppercase; color: #c6d6cd; }
.portada h1 { margin: 2px 0 4px; font-size: 22pt; line-height: 1.15; }
.portada .desc { color: #e3ece7; font-size: 10pt; }
.portada .meta { margin-top: 6px; font-size: 8.5pt; color: var(--sol); }
.cifras { display: flex; gap: 10px; margin: 14px 0 4px; }
.cifra { flex: 1; background: var(--bruma); border-radius: 8px; padding: 8px 12px; border-left: 4px solid var(--hoja); }
.cifra b { display: block; font-size: 16pt; color: var(--selva); line-height: 1.2; }
.cifra span { font-size: 8.5pt; color: var(--gris); }
h2 { color: var(--selva); font-size: 13.5pt; margin: 20px 0 8px; padding-bottom: 4px; border-bottom: 2px solid var(--hoja); break-after: avoid; }
h2 small { color: var(--gris); font-weight: 400; font-size: 9pt; margin-left: 6px; }
h3 { font-size: 10.5pt; margin: 12px 0 4px; color: var(--hoja); break-after: avoid; }
p { margin: 4px 0 8px; }
.proceso { border: 1px solid var(--linea); border-radius: 8px; padding: 10px 14px 8px; margin: 0 0 10px; break-inside: avoid; }
.proceso h3 { margin-top: 0; display: flex; gap: 8px; align-items: baseline; }
.proceso h3 .n { background: var(--hoja); color: #fff; border-radius: 50%; width: 20px; height: 20px; font-size: 9pt; display: inline-flex; align-items: center; justify-content: center; flex: none; }
.proceso ol { margin: 4px 0 6px; padding-left: 20px; }
.proceso li { margin: 2px 0; }
.resultado { background: #fdf6e7; border-left: 3px solid var(--sol); padding: 4px 10px; border-radius: 0 6px 6px 0; font-size: 9pt; }
.resultado b { color: #8a5f0c; }
table { width: 100%; border-collapse: collapse; margin: 4px 0 10px; font-size: 9pt; }
thead { display: table-header-group; }
th { text-align: left; background: var(--bruma); color: var(--selva); font-weight: 600; padding: 5px 8px; border-bottom: 1px solid var(--linea); }
td { padding: 5px 8px; border-bottom: 1px solid var(--linea); vertical-align: top; }
tr { break-inside: avoid; }
td.nombre { width: 34%; font-weight: 600; }
td.codigo { width: 22%; font-family: Consolas, monospace; font-size: 8.5pt; color: var(--selva); }
.grupo td { background: #eef3ef; color: var(--hoja); font-weight: 700; font-size: 8.5pt; text-transform: uppercase; letter-spacing: .05em; }
.marca { display: inline-block; font-size: 7.5pt; padding: 0 6px; border-radius: 9px; margin-left: 4px; font-weight: 600; vertical-align: 1px; }
.m-admin { background: #e7ecf8; color: #34509a; }
.m-costo { background: #f6ecd8; color: #8a5f0c; }
.m-sens { background: #fbe4e1; color: #a23a2c; }
.m-normal { background: #e3f1e9; color: var(--hoja); }
.nota { color: var(--gris); font-size: 8.7pt; }
ul.integra { padding-left: 18px; margin: 4px 0; }
ul.integra li { margin: 2px 0; }
.salto { break-before: page; }
.indice { columns: 2; column-gap: 24px; padding: 0; list-style: none; }
.indice li { padding: 5px 0; border-bottom: 1px dotted var(--linea); break-inside: avoid; }
.indice b { color: var(--hoja); display: inline-block; width: 26px; }
""".replace('__V__', VERSION)


def e(texto):
    return html.escape(str(texto), quote=False)


def rico(texto):
    """Escapa todo salvo <b>…</b> (los textos del manual solo usan negrita)."""
    return e(texto).replace('&lt;b&gt;', '<b>').replace('&lt;/b&gt;', '</b>')


def ruta_base(destino):
    return destino.partition('?')[0]


def grupos_de(modulo):
    """[(grupo o None, [(etiqueta, destino)])] respetando el orden del menú."""
    grupos, sueltos = [], []
    for etiqueta, destino in modulo['menu']:
        if isinstance(destino, list):
            if sueltos:
                grupos.append((None, sueltos))
                sueltos = []
            grupos.append((etiqueta, [(x[0], x[1]) for x in destino]))
        else:
            sueltos.append((etiqueta, destino))
    if sueltos:
        grupos.append((None, sueltos))
    return grupos


SIN_DESCRIPCION = []


def descripcion(clave, etiqueta):
    texto = M[clave]['pantallas'].get(etiqueta) or COMUNES.get(etiqueta)
    if not texto:
        SIN_DESCRIPCION.append(f'{clave}: {etiqueta}')
        return ''
    return texto


def cuerpo_modulo(num, modulo):
    clave = modulo['clave']
    datos = M[clave]
    nombre = datos.get('titulo', modulo['nombre'])
    grupos = grupos_de(modulo)
    pantallas = sum(len(items) for _, items in grupos)
    acciones = ACCIONES.get(clave, [])
    partes = [f"""
<div class="portada">{ISOTIPO}
  <div><div class="serie">Ceiba ERP · Manual de procesos por módulo</div>
  <h1>{num}. {e(nombre)}</h1>
  <div class="desc">{e(modulo.get('descripcion', ''))}</div>
  <div class="meta">Versión {VERSION} · {FECHA}</div></div>
</div>
<div class="cifras">
  <div class="cifra"><b>{len(datos['procesos'])}</b><span>procesos</span></div>
  <div class="cifra"><b>{pantallas}</b><span>pantallas en el menú</span></div>
  <div class="cifra"><b>{len(acciones)}</b><span>acciones con permiso</span></div>
</div>
<h2>Para qué sirve</h2>
<p>{e(datos['intro'])}</p>
<h2>Procesos <small>qué se hace y en qué orden</small></h2>"""]
    for i, (titulo, pasos, resultado) in enumerate(datos['procesos'], 1):
        lis = ''.join(f'<li>{rico(p)}</li>' for p in pasos)
        partes.append(f'<div class="proceso"><h3><span class="n">{i}</span>{e(titulo)}</h3><ol>{lis}</ol>'
                      f'<div class="resultado"><b>Resultado:</b> {rico(resultado)}</div></div>')

    partes.append('<h2>Actividades <small>pantallas del menú del módulo</small></h2>'
                  '<table><thead><tr><th>Pantalla</th><th>Qué se hace</th></tr></thead><tbody>')
    for grupo, items in grupos:
        if grupo:
            partes.append(f'<tr class="grupo"><td colspan="2">{e(grupo)}</td></tr>')
        for etiqueta, destino in items:
            marcas = ''
            if ruta_base(destino) in RUTAS_ADMIN:
                marcas += '<span class="marca m-admin">solo administrador</span>'
            if ruta_base(destino) in RUTAS_COSTOS:
                marcas += '<span class="marca m-costo">requiere ver costos</span>'
            partes.append(f'<tr><td class="nombre">{e(etiqueta)}{marcas}</td><td>{e(descripcion(clave, etiqueta))}</td></tr>')
    partes.append('</tbody></table>')

    for titulo, filas in datos.get('extra', []):
        partes.append(f'<h3>{e(titulo)}</h3><table><thead><tr><th>Pantalla o función</th><th>Qué se hace</th></tr></thead><tbody>')
        partes.extend(f'<tr><td class="nombre">{e(a)}</td><td>{e(b)}</td></tr>' for a, b in filas)
        partes.append('</tbody></table>')

    partes.append('<h2>Acciones y permisos <small>lo que el administrador habilita a cada usuario</small></h2>')
    if acciones:
        partes.append('<table><thead><tr><th>Acción</th><th>Qué permite</th><th>Tipo</th></tr></thead><tbody>')
        for accion, desc, sensible in acciones:
            tipo = ('<span class="marca m-sens">sensible</span>' if sensible
                    else '<span class="marca m-normal">operativa</span>')
            partes.append(f'<tr><td class="codigo">{clave}.{e(accion)}</td><td>{e(desc)}</td><td>{tipo}</td></tr>')
        partes.append('</tbody></table><p class="nota">Con el módulo asignado el usuario abre sus pantallas; '
                      'registrar, aprobar o anular depende de las acciones marcadas en su usuario o en su perfil '
                      '(un usuario nuevo empieza sin acciones). Las acciones <b>sensibles</b> conviene darlas solo '
                      'a responsables y revisarlas en Ajustes › Segregación de funciones. Se asignan en '
                      'Ajustes › Usuarios o con un perfil en Ajustes › Perfiles de acceso.</p>')
    else:
        partes.append('<p>Este módulo no tiene acciones adicionales: basta con tener el módulo asignado '
                      '(Ajustes › Usuarios o Perfiles de acceso).</p>')

    partes.append('<h2>Se integra con</h2><ul class="integra">')
    partes.extend(f'<li><b>{e(a)}:</b> {e(b)}</li>' for a, b in datos.get('integra', []))
    partes.append('</ul>')
    return nombre, ''.join(partes)


def documento(titulo, cuerpo):
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{e(titulo)}</title>'
            f'<style>{CSS}</style></head><body>{cuerpo}</body></html>')


def imprimir(html_path, pdf_path):
    subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--no-pdf-header-footer', '--virtual-time-budget=8000',
                    f'--print-to-pdf={pdf_path}', html_path.as_uri()],
                   check=True, capture_output=True, timeout=120)


def novedades():
    """«Novedades por versión.pdf» (Manuales): todas las versiones del CHANGELOG, la más reciente primero."""
    from core.manuales import versiones
    lista = versiones('1.0')
    partes = [f"""
<div class="portada">{ISOTIPO}
  <div><div class="serie">Ceiba ERP</div><h1>Novedades por versión</h1>
  <div class="desc">Qué cambió en el sistema en cada versión (de la más reciente a la primera).</div>
  <div class="meta">Versión {VERSION} · {FECHA} · {len(lista)} versiones</div></div>
</div>"""]
    for v in lista:  # los textos ya vienen escapados por versiones() (solo <b>)
        items = ''.join(f'<li>{i}</li>' for i in v['items'])
        partes.append(f'<div class="proceso"><h3><span class="n" style="width:auto;border-radius:10px;padding:0 8px">'
                      f'v{e(v["version"])}</span>{e(v["titulo"])} <small class="nota">· {e(v["fecha"])}</small></h3>'
                      f'<ul>{items}</ul></div>')
    h = TRABAJO / 'novedades.html'
    h.write_text(documento('Novedades por versión — Ceiba ERP', ''.join(partes)), encoding='utf-8')
    imprimir(h, RAIZ / 'Manuales' / 'Novedades por versión.pdf')
    print('ok novedades')


def main():
    if not CHROME:
        sys.exit('No se encontró Chrome ni Edge para imprimir los PDF.')
    SALIDA.mkdir(parents=True, exist_ok=True)
    for viejo in SALIDA.glob('[0-9][0-9]. *.pdf'):  # la numeración cambia si se agrega un módulo
        viejo.unlink()
    cuerpos, indice = [], []
    for num, modulo in enumerate(MODULOS, 1):
        nombre, cuerpo = cuerpo_modulo(num, modulo)
        archivo = f'{num:02d}. {nombre}'
        h = TRABAJO / f'{num:02d}.html'
        h.write_text(documento(f'{num}. {nombre} — Ceiba ERP', cuerpo), encoding='utf-8')
        imprimir(h, SALIDA / f'{archivo}.pdf')
        print('ok', archivo)
        cuerpos.append(f'<div class="salto">{cuerpo}</div>')
        indice.append(f'<li><b>{num}.</b>{e(nombre)} <span class="nota">— {e(modulo.get("descripcion", ""))}</span></li>')

    portada = f"""
<div class="portada">{ISOTIPO}
  <div><div class="serie">Ceiba ERP</div><h1>Manual de procesos por módulo</h1>
  <div class="desc">Procesos, actividades (pantallas) y acciones con permiso de cada módulo.</div>
  <div class="meta">Versión {VERSION} · {FECHA}</div></div>
</div>
<h2>Contenido</h2><ul class="indice">{''.join(indice)}</ul>
<h2>Cómo leer este manual</h2>
<p><b>Procesos</b>: los flujos de trabajo del módulo paso a paso, con el resultado que dejan en el sistema.
<b>Actividades</b>: cada pantalla del menú y para qué sirve. <b>Acciones y permisos</b>: lo que el administrador
habilita a cada usuario además del acceso al módulo; las sensibles requieren más control.
<b>Se integra con</b>: qué recibe o envía a otros módulos.</p>"""
    h = TRABAJO / '00.html'
    h.write_text(documento('Manual de procesos por módulo — Ceiba ERP', portada + ''.join(cuerpos)), encoding='utf-8')
    imprimir(h, SALIDA / '00. Manual completo (todos los módulos).pdf')
    print('ok completo')
    novedades()
    shutil.rmtree(TRABAJO, ignore_errors=True)
    if SIN_DESCRIPCION:
        print('SIN DESCRIPCION:', *sorted(set(SIN_DESCRIPCION)), sep='\n  ')


main()
