from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Record:
    """En källas uppgifter om ett bolag. `fields` innehåller bara det källan vet."""

    source: str
    name: str
    url: str | None
    fields: dict[str, object] = field(default_factory=dict)
