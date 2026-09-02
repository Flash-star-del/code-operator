"""Immutable data structures used by the preregistered comparison study."""

from dataclasses import asdict, dataclass
from typing import Any


class FrozenDict(dict):
    """A dict-compatible recursively frozen mapping that survives ``asdict``."""

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("FrozenDict is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable
    __ior__ = _immutable

    def __deepcopy__(self, memo: dict[int, Any]) -> "FrozenDict":
        memo[id(self)] = self
        return self


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return FrozenDict((key, _freeze(item)) for key, item in value.items())
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class SystemConfig:
    system_id: str
    cli_version: str
    executable_sha256: str
    model: str
    auth_type: str
    argv_template: tuple[str, ...]
    environment_names: tuple[str, ...]
    permission_mode: str
    output_mode: str


@dataclass(frozen=True)
class RunCell:
    phase: str
    track: str
    system_id: str
    task_id: str
    replicate: int
    order_index: int


@dataclass(frozen=True)
class FrozenManifest:
    schema_version: int
    study_id: str
    seed: int
    timeout_seconds: int
    systems: tuple[SystemConfig, ...]
    task_hashes: dict[str, dict[str, str]]
    pilot: tuple[RunCell, ...]
    formal: tuple[RunCell, ...]
    track_b_status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_hashes", _freeze(self.task_hashes))

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
