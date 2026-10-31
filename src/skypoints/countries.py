"""Country-code normalisation and routing to target tables.

Mirrors STAGING.REF_COUNTRY_ALIAS. The sample mixes ISO alpha-3 codes
(USA, IND, CAN) with non-standard ones (PHIL, AU); all are normalised to
ISO alpha-3 before routing.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Country:
    code: str          # ISO 3166-1 alpha-3
    name: str
    target_table: str


_COUNTRIES = {
    "USA": Country("USA", "United States", "TABLE_USA"),
    "IND": Country("IND", "India", "TABLE_INDIA"),
    "PHL": Country("PHL", "Philippines", "TABLE_PHILIPPINES"),
    "CAN": Country("CAN", "Canada", "TABLE_CANADA"),
    "AUS": Country("AUS", "Australia", "TABLE_AUSTRALIA"),
}

ALIASES: dict[str, str] = {
    "USA": "USA", "US": "USA",
    "IND": "IND", "IN": "IND",
    "PHL": "PHL", "PH": "PHL", "PHIL": "PHL",
    "CAN": "CAN", "CA": "CAN",
    "AUS": "AUS", "AU": "AUS",
}


def resolve(raw_code: str | None) -> Country | None:
    """Return the Country for any known alias (case/space-insensitive), else None."""
    if raw_code is None:
        return None
    iso = ALIASES.get(raw_code.strip().upper())
    return _COUNTRIES.get(iso) if iso else None


def all_countries() -> list[Country]:
    return list(_COUNTRIES.values())
