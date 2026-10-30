import http.client
import itertools
import json
import random
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import sbd
from sbd.web.server import ServidorHTTP, _comprobar_profundidad_json


class TestJSONHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.servidor = sbd.servidor(self.tmp.name)
        self.http = ServidorHTTP(('127.0.0.1', 0), self.servidor)
        self.hilo = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.hilo.start()
        self.url = f'http://127.0.0.1:{self.http.server_address[1]}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.hilo.join(timeout=5)
        self.servidor.cerrar()
        self.tmp.cleanup()

    def post(self, payload, tipo='application/json'):
        request = urllib.request.Request(self.url + '/api/ejecutar', data=payload,
                                         headers={'Content-Type': tipo})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                response.read()
                return response.status
        except urllib.error.HTTPError as error:
            with error:
                error.read()
                return error.code

    def test_json_ambiguo_no_ejecuta_ordenes(self):
        payloads = [
            b'{"orden":"tablas","orden":"crear base infiltrada"}',
            b'{"orden":"crear base infiltrada","extra":{"x":1,"x":2}}',
            b'{"orden":"crear base infiltrada","extra":NaN}',
            b'{"orden":"crear base infiltrada","extra":Infinity}',
            b'{"orden":"crear base infiltrada","extra":1e9999}',
            b'{"orden":"crear base infiltrada","extra":"\\ud800"}',
            b'{"orden":"crear base infiltrada","extra":[{"valor":"\\udfff"}]}',
            b'{"orden":"crear base infiltrada","\\udfff":1}',
            b'\xff\xfe' + '{"orden":"crear base infiltrada"}'.encode('utf-16-le'),
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload), 400)
                self.assertFalse(self.servidor.existe('infiltrada'))
        self.assertEqual(self.post(b'{"orden":"crear base valida","extra":"\\ud83d\\ude00"}',
                                   'Application/JSON; charset=UTF-8'), 200)
        self.assertTrue(self.servidor.existe('valida'))

    def test_tipo_de_contenido_exacto_y_no_duplicado(self):
        self.assertEqual(self.post(b'{}', 'application/jsonp'), 415)
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_address[1], timeout=10)
        try:
            conn.putrequest('POST', '/api/ejecutar')
            conn.putheader('Content-Type', 'application/json')
            conn.putheader('Content-Type', 'text/plain')
            conn.putheader('Content-Length', '0')
            conn.endheaders()
            response = conn.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
        finally:
            conn.close()

    def test_limite_profundidad_y_cadenas(self):
        for cantidad, status in ((63, 200), (64, 400), (1500, 400)):
            payload = b'{"orden":"tablas","extra":' + b'[' * cantidad + b'0' + b']' * cantidad + b'}'
            with self.subTest(cantidad=cantidad):
                self.assertEqual(self.post(payload), status)
        texto = ('[{' * 100) + '\\"\\\\' + ('}]' * 100) + '😀'
        self.assertEqual(self.post(json.dumps({'orden': 'tablas', 'extra': texto}).encode()), 200)

    def test_complejidad_antes_de_construir_objeto(self):
        def payload(cantidad):
            return b'{"orden":"tablas","extra":[' + b'0,' * (cantidad - 1) + b'0]}'
        self.assertEqual(self.post(payload(99998)), 200)
        with patch('sbd.web.server.json.loads', side_effect=AssertionError('se intentó decodificar')) as loads:
            self.assertEqual(self.post(payload(99999)), 400)
            loads.assert_not_called()
        self.assertEqual(self.post(json.dumps({'extra': ',[{}]' * 30000}).encode()), 200)

    def test_limite_escapes_antes_de_construir_objeto(self):
        payload = b'{"orden":"tablas","extra":"' + b'\\n' * 1000001 + b'"}'
        with patch('sbd.web.server.json.loads', side_effect=AssertionError('se intentó decodificar')) as loads:
            self.assertEqual(self.post(payload), 400)
            loads.assert_not_called()
        base = '\\' * 4095 + 'é'
        with patch('sbd.web.server.MAX_ESCAPES_JSON', 4096):
            self.assertEqual(self.post(json.dumps({'orden': 'tablas', 'extra': base}).encode()), 200)
            self.assertEqual(self.post(json.dumps({'orden': 'tablas', 'extra': base + '😀'}).encode()), 400)

    def test_recuento_escapes_cortos_y_largos(self):
        with patch('sbd.web.server.MAX_ESCAPES_JSON', 3):
            for largo in range(5):
                for caracteres in itertools.product('a"\\\né😀', repeat=largo):
                    texto = ''.join(caracteres)
                    esperados = sum(2 if c == '😀' else int(c in '"\\\né') for c in texto)
                    payload = json.dumps({'texto': texto}, ensure_ascii=True).encode()
                    if esperados <= 3:
                        _comprobar_profundidad_json(payload)
                    else:
                        with self.assertRaises(ValueError):
                            _comprobar_profundidad_json(payload)
        azar = random.Random(20261030)
        for largo in (4095, 4096, 4097, 8191, 8192):
            texto = ''.join(azar.choice('a"\\\né😀\x00') for _ in range(largo))
            esperados = sum(2 if c == '😀' else int(c in '"\\\né\x00') for c in texto)
            payload = json.dumps({'texto': texto}, ensure_ascii=True).encode()
            with patch('sbd.web.server.MAX_ESCAPES_JSON', esperados):
                _comprobar_profundidad_json(payload)
            with patch('sbd.web.server.MAX_ESCAPES_JSON', esperados - 1), self.assertRaises(ValueError):
                _comprobar_profundidad_json(payload)

    def test_referencia_del_ensayo_coincide_en_limites_y_escapes(self):
        from scripts.ensayo_json_carga import anterior
        def aceptado(funcion, payload):
            try:
                funcion(payload)
                return True
            except ValueError:
                return False
        with patch('sbd.web.server.MAX_ESCAPES_JSON', 3):
            for largo in range(5):
                for caracteres in itertools.product('a"\\\né😀', repeat=largo):
                    payload = json.dumps({'texto': ''.join(caracteres)}, ensure_ascii=True).encode()
                    self.assertEqual(aceptado(anterior, payload), aceptado(_comprobar_profundidad_json, payload))
        for cantidad in (63, 64):
            payload = b'{"extra":' + b'[' * cantidad + b'0' + b']' * cantidad + b'}'
            self.assertEqual(aceptado(anterior, payload), aceptado(_comprobar_profundidad_json, payload))
        for cantidad in (99998, 99999):
            payload = b'{"orden":"tablas","extra":[' + b'0,' * (cantidad - 1) + b'0]}'
            self.assertEqual(aceptado(anterior, payload), aceptado(_comprobar_profundidad_json, payload))


if __name__ == '__main__':
    unittest.main()
