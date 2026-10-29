"""Funciones escalares disponibles en las expresiones de SBD.

Cada entrada de FUNCTIONS es: nombre -> (mín. argumentos, máx. argumentos o None, función).
Los agregados (contar, sumar, media, minimo, maximo, juntar) están en el ejecutor.
"""
import calendar
import datetime
import math
import random
from decimal import ROUND_HALF_UP, Decimal

from .errors import SBDError
from .values import (Intervalo, arithmetic, compare, dias_entre, fecha_mas, intervalo,
                     leer_fecha, quote, sort_key, to_number, to_text, type_name)


def _strict(fn):
    """Si algún argumento es NULL, el resultado es NULL."""
    def wrapper(*args):
        if any(a is None for a in args):
            return None
        return fn(*args)
    return wrapper


def _int_arg(v):
    n = to_number(v)
    if n != int(n):
        raise SBDError(f"se esperaba un número entero y se recibió {quote(v)}")
    return int(n)


def _num(fn):
    return _strict(lambda x: fn(to_number(x)))


def _round(x, digits=0):
    x, digits = to_number(x), _int_arg(digits)
    if isinstance(x, int) and digits >= 0:
        return x
    q = Decimal(str(x)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    if isinstance(x, Decimal):
        return q
    return int(q) if isinstance(x, int) else float(q)


def _power(a, b):
    a, b = to_number(a), to_number(b)
    if isinstance(a, int) and isinstance(b, int) and abs(b) > 1000:
        a = float(a)
    return a ** b


def _sign(x):
    x = to_number(x)
    return (x > 0) - (x < 0)


def _substr(s, start, length=None):
    s = to_text(s)
    begin = _int_arg(start) - 1
    end = len(s) if length is None else begin + _int_arg(length)
    return s[max(begin, 0):max(end, 0)]


def _trim(method):
    def fn(s, chars=None):
        return getattr(to_text(s), method)(None if chars is None else to_text(chars))
    return _strict(fn)


def _pad(left):
    def fn(s, n, fill=' '):
        s, n, fill = to_text(s), _int_arg(n), to_text(fill)
        if len(s) >= n:
            return s[:n]
        if not fill:
            return s
        padding = (fill * n)[:n - len(s)]
        return padding + s if left else s + padding
    return _strict(fn)


def _coalesce(*args):
    for a in args:
        if a is not None:
            return a
    return None


def _nullif(a, b):
    if a is not None and b is not None and compare(a, b) == 0:
        return None
    return a


def _extreme(pick):
    def fn(*args):
        values = [a for a in args if a is not None]
        return pick(values, key=sort_key) if values else None
    return fn


def _concat(*args):
    return ''.join(to_text(a) for a in args if a is not None)


def _parse_datetime(v):
    s = to_text(v).strip()
    if s.lower() == 'now':
        return datetime.datetime.now()
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        raise SBDError(f"fecha u hora no válida: {quote(s)}") from None


def _now():
    return datetime.datetime.now().isoformat(sep=' ', timespec='seconds')


def _date(v='now'):
    if v is None:
        return None
    return _parse_datetime(v).date().isoformat()


def _date_part(attr):
    return _strict(lambda v: getattr(_parse_datetime(v), attr))


DIAS_SEMANA = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre',
         'octubre', 'noviembre', 'diciembre']


def _intervalo_de(v, para):
    if not isinstance(v, Intervalo):
        raise SBDError(f"{para} necesita un intervalo, como «30 dias» o «2 semanas»")
    return v


def _hace(iv):
    iv = _intervalo_de(iv, '«hace»')
    base = _now() if iv.segundos else datetime.date.today().isoformat()
    return fecha_mas(base, -iv)


def _dentro_de(iv):
    iv = _intervalo_de(iv, '«dentro de»')
    base = _now() if iv.segundos else datetime.date.today().isoformat()
    return fecha_mas(base, iv)


def _fecha(v):
    leida = leer_fecha(to_text(v))
    if leida is None:
        raise SBDError(f"{quote(v)} no es una fecha (se escriben así: 2024-05-01)")
    return leida[0]


def _meses_entre(a, b):
    da, db = _fecha(a), _fecha(b)
    meses = (db.year - da.year) * 12 + db.month - da.month
    if meses > 0 and (db.day, db.time()) < (da.day, da.time()):
        meses -= 1
    elif meses < 0 and (db.day, db.time()) > (da.day, da.time()):
        meses += 1
    return meses


def _edad(nacimiento, hasta=None):
    return _meses_entre(nacimiento, hasta or datetime.date.today().isoformat()) // 12


def _inicio_mes(v):
    return _fecha(v).date().replace(day=1).isoformat()


def _fin_mes(v):
    d = _fecha(v).date()
    return d.replace(day=calendar.monthrange(d.year, d.month)[1]).isoformat()


def _constructor(unidad):
    return _strict(lambda n: intervalo(to_number(n), unidad))


FUNCTIONS = {
    # números
    'absoluto': (1, 1, _num(abs)),
    'techo': (1, 1, _num(math.ceil)),
    'suelo': (1, 1, _num(math.floor)),
    'redondear': (1, 2, _strict(_round)),
    'raiz': (1, 1, _num(math.sqrt)),
    'potencia': (2, 2, _strict(_power)),
    'exp': (1, 1, _num(math.exp)),
    'ln': (1, 1, _num(math.log)),
    'log10': (1, 1, _num(math.log10)),
    'signo': (1, 1, _strict(_sign)),
    'resto': (2, 2, _strict(lambda a, b: arithmetic('%', a, b))),
    'aleatorio': (0, 0, random.random),
    # texto
    'mayusculas': (1, 1, _strict(lambda s: to_text(s).upper())),
    'minusculas': (1, 1, _strict(lambda s: to_text(s).lower())),
    'longitud': (1, 1, _strict(lambda s: len(to_text(s)))),
    'recortar': (1, 2, _trim('strip')),
    'recortar_izq': (1, 2, _trim('lstrip')),
    'recortar_der': (1, 2, _trim('rstrip')),
    'extraer': (2, 3, _strict(_substr)),
    'reemplazar': (3, 3, _strict(lambda s, a, b: to_text(s).replace(to_text(a), to_text(b)))),
    'posicion': (2, 2, _strict(lambda s, sub: to_text(s).find(to_text(sub)) + 1)),
    'invertir': (1, 1, _strict(lambda s: to_text(s)[::-1])),
    'repetir': (2, 2, _strict(lambda s, n: to_text(s) * max(_int_arg(n), 0))),
    'rellenar_izq': (2, 3, _pad(True)),
    'rellenar_der': (2, 3, _pad(False)),
    'unir_texto': (1, None, _concat),
    # nulos y comparación
    'primero_no_nulo': (1, None, _coalesce),
    'si_nulo': (2, 2, _coalesce),
    'nulo_si': (2, 2, _nullif),
    'mayor': (1, None, _extreme(max)),
    'menor': (1, None, _extreme(min)),
    'tipo': (1, 1, type_name),
    # fechas
    'hoy': (0, 0, lambda: datetime.date.today().isoformat()),
    'ahora': (0, 0, _now),
    'hora_actual': (0, 0, lambda: datetime.datetime.now().strftime('%H:%M:%S')),
    'dia_de': (1, 1, _date),
    'año': (1, 1, _date_part('year')),
    'mes': (1, 1, _date_part('month')),
    'dia': (1, 1, _date_part('day')),
    'hora': (1, 1, _date_part('hour')),
    'minuto': (1, 1, _date_part('minute')),
    'segundo': (1, 1, _date_part('second')),
    'formato_fecha': (2, 2, _strict(lambda v, fmt: _parse_datetime(v).strftime(to_text(fmt)))),
    # cuentas con fechas
    'hace': (1, 1, _strict(_hace)),
    'dentro_de': (1, 1, _strict(_dentro_de)),
    'dias': (1, 1, _constructor('dias')),
    'semanas': (1, 1, _constructor('semanas')),
    'meses': (1, 1, _constructor('meses')),
    'años': (1, 1, _constructor('años')),
    'horas': (1, 1, _constructor('horas')),
    'minutos': (1, 1, _constructor('minutos')),
    'segundos': (1, 1, _constructor('segundos')),
    'dias_entre': (2, 2, _strict(lambda a, b: dias_entre(to_text(a), to_text(b)))),
    'meses_entre': (2, 2, _strict(_meses_entre)),
    'edad': (1, 2, _strict(_edad)),
    'dia_semana': (1, 1, _strict(lambda v: DIAS_SEMANA[_fecha(v).weekday()])),
    'nombre_mes': (1, 1, _strict(lambda v: MESES[_fecha(v).month - 1])),
    'inicio_mes': (1, 1, _strict(_inicio_mes)),
    'fin_mes': (1, 1, _strict(_fin_mes)),
}

# Sin tilde también vale: anio(), posición()...
ALIASES = {'anio': 'año', 'posición': 'posicion', 'raíz': 'raiz', 'mayúsculas': 'mayusculas',
           'minúsculas': 'minusculas', 'día': 'dia', 'día_de': 'dia_de', 'días': 'dias', 'anios': 'años',
           'días_entre': 'dias_entre', 'día_semana': 'dia_semana'}
