"""Compara el análisis por carácter y por lotes con ocho peticiones HTTP locales."""
import argparse
import concurrent.futures
import hashlib
import json
import os
import queue
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def anterior(datos):
    from sbd.web import server as web
    profundidad = unidades = escapes = 0
    cadena, posicion = False, 0
    while coincidencia := (web._SIMBOLOS_CADENA_JSON if cadena else web._SIMBOLOS_JSON).search(datos, posicion):
        posicion = coincidencia.end()
        simbolo = coincidencia.group()
        if cadena:
            if simbolo == b'\\':
                escapes += 1
                if escapes > web.MAX_ESCAPES_JSON:
                    raise ValueError('demasiados escapes JSON')
                posicion += 1
            elif simbolo == b'"':
                cadena = False
        elif simbolo == b'"':
            cadena = True
        elif simbolo in (b'[', b'{'):
            profundidad += 1
            unidades += 1
            if profundidad > web.MAX_PROFUNDIDAD_JSON:
                raise ValueError('JSON demasiado anidado')
            if unidades > web.MAX_UNIDADES_JSON:
                raise ValueError('JSON demasiado complejo')
        elif simbolo == b',':
            unidades += 1
            if unidades > web.MAX_UNIDADES_JSON:
                raise ValueError('JSON demasiado complejo')
        elif simbolo in (b']', b'}'):
            profundidad -= 1
            if profundidad < 0:
                raise ValueError('JSON con cierre inesperado')


def servidor(datos, modo):
    from sbd.servidor import Servidor
    from sbd.web import server as web
    if modo == 'anterior':
        web._comprobar_profundidad_json = anterior
    with Servidor(datos) as instancia:
        http = web.ServidorHTTP(('127.0.0.1', 0), instancia, silencio=True)
        print(json.dumps({'puerto': http.server_address[1]}), flush=True)
        http.serve_forever()


def medir(modo):
    from sbd.servidor import Servidor
    from sbd.web.server import MAX_ESCAPES_JSON
    cuerpo = b'{"orden":"crear base infiltrada","extra":"' + b'\\n' * (MAX_ESCAPES_JSON + 1) + b'"}'
    with tempfile.TemporaryDirectory(prefix='sbd-ensayo-json-') as carpeta:
        datos = os.path.join(carpeta, 'datos')
        proceso = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--servidor', datos, modo],
                                   cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding='utf-8', env={**os.environ, 'PYTHONUTF8': '1'})
        cola = queue.Queue()
        threading.Thread(target=lambda: cola.put(proceso.stdout.readline()), daemon=True).start()
        try:
            try:
                puerto = json.loads(cola.get(timeout=20))['puerto']
            except (queue.Empty, ValueError, KeyError) as error:
                raise RuntimeError('el servidor de ensayo no arrancó') from error
            url = f'http://127.0.0.1:{puerto}'
            barrera = threading.Barrier(9)
            def solicitar():
                request = urllib.request.Request(url + '/api/ejecutar', data=cuerpo,
                                                 headers={'Content-Type': 'application/json'})
                barrera.wait(timeout=20)
                inicio = time.perf_counter()
                try:
                    with urllib.request.urlopen(request, timeout=30) as respuesta:
                        codigo = respuesta.status
                        respuesta.read()
                except urllib.error.HTTPError as error:
                    codigo = error.code
                    error.close()
                return codigo, round((time.perf_counter() - inicio) * 1000, 3)
            salud = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                tareas = [executor.submit(solicitar) for _ in range(8)]
                barrera.wait(timeout=20)
                inicio = time.perf_counter()
                while not all(tarea.done() for tarea in tareas):
                    antes = time.perf_counter()
                    with urllib.request.urlopen(url + '/salud', timeout=30) as respuesta:
                        if respuesta.status != 200:
                            raise RuntimeError('falló la consulta de salud')
                        respuesta.read()
                    salud.append(round((time.perf_counter() - antes) * 1000, 3))
                    time.sleep(0.05)
                respuestas = [tarea.result(timeout=30) for tarea in tareas]
                duracion = time.perf_counter() - inicio
            if not salud or any(codigo != 400 for codigo, _ in respuestas):
                raise RuntimeError('falló el rechazo de los cuerpos o no hubo consultas de salud')
            request = urllib.request.Request(url + '/api/ejecutar', data=b'{"orden":"crear base valida"}',
                                             headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=30) as respuesta:
                resultado = json.load(respuesta)
            if resultado.get('error'):
                raise RuntimeError('falló la orden válida posterior')
        finally:
            proceso.terminate()
            try:
                proceso.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                proceso.kill()
                proceso.communicate(timeout=20)
        with Servidor(datos) as instancia:
            if instancia.existe('infiltrada') or not instancia.existe('valida'):
                raise RuntimeError('el resultado de las órdenes no es el esperado')
    return {'duracion_s': round(duracion, 3), 'salud_max_ms': max(salud),
            'rechazo_max_ms': max(tiempo for _, tiempo in respuestas), 'consultas_salud': len(salud)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--muestras', type=int, default=3)
    parser.add_argument('--servidor', nargs=2, metavar=('DATOS', 'MODO'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.servidor:
        return servidor(*args.servidor)
    if not 1 <= args.muestras <= 10:
        parser.error('las muestras deben estar entre 1 y 10')
    archivo = ROOT / 'sbd' / 'web' / 'server.py'
    huella = hashlib.sha256(archivo.read_bytes()).hexdigest()
    guion = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    muestras = {'anterior': [], 'optimizado': []}
    for i in range(args.muestras):
        for modo in (('anterior', 'optimizado') if i % 2 == 0 else ('optimizado', 'anterior')):
            muestras[modo].append(medir(modo))
    if huella != hashlib.sha256(archivo.read_bytes()).hexdigest() or guion != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise RuntimeError('el código o el guion cambiaron durante la medición')
    medianas = {modo: round(statistics.median(item['duracion_s'] for item in valores), 3)
                for modo, valores in muestras.items()}
    print(json.dumps({'python': sys.version.split()[0], 'fuente_sha256': huella, 'guion_sha256': guion,
                      'muestras': muestras, 'mediana_duracion_s': medianas,
                      'nota': 'ocho cuerpos de unos 2 MB; carga HTTP local sin proxy; pocas muestras'}, indent=2))


if __name__ == '__main__':
    main()
