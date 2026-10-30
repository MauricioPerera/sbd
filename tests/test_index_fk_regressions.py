import os
import tempfile
import unittest
from decimal import Decimal

import sbd
from sbd.errors import SBDError


class TestIndexEquivalence(unittest.TestCase):
    def setUp(self):
        self.scan = sbd.conectar()
        self.indexed = sbd.conectar()
        for db in (self.scan, self.indexed):
            db.execute('crear tabla t { id entero valor entero importe dinero fecha_val fecha }')
            for i, value in enumerate((-3, -2, -1, 0, 1, 2, 3), 1):
                db.execute('insertar en t (id,valor,importe,fecha_val) (?,?,?,?)',
                           [i, value, Decimal(value) / 100, '2024-05-01'])
        self.indexed.execute('indexar t (valor) como idx_valor  indexar t (importe) como idx_importe '
                             'indexar t (fecha_val) como idx_fecha')

    def tearDown(self):
        self.scan.close()
        self.indexed.close()

    def test_rangos_y_entre_equivalen_a_escaneo(self):
        for column, boundaries in (('valor', (-1.6, -1.4, 1.4, 1.6)),
                                   ('importe', (Decimal('-0.016'), Decimal('-0.014'),
                                                Decimal('0.014'), Decimal('0.016')))):
            for boundary in boundaries:
                for op in ('=', '>', '>=', '<', '<='):
                    query = f'buscar t donde {column} {op} ? ordenar id mostrar id'
                    with self.subTest(column=column, op=op, boundary=boundary):
                        self.assertEqual(self.indexed.execute(query, [boundary]).rows,
                                         self.scan.execute(query, [boundary]).rows)
                query = f'buscar t donde ? < {column} ordenar id mostrar id'
                self.assertEqual(self.indexed.execute(query, [boundary]).rows,
                                 self.scan.execute(query, [boundary]).rows)
            query = f'buscar t donde {column} entre ? y ? ordenar id mostrar id'
            self.assertEqual(self.indexed.execute(query, [boundaries[0], boundaries[-1]]).rows,
                             self.scan.execute(query, [boundaries[0], boundaries[-1]]).rows)

    def test_fecha_no_se_normaliza_en_el_indice_al_comparar(self):
        query = 'buscar t donde fecha_val < ? ordenar id mostrar id'
        self.assertEqual(self.indexed.execute(query, ['2024-05-01T12:00']).rows,
                         self.scan.execute(query, ['2024-05-01T12:00']).rows)

    def test_actualizar_y_borrar_coinciden_con_escaneo(self):
        for db in (self.scan, self.indexed):
            db.execute('actualizar t poner importe = 9 donde valor > 1.6')
            db.execute('borrar de t donde valor < -1.6')
        self.assertEqual(self.indexed.execute('buscar t ordenar id').rows,
                         self.scan.execute('buscar t ordenar id').rows)

    def test_valor_exactamente_representable_conserva_uso_del_indice(self):
        plan = self.indexed.execute('explicar buscar t donde valor >= 2').rows
        self.assertIn('idx_valor', plan[0][1])

    def test_nan_no_hace_que_el_indice_omita_filas(self):
        for db in (self.scan, self.indexed):
            db.execute('crear tabla nan_tabla { id entero x decimal }')
            db.execute('insertar en nan_tabla (id,x) (?,?)', [1, float('nan')])
            db.execute('insertar en nan_tabla (id,x) (2,1)')
        self.indexed.execute('indexar nan_tabla (x)')
        query = 'buscar nan_tabla donde x = 1 ordenar id mostrar id'
        self.assertEqual(self.indexed.execute(query).rows, self.scan.execute(query).rows)
        # Al retirar el NaN, el índice vuelve a ser apto y sigue ordenado.
        for db in (self.scan, self.indexed):
            db.execute('borrar de nan_tabla donde id = 1')
        self.assertEqual(self.indexed.execute(query).rows, self.scan.execute(query).rows)
        self.assertIn('nan_tabla_x_idx', self.indexed.execute('explicar ' + query).rows[0][1])

    def test_parametro_nan_no_se_usa_como_clave_de_indice(self):
        for op in ('=', '<', '>', '<=', '>='):
            query = f'buscar t donde valor {op} ? ordenar id mostrar id'
            self.assertEqual(self.indexed.execute(query, [float('nan')]).rows,
                             self.scan.execute(query, [float('nan')]).rows)

    def test_indice_con_nan_se_reconstruye_tras_reabrir(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, 'datos.base')
            with sbd.conectar(ruta) as db:
                db.execute('crear tabla t { id entero x decimal } indexar t (x)')
                db.execute('insertar en t (id,x) (?,?)', [1, float('nan')])
                db.execute('insertar en t (id,x) (2,1)')
            with sbd.conectar(ruta) as db:
                self.assertEqual(db.execute('buscar t donde x = 1 ordenar id mostrar id').rows, [(1,), (2,)])
                db.execute('actualizar t poner x = 2 donde id = 1')
                self.assertEqual(db.execute('buscar t donde x = 1 mostrar id').rows, [(2,)])
                self.assertIn('t_x_idx', db.execute('explicar buscar t donde x = 1').rows[0][1])


class TestForeignKeyEquivalence(unittest.TestCase):
    def setUp(self):
        self.db = sbd.conectar()

    def tearDown(self):
        self.db.close()

    def test_decimal_no_apunta_a_entero_redondeado(self):
        self.db.execute('crear tabla p { id entero clave } crear tabla c { pid decimal -> p(id) } '
                        'insertar en p { id: 2 }')
        with self.assertRaises(SBDError):
            self.db.execute('insertar en c { pid: 1.6 }')
        self.db.execute('insertar en c { pid: 2.0 }')
        with self.assertRaises(SBDError):
            self.db.execute('borrar de p donde id = 2')
        with self.assertRaises(SBDError):
            self.db.execute('actualizar p poner id = 3 donde id = 2')

    def test_no_bloquea_padre_que_solo_coincide_por_redondeo(self):
        self.db.execute('crear tabla p { id decimal clave } crear tabla c { pid entero -> p(id) } '
                        'insertar en p (1.6) (2.0) insertar en c (2)')
        self.db.execute('borrar de p donde id = 1.6')
        with self.assertRaises(SBDError):
            self.db.execute('borrar de p donde id = 2.0')

    def test_dinero_y_decimal_comparan_sin_redondear(self):
        self.db.execute('crear tabla p { id dinero clave } crear tabla c { pid decimal -> p(id) } '
                        'insertar en p (1.01)')
        with self.assertRaises(SBDError):
            self.db.execute('insertar en c (1.006)')
        self.db.execute('insertar en c (1.01)')
        with self.assertRaises(SBDError):
            self.db.execute('borrar de p donde id = 1.01')

    def test_padre_de_texto_y_hijo_entero_usan_la_misma_comparacion(self):
        self.db.execute('crear tabla p { id texto clave } crear tabla c { pid entero -> p(id) } '
                        'insertar en p ("01") insertar en c (1)')
        with self.assertRaises(SBDError):
            self.db.execute('borrar de p donde id = "01"')

    def test_texto_numerico_se_compara_igual_con_o_sin_indice_hijo(self):
        for index in (False, True):
            with self.subTest(index=index):
                db = sbd.conectar()
                try:
                    db.execute('crear tabla p { id entero clave } crear tabla c { pid texto -> p(id) } '
                               'insertar en p (1) insertar en c ("01")')
                    if index:
                        db.execute('indexar c (pid)')
                    with self.assertRaises(SBDError):
                        db.execute('borrar de p donde id = 1')
                    with self.assertRaises(SBDError):
                        db.execute('actualizar p poner id = 2 donde id = 1')
                finally:
                    db.close()

    def test_cascada_y_anular_con_texto_numerico_indexado(self):
        for action in ('cascada', 'anular'):
            with self.subTest(action=action):
                db = sbd.conectar()
                try:
                    db.execute(f'crear tabla p {{ id entero clave }} crear tabla c {{ pid texto -> p(id) al borrar {action} }} '
                               'indexar c (pid) insertar en p (1) insertar en c ("01")')
                    db.execute('borrar de p donde id = 1')
                    expected = [] if action == 'cascada' else [(None,)]
                    self.assertEqual(db.execute('buscar c').rows, expected)
                finally:
                    db.close()

    def test_autorreferencia_se_compara_por_valor(self):
        self.db.execute('crear tabla t { id texto clave padre entero -> t(id) } insertar en t ("01", 1)')
        self.assertEqual(self.db.execute('buscar t').rows, [('01', 1)])

    def test_clave_compuesta_no_acepta_coincidencias_redondeadas(self):
        self.db.execute('crear tabla p { a entero b dinero clave (a,b) } '
                        'crear tabla c { x decimal y decimal, (x,y) -> p(a,b) al borrar cascada } '
                        'insertar en p (2,1.01)')
        for values in ((1.6, 1.01), (2, 1.006)):
            with self.subTest(values=values), self.assertRaises(SBDError):
                self.db.execute('insertar en c (?,?)', values)
        self.db.execute('insertar en c (2,1.01)  indexar c (y,x)')
        self.db.execute('borrar de p donde a = 2')
        self.assertEqual(self.db.execute('buscar c').rows, [])


if __name__ == '__main__':
    unittest.main()
