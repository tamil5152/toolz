"""Shop standards tables loaded from YAML files in standards/.

Each table file looks like::

    table: clearance_pct_by_material
    placeholder: true            # remove once the values are the shop's own
    source: "Shop standard ST-02, section 4.1"
    description: "Punch-die clearance per side, % of thickness"
    rows:
      - {material: mild_steel, thickness_mm_min: 0.0, thickness_mm_max: 3.0, clearance_pct: 5}

A row matches a lookup key ``k`` when the row has ``k`` equal to the key value, or
when it has ``k_min`` and ``k_max`` and the key value lies in that range (min
inclusive, max exclusive, so adjoining ranges do not overlap).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

DEFAULT_STANDARDS_DIR = Path(__file__).resolve().parent.parent / "standards"

Cell = str | float | int | bool


class StandardsError(LookupError):
    """A table is missing, malformed, or has no row (or several rows) for a key."""


class Table(BaseModel):
    table: str
    source: str
    description: str = ""
    placeholder: bool = Field(
        default=False, description="True while the values are not yet the shop's own"
    )
    rows: list[dict[str, Cell]]

    def lookup(self, key: Mapping[str, Cell]) -> dict[str, Cell]:
        """The single row matching every key value."""
        matches = [row for row in self.rows if all(_matches(row, k, v) for k, v in key.items())]
        if not matches:
            raise StandardsError(f"no row in {self.table!r} for {dict(key)}")
        if len(matches) > 1:
            raise StandardsError(f"{len(matches)} rows in {self.table!r} match {dict(key)}")
        return matches[0]


def _matches(row: Mapping[str, Cell], key: str, value: Cell) -> bool:
    if key in row:
        return row[key] == value
    low, high = row.get(f"{key}_min"), row.get(f"{key}_max")
    if low is None or high is None or isinstance(value, str | bool):
        return False
    return float(low) <= float(value) < float(high)


class Standards:
    """All tables in a standards directory, by table name."""

    def __init__(self, tables: Mapping[str, Table]) -> None:
        self.tables = dict(tables)

    @classmethod
    def load(cls, directory: Path = DEFAULT_STANDARDS_DIR) -> Standards:
        tables: dict[str, Table] = {}
        for path in sorted(directory.glob("*.yaml")):
            data: Any = yaml.safe_load(path.read_text())
            if not data:
                continue
            table = Table.model_validate(data)
            if table.table in tables:
                raise StandardsError(f"table {table.table!r} defined twice (in {path.name})")
            tables[table.table] = table
        return cls(tables)

    def table(self, name: str) -> Table:
        if name not in self.tables:
            raise StandardsError(f"no standards table {name!r}; add it to standards/")
        return self.tables[name]

    def lookup(self, table: str, **key: Cell) -> dict[str, Cell]:
        return self.table(table).lookup(key)

    def value(self, table: str, column: str, **key: Cell) -> float:
        """One numeric value from a table, e.g. a material's shear strength."""
        row = self.lookup(table, **key)
        if column not in row:
            raise StandardsError(f"table {table!r} has no column {column!r}")
        cell = row[column]
        if isinstance(cell, str | bool):
            raise StandardsError(f"{table}.{column} is not a number: {cell!r}")
        return float(cell)

    @property
    def placeholder_tables(self) -> list[str]:
        return sorted(name for name, t in self.tables.items() if t.placeholder)
