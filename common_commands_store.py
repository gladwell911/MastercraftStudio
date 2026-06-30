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
        device_id = str(data.device_id or "")
        request_id = str(data.request_id or "")
        if device_id and request_id:
            for command in snapshot.commands:
                if command.device_id == device_id and command.request_id == request_id:
                    return command
        next_revision = snapshot.revision + 1
        content = str(data.content or "")
        created = CommonCommand(
            id=uuid.uuid4().hex,
            title=str(data.title or content),
            content=content,
            device_id=device_id,
            request_id=request_id,
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

    def delete_command(self, command_id: str, *, expected_version: int) -> CommonCommand:
        snapshot = self.read_snapshot()
        next_revision = snapshot.revision + 1
        deleted_command: CommonCommand | None = None
        updated_commands: list[CommonCommand] = []
        normalized_id = str(command_id or "")
        for command in snapshot.commands:
            if command.id != normalized_id:
                updated_commands.append(command)
                continue
            if command.version != int(expected_version):
                raise CommonCommandsVersionConflictError(
                    expected_version=int(expected_version),
                    current_version=command.version,
                )
            deleted_command = command
        if deleted_command is None:
            raise KeyError(normalized_id)
        self._write_snapshot(
            CommonCommandsSnapshot(
                revision=next_revision,
                commands=self._sorted_commands(updated_commands),
            )
        )
        return deleted_command

    def pin_command(self, command_id: str, *, expected_version: int) -> CommonCommand:
        return self._set_pinned(command_id, expected_version=expected_version, pinned=True)

    def unpin_command(self, command_id: str, *, expected_version: int) -> CommonCommand:
        return self._set_pinned(command_id, expected_version=expected_version, pinned=False)

    def move_up(self, command_id: str, *, expected_version: int) -> CommonCommand:
        return self._move(command_id, expected_version=expected_version, direction=-1)

    def move_down(self, command_id: str, *, expected_version: int) -> CommonCommand:
        return self._move(command_id, expected_version=expected_version, direction=1)

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

    def _set_pinned(self, command_id: str, *, expected_version: int, pinned: bool) -> CommonCommand:
        snapshot = self.read_snapshot()
        next_revision = snapshot.revision + 1
        updated_commands: list[CommonCommand] = []
        updated_command: CommonCommand | None = None
        normalized_id = str(command_id or "")
        target_sort_order = self._next_sort_order(snapshot.commands, pinned=pinned)
        for command in snapshot.commands:
            if command.id != normalized_id:
                updated_commands.append(command)
                continue
            if command.version != int(expected_version):
                raise CommonCommandsVersionConflictError(
                    expected_version=int(expected_version),
                    current_version=command.version,
                )
            if command.pinned == pinned:
                return command
            updated_command = replace(
                command,
                pinned=pinned,
                sort_order=target_sort_order,
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

    def _move(self, command_id: str, *, expected_version: int, direction: int) -> CommonCommand:
        snapshot = self.read_snapshot()
        commands = list(snapshot.commands)
        normalized_id = str(command_id or "")
        current_command = next((item for item in commands if item.id == normalized_id), None)
        if current_command is None:
            raise KeyError(normalized_id)
        if current_command.version != int(expected_version):
            raise CommonCommandsVersionConflictError(
                expected_version=int(expected_version),
                current_version=current_command.version,
            )
        section_commands = [item for item in commands if item.pinned == current_command.pinned]
        section_index = next((idx for idx, item in enumerate(section_commands) if item.id == normalized_id), -1)
        if section_index < 0:
            raise KeyError(normalized_id)
        neighbor_index = section_index + direction
        if neighbor_index < 0 or neighbor_index >= len(section_commands):
            return current_command
        neighbor = section_commands[neighbor_index]
        next_revision = snapshot.revision + 1
        updated_commands: list[CommonCommand] = []
        for command in commands:
            if command.id == current_command.id:
                updated_commands.append(
                    replace(
                        command,
                        sort_order=neighbor.sort_order,
                        version=command.version + 1,
                        revision=next_revision,
                    )
                )
                continue
            if command.id == neighbor.id:
                updated_commands.append(
                    replace(
                        command,
                        sort_order=current_command.sort_order,
                    )
                )
                continue
            updated_commands.append(command)
        moved_command = next(item for item in updated_commands if item.id == current_command.id)
        self._write_snapshot(
            CommonCommandsSnapshot(
                revision=next_revision,
                commands=self._sorted_commands(updated_commands),
            )
        )
        return moved_command
