import json
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

import sbd
from sbd.errors import SBDError
from sbd.executor import Executor
from sbd.servidor import Sesion
from sbd.values import json_por_defecto


class TestLimiteOptimizado(unittest.TestCase):
    def setUp(self):
        self.db = sbd.conectar()
        self.db.execute('crear tabla t { id entero clave grupo entero valor texto }')
        self.db.execute('empezar')
        for i in range(1000):
            self.db.execute('insertar en t (id,grupo,valor) (?,?,?)', [i + 1, i % 4, f'fila {i}'])
        self.db.execute('confirmar')

    def tearDown(self):
        self.db.close()

    def test_consulta_simple_no_materializa_toda_la_tabla(self):
        table = self.db.table('t')
        with patch.object(table, 'scan', side_effect=AssertionError('se materializó la tabla')):
            self.assertEqual(self.db.execute('buscar t limite 2 saltar 3 mostrar id').rows, [(4,), (5,)])
            self.assertEqual(self.db.execute('buscar t donde grupo = 2 limite 2 saltar 1 mostrar id').rows,
                             [(7,), (11,)])
            self.assertEqual(self.db.execute('buscar t limite 0 mostrar valor').rows, [])

    def test_consulta_indexada_y_alias_respeta_filtro_y_offset(self):
        self.db.execute('indexar t (grupo)')
        q = 'buscar t como a donde a.grupo = ? mostrar a.id limite ? saltar ?'
        self.assertEqual(self.db.execute(q, [2, 3, 1]).rows, [(7,), (11,), (15,)])
        self.assertIn('detiene la lectura', self.db.execute('explicar buscar t limite 1').rows[-1][1])

    def test_orden_agrupacion_distintos_y_ventanas_mantienen_resultados(self):
        cases = [
            ('buscar t ordenar id desc mostrar id', 'buscar t ordenar id desc mostrar id limite 3 saltar 2'),
            ('buscar t mostrar distintos grupo ordenar grupo', 'buscar t mostrar distintos grupo ordenar grupo limite 2 saltar 1'),
            ('buscar t agrupar grupo mostrar grupo, contar() ordenar grupo',
             'buscar t agrupar grupo mostrar grupo, contar() ordenar grupo limite 2 saltar 1'),
            ('buscar t mostrar id, numero() sobre (ordenar id) como n ordenar id',
             'buscar t mostrar id, numero() sobre (ordenar id) como n ordenar id limite 3 saltar 2'),
        ]
        for full, limited in cases:
            with self.subTest(query=limited):
                expected = self.db.execute(full).rows
                offset, limit = (2, 3) if 'saltar 2' in limited else (1, 2)
                with patch.object(Executor, '_leer_con_limite', side_effect=AssertionError('ruta no segura')):
                    self.assertEqual(self.db.execute(limited).rows, expected[offset:offset + limit])

    def test_comprueba_columnas_y_no_optimiza_expresiones_aritmeticas(self):
        with self.assertRaises(SBDError):
            self.db.execute('buscar t limite 0 mostrar inexistente')
        with self.assertRaises(SBDError):
            self.db.execute('buscar t limite 1 mostrar 1 / (id - 2)')

    def test_orden_tras_deshacer_una_eliminacion(self):
        self.db.execute('empezar borrar de t donde id = 1 deshacer')
        self.assertEqual(self.db.execute('buscar t limite 3 mostrar id').rows, [(1,), (2,), (3,)])


class TestCuotaIncremental(unittest.TestCase):
    def setUp(self):
        self.db = sbd.conectar()
        self.db.execute('crear tabla t { id entero clave nombre texto monto dinero }')
        for i in range(25):
            self.db.execute('insertar en t (nombre,monto) (?,?)', [f'café {i}', Decimal('12.50')])

    def tearDown(self):
        self.db.close()

    def assert_snapshot_matches(self):
        reference = len(json.dumps(self.db._snapshot_data(), ensure_ascii=False, default=json_por_defecto).encode('utf-8'))
        self.assertEqual(self.db.quota_snapshot_size(), reference)

    def assert_journal_matches(self):
        reference = sum(len(json.dumps(redo, ensure_ascii=False, default=json_por_defecto).encode('utf-8')) + 128
                        for _, redo in (self.db._undo or ()) if redo is not None)
        self.assertEqual(self.db.quota_pending_size(), reference)

    def test_contadores_reflejan_insercion_actualizacion_borrado_y_rollback(self):
        self.assert_snapshot_matches()
        self.db.execute('empezar')
        self.assert_journal_matches()
        for command, params in [
            ('insertar en t (nombre,monto) (?,?)', ['漢字\n"\\', Decimal('0.10')]),
            ('actualizar t poner nombre = ? donde id = 1', ['texto más largo']),
            ('borrar de t donde id = 2', []),
        ]:
            self.db.execute(command, params)
            self.assert_snapshot_matches()
            self.assert_journal_matches()
        self.db.execute('deshacer')
        self.assert_snapshot_matches()
        self.db.execute('insertar en t (nombre,monto) (?,?)', ['nuevo', Decimal('3.25')])
        self.assert_snapshot_matches()

    def test_contadores_reflejan_cambios_de_esquema_y_su_rollback(self):
        self.assert_snapshot_matches()
        self.db.execute('empezar')
        for command in ['añadir a t extra texto = "hola"', 'renombrar columna t.nombre a titulo',
                        'indexar t (titulo)', 'insertar en t { titulo: "漢字" }']:
            self.db.execute(command)
            self.assert_snapshot_matches()
            self.assert_journal_matches()
        self.db.execute('deshacer')
        self.assert_snapshot_matches()
        self.assertEqual(self.db.table('t').colnames, ['id', 'nombre', 'monto'])

    def test_rollback_intercala_cambios_de_filas_y_esquema(self):
        self.assert_snapshot_matches()
        self.db.validate_write = lambda db: (db.quota_snapshot_size(), db.quota_pending_size())
        self.db.execute('empezar')
        for command in ['insertar en t { nombre: "antes" }', 'indexar t (nombre)',
                        'actualizar t poner nombre = "después" donde id = 1',
                        'añadir a t extra texto = "hola"', 'borrar de t donde id = 2']:
            self.db.execute(command)
            self.assert_snapshot_matches()
            self.assert_journal_matches()
        self.db.execute('deshacer')
        self.assert_snapshot_matches()
        self.assert_journal_matches()

    def test_actualizacion_rechazada_por_cuota_restaura_contadores(self):
        with tempfile.TemporaryDirectory() as carpeta:
            srv = sbd.servidor(carpeta)
            try:
                srv.cambiar_ajustes(registro=True, max_mb=1)
                sesion = Sesion(srv, usuario=srv.registrar('ana', 'clave1234'))
                sesion.ejecutar('crear base prueba crear tabla t { id entero clave valor texto }')
                sesion.ejecutar('insertar en t { valor: "pequeño" }')
                with self.assertRaisesRegex(SBDError, 'espacio'):
                    sesion.ejecutar('actualizar t poner valor = ? donde id = 1', ['x' * 1_100_000])
                self.assertEqual(sesion.ejecutar('buscar t mostrar valor').fetchall(), [('pequeño',)])
                db = sesion.db
                reference = len(json.dumps(db._snapshot_data(), ensure_ascii=False, default=json_por_defecto).encode('utf-8'))
                self.assertEqual(db.quota_snapshot_size(), reference)
                sesion.ejecutar('actualizar t poner valor = "correcto" donde id = 1')
                self.assertEqual(sesion.ejecutar('buscar t mostrar valor').fetchall(), [('correcto',)])
            finally:
                srv.cerrar()

    def test_validaciones_reutilizan_tamanos_de_filas_y_diario(self):
        self.db.quota_snapshot_size()  # primera medición completa
        table = self.db.table('t')
        self.db.validate_write = lambda db: (db.quota_snapshot_size(), db.quota_pending_size())
        with patch.object(table, '_snapshot_row_size', wraps=table._snapshot_row_size) as sizes, \
             patch.object(self.db, '_quota_redo_size', wraps=self.db._quota_redo_size) as redo_sizes:
            self.db.execute('empezar')
            for i in range(20):
                self.db.execute('insertar en t { nombre: ? }', [str(i)])
            self.assertLessEqual(sizes.call_count, 20)
            self.assertLessEqual(redo_sizes.call_count, 21)
            self.assert_snapshot_matches()
            self.assert_journal_matches()
            self.db.execute('deshacer')
        self.assert_snapshot_matches()


if __name__ == '__main__':
    unittest.main()
