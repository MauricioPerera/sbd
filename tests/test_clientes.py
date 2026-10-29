import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest

import sbd
from sbd.web.server import ServidorHTTP

PHP = shutil.which('php')
CLIENTE_PHP = os.path.join(os.path.dirname(__file__), '..', 'clientes', 'php', 'SBD.php')


@unittest.skipUnless(PHP, 'no hay PHP instalado')
class TestClientePHP(unittest.TestCase):
    def test_de_punta_a_punta(self):
        with tempfile.TemporaryDirectory() as tmp:
            srv = sbd.servidor(tmp)
            s = sbd.Sesion(srv)
            s.ejecutar('crear base tienda  crear tabla p { id entero clave  nombre texto  precio dinero  stock entero = 5 }'
                       'insertar en p (nombre, precio) ("té", 4.5)  crear usuario app clave "clave de la app" puede escribir')
            clave = s.ejecutar('crear clave api para app').rows[0][0]
            http = ServidorHTTP(('127.0.0.1', 0), srv)
            threading.Thread(target=http.serve_forever, daemon=True).start()
            script = os.path.join(tmp, 'prueba.php')
            with open(script, 'w') as f:
                f.write(f"""<?php
require {json.dumps(os.path.abspath(CLIENTE_PHP))};
$db = new SBD($argv[1], $argv[2], 'tienda');
$r = ['precio' => $db->consultar('buscar p mostrar precio')[0]['precio']];
$db->transaccion(function (SBD $t) {{ $t->ejecutar('actualizar p poner stock = 4 todas'); }});
try {{ $db->transaccion(function (SBD $t) {{ $t->ejecutar('vaciar p'); throw new RuntimeException('x'); }}); }}
catch (RuntimeException $e) {{ }}
$r['stock'] = $db->ejecutar('buscar p mostrar stock')->escalar();
try {{ $db->ejecutar('buscar nada'); }} catch (SBDException $e) {{ $r['error'] = $e->tipo; }}
echo json_encode($r);
""")
            try:
                salida = subprocess.run([PHP, script, f"http://127.0.0.1:{http.server_address[1]}", clave],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(json.loads(salida.stdout), {'precio': '4.50', 'stock': 4, 'error': 'orden'}, salida.stderr)
            finally:
                http.shutdown()
                http.server_close()
                srv.cerrar()


if __name__ == '__main__':
    unittest.main()
