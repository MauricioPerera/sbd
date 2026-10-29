<?php
/**
 * Cliente de SBD para PHP 7.4+. Sin dependencias (usa curl si está, si no, streams).
 *
 *   require 'SBD.php';
 *   $db = new SBD('https://datos.miweb.es', getenv('SBD_CLAVE'), 'tienda');
 *   foreach ($db->consultar('buscar productos donde precio < ? ordenar precio', [20]) as $p) {
 *       echo $p['nombre'], ' ', $p['precio'], "\n";   // el dinero llega como texto exacto: "12.50"
 *   }
 *   $id = $db->ejecutar('insertar en pedidos { cliente_id: ? } devolver id', [3])->escalar();
 *   $db->transaccion(function (SBD $t) use ($id) {
 *       $t->ejecutar('actualizar productos poner stock = stock - 1 donde id = ?', [7]);
 *       $t->ejecutar('insertar en lineas { pedido_id: ?, producto_id: 7, cantidad: 1 }', [$id]);
 *   });
 */

class SBDException extends Exception
{
    /** sintaxis, orden, permiso, ocupada, clave, conexion… */
    public $tipo;
    public $estado;
    public $orden;

    public function __construct($mensaje, $tipo = 'orden', $estado = null, $orden = null)
    {
        parent::__construct($mensaje);
        $this->tipo = $tipo;
        $this->estado = $estado;
        $this->orden = $orden;
    }
}

class SBDResultado implements IteratorAggregate, Countable
{
    public $orden;
    public $columnas;
    public $tipos;
    public $filas;
    public $filasAfectadas;
    public $ultimoId;
    public $mensaje;

    public function __construct(array $datos)
    {
        $this->orden = $datos['orden'] ?? null;
        $this->columnas = $datos['columnas'] ?? null;
        $this->tipos = $datos['tipos'] ?? [];
        $this->filas = $datos['filas'] ?? [];
        $this->filasAfectadas = $datos['filas_afectadas'] ?? null;
        $this->ultimoId = $datos['ultimo_id'] ?? null;
        $this->mensaje = $datos['mensaje'] ?? '';
    }

    /** Las filas como arrays asociativos. */
    public function objetos(): array
    {
        if (!$this->columnas) {
            return [];
        }
        return array_map(function ($fila) {
            return array_combine($this->columnas, $fila);
        }, $this->filas);
    }

    public function escalar()
    {
        return $this->filas ? $this->filas[0][0] : null;
    }

    public function primera()
    {
        $todas = $this->objetos();
        return $todas ? $todas[0] : null;
    }

    public function getIterator(): Iterator
    {
        return new ArrayIterator($this->objetos());
    }

    public function count(): int
    {
        return count($this->filas);
    }
}

class SBD
{
    private $url;
    private $clave;
    public $base;
    public $sesion = null;
    private $tiempo;

    public function __construct(string $url, string $clave, ?string $base = null, int $tiempo = 60)
    {
        $this->url = rtrim($url, '/');
        $this->clave = $clave;
        $this->base = $base;
        $this->tiempo = $tiempo;
    }

    private function peticion(string $metodo, string $ruta, ?array $cuerpo = null): array
    {
        $cabeceras = ['Authorization: Bearer ' . $this->clave, 'Accept: application/json'];
        $datos = null;
        if ($cuerpo !== null) {
            $datos = json_encode($cuerpo, JSON_UNESCAPED_UNICODE);
            $cabeceras[] = 'Content-Type: application/json';
        }
        if (function_exists('curl_init')) {
            $c = curl_init($this->url . $ruta);
            curl_setopt_array($c, [
                CURLOPT_CUSTOMREQUEST => $metodo, CURLOPT_HTTPHEADER => $cabeceras,
                CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => $this->tiempo,
            ]);
            if ($datos !== null) {
                curl_setopt($c, CURLOPT_POSTFIELDS, $datos);
            }
            $respuesta = curl_exec($c);
            if ($respuesta === false) {
                throw new SBDException('no se puede conectar con ' . $this->url . ': ' . curl_error($c), 'conexion');
            }
            $estado = curl_getinfo($c, CURLINFO_RESPONSE_CODE);
            curl_close($c);
        } else {
            $contexto = stream_context_create(['http' => [
                'method' => $metodo, 'header' => implode("\r\n", $cabeceras), 'content' => $datos ?? '',
                'timeout' => $this->tiempo, 'ignore_errors' => true,
            ]]);
            $respuesta = @file_get_contents($this->url . $ruta, false, $contexto);
            if ($respuesta === false) {
                throw new SBDException('no se puede conectar con ' . $this->url, 'conexion');
            }
            preg_match('{HTTP/\S+ (\d{3})}', $http_response_header[0] ?? '', $m);
            $estado = (int) ($m[1] ?? 0);
        }
        $json = json_decode($respuesta, true);
        if (!is_array($json)) {
            throw new SBDException("el servidor respondió $estado sin explicación", 'http', $estado);
        }
        return [$json, $estado];
    }

    /** Ejecuta una o varias órdenes y devuelve un SBDResultado por cada una. */
    public function todas(string $orden, array $parametros = []): array
    {
        $cuerpo = ['orden' => $orden, 'parametros' => array_values($parametros)];
        if ($this->base) {
            $cuerpo['base'] = $this->base;
        }
        if ($this->sesion) {
            $cuerpo['sesion'] = $this->sesion;
        }
        [$datos, $estado] = $this->peticion('POST', '/api/v1/ejecutar', $cuerpo);
        if (!empty($datos['base'])) {
            $this->base = $datos['base'];
        }
        if (!empty($datos['error'])) {
            throw new SBDException($datos['error'], $datos['tipo'] ?? 'orden', $estado, $datos['orden_fallida'] ?? null);
        }
        return array_map(function ($r) {
            return new SBDResultado($r);
        }, $datos['resultados']);
    }

    /** Ejecuta y devuelve el resultado de la última orden. */
    public function ejecutar(string $orden, array $parametros = []): SBDResultado
    {
        $todos = $this->todas($orden, $parametros);
        return $todos ? end($todos) : new SBDResultado(['mensaje' => 'Nada que hacer']);
    }

    /** Atajo: las filas como arrays asociativos. */
    public function consultar(string $orden, array $parametros = []): array
    {
        return $this->ejecutar($orden, $parametros)->objetos();
    }

    public function usar(string $base): self
    {
        $this->base = $base;
        return $this;
    }

    public function salud(): array
    {
        return $this->peticion('GET', '/salud')[0];
    }

    public function abrirSesion(): string
    {
        [$datos, $estado] = $this->peticion('POST', '/api/v1/sesiones', ['base' => $this->base]);
        if (!empty($datos['error'])) {
            throw new SBDException($datos['error'], $datos['tipo'] ?? 'sesion', $estado);
        }
        return $this->sesion = $datos['sesion'];
    }

    public function cerrarSesion(): void
    {
        if ($this->sesion) {
            $id = $this->sesion;
            $this->sesion = null;
            $this->peticion('DELETE', '/api/v1/sesiones/' . $id);
        }
    }

    /** Todo o nada: si la función lanza una excepción, se deshace; si no, se confirma. */
    public function transaccion(callable $funcion)
    {
        $t = new SBD($this->url, $this->clave, $this->base, $this->tiempo);
        $t->abrirSesion();
        try {
            $t->ejecutar('empezar');
            $valor = $funcion($t);
            $t->ejecutar('confirmar');
            return $valor;
        } catch (Throwable $e) {
            try {
                $t->ejecutar('deshacer');
            } catch (Throwable $ignorada) {
            }
            throw $e;
        } finally {
            $t->cerrarSesion();
        }
    }
}
