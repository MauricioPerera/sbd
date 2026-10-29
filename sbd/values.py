"""Conversión de tipos, comparación, aritmética y formato de los valores de SBD.

Los valores se representan con tipos de Python: None (NULL), bool, int, float y str.
DATE y TIMESTAMP se guardan como texto ISO ('2024-05-01', '2024-05-01 10:30:00').
"""
import calendar
import datetime
import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .errors import SBDError


@dataclass(frozen=True)
class Intervalo:
    """Un lapso de tiempo: «3 dias», «1 mes», «2 horas». Los meses van aparte porque no duran siempre lo mismo."""
    meses: int = 0
    dias: int = 0
    segundos: float = 0

    def __add__(self, otro):
        return Intervalo(self.meses + otro.meses, self.dias + otro.dias, self.segundos + otro.segundos)

    def __neg__(self):
        return Intervalo(-self.meses, -self.dias, -self.segundos)

    def por(self, n):
        if self.meses and n != int(n):
            raise SBDError("los meses y años no se pueden multiplicar por fracciones")
        dias = self.dias * n
        return Intervalo(int(self.meses * n), int(dias), self.segundos * n + (dias - int(dias)) * 86400)

    @property
    def aproximado(self):
        return self.meses * 30.436875 * 86400 + self.dias * 86400 + self.segundos

    def __lt__(self, otro):
        return self.aproximado < otro.aproximado

    def __gt__(self, otro):
        return self.aproximado > otro.aproximado

    def __str__(self):
        partes = []
        años, meses = divmod(abs(self.meses), 12)
        signo = '-' if (self.meses or self.dias or self.segundos) and self.aproximado < 0 else ''
        horas, resto = divmod(abs(self.segundos), 3600)
        minutos, segundos = divmod(resto, 60)
        for n, uno, varios in ((años, 'año', 'años'), (meses, 'mes', 'meses'), (abs(self.dias), 'día', 'días'),
                               (int(horas), 'hora', 'horas'), (int(minutos), 'minuto', 'minutos')):
            if n:
                partes.append(f"{n} {uno if n == 1 else varios}")
        if segundos:
            s = int(segundos) if float(segundos).is_integer() else round(segundos, 3)
            partes.append(f"{s} segundo{'' if s == 1 else 's'}")
        return signo + (' '.join(partes) or '0 días')


UNIDADES = {
    'dia': (0, 1, 0), 'día': (0, 1, 0), 'dias': (0, 1, 0), 'días': (0, 1, 0),
    'semana': (0, 7, 0), 'semanas': (0, 7, 0),
    'mes': (1, 0, 0), 'meses': (1, 0, 0),
    'año': (12, 0, 0), 'años': (12, 0, 0), 'anio': (12, 0, 0), 'anios': (12, 0, 0),
    'hora': (0, 0, 3600), 'horas': (0, 0, 3600),
    'minuto': (0, 0, 60), 'minutos': (0, 0, 60),
    'segundo': (0, 0, 1), 'segundos': (0, 0, 1),
}


def intervalo(n, unidad):
    meses, dias, segundos = UNIDADES[unidad]
    if meses:
        if n != int(n):
            raise SBDError(f"«{n} {unidad}»: los meses y años tienen que ser un número entero")
        return Intervalo(meses * int(n))
    return Intervalo(0, dias, segundos).por(n)


def leer_fecha(v):
    """datetime y si tenía hora, o None si el texto no es una fecha ISO."""
    if not isinstance(v, str) or len(v) < 10 or v[4:5] != '-' or v[7:8] != '-':
        return None
    try:
        dt = datetime.datetime.fromisoformat(v.strip())
    except ValueError:
        return None
    return dt, len(v.strip()) > 10


def escribir_fecha(dt, con_hora):
    if con_hora:
        return dt.isoformat(sep=' ', timespec='seconds')
    return dt.date().isoformat()


def mover_fecha(dt, iv):
    if iv.meses:
        total = dt.month - 1 + iv.meses
        año, mes = dt.year + total // 12, total % 12 + 1
        dt = dt.replace(year=año, month=mes, day=min(dt.day, calendar.monthrange(año, mes)[1]))
    return dt + datetime.timedelta(days=iv.dias, seconds=iv.segundos)


def fecha_mas(v, iv):
    leida = leer_fecha(v)
    if leida is None:
        raise SBDError(f"{quote(v)} no es una fecha: para sumarle {iv} escribe la fecha así: 2024-05-01")
    dt, con_hora = leida
    return escribir_fecha(mover_fecha(dt, iv), con_hora or bool(iv.segundos))


def dias_entre(a, b):
    """b - a en días: entero si las dos son fechas sin hora."""
    la, lb = leer_fecha(a), leer_fecha(b)
    if la is None or lb is None:
        raise SBDError(f"para contar días hacen falta dos fechas, y {quote(a if la is None else b)} no lo es")
    diferencia = (lb[0] - la[0]).total_seconds() / 86400
    return int(diferencia) if not la[1] and not lb[1] else round(diferencia, 6)


# Tipos internos -> como se escriben en SBD.
TYPE_NAMES = {'INTEGER': 'entero', 'REAL': 'decimal', 'TEXT': 'texto', 'BOOLEAN': 'logico',
              'DATE': 'fecha', 'TIMESTAMP': 'fechahora', 'MONEY': 'dinero'}
NUMEROS = (int, float, Decimal)
CENTIMO = Decimal('0.01')


def dinero(v):
    """Decimal exacto con dos decimales y redondeo comercial (0,005 sube)."""
    if isinstance(v, bool):
        raise ValueError
    if isinstance(v, float):
        v = repr(v)
    if isinstance(v, str):
        v = v.strip()
        if ',' in v and '.' not in v:
            v = v.replace(',', '.')
    try:
        return Decimal(v).quantize(CENTIMO, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError):
        raise ValueError from None


def _exacto(v):
    """Para operar con dinero sin perder céntimos: los float pasan por su forma escrita."""
    return Decimal(repr(v)) if isinstance(v, float) else Decimal(v)


def type_name(v):
    if v is None:
        return 'nulo'
    if isinstance(v, bool):
        return 'logico'
    if isinstance(v, int):
        return 'entero'
    if isinstance(v, float):
        return 'decimal'
    if isinstance(v, Decimal):
        return 'dinero'
    if isinstance(v, Intervalo):
        return 'intervalo'
    return 'texto'


def format_value(v):
    if v is None:
        return 'nulo'
    if v is True:
        return 'verdadero'
    if v is False:
        return 'falso'
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e16:
            return f'{v:.1f}'
        return repr(v)
    if isinstance(v, Decimal):
        return f'{v:.2f}' if v == v.quantize(CENTIMO) else format(v.normalize(), 'f')
    return str(v)  # también Intervalo: «3 días»


def quote(v):
    """Un valor tal como se escribe en SBD."""
    if isinstance(v, str):
        return '"' + v.replace('"', '""') + '"'
    return format_value(v)


def to_text(v):
    return v if isinstance(v, str) else format_value(v)


def to_number(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, NUMEROS):
        return v
    if isinstance(v, str):
        s = v.strip()
        try:
            return int(s)
        except ValueError:
            pass
        try:
            return float(s)
        except ValueError:
            pass
    raise SBDError(f"{quote(v)} no es un valor numérico")


def truth(v):
    """Valor de verdad: True, False o None (desconocido)."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, NUMEROS):
        return v != 0
    try:
        return to_number(v) != 0
    except SBDError:
        return False


def compare(a, b):
    """-1, 0 o 1. Ninguno de los dos puede ser NULL.

    Un número y un texto numérico se comparan como números; si el texto no es
    numérico, los números van antes que los textos.
    """
    a_num = isinstance(a, NUMEROS)
    b_num = isinstance(b, NUMEROS)
    if a_num != b_num:
        try:
            if a_num:
                b = to_number(b)
            else:
                a = to_number(a)
        except SBDError:
            return -1 if a_num else 1
    if isinstance(a, Decimal) != isinstance(b, Decimal) and (isinstance(a, float) or isinstance(b, float)):
        a, b = _exacto(a), _exacto(b)  # 19.99 escrito vale 19.99 exactos, no su aproximación binaria
    return (a > b) - (a < b)


def sort_key(v):
    """Clave de ordenación total: nulo < números < textos < intervalos."""
    if v is None:
        return (0, 0)
    if isinstance(v, NUMEROS):
        return (1, v)
    if isinstance(v, Intervalo):
        return (3, v.aproximado)
    return (2, v)


def hash_key(v):
    """Clave hash compatible con compare(): si compare(a, b) == 0, hash_key(a) == hash_key(b)."""
    if isinstance(v, NUMEROS):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return v
    return v


def _arithmetic_dates(op, a, b):
    """Cuentas con intervalos y fechas. None si no es el caso."""
    a_iv, b_iv = isinstance(a, Intervalo), isinstance(b, Intervalo)
    if a_iv or b_iv:
        if op == '+':
            if a_iv and b_iv:
                return a + b
            return fecha_mas(b, a) if a_iv else fecha_mas(a, b)
        if op == '-':
            if a_iv and b_iv:
                return a + (-b)
            if b_iv:
                return fecha_mas(a, -b)
            raise SBDError("no se puede restar una fecha a un intervalo; ¿querías fecha - intervalo?")
        if op == '*' and (a_iv != b_iv):
            return a.por(to_number(b)) if a_iv else b.por(to_number(a))
        if op == '/' and a_iv and not b_iv:
            n = to_number(b)
            if n == 0:
                raise SBDError("división por cero")
            return a.por(1 / n)
        raise SBDError(f"no se puede operar «{op}» con un intervalo ({a if a_iv else b})")
    if op == '-' and isinstance(a, str) and isinstance(b, str) and leer_fecha(a) and leer_fecha(b):
        return dias_entre(b, a)
    return None


def arithmetic(op, a, b):
    fecha = _arithmetic_dates(op, a, b)
    if fecha is not None:
        return fecha
    a, b = to_number(a), to_number(b)
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        a, b = _exacto(a), _exacto(b)
    if op == '+':
        return a + b
    if op == '-':
        return a - b
    if op == '*':
        return a * b
    if b == 0:
        raise SBDError("división por cero")
    both_int = isinstance(a, int) and isinstance(b, int)
    if op == '/':
        if both_int:  # división entera truncada hacia cero
            q = abs(a) // abs(b)
            return q if (a >= 0) == (b >= 0) else -q
        return a / b
    if op == '%':
        if both_int:
            r = abs(a) % abs(b)
            return r if a >= 0 else -r
        return math.fmod(a, b)
    raise SBDError(f"operador desconocido {op}")


# ------------------------------------------------------- conversión de tipos

def _to_integer(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, str):
        s = v.strip()
        try:
            return int(s)
        except ValueError:
            v = float(s)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            raise ValueError
        rounded = math.floor(abs(v) + 0.5)  # redondeo "half away from zero"
        return int(rounded if v >= 0 else -rounded)
    raise ValueError


def _to_real(v):
    if isinstance(v, (bool, int, float, Decimal)):
        return float(v)
    if isinstance(v, str):
        return float(v.strip())
    raise ValueError


_TRUE = {'verdadero', 'v', 'si', 'sí', 's', '1', 'true', 'yes'}
_FALSE = {'falso', 'f', 'no', 'n', '0', 'false'}


def _to_boolean(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, NUMEROS):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
    raise ValueError


def _to_date(v):
    if not isinstance(v, str):
        raise ValueError
    s = v.strip()
    try:
        return datetime.date.fromisoformat(s).isoformat()
    except ValueError:
        return datetime.datetime.fromisoformat(s).date().isoformat()


def _to_timestamp(v):
    if not isinstance(v, str):
        raise ValueError
    return datetime.datetime.fromisoformat(v.strip()).isoformat(sep=' ')


_CONVERTERS = {
    'MONEY': dinero,
    'INTEGER': _to_integer,
    'REAL': _to_real,
    'TEXT': to_text,
    'BOOLEAN': _to_boolean,
    'DATE': _to_date,
    'TIMESTAMP': _to_timestamp,
}


def convert(value, typ, length=None, context='valor'):
    """Convierte `value` al tipo de columna `typ` o lanza SBDError."""
    if typ is None and isinstance(value, Intervalo):
        return str(value)
    if value is None or typ is None:
        return value
    try:
        result = _CONVERTERS[typ](value)
    except (ValueError, TypeError, OverflowError):
        raise SBDError(f"{context}: {quote(value)} no es un valor válido de tipo {TYPE_NAMES[typ]}") from None
    if length is not None and len(result) > length:
        raise SBDError(f"{context}: el texto {quote(result)} supera la longitud máxima ({length})")
    return result


def sumar_numeros(valores):
    """Suma sin mezclar mal dinero y decimales."""
    numeros = [to_number(v) for v in valores]
    if any(isinstance(n, Decimal) for n in numeros):
        return sum((_exacto(n) for n in numeros), Decimal(0))
    return sum(numeros)


def json_por_defecto(v):
    """Para json.dump: el dinero se guarda como texto exacto."""
    if isinstance(v, Decimal):
        return format(v, 'f')
    raise TypeError(f"no se puede guardar {type(v).__name__}")
