import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import sbd
from sbd.errors import PermisoDenegado
from sbd.repl import Consola
from sbd.servidor import Servidor
from sbd.web.server import Nucleo


class TestLimiteMemoria(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = Servidor(self.tmp.name)
        self.srv.cambiar_ajustes(registro=True)
        self.sesion = sbd.Sesion(self.srv, usuario=self.srv.registrar('ana', 'clave1234'))
        self.sesion.ejecutar('crear base prueba crear tabla t { id entero clave } insertar en t {}')
        self.srv.cambiar_ajustes(max_memoria_mb=1)

    def tearDown(self):
        self.srv.cerrar()
        self.tmp.cleanup()

    def test_uso_al_limite_impide_escritura_pero_no_lectura_o_borrado(self):
        with patch.object(Servidor, 'memoria_mb', return_value=1):
            with self.assertRaises(PermisoDenegado):
                self.sesion.ejecutar('insertar en t {}')
            self.assertEqual(self.sesion.ejecutar('buscar t').rowcount, 1)
            self.sesion.ejecutar('vaciar t')
            sbd.Sesion(self.srv, 'prueba').ejecutar('insertar en t {}')

    def test_memoria_no_disponible_no_se_interpreta_como_cero(self):
        with patch.object(Servidor, 'memoria_mb', return_value=None):
            with self.assertRaisesRegex(PermisoDenegado, 'memoria'):
                self.sesion.ejecutar('insertar en t {}')
            self.assertEqual(self.sesion.ejecutar('buscar t').rowcount, 1)
            self.assertIsNone(Nucleo(self.srv).ajustes(None)['memoria_mb'])

    def test_si_operacion_supera_limite_se_deshace(self):
        with patch.object(Servidor, 'memoria_mb', side_effect=[0, 2]):
            with self.assertRaises(PermisoDenegado):
                self.sesion.ejecutar('insertar en t {}')
        self.assertEqual(self.sesion.ejecutar('buscar t').rowcount, 1)

    @unittest.skipUnless(sys.platform == 'win32', 'la prueba usa la API de memoria de Windows')
    def test_memoria_real_windows_y_limite(self):
        memoria = self.srv.memoria_mb()
        self.assertIsInstance(memoria, int)
        self.assertGreater(memoria, 1)
        with self.assertRaises(PermisoDenegado):
            self.sesion.ejecutar('insertar en t {}')


class TestConsolaCodificacion(unittest.TestCase):
    def test_salida_cp1252_muestra_tabla_sin_fallar_y_preserva_datos_escapados(self):
        with tempfile.TemporaryDirectory() as carpeta:
            env = dict(os.environ, PYTHONIOENCODING='cp1252:strict', PYTHONUTF8='0')
            command = [sys.executable, '-m', 'sbd', carpeta, '-e',
                       'crear base prueba crear tabla t { nombre texto } insertar en t ("café") ("漢") buscar t']
            result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode('cp1252', errors='replace'))
            output = result.stdout.decode('cp1252')
            self.assertIn('café', output)
            self.assertIn(r'\u6f22', output)
            self.assertIn('-', output)
            with sbd.servidor(carpeta) as servidor:
                self.assertEqual(sbd.Sesion(servidor, 'prueba').ejecutar('buscar t').rows, [('café',), ('漢',)])

    def test_ejemplo_y_ayuda_funcionan_con_cp1252(self):
        with tempfile.TemporaryDirectory() as carpeta:
            env = dict(os.environ, PYTHONIOENCODING='cp1252:strict', PYTHONUTF8='0')
            for extra in (['-h'], [carpeta, '-f', 'ejemplos/tienda.sbd', '-e', 'buscar productos mostrar contar()']):
                with self.subTest(extra=extra):
                    result = subprocess.run([sys.executable, '-m', 'sbd', *extra], env=env,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr.decode('cp1252', errors='replace'))
                    if '-f' in extra:
                        self.assertIn('9', result.stdout.decode('cp1252'))

    def test_error_en_stderr_con_cp1252_no_da_unicode_error(self):
        env = dict(os.environ, PYTHONIOENCODING='cp1252:strict', PYTHONUTF8='0')
        result = subprocess.run([sys.executable, '-m', 'sbd', '--servidor', 'https://localhost'], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(b'UnicodeEncodeError', result.stderr)
        self.assertIn(b'clave', result.stderr)

    def test_consola_interactiva_con_salida_cp1252(self):
        with tempfile.TemporaryDirectory() as carpeta:
            env = dict(os.environ, PYTHONIOENCODING='cp1252:strict', PYTHONUTF8='0')
            result = subprocess.run([sys.executable, '-m', 'sbd', carpeta], input=b'crear base prueba\nsalir\n',
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode('cp1252', errors='replace'))
            self.assertIn(b'prueba', result.stdout)

    def test_utf8_preserva_tabla_y_caracteres(self):
        with tempfile.TemporaryDirectory() as carpeta:
            env = dict(os.environ, PYTHONIOENCODING='utf-8:strict')
            result = subprocess.run([sys.executable, '-m', 'sbd', carpeta, '-e', 'crear base t calcular "漢", 1'], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
            self.assertIn('漢', result.stdout.decode('utf-8'))
            self.assertIn('│', result.stdout.decode('utf-8'))

    def test_salida_sin_codificacion_conserva_unicode(self):
        salida = io.StringIO()
        Consola(None, salida=salida).escribir('tabla │ 漢')
        self.assertEqual(salida.getvalue(), 'tabla │ 漢\n')


if __name__ == '__main__':
    unittest.main()
