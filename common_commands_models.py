from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(slots=True, frozen=True)
class CommonCommand:
    id: str
    device_id: str
    request_id: str
    command_text: str
    pinned: bool = False
    sort_order: int = 0
    version: int = 1
    revision: int = 0

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CommonCommand":
        return cls(
            id=str(payload.get("id") or ""),
            device_id=str(payload.get("device_id") or ""),
            request_id=str(payload.get("request_id") or ""),
            command_text=str(payload.get("command_text") or ""),
            pinned=_as_bool(payload.get("pinned")),
            sort_order=_as_int(payload.get("sort_order"), 0),
            version=_as_int(payload.get("version"), 1),
            revision=_as_int(payload.get("revision"), 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class CommonCommandCreate:
    device_id: str
    request_id: str
    command_text: str
    pinned: bool = False


@dataclass(slots=True, frozen=True)
class CommonCommandUpdate:
    expected_version: int
    command_text: str | None = None
    pinned: bool | None = None


@dataclass(slots=True, frozen=True)
class CommonCommandsSnapshot:
    revision: int
    commands: list[CommonCommand]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CommonCommandsSnapshot":
        commands = [CommonCommand.from_dict(item) for item in payload.get("commands") or []]
        commands.sort(key=lambda item: (0 if item.pinned else 1, item.sort_order, item.id))
        return cls(
            revision=_as_int(payload.get("revision"), 0),
            commands=commands,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "commands": [item.to_dict() for item in self.commands],
        }
