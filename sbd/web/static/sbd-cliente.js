/* Cliente de SBD para JavaScript: navegador y Node 18 o superior (usa fetch).

     import { SBD } from 'https://datos.miweb.es/cliente/sbd.js';   // o copia este fichero

     const db = new SBD({ url: 'https://datos.miweb.es', clave: 'sbd_…', base: 'tienda' });
     const productos = await db.consultar('buscar productos donde precio < ? ordenar precio', [20]);
     const id = (await db.ejecutar('insertar en pedidos { cliente_id: ? } devolver id', [3])).escalar();
     await db.transaccion(async t => {
       await t.ejecutar('actualizar productos poner stock = stock - 1 donde id = ?', [7]);
     });

   El dinero llega como texto exacto («12.50»): conviértelo con cuidado, los números de
   JavaScript pierden céntimos. Desde el navegador, la web tiene que estar en «--origenes»
   del servidor, y la clave queda a la vista de quien abra la página: úsala solo con un
   usuario de permisos mínimos o, mejor, desde tu propio servidor. */

export class ErrorSBD extends Error {
  constructor(mensaje, { tipo = 'orden', estado = null, orden = null } = {}) {
    super(mensaje);
    this.name = 'ErrorSBD';
    this.tipo = tipo;        // sintaxis, orden, permiso, ocupada, clave, transaccion_abierta…
    this.estado = estado;    // código HTTP
    this.orden = orden;      // la orden que falló
  }
}

export class Resultado {
  constructor(datos) {
    this.orden = datos.orden ?? null;
    this.columnas = datos.columnas ?? null;
    this.tipos = datos.tipos ?? [];
    this.filas = datos.filas ?? [];
    this.filasAfectadas = datos.filas_afectadas ?? this.filas.length;
    this.ultimoId = datos.ultimo_id ?? null;
    this.mensaje = datos.mensaje ?? '';
    this.ms = datos.ms ?? null;
  }

  /** Las filas como objetos: [{ id: 1, nombre: 'té' }, …] */
  objetos() {
    return this.columnas ? this.filas.map(f => Object.fromEntries(this.columnas.map((c, i) => [c, f[i]]))) : [];
  }

  escalar() { return this.filas.length ? this.filas[0][0] : null; }
  primera() { return this.objetos()[0] ?? null; }
  get length() { return this.filas.length; }
  [Symbol.iterator]() { return this.filas[Symbol.iterator](); }
}

export class SBD {
  constructor({ url, clave, base = null, tiempo = 60000 } = {}) {
    if (!url || !clave) throw new ErrorSBD('hacen falta url y clave: new SBD({ url, clave, base })', { tipo: 'configuracion' });
    this.url = url.replace(/\/+$/, '');
    this.clave = clave;
    this.base = base;
    this.tiempo = tiempo;
    this._sesion = null;
  }

  async _peticion(metodo, ruta, cuerpo) {
    const control = new AbortController();
    const temporizador = setTimeout(() => control.abort(), this.tiempo);
    let respuesta, datos;
    try {
      respuesta = await fetch(this.url + ruta, {
        method: metodo,
        headers: { Authorization: `Bearer ${this.clave}`, ...(cuerpo ? { 'Content-Type': 'application/json' } : {}) },
        body: cuerpo ? JSON.stringify(cuerpo) : undefined,
        signal: control.signal,
      });
      datos = await respuesta.json();
    } catch (e) {
      throw new ErrorSBD(`no se puede conectar con ${this.url}: ${e.message}`, { tipo: 'conexion' });
    } finally {
      clearTimeout(temporizador);
    }
    if (datos.error && (ruta !== '/api/v1/ejecutar' || !respuesta.ok || !datos.resultados)) {
      throw new ErrorSBD(datos.error, { tipo: datos.tipo, estado: respuesta.status, orden: datos.orden_fallida });
    }
    return datos;
  }

  /** Ejecuta una o varias órdenes y devuelve un Resultado por cada una. */
  async todas(orden, parametros = []) {
    const cuerpo = { orden, parametros };
    if (this.base) cuerpo.base = this.base;
    if (this._sesion) cuerpo.sesion = this._sesion;
    const datos = await this._peticion('POST', '/api/v1/ejecutar', cuerpo);
    if (datos.base) this.base = datos.base;
    if (datos.error) throw new ErrorSBD(datos.error, { tipo: datos.tipo, orden: datos.orden_fallida });
    return datos.resultados.map(r => new Resultado(r));
  }

  /** Ejecuta y devuelve el Resultado de la última orden. */
  async ejecutar(orden, parametros = []) {
    const resultados = await this.todas(orden, parametros);
    return resultados.at(-1) ?? new Resultado({ mensaje: 'Nada que hacer' });
  }

  /** Atajo: las filas como objetos. */
  async consultar(orden, parametros = []) {
    return (await this.ejecutar(orden, parametros)).objetos();
  }

  usar(base) {
    this.base = base;
    return this;
  }

  async salud() {
    return this._peticion('GET', '/salud');
  }

  /** await db.transaccion(async t => { … }): confirma al terminar; si algo lanza un error, deshace. */
  async transaccion(funcion) {
    const t = new SBD({ url: this.url, clave: this.clave, base: this.base, tiempo: this.tiempo });
    t._sesion = (await t._peticion('POST', '/api/v1/sesiones', { base: this.base })).sesion;
    try {
      await t.ejecutar('empezar');
      const valor = await funcion(t);
      await t.ejecutar('confirmar');
      return valor;
    } catch (error) {
      await t.ejecutar('deshacer').catch(() => {});
      throw error;
    } finally {
      await t._peticion('DELETE', `/api/v1/sesiones/${t._sesion}`).catch(() => {});
    }
  }
}

export default SBD;
