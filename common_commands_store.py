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
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        return CommonCommandsSnapshot.from_dict(payload)

    def create_command(self, data: CommonCommandCreate) -> CommonCommand:
        snapshot = self.read_snapshot()
        for command in snapshot.commands:
            if command.device_id == data.device_id and command.request_id == data.request_id:
                return command
        next_revision = snapshot.revision + 1
        created = CommonCommand(
            id=uuid.uuid4().hex,
            device_id=str(data.device_id or ""),
            request_id=str(data.request_id or ""),
            command_text=str(data.command_text or ""),
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
            updated_command = replace(
                command,
                command_text=command.command_text if data.command_text is None else str(data.command_text),
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
        self.path.write_text(
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _sorted_commands(self, commands: list[CommonCommand]) -> list[CommonCommand]:
        return sorted(commands, key=lambda item: (0 if item.pinned else 1, item.sort_order, item.id))

    def _next_sort_order(self, commands: list[CommonCommand], *, pinned: bool) -> int:
        sort_orders = [item.sort_order for item in commands if item.pinned == pinned]
        if not sort_orders:
            return 0
        return max(sort_orders) + 1
