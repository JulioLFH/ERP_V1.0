"""Íconos isométricos 3D de los módulos (pantalla de Inicio), al estilo de la presentación comercial: una base con el
borde verde oscuro y una escena por módulo (almacén, planta, banco, camión…) con el color del módulo como acento.

Genera static/img/modulos/<clave>.svg. Uso: python scripts/iconos_modulos.py
Proyección isométrica: x hacia abajo a la derecha, y hacia abajo a la izquierda, z hacia arriba; la base va de (0, 0)
a (12, 12). Las caras visibles son la superior, la «izquierda» (plano y = máx.) y la «derecha» (plano x = máx.)."""
import math
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

C, S = math.cos(math.radians(30)), 0.5
VISTA = (-11.0, -9.6, 22.0, 22.6)  # viewBox común: la base se ve del mismo tamaño en todos los íconos

SUELO, GRILLA, BORDE_I, BORDE_D = '#e8efeb', '#d5dfd9', '#2f6f50', '#235a40'
MURO, TECHO, OSCURO, BLANCO = '#dfe6e2', '#6f8a7d', '#33463d', '#f5f8f6'
ARBOL, TRONCO, CARTON, MADERA = '#66a582', '#86705a', '#d8b47c', '#b88c58'
ORO, PIEL, ALERTA, VIDRIO = '#e3b23c', '#f0c8a2', '#e05a47', '#3f5f55'


def P(x, y, z=0.0):
    return (x - y) * C, (x + y) * S - z


def tono(color, f):
    """Aclara (f > 0, hacia el blanco) u oscurece (f < 0, hacia el negro) un color #rrggbb."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    if f >= 0:
        r, g, b = (int(c + (255 - c) * f) for c in (r, g, b))
    else:
        r, g, b = (int(c * (1 + f)) for c in (r, g, b))
    return f'#{r:02x}{g:02x}{b:02x}'


class Icono:
    def __init__(self, acento):
        self.a = acento
        self.el = []
        self.puntos = []

    # ---------------------------------------------------------------- primitivas
    def poligono2(self, pts, color, extra=''):
        self.puntos += pts
        txt = ' '.join(f'{X:.2f},{Y:.2f}' for X, Y in pts)
        self.el.append(f'<polygon points="{txt}" fill="{color}" stroke="{color}" stroke-width=".04" '
                       f'stroke-linejoin="round"{extra}/>')

    def poligono(self, pts3, color, extra=''):
        self.poligono2([P(*p) for p in pts3], color, extra)

    def linea(self, pts3, color, ancho=.12, extra=''):
        pts = [P(*p) for p in pts3]
        self.puntos += pts
        txt = ' '.join(f'{X:.2f},{Y:.2f}' for X, Y in pts)
        self.el.append(f'<polyline points="{txt}" fill="none" stroke="{color}" stroke-width="{ancho}" '
                       f'stroke-linecap="round" stroke-linejoin="round"{extra}/>')

    def circulo(self, x, y, z, r, color, extra=''):
        X, Y = P(x, y, z)
        self.puntos += [(X - r, Y - r), (X + r, Y + r)]
        self.el.append(f'<circle cx="{X:.2f}" cy="{Y:.2f}" r="{r:.2f}" fill="{color}"{extra}/>')

    def sombra(self, x, y, w, d, h):
        k = min(h * .45, 2.2)
        self.poligono([(x, y, 0), (x + w + k, y, 0), (x + w + k, y + d, 0), (x, y + d, 0)], '#000',
                      ' fill-opacity=".10" stroke-opacity="0"')

    def caja(self, x, y, z, w, d, h, color, superior=None, sombra=True):
        if sombra and z == 0:
            self.sombra(x, y, w, d, h)
        self.poligono([(x, y, z + h), (x + w, y, z + h), (x + w, y + d, z + h), (x, y + d, z + h)],
                      superior or tono(color, .2))
        self.poligono([(x, y + d, z), (x + w, y + d, z), (x + w, y + d, z + h), (x, y + d, z + h)], color)
        self.poligono([(x + w, y, z), (x + w, y + d, z), (x + w, y + d, z + h), (x + w, y, z + h)], tono(color, -.18))
        return (x, y, z, w, d, h)

    def cara(self, c, lado, u0, v0, u1, v1, color, extra=''):
        """Rectángulo sobre una cara de la caja c: lado 'I' (izquierda), 'D' (derecha) o 'S' (superior); u, v de 0 a 1."""
        x, y, z, w, d, h = c
        if lado == 'I':
            f = lambda u, v: (x + w * u, y + d, z + h * v)  # noqa: E731
        elif lado == 'D':
            f = lambda u, v: (x + w, y + d - d * u, z + h * v)  # noqa: E731
        else:
            f = lambda u, v: (x + w * u, y + d * v, z + h)  # noqa: E731
        self.poligono([f(u0, v0), f(u1, v0), f(u1, v1), f(u0, v1)], color, extra)

    def ventanas(self, c, lado, cols, filas, color=VIDRIO, mu=.12, mv=.14):
        for i in range(cols):
            for j in range(filas):
                u0 = mu + (1 - 2 * mu) * i / cols
                v0 = mv + (1 - 2 * mv) * j / filas
                self.cara(c, lado, u0 + .04, v0 + .05, u0 + (1 - 2 * mu) / cols - .04,
                          v0 + (1 - 2 * mv) / filas - .05, color)

    def galpon(self, x, y, w, d, h, alto_techo, muro, techo):
        """Nave a dos aguas con la cumbrera a lo largo de x."""
        c = self.caja(x, y, 0, w, d, h, muro)
        cumbre = h + alto_techo
        self.poligono([(x, y, h), (x + w, y, h), (x + w, y + d / 2, cumbre), (x, y + d / 2, cumbre)], tono(techo, .12))
        self.poligono([(x, y + d / 2, cumbre), (x + w, y + d / 2, cumbre), (x + w, y + d, h), (x, y + d, h)], techo)
        self.poligono([(x + w, y, h), (x + w, y + d, h), (x + w, y + d / 2, cumbre)], tono(muro, -.18))
        return c

    def _elipse(self, cx, cy, z, r, desde, hasta, n=18):
        return [(cx + r * math.cos(math.radians(t)), cy + r * math.sin(math.radians(t)), z)
                for t in (desde + (hasta - desde) * i / n for i in range(n + 1))]

    def cilindro(self, cx, cy, z, r, h, color, superior=None, sombra=True):
        if sombra and z == 0:
            self.sombra(cx - r, cy - r, 2 * r, 2 * r, h)
        # mitad frontal: de -45° a 135° (la parte de 45° a 135° mira a la izquierda: más clara)
        for desde, hasta, f in ((45, 135, 0), (-45, 45, -.18)):
            abajo = self._elipse(cx, cy, z, r, desde, hasta, 9)
            arriba = self._elipse(cx, cy, z + h, r, hasta, desde, 9)
            self.poligono(abajo + arriba, tono(color, f))
        self.poligono(self._elipse(cx, cy, z + h, r, 0, 360, 36), superior or tono(color, .22))

    def cono(self, cx, cy, z, r, h, color):
        for desde, hasta, f in ((45, 135, 0), (-45, 45, -.2)):
            self.poligono(self._elipse(cx, cy, z, r, desde, hasta, 9) + [(cx, cy, z + h)], tono(color, f))

    def rueda(self, cx, y, cz, r, plano='I', color=OSCURO, x=None):
        """Rueda en el plano de la cara izquierda (y fija) o derecha (x fija)."""
        pts = []
        for i in range(24):
            t = math.radians(i * 15)
            pts.append((cx + r * math.cos(t), y, cz + r * math.sin(t)) if plano == 'I'
                       else (x, cx + r * math.cos(t), cz + r * math.sin(t)))
        self.poligono(pts, color)
        cen = [(p[0], p[1], cz + (p[2] - cz) * .45) if plano == 'I' else p for p in pts]
        if plano == 'I':
            cen = [(cx + (p[0] - cx) * .45, y, cz + (p[2] - cz) * .45) for p in pts]
        else:
            cen = [(x, cx + (p[1] - cx) * .45, cz + (p[2] - cz) * .45) for p in pts]
        self.poligono(cen, '#9aa7a1')

    def extruir(self, perfil, z, h, color):
        """Prisma con el perfil (puntos x, y en el plano) extruido en z: caras laterales visibles y la superior."""
        n = len(perfil)
        caras = []
        for i in range(n):
            (x0, y0), (x1, y1) = perfil[i], perfil[(i + 1) % n]
            nx, ny = y1 - y0, -(x1 - x0)  # normal hacia afuera (perfil en sentido antihorario visto desde arriba)
            if nx + ny > 0:
                f = -.18 if nx > ny else 0
                caras.append(((x0 + x1 + y0 + y1) / 2, [(x0, y0, z), (x1, y1, z), (x1, y1, z + h), (x0, y0, z + h)],
                              tono(color, f)))
        for _, pts, col in sorted(caras, key=lambda c: c[0]):
            self.poligono(pts, col)
        self.poligono([(px, py, z + h) for px, py in perfil], tono(color, .2))

    def arbol(self, x, y, s=1.0):
        self.caja(x - .13 * s, y - .13 * s, 0, .26 * s, .26 * s, .9 * s, TRONCO)
        self.caja(x - .75 * s, y - .75 * s, .9 * s, 1.5 * s, 1.5 * s, 1.35 * s, ARBOL, sombra=False)

    def persona(self, x, y, color, s=1.0):
        self.cilindro(x, y, 0, .8 * s, 2.0 * s, color)
        self.circulo(x, y, 2.0 * s + .8 * s, .74 * s, PIEL)

    def monedas(self, x, y, n, r=1.1):
        for i in range(n):
            self.cilindro(x, y, i * .42, r, .42, ORO, sombra=(i == 0))
        self.cara((x - r, y - r, n * .42 - .01, 2 * r, 2 * r, .01), 'S', .35, .35, .65, .65, tono(ORO, -.15))

    def papel(self, x, y, z, w, d, lineas=4, color=BLANCO, tinta='#b8c4bf'):
        c = self.caja(x, y, z, w, d, .12, color, sombra=False)
        for i in range(lineas):
            v = .18 + .66 * i / max(lineas - 1, 1)
            self.cara(c, 'S', .15, v, .85 if i % 2 else .65, v + .06, tinta)
        return c

    def tablero_pie(self, x, y, w, h, color=BLANCO, grosor=.45, base=OSCURO):
        """Pizarra o pantalla de pie mirando a la izquierda (cara I)."""
        return self.caja(x, y, 0, w, grosor, h, color)

    def base(self):
        g = .9
        self.poligono([(0, 12, -g), (12, 12, -g), (12, 12, 0), (0, 12, 0)], BORDE_I)
        self.poligono([(12, 0, -g), (12, 12, -g), (12, 12, 0), (12, 0, 0)], BORDE_D)
        self.poligono([(0, 0, 0), (12, 0, 0), (12, 12, 0), (0, 12, 0)], SUELO)
        for k in (3, 6, 9):
            self.linea([(k, 0, 0), (k, 12, 0)], GRILLA, .05)
            self.linea([(0, k, 0), (12, k, 0)], GRILLA, .05)

    def svg(self, titulo):
        xs = [p[0] for p in self.puntos]
        ys = [p[1] for p in self.puntos]
        x0, y0, w, h = VISTA
        if min(xs) < x0 or max(xs) > x0 + w or min(ys) < y0 or max(ys) > y0 + h:
            print(f'  aviso: {titulo} se sale del marco ({min(xs):.1f}, {min(ys):.1f}) – ({max(xs):.1f}, {max(ys):.1f})')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0} {y0} {w} {h}" role="img" '
                f'aria-label="{titulo}">' + ''.join(self.el) + '</svg>\n')


# ---------------------------------------------------------------------------------------------------- escenas
def edificio(i, x, y, w, d, h, cols=3, filas=None, muro=MURO):
    c = i.caja(x, y, 0, w, d, h, muro)
    filas = filas or max(int(h // 1.6), 1)
    i.ventanas(c, 'I', cols, filas)
    i.ventanas(c, 'D', cols, filas)
    return c


def e_tablero(i):  # monitor con gráfico
    i.caja(5.2, 5.4, 0, 1.6, 1.6, .35, '#55665e')
    i.caja(5.75, 5.95, .35, .5, .5, 1.6, '#55665e', sombra=False)
    m = i.caja(1.8, 5.7, 1.9, 8.4, .5, 5.6, OSCURO)
    i.cara(m, 'I', .05, .07, .95, .93, '#1f2d27')
    for k, (u, v) in enumerate(((.12, .35), (.27, .55), (.42, .45), (.57, .75), (.72, .62))):
        i.cara(m, 'I', u, .14, u + .1, v, i.a if k % 2 else tono(i.a, .4))
    i.cara(m, 'I', .1, .82, .45, .86, '#9fd3b5')
    i.arbol(10, 10, .9)


def e_crm(i):  # embudo de ventas
    i.cilindro(6, 6, 0, 1.4, 1.5, tono(i.a, -.15))
    i.cilindro(6, 6, 1.5, 2.4, 1.5, i.a, sombra=False)
    i.cilindro(6, 6, 3.0, 3.4, 1.5, tono(i.a, .3), sombra=False)
    i.persona(10, 3.5, '#5b7a6c', .8)
    i.persona(2.5, 10.2, '#5b7a6c', .8)


def e_toma_pedido(i):  # celular y paquete
    t = i.caja(3.5, 5, 0, 4.6, .8, 7.6, '#2b3833')
    i.cara(t, 'I', .08, .1, .92, .9, tono(i.a, .15))
    for k in range(4):
        v = .68 - k * .15
        i.cara(t, 'I', .16, v, .84, v + .1, BLANCO)
        i.cara(t, 'I', .2, v + .03, .3, v + .07, i.a)
    i.cara(t, 'I', .4, .02, .6, .06, '#56645e')
    p = i.caja(8.4, 8.2, 0, 2.6, 2.6, 2.0, CARTON)
    i.cara(p, 'S', .42, 0, .58, 1, tono(CARTON, -.2))


def e_ventas(i):  # tienda con toldo
    c = i.caja(2.4, 2.6, 0, 7.2, 5.2, 4.2, MURO)
    i.cara(c, 'I', .08, .0, .3, .62, OSCURO)
    i.cara(c, 'I', .38, .2, .92, .62, VIDRIO)
    i.ventanas(c, 'D', 3, 2)
    for k in range(8):  # toldo a rayas
        x0, x1 = 2.4 + 7.2 * k / 8, 2.4 + 7.2 * (k + 1) / 8
        i.poligono([(x0, 7.8, 4.0), (x1, 7.8, 4.0), (x1, 9.2, 3.1), (x0, 9.2, 3.1)], i.a if k % 2 == 0 else BLANCO)
    letrero = i.caja(4.2, 3.6, 4.2, 3.6, .4, 1.3, i.a, sombra=False)
    i.cara(letrero, 'I', .15, .35, .85, .65, BLANCO)
    i.arbol(10.6, 10.3, .85)


def e_compras(i):  # parihuela con cajas y bolsa
    i.caja(2.6, 3.4, 0, 5.6, 5.6, .5, MADERA)
    for x, y, z in ((2.9, 3.7, .5), (5.6, 3.7, .5), (2.9, 6.4, .5), (5.6, 6.4, .5), (4.2, 5.0, 2.6)):
        b = i.caja(x, y, z, 2.5, 2.5, 2.1, CARTON, sombra=False)
        i.cara(b, 'S', .42, 0, .58, 1, tono(CARTON, -.2))
    b = i.caja(8.8, 8.6, 0, 2.4, 1.4, 2.8, i.a)
    i.linea([(9.4, 9.3, 2.8), (9.6, 9.3, 3.7), (10.4, 9.3, 3.7), (10.6, 9.3, 2.8)], tono(i.a, -.3), .22)
    i.cara(b, 'I', .3, .45, .7, .62, BLANCO)


def e_portal_proveedores(i):  # almacén del proveedor y laptop
    c = i.galpon(1.2, 1.2, 5.8, 4.4, 3.0, 1.5, '#9db0a6', i.a)
    i.cara(c, 'I', .3, 0, .7, .7, OSCURO)
    i.caja(6.2, 7.2, 0, 4.6, 3.4, .35, '#3b4a44')
    i.poligono([(6.2, 7.2, .35), (10.8, 7.2, .35), (10.8, 6.6, 3.6), (6.2, 6.6, 3.6)], '#2b3833')
    i.poligono([(6.5, 7.15, .7), (10.5, 7.15, .7), (10.5, 6.7, 3.3), (6.5, 6.7, 3.3)], tono(i.a, .2))
    i.poligono([(7.0, 7.1, 1.4), (9.0, 7.1, 1.4), (9.0, 6.85, 2.6), (7.0, 6.85, 2.6)], BLANCO)
    i.arbol(10.8, 2.0, .8)


def e_inventario(i):  # almacén con parihuelas
    c = i.galpon(1.0, 1.0, 7.4, 5.4, 3.6, 1.8, '#9db0a6', TECHO)
    i.cara(c, 'I', .3, 0, .72, .75, OSCURO)
    i.cara(c, 'I', .3, .75, .72, .8, i.a)
    for x, y in ((8.6, 7.6), (5.2, 8.9)):
        i.caja(x, y, 0, 2.4, 2.4, .35, MADERA)
        b = i.caja(x + .15, y + .15, .35, 2.1, 2.1, 1.7, CARTON, sombra=False)
        i.cara(b, 'S', .42, 0, .58, 1, tono(CARTON, -.2))
    i.cilindro(10.5, 4.6, 0, .6, 1.5, i.a)


def e_codigo_barras(i):  # caja con etiqueta de barras y láser
    c = i.caja(2.8, 3.2, 0, 6.2, 5.8, 5.2, CARTON)
    i.cara(c, 'S', .42, 0, .58, 1, tono(CARTON, -.2))
    i.cara(c, 'I', .15, .2, .85, .75, BLANCO)
    for k, ancho in enumerate((.03, .015, .04, .02, .015, .035, .02, .03, .015, .04, .02, .03)):
        u = .2 + k * .052
        i.cara(c, 'I', u, .3, u + ancho, .68, OSCURO)
    i.cara(c, 'D', .2, .35, .8, .6, BLANCO)
    i.linea([(2.4, 9.3, 2.9), (9.6, 9.3, 2.9)], ALERTA, .14, ' stroke-opacity=".85"')
    i.circulo(10.3, 10.0, 1.2, .55, i.a)


def e_manufactura(i):  # planta con tanque y chimenea
    i.cilindro(2.6, 2.4, 0, 1.6, 5.0, '#c9d2ce')
    i.caja(7.6, 1.2, 0, 1.1, 1.1, 7.2, '#a9b6b0')
    i.cara((7.6, 1.2, 0, 1.1, 1.1, 7.2), 'I', 0, .82, 1, .9, i.a)
    c = i.caja(1.6, 4.6, 0, 8.6, 4.6, 3.2, MURO)
    for k in range(4):  # techo en diente de sierra
        x0 = 1.6 + 8.6 * k / 4
        x1 = 1.6 + 8.6 * (k + 1) / 4
        i.poligono([(x0, 4.6, 3.2), (x0, 9.2, 3.2), (x0 + .2, 9.2, 4.4), (x0 + .2, 4.6, 4.4)], TECHO)
        i.poligono([(x0 + .2, 4.6, 4.4), (x0 + .2, 9.2, 4.4), (x1, 9.2, 3.2), (x1, 4.6, 3.2)], tono(TECHO, .25))
    i.cara(c, 'I', .1, 0, .3, .65, OSCURO)
    i.ventanas((c[0] + 8.6 * .35, c[1], 0, 8.6 * .6, c[4], 3.2), 'I', 3, 1)
    i.ventanas(c, 'D', 2, 1)
    i.circulo(10.6, 10.4, 3.6, .45, i.a)


def e_planificacion(i):  # diagrama de Gantt en una pizarra y engranaje
    p = i.tablero_pie(1.4, 4.4, 8.4, 6.2, BLANCO)
    i.cara(p, 'I', 0, .84, 1, 1, OSCURO)
    for k in range(4):
        i.cara(p, 'I', .23 + .2 * k, .06, .235 + .2 * k, .82, '#d7dfdb')
    for k, (u0, u1) in enumerate(((.05, .42), (.3, .68), (.52, .95), (.18, .55))):
        v = .66 - k * .18
        i.cara(p, 'I', u0, v, u1, v + .11, i.a if k % 2 == 0 else tono(i.a, .45))
    i.sombra(8.2, 8.0, 3.0, 3.0, 1.0)
    i.extruir([(9.7 + px, 9.5 + py) for px, py in engranaje(1.2, 7, .45)], 0, .9, '#9aa7a1')
    i.poligono(i._elipse(9.7, 9.5, .91, .45, 0, 360, 20), OSCURO)


def e_control_calidad(i):  # faja con botellas y sello
    f = i.caja(1.2, 5.2, 0, 9.8, 2.2, 1.1, '#56665f')
    i.cara(f, 'S', 0, .15, 1, .85, '#33433c')
    for x in (2.4, 4.6, 6.8, 9.0):
        i.cilindro(x, 6.3, 1.1, .6, 1.9, BLANCO, sombra=False)
        i.cilindro(x, 6.3, 3.0, .32, .45, i.a, sombra=False)
    i.circulo(8.6, 2.6, 6.0, 1.75, i.a)
    X, Y = P(8.6, 2.6, 6.0)
    i.el.append(f'<polyline points="{X - .8:.2f},{Y:.2f} {X - .2:.2f},{Y + .6:.2f} {X + .9:.2f},{Y - .6:.2f}" '
                f'fill="none" stroke="#fff" stroke-width=".38" stroke-linecap="round" stroke-linejoin="round"/>')


def e_mantenimiento(i):  # máquina con alerta y caja de herramientas
    m = i.caja(2.2, 2.8, 0, 6.4, 4.8, 3.6, '#a9b8b1')
    i.cara(m, 'I', .1, .3, .55, .8, VIDRIO)
    i.cara(m, 'D', .25, .3, .75, .75, '#7d8d86')
    i.cilindro(4.0, 4.2, 3.6, 1.0, .6, '#7d8d86', sombra=False)
    p = i.caja(6.6, 3.4, 3.6, 1.6, 1.6, 1.4, i.a, sombra=False)
    i.cara(p, 'I', .2, .3, .8, .7, BLANCO)
    i.circulo(5.4, 5.2, 7.2, .55, ALERTA)
    t = i.caja(8.2, 8.6, 0, 3.0, 1.8, 1.5, i.a)
    i.cara(t, 'I', 0, .62, 1, .7, tono(i.a, -.35))
    i.linea([(9.1, 9.5, 1.5), (9.1, 9.5, 2.1), (10.3, 9.5, 2.1), (10.3, 9.5, 1.5)], OSCURO, .2)


def e_costos(i):  # monedas y calculadora
    i.monedas(4.0, 3.6, 7)
    i.monedas(7.4, 3.0, 10)
    i.monedas(8.6, 6.8, 4)
    c = i.caja(1.6, 6.6, 0, 3.6, 4.4, .7, '#33433c')
    i.cara(c, 'S', .12, .08, .88, .3, '#9fd3b5')
    for a in range(3):
        for b in range(3):
            i.cara(c, 'S', .14 + a * .26, .4 + b * .19, .32 + a * .26, .53 + b * .19, i.a if (a, b) == (2, 2) else BLANCO)


def e_presupuestos(i):  # pizarra con barras y monedas
    p = i.tablero_pie(2.0, 4.6, 7.0, 6.2, '#c9b48e')
    i.cara(p, 'I', .06, .06, .94, .94, BLANCO)
    for k, v in enumerate((.35, .55, .45, .78)):
        u = .14 + k * .2
        i.cara(p, 'I', u, .14, u + .13, v, i.a if k == 3 else tono(i.a, .45))
    i.monedas(9.4, 9.0, 5, 1.0)


def e_activos(i):  # edificio y máquina con placa de activo
    edificio(i, 1.4, 1.4, 4.4, 4.4, 6.4, 2)
    m = i.caja(6.0, 6.2, 0, 4.6, 3.4, 2.6, '#a9b8b1')
    i.cara(m, 'I', .1, .25, .45, .75, VIDRIO)
    i.cara(m, 'I', .58, .4, .9, .72, i.a)
    i.cara(m, 'I', .62, .48, .86, .64, BLANCO)
    i.rueda(7.0, 9.62, .1, .1, color=OSCURO)


def e_obras(i):  # grúa, estructura y conos
    i.caja(1.6, 1.6, 0, .8, .8, 10.2, i.a)
    i.caja(-.4, 1.7, 9.6, 11.0, .6, .5, i.a, sombra=False)
    i.caja(-.4, 1.6, 8.6, 1.4, .8, 1.0, '#6b7b74', sombra=False)
    i.linea([(8.6, 2.0, 9.6), (8.6, 2.0, 6.6)], OSCURO, .08)
    i.caja(8.0, 1.5, 5.6, 1.2, 1.0, 1.0, CARTON, sombra=False)
    for z in (0, 2.4, 4.8):
        i.caja(4.6, 4.6, z, 5.4, 5.4, .35, '#c9d2ce', sombra=(z == 0))
        if z < 4.8:
            for x, y in ((4.7, 9.6), (9.6, 9.6), (9.6, 4.7)):
                i.caja(x, y, z + .35, .3, .3, 2.05, '#9aa7a1', sombra=False)
    for x, y in ((2.6, 10.6), (4.4, 11.0)):
        i.caja(x - .5, y - .5, 0, 1.0, 1.0, .15, tono(i.a, -.2))
        i.cono(x, y, .15, .45, 1.4, i.a)


def e_proyectos(i):  # tablero kanban y bandera
    p = i.tablero_pie(1.6, 4.2, 7.4, 5.6, BLANCO)
    for col in range(3):
        u = .06 + col * .31
        i.cara(p, 'I', u, .86, u + .27, .92, OSCURO)
        for k in range(3 - col):
            v = .66 - k * .2
            i.cara(p, 'I', u, v, u + .27, v + .14, i.a if col == 2 else tono(i.a, .5))
    i.caja(9.6, 8.6, 0, .25, .25, 5.4, '#6b7b74')
    i.poligono([(9.85, 8.85, 5.4), (11.8, 8.85, 4.75), (9.85, 8.85, 4.1)], i.a)


def e_sst(i):  # botiquín y conos
    c = i.caja(2.6, 3.4, 0, 5.8, 4.8, 3.4, BLANCO)
    i.cara(c, 'S', .42, .18, .58, .82, i.a)
    i.cara(c, 'S', .2, .4, .8, .6, i.a)
    i.cara(c, 'I', .42, .2, .58, .8, i.a)
    i.cara(c, 'I', .3, .4, .7, .6, i.a)
    i.linea([(4.4, 5.8, 3.4), (4.4, 5.8, 4.1), (6.6, 5.8, 4.1), (6.6, 5.8, 3.4)], OSCURO, .22)
    for x, y in ((9.8, 6.6), (9.4, 9.8)):
        i.caja(x - .55, y - .55, 0, 1.1, 1.1, .15, '#e8833a')
        i.cono(x, y, .15, .5, 1.6, '#f08c3a')


def e_calidad(i):  # podio con trofeo
    i.caja(1.4, 4.4, 0, 3.2, 3.2, 2.0, '#c9d2ce')
    c = i.caja(4.6, 4.4, 0, 3.2, 3.2, 3.6, BLANCO)
    i.caja(7.8, 4.4, 0, 3.2, 3.2, 1.3, '#c9d2ce')
    i.cara(c, 'I', .4, .45, .6, .75, i.a)
    i.cilindro(6.2, 6.0, 3.6, .7, .35, ORO, sombra=False)
    i.cilindro(6.2, 6.0, 3.95, .22, .7, ORO, sombra=False)
    i.cilindro(6.2, 6.0, 4.65, 1.05, 1.3, ORO, superior=tono(ORO, -.25), sombra=False)
    i.circulo(9.4, 5.9, 2.2, .45, i.a)


def e_planillas(i):  # papeles, monedas y persona
    for k in range(6):
        i.papel(2.0 + k * .08, 3.6 - k * .06, k * .14, 5.0, 6.0, 0)
    i.papel(2.4, 3.3, .84, 5.0, 6.0, 5, BLANCO, tono(i.a, .3))
    i.monedas(9.0, 3.6, 5, 1.0)
    i.persona(9.6, 9.4, i.a, .9)


def e_empleados(i):  # tres personas
    i.persona(4.0, 3.6, tono(i.a, .35), .95)
    i.persona(9.0, 4.4, tono(i.a, .35), .95)
    i.persona(6.4, 7.4, i.a, 1.25)
    i.arbol(2.4, 10.2, .8)


def e_tiempo_personal(i):  # sombrilla y reposera
    i.caja(5.9, 5.9, 0, .22, .22, 4.6, '#8a7a6a')
    i.cono(6.0, 6.0, 4.0, 3.6, 1.6, i.a)
    i.caja(6.6, 8.2, 0, 4.0, 1.8, .7, BLANCO)
    i.poligono([(6.6, 8.2, .7), (6.6, 10.0, .7), (5.6, 10.0, 2.4), (5.6, 8.2, 2.4)], tono(i.a, .45))
    i.arbol(2.2, 9.8, .8)


def e_requerimientos(i):  # portapapeles y caja de materiales
    p = i.tablero_pie(1.8, 4.0, 5.6, 7.0, '#c9b48e')
    i.cara(p, 'I', .08, .05, .92, .9, BLANCO)
    i.cara(p, 'I', .33, .86, .67, .97, OSCURO)
    for k in range(4):
        v = .68 - k * .17
        i.cara(p, 'I', .16, v, .26, v + .09, i.a)
        i.cara(p, 'I', .32, v + .02, .82, v + .07, '#b8c4bf')
    b = i.caja(8.0, 7.6, 0, 3.0, 3.0, 2.4, CARTON)
    i.cara(b, 'S', .42, 0, .58, 1, tono(CARTON, -.2))
    i.cara(b, 'I', .25, .35, .75, .65, BLANCO)


def e_rendiciones(i):  # recibo de pie y monedas
    zig = [(2.2 + .5 * k, 5.4, .3 if k % 2 else 0) for k in range(13)]
    i.poligono(zig + [(8.2, 5.4, 7.2), (2.2, 5.4, 7.2)], BLANCO)
    i.poligono([(8.2, 5.4, 0), (8.2, 4.9, 0), (8.2, 4.9, 7.2), (8.2, 5.4, 7.2)], '#c9d2ce')
    for k in range(6):
        z = 6.2 - k * .9
        i.linea([(2.8, 5.4, z), (6.6 if k % 2 else 7.6, 5.4, z)], '#b8c4bf', .2)
    i.linea([(5.4, 5.4, 1.1), (7.6, 5.4, 1.1)], i.a, .35)
    i.monedas(9.6, 9.0, 4, 1.0)


def camion(i, x, y, largo, cabina):
    t = i.caja(x, y, .65, largo, 2.8, 3.3, BLANCO)
    i.cara(t, 'I', 0, .45, 1, .62, cabina)
    c = i.caja(x + largo + .1, y, .65, 2.3, 2.8, 2.6, cabina)
    i.cara(c, 'D', .12, .5, .88, .88, VIDRIO)
    i.cara(c, 'I', .55, .5, .95, .85, VIDRIO)
    for rx in (x + 1.1, x + largo - 1.0, x + largo + 1.3):
        i.rueda(rx, y + 2.82, .55, .55)


def e_logistica(i):  # almacén y camión
    c = i.galpon(.8, .6, 6.4, 4.0, 3.0, 1.4, '#9db0a6', TECHO)
    i.cara(c, 'I', .3, 0, .7, .75, OSCURO)
    camion(i, 2.2, 6.6, 6.0, i.a)


def e_distribucion(i):  # carretera, furgoneta y marcador
    i.poligono([(0, 6.2, 0), (12, 6.2, 0), (12, 9.4, 0), (0, 9.4, 0)], '#c5cfca')
    for k in range(5):
        i.poligono([(1 + k * 2.4, 7.7, 0), (2.2 + k * 2.4, 7.7, 0), (2.2 + k * 2.4, 7.9, 0), (1 + k * 2.4, 7.9, 0)],
                   BLANCO)
    camion(i, 4.0, 6.5, 3.6, '#5f7f71')
    for desde, hasta, f in ((45, 135, 0), (-45, 45, -.2)):  # marcador del mapa: cono invertido y esfera
        i.poligono(i._elipse(5.0, 2.6, 6.2, 1.1, desde, hasta, 9) + [(5.0, 2.6, 3.4)], tono(i.a, f))
    i.circulo(5.0, 2.6, 6.6, 1.55, i.a)
    i.circulo(5.0, 2.6, 6.6, .6, BLANCO)
    i.arbol(10.6, 2.4, .8)


def auto(i, x, y, color):
    i.caja(x, y, .45, 4.4, 2.2, 1.1, color)
    c = i.caja(x + .9, y + .15, 1.55, 2.4, 1.9, .95, tono(color, .25), sombra=False)
    i.cara(c, 'I', .08, .1, .92, .9, VIDRIO)
    i.cara(c, 'D', .1, .1, .9, .9, VIDRIO)
    for rx in (x + 1.0, x + 3.4):
        i.rueda(rx, y + 2.22, .45, .45)


def e_flota(i):  # dos autos
    auto(i, 1.4, 2.8, '#9aa7a1')
    auto(i, 5.4, 7.0, i.a)
    i.arbol(10.6, 2.6, .85)


def banco(i, x, y, w, d, techo):
    i.caja(x, y, 0, w, d, .6, '#c9d2ce')
    i.caja(x + .6, y + .4, .6, w - 1.2, d - 1.6, 3.6, MURO, sombra=False)
    for k in range(5):
        i.cilindro(x + .9 + (w - 1.8) * k / 4, y + d - .7, .6, .32, 3.6, BLANCO, sombra=False)
    i.caja(x, y, 4.2, w, d, .55, '#c9d2ce', sombra=False)
    z, cumbre = 4.75, 6.4
    i.poligono([(x, y, z), (x + w, y, z), (x + w, y + d / 2, cumbre), (x, y + d / 2, cumbre)], tono(techo, .15))
    i.poligono([(x, y + d / 2, cumbre), (x + w, y + d / 2, cumbre), (x + w, y + d, z), (x, y + d, z)], techo)
    i.poligono([(x + w, y, z), (x + w, y + d, z), (x + w, y + d / 2, cumbre)], tono(MURO, -.18))


def e_tesoreria(i):  # banco con columnas
    banco(i, 1.4, 2.0, 9.0, 7.4, i.a)
    i.monedas(10.6, 10.6, 3, .8)


def e_finanzas(i):  # barras crecientes y moneda
    for k, h in enumerate((2.0, 3.4, 4.8, 6.4)):
        i.caja(1.6 + k * 2.3, 6.2 - k * .9, 0, 1.8, 1.8, h, tono(i.a, .45 - k * .15))
    i.linea([(1.6, 7.2, 3.0), (4.0, 6.2, 4.6), (6.4, 5.4, 6.0), (9.4, 4.0, 8.0)], ORO, .3)
    i.poligono([(9.6, 3.9, 8.6), (9.9, 3.8, 7.5), (8.9, 4.2, 7.8)], ORO)
    i.monedas(9.6, 9.6, 3, .9)


def e_contabilidad(i):  # libros
    for k, (dx, col) in enumerate(((0, tono(i.a, .35)), (.3, i.a), (-.2, tono(i.a, -.25)))):
        b = i.caja(2.6 + dx, 3.6 - dx, k * 1.0, 5.6, 4.0, .9, col, sombra=(k == 0))
        i.cara(b, 'D', .03, .2, .97, .8, BLANCO)  # hojas a los lados; la tapa es la cara superior
        i.cara(b, 'I', .03, .2, .9, .8, '#eef2ef')
    i.caja(9.0, 5.0, 0, .9, 4.4, 4.6, tono(i.a, -.1))
    i.cara((9.0, 5.0, 0, .9, 4.4, 4.6), 'D', .1, .55, .9, .62, ORO)
    i.papel(7.6, 9.8, 0, 3.0, 1.8, 3)


def e_sunat(i):  # edificio público con bandera
    banco(i, 1.6, 3.2, 7.6, 6.2, '#8c9b94')
    i.caja(9.8, 2.2, 0, .22, .22, 8.2, '#6b7b74')
    for k, col in enumerate((i.a, BLANCO, i.a)):
        i.poligono([(10.0 + k * .8, 2.3, 8.2), (10.8 + k * .8, 2.3, 8.2), (10.8 + k * .8, 2.3, 6.8),
                    (10.0 + k * .8, 2.3, 6.8)], col)


def e_cliente(i):  # torre de oficinas
    c = edificio(i, 3.2, 3.2, 5.6, 5.6, 9.0, 3, 5)
    i.caja(4.8, 4.8, 9.0, 2.4, 2.4, .9, i.a, sombra=False)
    i.cara(c, 'I', .4, 0, .6, .13, OSCURO)
    i.arbol(10.6, 10.4, .9)
    i.arbol(1.8, 10.6, .7)


def e_contactos(i):  # tarjeta de contacto y dos personas
    p = i.tablero_pie(2.0, 3.6, 6.6, 4.8, BLANCO)
    i.cara(p, 'I', .08, .25, .38, .8, tono(i.a, .3))
    i.cara(p, 'I', .14, .32, .32, .55, i.a)
    for k in range(3):
        v = .65 - k * .18
        i.cara(p, 'I', .46, v, .9 if k else .78, v + .08, '#b8c4bf' if k else OSCURO)
    i.persona(5.4, 8.6, i.a)
    i.persona(8.6, 9.2, tono(i.a, .4), .85)


def e_consulta_ruc(i):  # documento y lupa
    p = i.tablero_pie(1.8, 3.6, 5.6, 7.0, BLANCO)
    i.cara(p, 'I', .1, .82, .6, .9, OSCURO)
    for k in range(5):
        v = .68 - k * .13
        i.cara(p, 'I', .1, v, .9 if k % 2 else .7, v + .06, '#b8c4bf')
    X, Y = P(8.0, 7.6, 4.2)
    i.puntos += [(X - 3, Y - 3), (X + 4, Y + 4)]
    i.el.append(f'<line x1="{X + 1.5:.2f}" y1="{Y + 1.5:.2f}" x2="{X + 3.6:.2f}" y2="{Y + 3.6:.2f}" '
                f'stroke="{OSCURO}" stroke-width=".8" stroke-linecap="round"/>')
    i.el.append(f'<circle cx="{X:.2f}" cy="{Y:.2f}" r="2.1" fill="#d7ece4" fill-opacity=".7" stroke="{i.a}" '
                f'stroke-width=".55"/>')


def e_conversaciones(i):  # dos globos de diálogo
    a = i.caja(1.6, 3.4, 3.0, 6.2, .6, 3.8, i.a, sombra=False)
    i.poligono([(2.6, 4.0, 3.0), (3.8, 4.0, 3.0), (2.4, 4.0, 1.9)], i.a)
    for k in range(3):
        i.cara(a, 'I', .28 + k * .17, .42, .36 + k * .17, .58, BLANCO)
    b = i.caja(4.8, 7.6, 1.2, 6.0, .6, 3.2, BLANCO, sombra=False)
    i.poligono([(9.0, 8.2, 1.2), (10.0, 8.2, 1.2), (10.6, 8.2, .2)], BLANCO)
    i.cara(b, 'I', .12, .55, .88, .68, '#b8c4bf')
    i.cara(b, 'I', .12, .3, .6, .43, '#b8c4bf')
    i.sombra(1.6, 3.4, 6.2, .6, 1.5)
    i.sombra(4.8, 7.6, 6.0, .6, 1.0)


def e_calendario(i):  # calendario de pie
    c = i.caja(2.0, 4.8, 0, 7.6, 1.0, 6.6, BLANCO)
    i.cara(c, 'I', 0, .76, 1, 1, i.a)
    for a in range(5):
        for b in range(3):
            u, v = .07 + a * .18, .1 + b * .2
            i.cara(c, 'I', u, v, u + .13, v + .14, tono(i.a, .55) if (a, b) == (3, 1) else '#e1e7e4')
    for x in (3.4, 8.0):
        i.cilindro(x, 5.3, 6.6, .25, .7, OSCURO, sombra=False)
    i.arbol(10.4, 9.8, .8)


def e_actividades(i):  # lista de tareas con marcas
    p = i.tablero_pie(2.0, 4.4, 7.2, 6.4, BLANCO)
    for k in range(4):
        v = .74 - k * .2
        i.cara(p, 'I', .08, v, .2, v + .14, i.a if k < 3 else '#d7dfdb')
        i.cara(p, 'I', .28, v + .04, .9 if k % 2 else .75, v + .1, '#b8c4bf')
    for k in range(3):
        z = .74 - k * .2
        x0, zz = 2.0 + 7.2 * .1, 6.4 * (z + .07)
        i.linea([(x0, 4.85, zz), (x0 + .3, 4.85, zz - .3), (x0 + .75, 4.85, zz + .35)], BLANCO, .16)
    i.circulo(10.2, 9.4, 1.4, 1.1, tono(i.a, .3))
    X, Y = P(10.2, 9.4, 1.4)
    i.el.append(f'<polyline points="{X:.2f},{Y - .7:.2f} {X:.2f},{Y:.2f} {X + .5:.2f},{Y + .3:.2f}" fill="none" '
                f'stroke="{OSCURO}" stroke-width=".18" stroke-linecap="round"/>')


def engranaje(r, dientes, alto=.75):
    pts = []
    for k in range(dientes * 4):
        t = 2 * math.pi * k / (dientes * 4)
        rr = r + (alto if k % 4 in (1, 2) else 0)
        pts.append((rr * math.cos(t), rr * math.sin(t)))
    return pts


def e_ajustes(i):  # engranajes
    for cx, cy, r, z, h, col in ((4.8, 4.8, 3.0, 0, 1.3, '#9aa7a1'), (8.9, 8.6, 1.8, 0, 1.0, i.a)):
        i.sombra(cx - r, cy - r, 2 * r, 2 * r, h)
        i.extruir([(cx + px, cy + py) for px, py in engranaje(r, 10 if r > 2 else 7)], z, h, col)
        i.poligono(i._elipse(cx, cy, z + h + .01, r * .35, 0, 360, 24), tono(col, -.35))


def e_generico(i):
    edificio(i, 3.0, 3.0, 6.0, 6.0, 6.0, 3)
    i.caja(4.6, 4.6, 6.0, 2.8, 2.8, .8, i.a, sombra=False)


ESCENAS = {k[2:]: v for k, v in globals().items() if k.startswith('e_') and callable(v)}


def generar():
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'erp.settings')
    django.setup()
    from core.modulos import MODULOS
    carpeta = os.path.join(BASE_DIR, 'static', 'img', 'modulos')
    os.makedirs(carpeta, exist_ok=True)
    for m in MODULOS:
        i = Icono(m['color'])
        i.base()
        ESCENAS.get(m['clave'], e_generico)(i)
        with open(os.path.join(carpeta, f"{m['clave']}.svg"), 'w', encoding='utf-8', newline='\n') as f:
            f.write(i.svg(m['nombre']))
    print(f'{len(MODULOS)} íconos en {carpeta}; sin escena propia:',
          [m['clave'] for m in MODULOS if m['clave'] not in ESCENAS] or 'ninguno')


if __name__ == '__main__':
    generar()
