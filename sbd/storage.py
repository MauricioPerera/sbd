"""Estructuras de almacenamiento: columnas, índices y tablas.

Cada fila es un dict {columna: valor} identificado por un rowid interno. Los índices
son tablas hash de (valores de las columnas indexadas) -> conjunto de rowids.

Los cambios de estructura (añadir, quitar, indexar...) nunca modifican una tabla en sitio:
crean una copia con clone() y la sustituyen en el catálogo, para que deshacer una
transacción sea simplemente volver a poner la versión anterior.
"""
import bisect
import re
from dataclasses import dataclass
from typing import Any, Optional

from .errors import SBDError
from .parser import KEYWORDS
from .values import TYPE_NAMES, sort_key

_SIMPLE_IDENT = re.compile(r'[^\W\d]\w*\Z')
_ON_DELETE_WORDS = {'cascade': 'cascada', 'set null': 'anular'}


def quote_ident(name):
    """Un nombre tal como se escribe en SBD (entre `` si choca con el lenguaje)."""
    if _SIMPLE_IDENT.match(name) and name == name.lower() and name not in KEYWORDS:
        return name
    return '`' + name.replace('`', '``') + '`'


def _cols_sql(columns):
    return ', '.join(quote_ident(c) for c in columns)


@dataclass(eq=False)
class Column:
    name: str
    type: Optional[str] = None
    length: Optional[int] = None
    not_null: bool = False
    default: Any = None  # expresión (AST)
    default_sql: Optional[str] = None

    def type_sbd(self):
        if self.type is None:
            return None
        if self.length is not None:
            return f'texto({self.length})'
        return TYPE_NAMES[self.type]


class Index:
    def __init__(self, name, columns, unique=False, auto=False):
        self.name = name
        self.columns = list(columns)
        self.unique = unique
        self.auto = auto  # creado por una restricción clave / unico de la tabla
        self.map = {}
        # Índices de una columna: además de la tabla hash, las claves ordenadas para buscar por rangos.
        self._ordenadas = [] if len(self.columns) == 1 else None   # [orden de la clave]
        self._claves = [] if len(self.columns) == 1 else None      # [clave], en el mismo orden

    def key(self, row):
        return tuple(row[c] for c in self.columns)

    def add(self, rowid, row):
        key = self.key(row)
        if any(v is None for v in key):
            return  # NULL nunca es igual a nada: no entra en el índice
        bucket = self.map.get(key)
        if bucket is None:
            self.map[key] = {rowid}
            if self._ordenadas is not None:
                orden = sort_key(key[0])
                i = bisect.bisect_right(self._ordenadas, orden)
                self._ordenadas.insert(i, orden)
                self._claves.insert(i, key)
        else:
            bucket.add(rowid)

    def remove(self, rowid, row):
        key = self.key(row)
        bucket = self.map.get(key)
        if bucket is not None:
            bucket.discard(rowid)
            if not bucket:
                del self.map[key]
                if self._ordenadas is not None:
                    orden = sort_key(key[0])
                    i = bisect.bisect_left(self._ordenadas, orden)
                    while self._claves[i] != key:
                        i += 1
                    del self._ordenadas[i]
                    del self._claves[i]

    def find(self, key):
        return sorted(self.map.get(key, ()))

    def find_range(self, low=None, low_inclusive=True, high=None, high_inclusive=True):
        """Filas cuya única columna está entre «low» y «high» (None: sin ese límite)."""
        izquierda = 0
        if low is not None:
            buscar = bisect.bisect_left if low_inclusive else bisect.bisect_right
            izquierda = buscar(self._ordenadas, sort_key(low))
        derecha = len(self._ordenadas)
        if high is not None:
            buscar = bisect.bisect_right if high_inclusive else bisect.bisect_left
            derecha = buscar(self._ordenadas, sort_key(high))
        filas = []
        for clave in self._claves[izquierda:derecha]:
            filas.extend(self.map[clave])
        return sorted(filas)

    def conflicts(self, row, ignore=None):
        key = self.key(row)
        if any(v is None for v in key):
            return False
        bucket = self.map.get(key)
        return bool(bucket) and any(rid != ignore for rid in bucket)

    def rebuilt(self, rows, columns=None):
        index = Index(self.name, columns or self.columns, self.unique, self.auto)
        for rowid, row in rows.items():
            index.add(rowid, row)
        return index

    def to_sbd(self, table_name):
        unique = 'unico ' if self.unique else ''
        return f"indexar {unique}{quote_ident(table_name)} ({_cols_sql(self.columns)}) como {quote_ident(self.name)}"


class Table:
    def __init__(self, name, columns, primary_key=(), uniques=(), checks=(), foreign_keys=()):
        self.name = name
        self.columns = list(columns)
        self.primary_key = list(primary_key)
        self.uniques = [list(u) for u in uniques]
        self.checks = list(checks)  # [(expresión, sql)]
        self.foreign_keys = list(foreign_keys)
        self.indexes = {}
        self.rows = {}
        self.next_rowid = 1
        self.autoinc = 0  # mayor valor visto en la columna autoincremental
        self.unordered = False

    @property
    def colnames(self):
        return [c.name for c in self.columns]

    def has_column(self, name):
        return any(c.name == name for c in self.columns)

    def column(self, name):
        for c in self.columns:
            if c.name == name:
                return c
        raise SBDError(f"la tabla {self.name} no tiene ninguna columna «{name}»")

    @property
    def money_columns(self):
        return [c.name for c in self.columns if c.type == 'MONEY']

    @property
    def auto_column(self):
        """Una clave de una sola columna entera se numera sola si no se le da valor."""
        if len(self.primary_key) == 1:
            col = self.column(self.primary_key[0])
            if col.type == 'INTEGER':
                return col.name
        return None

    def scan(self):
        if self.unordered:
            # Se reordena el mismo dict (no uno nuevo): las copias de clone() lo comparten.
            items = sorted(self.rows.items())
            self.rows.clear()
            self.rows.update(items)
            self.unordered = False
        return list(self.rows.items())

    def insert_raw(self, rowid, row):
        self.rows[rowid] = row
        for index in self.indexes.values():
            index.add(rowid, row)

    def delete_raw(self, rowid):
        row = self.rows.pop(rowid)
        for index in self.indexes.values():
            index.remove(rowid, row)
        return row

    def update_raw(self, rowid, row):
        old = self.rows[rowid]
        for index in self.indexes.values():
            index.remove(rowid, old)
            index.add(rowid, row)
        self.rows[rowid] = row
        return old

    def find_conflict(self, row, ignore=None):
        for index in self.indexes.values():
            if index.unique and index.conflicts(row, ignore):
                return index
        return None

    def conflicting_rowids(self, row):
        """Filas que ya tienen el mismo valor que «row» en alguna clave o columna única."""
        found = set()
        for index in self.indexes.values():
            if index.unique:
                key = index.key(row)
                if not any(v is None for v in key):
                    found |= index.map.get(key, set())
        return found

    def find_index(self, columns):
        wanted = set(columns)
        best = None
        for index in self.indexes.values():
            if set(index.columns) == wanted and (best is None or (index.unique and not best.unique)):
                best = index
        return best

    def rebuild_indexes(self):
        self.indexes = {name: index.rebuilt(self.rows) for name, index in self.indexes.items()}

    def clone(self):
        copy = Table.__new__(Table)
        copy.__dict__.update(self.__dict__)
        copy.columns = list(self.columns)
        copy.primary_key = list(self.primary_key)
        copy.uniques = [list(u) for u in self.uniques]
        copy.checks = list(self.checks)
        copy.foreign_keys = list(self.foreign_keys)
        copy.indexes = dict(self.indexes)
        return copy

    def to_sbd(self):
        """La orden «crear tabla» que reconstruye esta tabla."""
        single_pk = self.primary_key if len(self.primary_key) == 1 else []
        single_unique = {u[0] for u in self.uniques if len(u) == 1}
        single_fk = {fk.columns[0]: fk for fk in self.foreign_keys if len(fk.columns) == 1}
        width = max(len(quote_ident(c.name)) for c in self.columns)
        lines = []
        for col in self.columns:
            parts = [quote_ident(col.name).ljust(width)]
            if col.type:
                parts.append(col.type_sbd())
            if col.name in single_pk:
                parts.append('clave')
            elif col.not_null:
                parts.append('obligatorio')
            if col.name in single_unique:
                parts.append('unico')
            if col.default_sql is not None:
                parts.append(f'= {col.default_sql}')
            if col.name in single_fk:
                parts.append(_reference_sbd(single_fk[col.name]))
            lines.append(' '.join(parts))
        if len(self.primary_key) > 1:
            lines.append(f"clave ({_cols_sql(self.primary_key)})")
        for cols in self.uniques:
            if len(cols) > 1:
                lines.append(f"unico ({_cols_sql(cols)})")
        for fk in self.foreign_keys:
            if len(fk.columns) > 1:
                lines.append(f"({_cols_sql(fk.columns)}) {_reference_sbd(fk)}")
        for _, sql in self.checks:
            lines.append(f"comprobar {sql}")
        return f"crear tabla {quote_ident(self.name)} {{\n  " + '\n  '.join(lines) + "\n}"


def _reference_sbd(fk):
    text = f"-> {quote_ident(fk.ref_table)}({_cols_sql(fk.ref_columns)})"
    if fk.on_delete in _ON_DELETE_WORDS:
        text += f" al borrar {_ON_DELETE_WORDS[fk.on_delete]}"
    return text
