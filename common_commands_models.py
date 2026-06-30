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
    title: str
    content: str
    device_id: str
    request_id: str
    pinned: bool = False
    sort_order: int = 0
    version: int = 1
    revision: int = 0

    @property
    def command_text(self) -> str:
        return self.content

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CommonCommand":
        content = str(payload.get("content") or payload.get("command_text") or "")
        return cls(
            id=str(payload.get("id") or ""),
            title=str(payload.get("title") or content),
            content=content,
            device_id=str(payload.get("device_id") or ""),
            request_id=str(payload.get("request_id") or ""),
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
    title: str = ""
    content: str = ""
    pinned: bool = False

    @property
    def command_text(self) -> str:
        return self.content


@dataclass(slots=True, frozen=True)
class CommonCommandUpdate:
    expected_version: int
    title: str | None = None
    content: str | None = None
    pinned: bool | None = None

    @property
    def command_text(self) -> str | None:
        return self.content


@dataclass(slots=True, frozen=True)
class CommonCommandsSnapshot:
    revision: int
    commands: list[CommonCommand]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CommonCommandsSnapshot":
        raw_commands = payload.get("commands")
        if raw_commands is None:
            raw_commands = []
        if not isinstance(raw_commands, list):
            raise ValueError("commands must be a list")
        commands: list[CommonCommand] = []
        for item in raw_commands:
            if not isinstance(item, Mapping):
                raise ValueError("command entries must be mappings")
            commands.append(CommonCommand.from_dict(item))
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
