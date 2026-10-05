"""Classify listed securities from their directory names, so the universe holds only common
stock (and ADRs when enabled), never warrants, units, rights, preferreds, notes or funds.

Exchange directories carry no security-type field, but their names follow consistent wording
("... - Common Stock", "... American Depositary Shares", "... 6.5% Series A Preferred Stock").
When nothing identifies a security as excluded, it is treated as common stock: liquidity and
fundamentals filters downstream remove oddities, whereas wrongly excluding a real stock would
hide it for good.
"""

import re
from enum import StrEnum

from app.models.ticker import TickerType
from app.providers.base import ListedSecurity


class SecurityClass(StrEnum):
    COMMON = "common"
    ADR = "adr"
    ETF = "etf"
    PREFERRED = "preferred"
    WARRANT = "warrant"
    RIGHT = "right"
    UNIT = "unit"
    NOTE = "note"
    FUND = "fund"
    TEST = "test"


UNIVERSE_EXCHANGES = frozenset({"NASDAQ", "NYSE", "AMEX"})

_PREFERRED = re.compile(r"\bpreferred\b|\bpfd\b|\bpref(erence)? shares?\b")
_WARRANT = re.compile(r"\bwarrants?\b")
_RIGHT = re.compile(r"\brights?\b")
_COMMON_UNITS = re.compile(r"\bcommon units?\b|\bunits? representing limited partner")
_UNIT = re.compile(r"\bunits?\b")
_NOTE = re.compile(
    r"\bnotes? due\b|\bsenior notes?\b|\bsubordinated notes?\b|\bdebentures?\b|\bbonds? due\b"
    r"|\bexchange[- ]traded notes?\b|\betns?\b"
)
_FUND = re.compile(r"\bfunds?\b")
_ADR = re.compile(
    r"american depositary|american depository|\bads\b|\badrs?\b|new york registry|\bny registry"
)


def classify(security: ListedSecurity) -> SecurityClass:
    """Order matters: SPAC unit names mention their warrants and rights, MLP "common units"
    are equity, and preferred depositary shares must not be mistaken for ADRs."""
    if security.is_test_issue:
        return SecurityClass.TEST
    if security.is_etf:
        return SecurityClass.ETF

    name = security.name.lower()
    symbol = security.symbol
    if "$" in symbol or _PREFERRED.search(name):
        return SecurityClass.PREFERRED
    if _COMMON_UNITS.search(name):
        return SecurityClass.COMMON
    if _UNIT.search(name) or symbol.endswith(".U"):
        return SecurityClass.UNIT
    if _WARRANT.search(name) or symbol.endswith(".WS"):
        return SecurityClass.WARRANT
    if _RIGHT.search(name):
        return SecurityClass.RIGHT
    if _NOTE.search(name):
        return SecurityClass.NOTE
    if _FUND.search(name):
        return SecurityClass.FUND
    if _ADR.search(name):
        return SecurityClass.ADR
    return SecurityClass.COMMON


def universe_type(security: ListedSecurity) -> TickerType | None:
    """Spec §5.3: common stock on NYSE, Nasdaq or NYSE American. ADRs are always stored (as
    `adr`) so the `include_adrs` setting can be flipped at scan time without a re-backfill."""
    if security.exchange not in UNIVERSE_EXCHANGES:
        return None
    match classify(security):
        case SecurityClass.COMMON:
            return TickerType.COMMON
        case SecurityClass.ADR:
            return TickerType.ADR
        case _:
            return None
