"""Request-time access to the active dataset.

The resolver and the findings engine both operate on an in-memory graph. Loading
and evaluating are cached per snapshot, because snapshots never change once
written.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import get_settings
from ..domain.entities import AccessGraph
from ..findings import Finding, evaluate
from ..storage.repository import SnapshotSummary, active_graph

_FINDINGS_CACHE: dict[int, list[Finding]] = {}


@dataclass(frozen=True, slots=True)
class Dataset:
    snapshot: SnapshotSummary
    graph: AccessGraph

    @property
    def findings(self) -> list[Finding]:
        cached = _FINDINGS_CACHE.get(self.snapshot.id)
        if cached is None:
            cached = evaluate(self.graph, get_settings())
            _FINDINGS_CACHE[self.snapshot.id] = cached
        return cached


class NoDataset(Exception):
    """Raised when no snapshot has been imported yet."""


def current_dataset() -> Dataset:
    snapshot, graph = active_graph()
    if snapshot is None or graph is None:
        raise NoDataset
    return Dataset(snapshot=snapshot, graph=graph)


def invalidate(snapshot_id: int | None = None) -> None:
    if snapshot_id is None:
        _FINDINGS_CACHE.clear()
    else:
        _FINDINGS_CACHE.pop(snapshot_id, None)
