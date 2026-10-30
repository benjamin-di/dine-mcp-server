"""The provider registry: one explicit entry per provider definition (startup checks they agree)."""

from __future__ import annotations

from .catchtable import CatchTable
from .formitable import Formitable
from .resdiary import ResDiary
from .sevenrooms import SevenRooms
from .tabelog import Tabelog
from .tablecheck import TableCheck
from .zenchef import Zenchef

REGISTRY: dict[str, type] = {
    "catchtable": CatchTable,
    "formitable": Formitable,
    "resdiary": ResDiary,
    "sevenrooms": SevenRooms,
    "tablecheck": TableCheck,
    "tabelog": Tabelog,
    "zenchef": Zenchef,
}
