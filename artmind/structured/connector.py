from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Column:
    name: str
    dtype: str


@dataclass
class Profile:
    kind: str                 # 'categorical' | 'numeric' | 'other'
    distinct_sample: list      # up to N sampled distinct values (categorical)
    cardinality: int | None    # distinct count (categorical)
    minimum: Any = None        # numeric
    maximum: Any = None        # numeric
    null_rate: float | None = None


class Datasource(Protocol):
    def introspect_schema(self, table: str) -> list[Column]: ...
    def profile_columns(self, table: str) -> dict[str, Profile]: ...
    def run_sql(self, sql: str) -> list[dict]: ...           # direct, no LLM
    def load_table(self, path, table: str, *, header_row: int = 0) -> int: ...  # returns row_count
