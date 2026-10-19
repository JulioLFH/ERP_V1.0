"""Convierte las pruebas fallidas de la salida de `manage.py test` en anotaciones de GitHub Actions (se ven en el
resumen de la ejecución, también sin iniciar sesión): una por prueba, con las últimas líneas del error.

    python scripts/anotar_fallas_ci.py salida_pruebas.txt
"""
import re
import sys

SEPARADOR = '=' * 70


def anotar(ruta):
    texto = open(ruta, encoding='utf-8', errors='replace').read()
    bloques = texto.split(SEPARADOR)[1:]
    n = 0
    for bloque in bloques:
        cabecera = re.match(r'\s*(FAIL|ERROR): (\S+) \(([^)]+)\)', bloque)
        if not cabecera:
            continue
        tipo, prueba, clase = cabecera.groups()
        partes = bloque.split('-' * 70)  # cabecera, traceback y (en la última) el resumen "Ran N tests"
        cuerpo = (partes[1] if len(partes) > 1 else partes[0]).strip().splitlines()
        lugar = next((l.strip() for l in reversed(cuerpo) if l.strip().startswith('File "') and '/tests' in l), '')
        detalle = '\n'.join(([lugar] if lugar else []) + cuerpo[-12:])
        detalle = detalle.replace('%', '%25').replace('\r', '').replace('\n', '%0A')
        print(f'::error title={tipo} {clase}.{prueba}::{detalle}')
        n += 1
    print(f'{n} prueba(s) con falla anotadas.')


if __name__ == '__main__':
    anotar(sys.argv[1])
