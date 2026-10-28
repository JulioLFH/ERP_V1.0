"""Íconos 3D de los módulos (pantalla de Inicio): un solo objeto grande por módulo, con la forma del ícono de siempre
(cono en Obras, camión en Logística, engranaje en Ajustes…) y volumen: la figura de frente con su espesor hacia
arriba a la derecha, caras laterales sombreadas y una sombra suave en el piso. El color del módulo es el del objeto.

Genera static/img/modulos/<clave>.svg. Uso: python scripts/iconos_modulos.py
Coordenadas de dibujo: 0 a 100, con y hacia abajo. El marco (viewBox) se ajusta a cada objeto para que llene el ícono."""
import math
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

ESPESOR = (6.5, -6.5)  # hacia dónde va el volumen (arriba a la derecha)
OSCURO, BLANCO, CLARO, ORO, VIDRIO = '#24332c', '#ffffff', '#e3eae6', '#f0b93a', '#d7ece4'


def tono(color, f):
    """Aclara (f > 0, hacia el blanco) u oscurece (f < 0, hacia el negro) un color #rrggbb."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    if f >= 0:
        r, g, b = (int(c + (255 - c) * f) for c in (r, g, b))
    else:
        r, g, b = (int(c * (1 + f)) for c in (r, g, b))
    return f'#{r:02x}{g:02x}{b:02x}'


# ---------------------------------------------------------------------------------------------------- formas 2D
def circ(cx, cy, r, a0=0, a1=360, n=None, ry=None):
    n = n or max(int(abs(a1 - a0) / 7.5), 6)
    ry = r if ry is None else ry
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + (0 if a1 - a0 == 360 else 1))]


def rect(x, y, w, h):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def rrect(x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    return (circ(x + w - r, y + r, r, -90, 0, 6) + circ(x + w - r, y + h - r, r, 0, 90, 6) +
            circ(x + r, y + h - r, r, 90, 180, 6) + circ(x + r, y + r, r, 180, 270, 6))


def girar(pts, ang, cx=50, cy=50):
    c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in pts]


def engranaje(cx, cy, r, dientes, alto):
    pts = []
    for k in range(dientes * 4):
        t = 2 * math.pi * (k + .5) / (dientes * 4)
        rr = r + (alto if k % 4 in (1, 2) else 0)
        pts.append((cx + rr * math.cos(t), cy + rr * math.sin(t)))
    return pts


def estrella(cx, cy, r1, r2, puntas=5):
    return [(cx + (r1 if k % 2 == 0 else r2) * math.sin(math.pi * k / puntas),
             cy - (r1 if k % 2 == 0 else r2) * math.cos(math.pi * k / puntas)) for k in range(2 * puntas)]


def _dentro(anillos, x, y):
    dentro = False
    for anillo in anillos:
        n = len(anillo)
        for i in range(n):
            (x0, y0), (x1, y1) = anillo[i], anillo[(i + 1) % n]
            if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                dentro = not dentro
    return dentro


def _txt(pts):
    return ' '.join(f'{x:.2f},{y:.2f}' for x, y in pts)


class Icono:
    def __init__(self, acento):
        self.a = acento
        self.el, self.defs, self.puntos = [], [], []

    def _grad(self, c1, c2, x2=1, y2=1):
        gid = f'g{len(self.defs) + 1}'
        self.defs.append(f'<linearGradient id="{gid}" x1="0" y1="0" x2="{x2}" y2="{y2}">'
                         f'<stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient>')
        return f'url(#{gid})'

    def solido(self, anillos, color, prof=1.0):
        """Figura plana (con huecos si hay varios anillos) con volumen: lados visibles sombreados y el frente con un
        degradado suave."""
        if isinstance(anillos[0][0], (int, float)):
            anillos = [anillos]
        ox, oy = ESPESOR[0] * prof, ESPESOR[1] * prof
        lados = []
        for anillo in anillos:
            n = len(anillo)
            for i in range(n):
                (x0, y0), (x1, y1) = anillo[i], anillo[(i + 1) % n]
                largo = math.hypot(x1 - x0, y1 - y0)
                if largo < 1e-6:
                    continue
                nx, ny = (y1 - y0) / largo, -(x1 - x0) / largo
                mx, my = (x0 + x1) / 2, (y0 + y1) / 2
                if _dentro(anillos, mx + nx * .4, my + ny * .4):
                    nx, ny = -nx, -ny
                if nx * ox + ny * oy <= 1e-6:
                    continue
                f = -.08 - .26 * max(nx, 0) + .04 * max(-ny, 0)
                lados.append((mx * ox + my * oy, [(x0, y0), (x1, y1), (x1 + ox, y1 + oy), (x0 + ox, y0 + oy)],
                              tono(color, f)))
                self.puntos += [(x0 + ox, y0 + oy)]
        for _, pts, col in sorted(lados, key=lambda q: -q[0]):
            self.el.append(f'<polygon points="{_txt(pts)}" fill="{col}" stroke="{col}" stroke-width=".35" '
                           f'stroke-linejoin="round"/>')
        d = ' '.join('M' + ' L'.join(f'{x:.2f},{y:.2f}' for x, y in a) + ' Z' for a in anillos)
        self.el.append(f'<path d="{d}" fill="{self._grad(tono(color, .2), tono(color, -.06))}" fill-rule="evenodd"/>')
        for a in anillos:
            self.puntos += a

    def plano(self, pts, color, extra=''):
        """Detalle pintado sobre el frente (sin volumen)."""
        self.puntos += pts
        self.el.append(f'<polygon points="{_txt(pts)}" fill="{color}"{extra}/>')

    def circulo(self, cx, cy, r, color, extra=''):
        self.puntos += [(cx - r, cy - r), (cx + r, cy + r)]
        self.el.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="{color}"{extra}/>')

    def trazo(self, pts, color, ancho, extra=''):
        self.puntos += pts
        self.el.append(f'<polyline points="{_txt(pts)}" fill="none" stroke="{color}" stroke-width="{ancho}" '
                       f'stroke-linecap="round" stroke-linejoin="round"{extra}/>')

    def redondo(self, pts, color):
        """Cuerpo de revolución visto de frente (cono, cilindro): degradado de izquierda (luz) a derecha (sombra)."""
        self.puntos += pts
        g = self._grad(tono(color, .32), tono(color, -.3), 1, 0)
        self.el.append(f'<polygon points="{_txt(pts)}" fill="{g}"/>')

    def svg(self, titulo):
        xs, ys = [p[0] for p in self.puntos], [p[1] for p in self.puntos]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        ancho = x1 - x0
        sombra = (f'<ellipse cx="{(x0 + x1) / 2 - 2:.2f}" cy="{y1 + 1.5:.2f}" rx="{ancho * .42:.2f}" '
                  f'ry="{max(ancho * .07, 2.5):.2f}" fill="url(#sombra)"/>')
        y1 += 5
        lado = max(x1 - x0, y1 - y0) + 6
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        defs = ('<radialGradient id="sombra"><stop offset="0" stop-color="#0f3d2e" stop-opacity=".28"/>'
                '<stop offset="1" stop-color="#0f3d2e" stop-opacity="0"/></radialGradient>' + ''.join(self.defs))
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{cx - lado / 2:.1f} {cy - lado / 2:.1f} '
                f'{lado:.1f} {lado:.1f}" role="img" aria-label="{titulo}"><defs>{defs}</defs>{sombra}'
                + ''.join(self.el) + '</svg>\n')


# ---------------------------------------------------------------------------------------------------- objetos
def i_obras(i):  # cono de tránsito
    i.solido(rect(14, 80, 72, 9), tono(i.a, -.15), .9)
    ap, base, rx, ry = (50, 8), 82, 26, 6.5

    def anillo(t):
        return 50 - rx * t, 50 + rx * t, 8 + (base - 8) * t

    i.redondo([ap] + circ(50, base, rx, 180, 0, 24, ry)[::-1][::-1], i.a)
    for t0, t1 in ((.3, .45), (.62, .78)):
        _, _, y0 = anillo(t0)
        _, _, y1 = anillo(t1)
        banda = circ(50, y1, rx * t1, 0, 180, 18, ry * t1) + circ(50, y0, rx * t0, 180, 0, 18, ry * t0)
        i.redondo(banda, '#f4f6f5')
    i.plano([(46, 14), (48.5, 12), (43, 44), (39, 46)], BLANCO, ' fill-opacity=".35"')


def i_tablero(i):  # velocímetro
    i.solido(circ(50, 64, 40, 180, 360, 30) + [(90, 74), (10, 74)], i.a)
    for k in range(7):
        t = math.radians(195 + k * 25)
        i.trazo([(50 + 29 * math.cos(t), 64 + 29 * math.sin(t)), (50 + 35 * math.cos(t), 64 + 35 * math.sin(t))],
                BLANCO, 3.4)
    i.plano([(47, 66), (53, 62), (75, 38)], OSCURO)
    i.circulo(50, 64, 7, OSCURO)
    i.circulo(50, 64, 2.6, BLANCO)


def i_crm(i):  # tablero kanban
    i.solido(rrect(12, 16, 76, 66, 9), i.a)
    for col, tarjetas in enumerate((2, 3, 1)):
        x = 20 + col * 21.5
        for k in range(tarjetas):
            i.plano(rrect(x, 25 + k * 16, 17, 12, 3), BLANCO, ' fill-opacity=".92"')


def i_toma_pedido(i):  # celular
    i.solido(rrect(28, 6, 44, 86, 9), '#2b3a33')
    i.plano(rrect(32, 15, 36, 66, 3), tono(i.a, .1))
    for k in range(4):
        i.plano(rrect(36, 21 + k * 14, 28, 10, 2.5), BLANCO)
        i.plano(rrect(38.5, 24 + k * 14, 5, 4, 1.5), i.a)
    i.plano(rrect(44, 9.5, 12, 2.6, 1.3), '#5b6b64')
    i.plano(rrect(43, 84.5, 14, 2.6, 1.3), '#5b6b64')


def i_ventas(i):  # etiqueta de precio
    cuerpo = [(34, 20), (86, 20), (86, 76), (34, 76), (12, 48)]
    i.solido([cuerpo, circ(30, 48, 5.5, n=24)], i.a)
    for k, w in enumerate((36, 26, 30)):
        i.plano(rrect(44, 34 + k * 11, w, 5.5, 2.75), BLANCO)


def i_compras(i):  # bolsa
    asa = circ(50, 36, 17, 180, 360, 20) + circ(50, 36, 11, 360, 180, 20)
    i.solido(asa, tono(i.a, -.25), .55)
    i.solido(rrect(17, 34, 66, 56, 6), i.a)
    for x in (37, 63):
        i.circulo(x, 44, 3, tono(i.a, -.35))
    i.plano(rrect(36, 58, 28, 6, 3), BLANCO, ' fill-opacity=".85"')


def i_portal_proveedores(i):  # tienda
    i.solido(rect(18, 40, 64, 48), tono(i.a, .15))
    toldo = [(10, 24), (90, 24)] + [p for k in range(8) for p in circ(90 - 10 * k - 5, 40, 5, 0, 180, 8)]
    i.solido(toldo, i.a, .8)
    for k in range(0, 8, 2):
        x = 10 + 10 * k
        i.plano([(x, 24), (x + 10, 24)] + circ(x + 5, 40, 5, 0, 180, 8)[::-1], BLANCO)
    i.plano(rrect(27, 56, 18, 32, 2), '#f5f8f6')
    i.plano(rrect(52, 54, 22, 18, 2), VIDRIO)


def i_inventario(i):  # cajas apiladas
    for x, y in ((14, 52), (50, 52), (32, 16)):
        i.solido(rect(x, y, 34, 34), i.a, .85)
        i.plano(rect(x + 14, y, 6, 34), BLANCO, ' fill-opacity=".75"')
        i.plano(rect(x + 4, y + 25, 9, 5), BLANCO, ' fill-opacity=".9"')


def i_codigo_barras(i):  # etiqueta con barras
    i.solido(rrect(12, 20, 76, 60, 7), i.a)
    i.plano(rrect(20, 28, 60, 44, 3), BLANCO)
    x = 26
    for k, w in enumerate((3, 1.5, 4, 2, 1.5, 3.5, 2, 3, 1.5, 4, 2, 3, 1.5, 3.5)):
        if k % 2 == 0:
            i.plano(rect(x, 33, w, 26), OSCURO)
        x += w + 1.4
    i.plano(rrect(26, 62, 48, 4, 2), '#b8c4bf')


def i_manufactura(i):  # engranaje
    i.solido([engranaje(50, 50, 30, 9, 8), circ(50, 50, 11, n=32)], i.a)
    i.plano(circ(50, 50, 20, n=40), BLANCO, ' fill-opacity=".14"')


def i_planificacion(i):  # diagrama
    for r in (rect(48, 34, 4, 14), rect(24, 46, 52, 4), rect(24, 46, 4, 12), rect(72, 46, 4, 12)):
        i.solido(r, '#7b8a84', .45)
    i.solido(rrect(32, 10, 36, 24, 5), i.a)
    for x in (8, 54):
        i.solido(rrect(x, 58, 38, 24, 5), tono(i.a, .25))
        i.plano(rrect(x + 7, 67, 24, 5, 2.5), BLANCO)
    i.plano(rrect(40, 19, 20, 5, 2.5), BLANCO)


def portapapeles(i, color):
    i.solido(rrect(20, 14, 60, 78, 7), color)
    i.plano(rrect(27, 24, 46, 61, 3), BLANCO)
    i.solido(rrect(37, 7, 26, 13, 4), OSCURO, .45)


def i_control_calidad(i):  # portapapeles con visto bueno
    portapapeles(i, i.a)
    i.trazo([(36, 55), (46, 66), (65, 41)], i.a, 7.5)


def i_presupuestos(i):  # portapapeles con barras
    portapapeles(i, i.a)
    for k, h in enumerate((16, 28, 22, 38)):
        i.plano(rect(32 + k * 10, 78 - h, 6.5, h), i.a if k == 3 else tono(i.a, .45))


def i_requerimientos(i):  # portapapeles con «más»
    portapapeles(i, i.a)
    i.plano(rrect(46, 38, 8, 34, 3), i.a)
    i.plano(rrect(33, 51, 34, 8, 3), i.a)


def i_mantenimiento(i):  # llave
    mango = rrect(36, 43, 56, 14, 7)
    cabeza = circ(24, 50, 19, 200, 520, 36) + [(15, 56.5), (15, 43.5)]
    i.solido([girar(mango, 45), girar(circ(83, 50, 3.5, n=16), 45)], i.a)
    i.solido(girar(cabeza, 45), i.a)


def i_costos(i):  # calculadora
    i.solido(rrect(22, 8, 56, 84, 8), i.a)
    i.plano(rrect(29, 15, 42, 17, 3), '#dff3e8')
    i.plano(rrect(46, 21, 20, 5, 2), OSCURO)
    for a in range(3):
        for b in range(4):
            col = ORO if (a, b) == (2, 3) else BLANCO
            i.plano(rrect(29 + a * 14.5, 38 + b * 12.5, 11.5, 9.5, 2.5), col)


def i_activos(i):  # edificio con engranaje
    i.solido(rect(20, 12, 46, 76), i.a)
    for a in range(3):
        for b in range(5):
            i.plano(rect(26 + a * 13, 19 + b * 11.5, 8, 7), BLANCO, ' fill-opacity=".9"')
    i.plano(rect(37, 76, 12, 12), OSCURO)
    i.solido([engranaje(72, 72, 13, 7, 4.5), circ(72, 72, 5, n=20)], '#5d6d66', .6)


def i_proyectos(i):  # lista con vistos
    i.solido(rrect(14, 14, 72, 72, 10), i.a)
    for k in range(3):
        y = 31 + k * 19
        i.trazo([(24, y), (28.5, y + 4.5), (36, y - 4)], BLANCO, 3.8)
        i.plano(rrect(42, y - 3, 34 if k != 1 else 26, 6, 3), BLANCO)


def i_sst(i):  # corazón con pulso
    pts = []
    for k in range(60):
        t = 2 * math.pi * k / 60
        pts.append((50 + 2.4 * 16 * math.sin(t) ** 3,
                    44 - 2.4 * (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))))
    i.solido(pts, i.a)
    i.trazo([(20, 48), (36, 48), (42, 36), (51, 62), (58, 42), (63, 48), (80, 48)], BLANCO, 4.2)


def i_calidad(i):  # medalla
    for lado in (1, -1):
        cinta = [(50 - 14 * lado, 56), (50 - 2 * lado, 60), (50 - 8 * lado, 94), (50 - 15 * lado, 86),
                 (50 - 24 * lado, 90)]
        i.solido(cinta if lado == 1 else cinta[::-1], tono(i.a, -.15), .5)
    i.solido(circ(50, 38, 28, n=48), ORO)
    i.plano(circ(50, 38, 20, n=40), tono(ORO, .25))
    i.plano(estrella(50, 39, 12, 5), i.a)


def i_planillas(i):  # fotocheck
    i.solido([rrect(24, 8, 52, 84, 8), rrect(42, 14, 16, 4.5, 2.2)], i.a)
    i.circulo(50, 40, 10, BLANCO)
    i.plano(circ(50, 66, 17, 180, 360, 20, 11), BLANCO)
    i.plano(rrect(34, 72, 32, 4.5, 2.2), BLANCO)
    i.plano(rrect(39, 80, 22, 4.5, 2.2), BLANCO, ' fill-opacity=".8"')


def i_empleados(i):  # tarjeta de empleado
    i.solido(rrect(8, 20, 84, 60, 8), i.a)
    i.circulo(30, 42, 8.5, BLANCO)
    i.plano(circ(30, 64, 14, 180, 360, 20, 9.5), BLANCO)
    for k, w in enumerate((32, 24, 28)):
        i.plano(rrect(50, 36 + k * 11, w, 5.5, 2.7), BLANCO)


def i_tiempo_personal(i):  # sombrilla
    gancho = circ(41, 82, 10, 0, 180, 14) + circ(41, 82, 5.5, 180, 0, 14)
    i.solido(rect(48, 46, 5, 36), '#45544e', .4)
    i.solido(gancho, '#45544e', .4)
    lona = circ(50, 50, 40, 180, 360, 30) + [p for k in range(4) for p in circ(80 - 20 * k, 50, 10, 0, -180, 10)]
    i.solido(lona, i.a)
    for k in (1, 3):
        x = 10 + 20 * k
        i.plano([(50, 10)] + circ(x + 10, 50, 10, -180, 0, 10), BLANCO, ' fill-opacity=".3"')


def i_rendiciones(i):  # recibo y moneda
    dientes = [(76 - 6.5 * k, 84 if k % 2 == 0 else 78) for k in range(9)]
    i.solido([(24, 8), (76, 8)] + dientes + [(24, 84)], i.a)
    for k, w in enumerate((36, 28, 32)):
        i.plano(rrect(32, 20 + k * 10, w, 5, 2.5), BLANCO)
    i.trazo([(30, 58), (70, 58)], BLANCO, 2.2, ' stroke-dasharray="4 4"')
    i.solido(circ(72, 72, 14, n=40), ORO, .6)
    i.plano(circ(72, 72, 9, n=30), tono(ORO, .3))


def i_logistica(i):  # camión
    i.solido(rrect(6, 22, 58, 46, 4), i.a)
    i.plano(rrect(12, 38, 46, 8, 3), BLANCO, ' fill-opacity=".85"')
    i.solido([(66, 36), (82, 36), (94, 52), (94, 68), (66, 68)], tono(i.a, -.12))
    i.plano([(70, 41), (80, 41), (88, 52), (70, 52)], VIDRIO)
    i.solido(rect(6, 68, 88, 5), OSCURO, .4)
    for x in (22, 46, 80):
        i.solido(circ(x, 76, 9.5, n=32), '#2e3b35', .55)
        i.plano(circ(x, 76, 4, n=20), '#aab5b0')


def i_distribucion(i):  # marcador de ubicación
    i.solido(circ(50, 38, 30, 145, 395, 40) + [(50, 94)], i.a)
    i.circulo(50, 38, 12, BLANCO)


def i_flota(i):  # auto de frente
    for x in (16, 70):
        i.solido(rrect(x, 70, 14, 16, 3), OSCURO, .5)
    i.solido([(27, 26), (73, 26), (85, 50), (15, 50)], tono(i.a, .1))
    i.solido(rrect(10, 46, 80, 28, 9), i.a)
    i.plano([(31, 31), (69, 31), (77, 47), (23, 47)], VIDRIO)
    for x in (24, 76):
        i.circulo(x, 59, 5.5, BLANCO)
    i.plano(rrect(38, 56, 24, 8, 3), tono(i.a, -.35))


def banco(i, color, bandera=False):
    i.solido(rect(10, 80, 80, 9), '#c6d0cb', .8)
    for k in range(4):
        i.solido(rect(18 + k * 18.3, 44, 9, 36), '#f2f5f3', .6)
    i.solido(rect(12, 36, 76, 8), color)
    i.solido([(8, 36), (50, 12), (92, 36)], color)
    i.plano([(32, 31), (50, 21), (68, 31)], BLANCO, ' fill-opacity=".35"')
    if bandera:
        i.solido(rect(49, 0, 2.5, 14), OSCURO, .3)
        for k, col in enumerate((color, BLANCO, color)):
            i.plano(rect(51.5 + k * 6, 1, 6, 9), col)


def i_tesoreria(i):  # banco
    banco(i, i.a)


def i_sunat(i):  # edificio público con bandera
    banco(i, i.a, bandera=True)


def i_finanzas(i):  # chanchito
    for x in (26, 58):
        i.solido(rrect(x, 72, 12, 16, 3), tono(i.a, -.1), .6)
    i.solido([(30, 36), (37, 18), (47, 32)], tono(i.a, -.05), .6)
    i.solido(circ(48, 56, 35, n=56, ry=27), i.a)
    i.solido(rrect(78, 47, 13, 17, 6), tono(i.a, .15), .5)
    i.circulo(83, 53, 1.6, tono(i.a, -.4))
    i.circulo(83, 58.5, 1.6, tono(i.a, -.4))
    i.circulo(68, 46, 2.8, OSCURO)
    i.plano(rrect(38, 33, 20, 4, 2), tono(i.a, -.45))
    i.solido(circ(48, 17, 11, n=36), ORO, .55)
    i.plano(circ(48, 17, 6.5, n=24), tono(ORO, .3))


def i_contabilidad(i):  # libro con marcador
    i.solido(rrect(27, 12, 54, 78, 4), '#f2f5f3', .8)
    i.solido(rrect(20, 8, 56, 80, 5), i.a)
    i.plano(rrect(20, 8, 10, 80, 4), tono(i.a, -.25))
    i.plano(rrect(38, 28, 30, 12, 2.5), BLANCO)
    i.plano([(56, 8), (68, 8), (68, 36), (62, 30), (56, 36)], ORO)


def i_cliente(i):  # persona frente al monitor
    i.solido(rrect(26, 8, 64, 46, 6), '#2b3a33')
    i.plano(rrect(30, 12, 56, 36, 3), tono(i.a, .35))
    i.solido(rect(54, 54, 8, 9), '#2b3a33', .5)
    i.solido(circ(34, 96, 24, 180, 360, 24, 24), i.a, .8)
    i.solido(circ(34, 60, 12, n=36), i.a, .8)


def i_contactos(i):  # dos personas
    i.solido(circ(66, 76, 21, 180, 360, 24, 19), tono(i.a, .35), .7)
    i.solido(circ(66, 36, 12, n=36), tono(i.a, .35), .7)
    i.solido(circ(38, 92, 25, 180, 360, 24, 23), i.a, .8)
    i.solido(circ(38, 46, 14, n=36), i.a, .8)


def i_consulta_ruc(i):  # lupa
    i.solido(girar(rrect(62, 44, 34, 12, 6), 45, 62, 50), OSCURO, .6)
    i.solido([circ(40, 40, 30, n=48), circ(40, 40, 20, n=40)], i.a)
    i.plano(circ(40, 40, 20, n=40), VIDRIO, ' fill-opacity=".55"')
    i.plano(circ(33, 32, 6, n=16), BLANCO, ' fill-opacity=".7"')


def i_conversaciones(i):  # globo de diálogo
    i.solido([(24, 62), (42, 62), (18, 86)], i.a)
    i.solido(rrect(10, 16, 80, 54, 15), i.a)
    for x in (32, 50, 68):
        i.circulo(x, 43, 5.5, BLANCO)


def i_calendario(i):  # calendario
    i.solido(rrect(12, 16, 76, 74, 8), i.a)
    i.plano(rrect(18, 36, 64, 48, 3), BLANCO)
    for a in range(4):
        for b in range(3):
            i.plano(rrect(23 + a * 14.5, 41 + b * 14, 10, 9.5, 2), i.a if (a, b) == (2, 1) else CLARO)
    for x in (30, 64):
        i.solido(rrect(x, 8, 7, 18, 3.5), OSCURO, .4)


def i_actividades(i):  # casilla con visto
    i.solido(rrect(14, 14, 72, 72, 13), i.a)
    i.trazo([(31, 50), (45, 64), (70, 36)], BLANCO, 9)


def i_ajustes(i):  # engranaje
    i.solido([engranaje(50, 50, 31, 8, 9), circ(50, 50, 12, n=32)], i.a)
    i.plano(circ(50, 50, 21, n=40), BLANCO, ' fill-opacity=".12"')


def i_asistente(i):  # Lucho: globo de diálogo con casco de planta
    i.solido([(26, 72), (42, 72), (20, 92)], i.a)
    i.solido(rrect(8, 36, 84, 44, 14), i.a)
    i.solido(circ(50, 34, 24, 180, 360, 26), ORO, .7)
    i.solido(rrect(16, 31, 68, 7, 3.5), tono(ORO, -.12), .7)
    i.plano(rrect(47, 12, 6, 20, 3), tono(ORO, .3))
    for x in (36, 64):
        i.circulo(x, 54, 5.5, BLANCO)
        i.circulo(x + 1, 55, 2.4, OSCURO)
    i.trazo([(40, 66), (50, 71), (60, 66)], BLANCO, 3.5)


def i_generico(i):
    i.solido(rrect(14, 14, 72, 72, 12), i.a)


OBJETOS = {k[2:]: v for k, v in globals().items() if k.startswith('i_') and callable(v)}


def generar():
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'erp.settings')
    django.setup()
    from core.modulos import MODULOS
    carpeta = os.path.join(BASE_DIR, 'static', 'img', 'modulos')
    os.makedirs(carpeta, exist_ok=True)
    for m in MODULOS:
        i = Icono(m['color'])
        OBJETOS.get(m['clave'], i_generico)(i)
        with open(os.path.join(carpeta, f"{m['clave']}.svg"), 'w', encoding='utf-8', newline='\n') as f:
            f.write(i.svg(m['nombre']))
    print(f'{len(MODULOS)} íconos en {carpeta}; sin objeto propio:',
          [m['clave'] for m in MODULOS if m['clave'] not in OBJETOS] or 'ninguno')


if __name__ == '__main__':
    generar()
