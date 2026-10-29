import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from decimal import Decimal

import sbd
from sbd.cliente import SesionRemota
from sbd.web.server import ServidorHTTP


class ConServidor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = sbd.servidor(self.tmp.name, limite_segundos=5)
        s = sbd.Sesion(self.srv)
        s.ejecutar('''
            crear base tienda
            crear tabla productos { id entero clave  nombre texto obligatorio  precio dinero  stock entero = 0 }
            insertar en productos (nombre, precio, stock) ("té", 4.5, 10) ("café", 12.99, 3)
            crear base privada
            crear usuario app clave "clave de la app" puede escribir en tienda
            crear usuario lector clave "clave del lector" puede leer en tienda
        ''')
        self.clave = s.ejecutar('crear clave api para app llamada "pruebas"').rows[0][0]
        self.clave_lector = s.ejecutar('crear clave api para lector').rows[0][0]
        self.http = ServidorHTTP(('127.0.0.1', 0), self.srv)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.db = sbd.conectar_servidor(self.url, clave=self.clave, base='tienda')

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.srv.cerrar()
        self.tmp.cleanup()


class TestApi(ConServidor):
    def test_consultar_con_parametros_y_dinero_exacto(self):
        filas = self.db.consultar('buscar productos donde precio < ? ordenar precio', [20])
        self.assertEqual([(f['nombre'], f['precio']) for f in filas], [('té', Decimal('4.50')), ('café', Decimal('12.99'))])
        nuevo = self.db.ejecutar('insertar en productos { nombre: ?, precio: ? } devolver id', ['mate', Decimal('7.10')])
        self.assertEqual(nuevo.escalar(), 3)
        self.assertEqual(self.db.ejecutar('buscar productos donde id = 3 mostrar precio').escalar(), Decimal('7.10'))

    def test_transaccion_confirma_o_deshace(self):
        with self.db.transaccion() as t:
            t.ejecutar('actualizar productos poner stock = stock - 1 donde id = 1')
        self.assertEqual(self.db.ejecutar('buscar productos donde id = 1 mostrar stock').escalar(), 9)
        with self.assertRaises(RuntimeError):
            with self.db.transaccion() as t:
                t.ejecutar('vaciar productos')
                raise RuntimeError('la aplicación falla a mitad')
        self.assertEqual(self.db.ejecutar('buscar productos mostrar contar()').escalar(), 2)

    def test_una_peticion_no_puede_dejar_una_transaccion_abierta(self):
        with self.assertRaises(sbd.ErrorServidor) as ctx:
            self.db.ejecutar('empezar  vaciar productos')
        self.assertEqual(ctx.exception.tipo, 'transaccion_abierta')
        self.assertEqual(self.db.ejecutar('buscar productos mostrar contar()').escalar(), 2)
        self.db.ejecutar('empezar  insertar en productos { nombre: "x" }  confirmar')  # en una sola petición, sí
        self.assertEqual(self.db.ejecutar('buscar productos mostrar contar()').escalar(), 3)

    def test_errores_con_su_tipo(self):
        casos = [('buscar nada', 'orden'), ('selec', 'sintaxis'), ('buscar productos {', 'incompleta'),
                 ('crear base otra', 'permiso')]
        for orden, tipo in casos:
            with self.assertRaises(sbd.SBDError, msg=orden) as ctx:
                self.db.ejecutar(orden)
            self.assertEqual(ctx.exception.tipo, tipo, orden)
        with self.assertRaises(sbd.PermisoDenegado):
            sbd.conectar_servidor(self.url, clave=self.clave, base='privada').ejecutar('tablas')
        with self.assertRaises(sbd.PermisoDenegado):
            sbd.conectar_servidor(self.url, clave=self.clave_lector, base='tienda').ejecutar('vaciar productos')
        with self.assertRaises(sbd.ErrorServidor) as ctx:
            sbd.conectar_servidor(self.url, clave='sbd_kmala_x', base='tienda').ejecutar('tablas')
        self.assertEqual((ctx.exception.tipo, ctx.exception.estado), ('clave', 401))

    def test_base_ocupada_espera_y_avisa(self):
        t = self.db.transaccion().__enter__()
        t.ejecutar('insertar en productos { nombre: "a medias" }')
        inicio = time.time()
        with self.assertRaises(sbd.BaseOcupada):
            self.db.ejecutar('buscar productos')
        self.assertGreater(time.time() - inicio, 4)  # ha esperado antes de rendirse
        t.ejecutar('deshacer')
        t.cerrar_sesion()
        self.assertEqual(self.db.ejecutar('buscar productos mostrar contar()').escalar(), 2)

    def test_salud_y_catalogo(self):
        self.assertEqual(self.db.salud()['version'], sbd.__version__)
        self.assertEqual(self.db.catalogo()['tablas']['productos'], ['id', 'nombre', 'precio', 'stock'])

    def test_cors_solo_para_origenes_permitidos(self):
        self.http.origenes = {'https://miapp.es'}
        def preflight(origen):
            req = urllib.request.Request(self.url + '/api/v1/ejecutar', method='OPTIONS', headers={'Origin': origen})
            try:
                with urllib.request.urlopen(req) as resp:
                    return resp.status, resp.headers.get('Access-Control-Allow-Origin')
            except urllib.error.HTTPError as e:
                return e.code, None
        self.assertEqual(preflight('https://miapp.es'), (204, 'https://miapp.es'))
        self.assertEqual(preflight('https://otra.web'), (403, None))

    def test_auditoria_firma_con_el_usuario(self):
        self.db.ejecutar('insertar en productos { nombre: "auditado" }')
        ultima = sbd.Sesion(self.srv).ejecutar('historial limite 1').rows[0]
        self.assertEqual((ultima[1], ultima[3]), ('app · api', 'insertar en productos { nombre: "auditado" }'))


class TestConsolaRemota(ConServidor):
    def test_la_consola_usa_el_servidor(self):
        consola = SesionRemota(self.url, self.http.nucleo.clave_local, 'tienda')
        consola.ejecutar('empezar  insertar en productos { nombre: "desde la consola" }')
        self.assertTrue(consola.en_transaccion())
        with self.assertRaises(sbd.BaseOcupada):
            self.db.ejecutar('buscar productos')  # la consola tiene la base reservada
        consola.ejecutar('confirmar')
        self.assertFalse(consola.en_transaccion())
        self.assertEqual(self.db.ejecutar('buscar productos mostrar contar()').escalar(), 3)
        self.assertIn('productos', consola.catalogo()['tablas'])
        consola.ejecutar('crear base desde_consola')  # la clave local puede todo
        consola.cerrar()


if __name__ == '__main__':
    unittest.main()
