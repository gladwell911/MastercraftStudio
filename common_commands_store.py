from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path

from common_commands_models import (
    CommonCommand,
    CommonCommandCreate,
    CommonCommandUpdate,
    CommonCommandsSnapshot,
)


class CommonCommandsReadError(RuntimeError):
    def __init__(self, path: Path, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(f"{message}: {path}")
        self.path = Path(path)
        self.cause = cause


class CommonCommandsVersionConflictError(RuntimeError):
    def __init__(self, *, expected_version: int, current_version: int) -> None:
        super().__init__(f"stale common command version: expected {expected_version}, current {current_version}")
        self.expected_version = int(expected_version)
        self.current_version = int(current_version)


class DesktopCommonCommandsStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_snapshot(CommonCommandsSnapshot(revision=0, commands=[]))

    def read_snapshot(self) -> CommonCommandsSnapshot:
        self.initialize()
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CommonCommandsReadError(self.path, "failed to read common commands store", cause=exc) from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CommonCommandsReadError(self.path, "failed to parse common commands store", cause=exc) from exc
        if not isinstance(payload, dict):
            raise CommonCommandsReadError(self.path, "common commands store root must be an object")
        try:
            return CommonCommandsSnapshot.from_dict(payload)
        except Exception as exc:
            raise CommonCommandsReadError(self.path, "common commands store has invalid shape", cause=exc) from exc

    def create_command(self, data: CommonCommandCreate) -> CommonCommand:
        snapshot = self.read_snapshot()
        for command in snapshot.commands:
            if command.device_id == data.device_id and command.request_id == data.request_id:
                return command
        next_revision = snapshot.revision + 1
        content = str(data.content or "")
        created = CommonCommand(
            id=uuid.uuid4().hex,
            title=str(data.title or content),
            content=content,
            device_id=str(data.device_id or ""),
            request_id=str(data.request_id or ""),
            pinned=bool(data.pinned),
            sort_order=self._next_sort_order(snapshot.commands, pinned=bool(data.pinned)),
            version=1,
            revision=next_revision,
        )
        updated_commands = list(snapshot.commands)
        updated_commands.append(created)
        self._write_snapshot(
            CommonCommandsSnapshot(
                revision=next_revision,
                commands=self._sorted_commands(updated_commands),
            )
        )
        return created

    def update_command(self, command_id: str, data: CommonCommandUpdate) -> CommonCommand:
        snapshot = self.read_snapshot()
        next_revision = snapshot.revision + 1
        updated_commands: list[CommonCommand] = []
        updated_command: CommonCommand | None = None
        normalized_id = str(command_id or "")
        for command in snapshot.commands:
            if command.id != normalized_id:
                updated_commands.append(command)
                continue
            if command.version != int(data.expected_version):
                raise CommonCommandsVersionConflictError(
                    expected_version=int(data.expected_version),
                    current_version=command.version,
                )
            next_content = command.content if data.content is None else str(data.content)
            updated_command = replace(
                command,
                title=command.title if data.title is None else str(data.title),
                content=next_content,
                pinned=command.pinned if data.pinned is None else bool(data.pinned),
                version=command.version + 1,
                revision=next_revision,
            )
            updated_commands.append(updated_command)
        if updated_command is None:
            raise KeyError(normalized_id)
        self._write_snapshot(
            CommonCommandsSnapshot(
                revision=next_revision,
                commands=self._sorted_commands(updated_commands),
            )
        )
        return updated_command

    def _write_snapshot(self, snapshot: CommonCommandsSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._temp_path()
        payload = json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2)
        try:
            temp_path.write_text(payload, encoding="utf-8")
            self._replace_file(temp_path, self.path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def _replace_file(self, source: Path, target: Path) -> None:
        source.replace(target)

    def _temp_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")

    def _sorted_commands(self, commands: list[CommonCommand]) -> list[CommonCommand]:
        return sorted(commands, key=lambda item: (0 if item.pinned else 1, item.sort_order, item.id))

    def _next_sort_order(self, commands: list[CommonCommand], *, pinned: bool) -> int:
        sort_orders = [item.sort_order for item in commands if item.pinned == pinned]
        if not sort_orders:
            return 0
        return max(sort_orders) + 1
