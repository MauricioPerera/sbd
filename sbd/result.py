"""Resultado de ejecutar una orden."""


class Result:
    """Filas devueltas por una consulta, o el resumen de una orden que no devuelve filas.

    - columns: lista de nombres de columna (None si la sentencia no devuelve filas)
    - rows: lista de tuplas
    - rowcount: filas devueltas o afectadas
    - lastrowid: id de la última fila insertada (insertar)
    - message: texto descriptivo ("3 filas insertadas", "Tabla 'x' creada"...)
    """

    def __init__(self, columns=None, rows=None, rowcount=None, message='', lastrowid=None):
        self.columns = columns
        self.rows = [tuple(r) for r in rows] if rows is not None else []
        self.rowcount = len(self.rows) if rowcount is None else rowcount
        self.message = message or (f"{len(self.rows)} fila(s)" if columns is not None else '')
        self.lastrowid = lastrowid

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        return self.rows[index]

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def scalar(self):
        """Primera columna de la primera fila (o None)."""
        return self.rows[0][0] if self.rows else None

    def dicts(self):
        return [dict(zip(self.columns, r)) for r in self.rows] if self.columns else []

    def __repr__(self):
        if self.columns is None:
            return f"<Result {self.message!r}>"
        return f"<Result columns={self.columns} rows={len(self.rows)}>"
