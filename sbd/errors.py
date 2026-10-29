"""Excepciones de SBD."""


class SBDError(Exception):
    """Error al analizar o ejecutar una orden de SBD."""


class IncompleteInputError(SBDError):
    """La entrada se corta a mitad de un texto, un comentario o una orden."""


class BaseOcupada(SBDError):
    """Otra sesión tiene una transacción abierta en esa base."""


class PermisoDenegado(SBDError):
    """El usuario no tiene permiso para esa orden o esa base."""
