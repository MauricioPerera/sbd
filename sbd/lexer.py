"""Analizador léxico de SBD: convierte el texto en una lista de tokens."""
import re
from dataclasses import dataclass

from .errors import IncompleteInputError, SBDError


@dataclass(frozen=True)
class Token:
    type: str  # word, ident, num, str, date, op, param, eof
    value: object
    pos: int
    end: int


TWO_CHAR_OPS = {'<=', '>=', '!=', '<>', '->', '=='}
ONE_CHAR_OPS = set('=<>+-*/%(),.;:{}[]&')

# 2024-05-01  ·  2024-05-01 10:30  ·  2024-05-01 10:30:15
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?(?![\w:])')


def tokenize(source):
    tokens = []
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        if c.isspace():
            i += 1
            continue
        if source.startswith('--', i) or c == '#':
            j = source.find('\n', i)
            i = n if j == -1 else j + 1
            continue
        if source.startswith('/*', i):
            j = source.find('*/', i + 2)
            if j == -1:
                raise IncompleteInputError("comentario /* sin cerrar")
            i = j + 2
            continue

        start = i
        if c.isalpha() or c == '_':
            i += 1
            while i < n and (source[i].isalnum() or source[i] == '_'):
                i += 1
            # Las palabras no distinguen mayúsculas ni tildes de «ñ»: AÑADIR = añadir.
            tokens.append(Token('word', source[start:i].lower(), start, i))
        elif c.isdigit():
            date = _DATE.match(source, i)
            if date:
                i = date.end()
                tokens.append(Token('date', date.group().replace('T', ' '), start, i))
                continue
            while i < n and source[i].isdigit():
                i += 1
            is_float = False
            if i + 1 < n and source[i] == '.' and source[i + 1].isdigit():
                is_float = True
                i += 1
                while i < n and source[i].isdigit():
                    i += 1
            if i < n and source[i] in 'eE':
                j = i + 1
                if j < n and source[j] in '+-':
                    j += 1
                if j < n and source[j].isdigit():
                    is_float = True
                    i = j
                    while i < n and source[i].isdigit():
                        i += 1
            if i < n and (source[i].isalpha() or source[i] == '_'):
                raise SBDError(f"número mal escrito: {source[start:i + 1]!r}")
            text = source[start:i]
            tokens.append(Token('num', float(text) if is_float else int(text), start, i))
        elif c in '"\'':
            value, i = _read_quoted(source, i, c, "texto sin cerrar: falta una comilla")
            tokens.append(Token('str', value, start, i))
        elif c == '`':
            value, i = _read_quoted(source, i, '`', "nombre entre ` sin cerrar")
            if not value:
                raise SBDError("nombre vacío entre ``")
            tokens.append(Token('ident', value, start, i))
        elif c == '?':
            i += 1
            tokens.append(Token('param', None, start, i))
        else:
            two = source[i:i + 2]
            if two in TWO_CHAR_OPS:
                i += 2
                tokens.append(Token('op', {'<>': '!=', '==': '='}.get(two, two), start, i))
            elif c in ONE_CHAR_OPS:
                i += 1
                tokens.append(Token('op', c, start, i))
            else:
                raise SBDError(f"carácter inesperado {c!r} en la posición {i}")
    tokens.append(Token('eof', None, n, n))
    return tokens


def _read_quoted(source, i, quote, error):
    """Lee desde la comilla de apertura; la comilla doblada ("" o '') se escapa a sí misma."""
    parts = []
    i += 1
    while True:
        j = source.find(quote, i)
        if j == -1:
            raise IncompleteInputError(error)
        parts.append(source[i:j])
        if source.startswith(quote * 2, j):
            parts.append(quote)
            i = j + 2
            continue
        return ''.join(parts), j + 1
