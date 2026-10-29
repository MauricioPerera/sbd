"""Conectarse a un servidor de SBD desde cualquier programa en Python.

    import sbd

    db = sbd.conectar_servidor('https://datos.miweb.es', clave='sbd_…', base='tienda')
    for p in db.consultar('buscar productos donde precio < ? ordenar precio', [20]):
        print(p['nombre'], p['precio'])                 # el dinero llega como Decimal exacto

    nuevo = db.ejecutar('insertar en pedidos { cliente_id: ? } devolver id', [3]).escalar()

    with db.transaccion() as t:                          # todo o nada
        t.ejecutar('actualizar productos poner stock = stock - 1 donde id = ?', [7])
        t.ejecutar('insertar en lineas { pedido_id: ?, producto_id: ?, cantidad: 1 }', [nuevo, 7])

Solo usa la biblioteca estándar.
"""
import json
import ssl
import urllib.error
import urllib.request
from decimal import Decimal

from .errors import BaseOcupada, IncompleteInputError, PermisoDenegado, SBDError
from .result import Result


class ErrorServidor(SBDError):
    """El servidor ha rechazado la orden. «tipo» dice por qué: sintaxis, orden, permiso, ocupada, clave…"""

    def __init__(self, mensaje, tipo='orden', estado=None, orden=None):
        super().__init__(mensaje)
        self.tipo = tipo
        self.estado = estado
        self.orden = orden


_POR_TIPO = {'ocupada': BaseOcupada, 'permiso': PermisoDenegado, 'incompleta': IncompleteInputError}


def _error(datos, estado):
    clase = _POR_TIPO.get(datos.get('tipo'))
    mensaje = datos.get('error') or f'error {estado}'
    if clase is not None:
        e = clase(mensaje)
        e.tipo, e.estado, e.orden = datos.get('tipo'), estado, datos.get('orden_fallida')
        return e
    return ErrorServidor(mensaje, datos.get('tipo', 'orden'), estado, datos.get('orden_fallida'))


class Resultado(Result):
    """Las filas de una consulta, o el resumen de un cambio.

    columnas, filas (tuplas), filas_afectadas, ultimo_id, mensaje
    escalar() · primera() · dicts() · len() · for fila in resultado
    """

    def __init__(self, datos):
        tipos = datos.get('tipos') or []
        filas = datos.get('filas') or []
        dinero = [i for i, t in enumerate(tipos) if t == 'dinero']
        if dinero:
            filas = [[Decimal(v) if i in dinero and v is not None else v for i, v in enumerate(f)] for f in filas]
        super().__init__(datos.get('columnas'), filas, datos.get('filas_afectadas'), datos.get('mensaje', ''),
                         datos.get('ultimo_id'))
        self.orden = datos.get('orden')
        self.tipos = tipos
        self.ms = datos.get('ms')

    @property
    def columnas(self):
        return self.columns

    @property
    def filas(self):
        return self.rows

    @property
    def filas_afectadas(self):
        return self.rowcount

    @property
    def ultimo_id(self):
        return self.lastrowid

    @property
    def mensaje(self):
        return self.message

    def escalar(self):
        return self.scalar()

    def primera(self):
        return dict(zip(self.columns, self.rows[0])) if self.rows else None


def _a_json(v):
    if isinstance(v, Decimal):
        return format(v, 'f')
    raise TypeError(f"no se puede enviar un {type(v).__name__} como parámetro")


class Cliente:
    def __init__(self, url, clave, base=None, tiempo=60, verificar_certificado=True, _cabeceras=None):
        self.url = url.rstrip('/')
        self.clave = clave
        self.base = base
        self.tiempo = tiempo
        self._sesion = None
        self._cabeceras = dict(_cabeceras or {})
        self._contexto = None if verificar_certificado else ssl._create_unverified_context()

    # ------------------------------------------------------------ HTTP
    def _peticion(self, metodo, ruta, cuerpo=None):
        datos = None if cuerpo is None else json.dumps(cuerpo, default=_a_json).encode('utf-8')
        cabeceras = {'Authorization': f'Bearer {self.clave}', 'Accept': 'application/json', **self._cabeceras}
        if datos is not None:
            cabeceras['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.url + ruta, data=datos, headers=cabeceras, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=self.tiempo, context=self._contexto) as resp:
                return json.load(resp), resp.status
        except urllib.error.HTTPError as e:
            try:
                return json.load(e), e.code
            except ValueError:
                raise ErrorServidor(f"el servidor respondió {e.code} sin explicación", 'http', e.code) from None
        except urllib.error.URLError as e:
            raise ErrorServidor(f"no se puede conectar con {self.url}: {e.reason}", 'conexion') from None

    # ------------------------------------------------------------ órdenes
    def todas(self, orden, parametros=()):
        """Ejecuta una o varias órdenes y devuelve un Resultado por cada una."""
        cuerpo = {'orden': orden, 'parametros': list(parametros)}
        if self.base:
            cuerpo['base'] = self.base
        if self._sesion:
            cuerpo['sesion'] = self._sesion
        datos, estado = self._peticion('POST', '/api/v1/ejecutar', cuerpo)
        if datos.get('base'):
            self.base = datos['base']
        self.en_transaccion = bool(datos.get('transaccion'))
        if datos.get('error'):
            raise _error(datos, estado)
        return [Resultado(r) for r in datos['resultados']]

    def ejecutar(self, orden, parametros=()):
        """Ejecuta y devuelve el Resultado de la última orden."""
        resultados = self.todas(orden, parametros)
        return resultados[-1] if resultados else Resultado({'mensaje': 'Nada que hacer'})

    def consultar(self, orden, parametros=()):
        """Atajo: las filas como lista de diccionarios."""
        return self.ejecutar(orden, parametros).dicts()

    def usar(self, base):
        self.base = base
        return self

    def catalogo(self):
        ruta = '/api/v1/catalogo' + (f'?base={urllib.request.quote(self.base)}' if self.base else '')
        datos, estado = self._peticion('GET', ruta)
        if datos.get('error'):
            raise _error(datos, estado)
        return datos

    def salud(self):
        datos, _ = self._peticion('GET', '/salud')
        return datos

    # ------------------------------------------------------------ sesiones
    def abrir_sesion(self):
        """Una sesión mantiene la base en uso y las transacciones entre peticiones."""
        datos, estado = self._peticion('POST', '/api/v1/sesiones', {'base': self.base})
        if datos.get('error'):
            raise _error(datos, estado)
        self._sesion = datos['sesion']
        return self._sesion

    def cerrar_sesion(self):
        if self._sesion:
            try:
                self._peticion('DELETE', f'/api/v1/sesiones/{self._sesion}')
            finally:
                self._sesion = None

    def transaccion(self):
        """with db.transaccion() as t: … — al salir confirma; si hay una excepción, deshace."""
        return _Transaccion(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cerrar_sesion()


class _Transaccion:
    def __init__(self, cliente):
        self._padre = cliente
        self._cliente = Cliente(cliente.url, cliente.clave, cliente.base, cliente.tiempo,
                                _cabeceras=cliente._cabeceras)
        self._cliente._contexto = cliente._contexto

    def __enter__(self):
        self._cliente.abrir_sesion()
        self._cliente.ejecutar('empezar')
        return self._cliente

    def __exit__(self, tipo, valor, traza):
        try:
            self._cliente.ejecutar('deshacer' if tipo else 'confirmar')
        finally:
            self._cliente.cerrar_sesion()
        return False


class SesionRemota:
    """Lo que necesita la consola, sobre un servidor en marcha en vez de sobre los ficheros."""

    def __init__(self, url, clave, base=None, directorio=None, verificar_certificado=True):
        cabeceras = {'SBD-Directorio': directorio} if directorio else {}
        self.cliente = Cliente(url, clave, base, verificar_certificado=verificar_certificado, _cabeceras=cabeceras)
        self.cliente.abrir_sesion()
        self.url = url

    @property
    def base(self):
        return self.cliente.base

    def iterar(self, texto, params=()):
        datos, estado = self.cliente._peticion('POST', '/api/v1/ejecutar', {
            'orden': texto, 'parametros': list(params), 'sesion': self.cliente._sesion,
            **({'base': self.cliente.base} if self.cliente.base else {})})
        if 'base' in datos:
            self.cliente.base = datos['base']
        self._transaccion = bool(datos.get('transaccion'))
        for r in datos.get('resultados', []):
            yield Resultado(r)
        if datos.get('error'):
            raise _error(datos, estado)

    def ejecutar(self, texto, params=()):
        resultado = Result(message='Nada que hacer')
        for resultado in self.iterar(texto, params):
            pass
        return resultado

    def en_transaccion(self):
        return getattr(self, '_transaccion', False)

    def catalogo(self):
        try:
            return self.cliente.catalogo()
        except SBDError:
            return {'bases': [], 'tablas': {}, 'vistas': []}

    def cerrar(self):
        try:
            self.cliente.cerrar_sesion()
        except SBDError:
            pass


def conectar_servidor(url, clave, base=None, **opciones):
    """Cliente para un servidor de SBD: sbd.conectar_servidor('https://…', clave='sbd_…', base='tienda')."""
    return Cliente(url, clave, base, **opciones)
