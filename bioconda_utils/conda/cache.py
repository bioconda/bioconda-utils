"""Freshness policy shared by full repodata and mutable shard indexes."""

import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class RepodataCachePolicy:
    max_age: float = 8 * 60 * 60
    refresh_after: float | None = None

    @classmethod
    def for_run(cls, *, refresh: bool = False) -> "RepodataCachePolicy":
        return cls(
            refresh_after=datetime.datetime.now(datetime.UTC).timestamp()
            if refresh
            else None
        )

    def is_fresh(self, fetched_at: float) -> bool:
        age = datetime.datetime.now(datetime.UTC).timestamp() - fetched_at
        return 0 <= age < self.max_age and (
            self.refresh_after is None or fetched_at >= self.refresh_after
        )
