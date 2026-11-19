"""Common command menus, editing, and remote operations."""

import wx
from common_commands_models import CommonCommandCreate, CommonCommandUpdate, CommonCommandsSnapshot
from common_commands_store import (
    CommonCommandsReadError,
    CommonCommandsVersionConflictError,
    CommonCommandsWriteError,
)
from mc_frame_dialogs import CommonCommandsDialog

import mc_frame_bindings as bindings


class CommonCommandsMixin:
    def _common_command_label(self, command) -> str:
        title = str(getattr(command, "title", "") or "").strip()
        if title:
            return title
        content = str(getattr(command, "content", "") or "").strip()
        return content or "未命名命令"

    def _get_common_commands_dialog(self) -> CommonCommandsDialog:
        dialog = getattr(self, "common_commands_dialog", None)
        if dialog is not None:
            try:
                if dialog.IsBeingDeleted():
                    dialog = None
            except Exception:
                dialog = None
        if dialog is None:
            dialog = CommonCommandsDialog(self)
            self.common_commands_dialog = dialog
        return dialog

    def _show_common_commands_surface(self) -> bool:
        try:
            dialog = self._get_common_commands_dialog()
            dialog.refresh_commands()
        except CommonCommandsReadError as exc:
            wx.MessageBox(str(exc), "提示", wx.OK | wx.ICON_WARNING)
            return False
        dialog.Show()
        dialog.Raise()
        dialog.focus_default_control()
        return True

    def _selected_common_command(self):
        dialog = getattr(self, "common_commands_dialog", None)
        if dialog is None:
            return None
        return dialog.selected_command()

    def _selected_common_command_id(self) -> str:
        dialog = getattr(self, "common_commands_dialog", None)
        if dialog is None:
            return ""
        return dialog.selected_command_id()

    def _restore_common_commands_focus(self, select_id: str | None = None) -> None:
        dialog = getattr(self, "common_commands_dialog", None)
        if dialog is None:
            return
        dialog.refresh_commands(select_id)
        if select_id and dialog.common_commands_list_ids:
            dialog.common_commands_list_model.set_selection_by_id(select_id)
            self._focus_control_safely(dialog.common_commands_list)
            return
        dialog.focus_default_control()

    def _write_common_commands_snapshot(self, snapshot: CommonCommandsSnapshot) -> None:
        self.common_commands_store._write_snapshot(snapshot)

    def _show_common_commands_store_error(self, exc: Exception) -> None:
        wx.MessageBox(str(exc), "提示", wx.OK | wx.ICON_WARNING)

    def _add_common_command(self) -> bool:
        saved = {}

        def save(title, content):
            try:
                saved["command"] = self.common_commands_store.create_command(CommonCommandCreate(title=title, content=content))
                return True
            except (CommonCommandsReadError, CommonCommandsWriteError) as exc:
                self._show_common_commands_store_error(exc)
                return False

        dlg = bindings.app.CommonCommandEditDialog(self, dialog_title="添加常用命令", on_save=save)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            title, content = dlg.values()
        finally:
            dlg.Destroy()
        command = saved.get("command")
        if command is None:
            return False
        self._restore_common_commands_focus(command.id)
        self.SetStatusText("常用命令已保存")
        return True

    def _edit_selected_common_command(self) -> bool:
        command = self._selected_common_command()
        if command is None:
            return False
        saved = {}

        def save(title, content):
            try:
                saved["command"] = self.common_commands_store.update_command(
                    command.id,
                    CommonCommandUpdate(
                        expected_version=int(getattr(command, "version", 0) or 0),
                        title=title,
                        content=content,
                    ),
                )
                return True
            except (CommonCommandsReadError, CommonCommandsWriteError, CommonCommandsVersionConflictError) as exc:
                self._show_common_commands_store_error(exc)
                return False

        dlg = bindings.app.CommonCommandEditDialog(
            self,
            dialog_title="编辑常用命令",
            initial_title=str(getattr(command, "title", "") or ""),
            initial_content=str(getattr(command, "content", "") or ""),
            on_save=save,
        )
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            title, content = dlg.values()
        finally:
            dlg.Destroy()
        updated = saved.get("command")
        if updated is None:
            return False
        self._restore_common_commands_focus(updated.id)
        self.SetStatusText("常用命令已保存")
        return True

    def _delete_selected_common_command(self) -> bool:
        command = self._selected_common_command()
        if command is None:
            return False
        if not self._confirm("确定删除该常用命令吗？"):
            return False
        try:
            self.common_commands_store.delete_command(
                command.id,
                expected_version=int(getattr(command, "version", 0) or 0),
            )
            snapshot = self.common_commands_store.read_snapshot()
        except (CommonCommandsReadError, CommonCommandsWriteError, CommonCommandsVersionConflictError) as exc:
            self._show_common_commands_store_error(exc)
            return False
        remaining = list(snapshot.commands)
        next_id = ""
        if remaining:
            next_id = next(
                (
                    item.id
                    for item in remaining
                    if (0 if item.pinned else 1, item.sort_order, item.id)
                    >= (0 if command.pinned else 1, command.sort_order, command.id)
                ),
                remaining[-1].id,
            )
        self._restore_common_commands_focus(next_id or None)
        self.SetStatusText("常用命令已删除")
        return True

    def _toggle_selected_common_command_pin(self) -> bool:
        command = self._selected_common_command()
        if command is None:
            return False
        try:
            if bool(getattr(command, "pinned", False)):
                updated = self.common_commands_store.unpin_command(
                    command.id,
                    expected_version=int(getattr(command, "version", 0) or 0),
                )
                status = "常用命令已取消置顶"
            else:
                updated = self.common_commands_store.pin_command(
                    command.id,
                    expected_version=int(getattr(command, "version", 0) or 0),
                )
                status = "常用命令已置顶"
        except (CommonCommandsReadError, CommonCommandsWriteError, CommonCommandsVersionConflictError) as exc:
            self._show_common_commands_store_error(exc)
            return False
        self._restore_common_commands_focus(updated.id)
        self.SetStatusText(status)
        return True

    def _move_selected_common_command(self, direction: int) -> bool:
        command = self._selected_common_command()
        if command is None:
            return False
        try:
            if direction < 0:
                updated = self.common_commands_store.move_up(
                    command.id,
                    expected_version=int(getattr(command, "version", 0) or 0),
                )
            else:
                updated = self.common_commands_store.move_down(
                    command.id,
                    expected_version=int(getattr(command, "version", 0) or 0),
                )
        except (CommonCommandsReadError, CommonCommandsWriteError, CommonCommandsVersionConflictError) as exc:
            self._show_common_commands_store_error(exc)
            return False
        if updated.id != command.id or int(getattr(updated, "version", 0) or 0) != int(getattr(command, "version", 0) or 0):
            self._restore_common_commands_focus(updated.id)
            self.SetStatusText("常用命令顺序已更新")
            return True
        return False

    def _move_selected_common_command_up(self) -> bool:
        return self._move_selected_common_command(-1)

    def _move_selected_common_command_down(self) -> bool:
        return self._move_selected_common_command(1)

    def _send_selected_common_command(self) -> bool:
        command = self._selected_common_command()
        if command is None:
            return False
        content = str(getattr(command, "content", "") or "")
        ok, message = self._submit_question(content.strip(), source="local")
        if not ok:
            if message:
                wx.MessageBox(message, "提示", wx.OK | wx.ICON_WARNING)
            return False
        return True

    def _show_common_commands_menu(self) -> bool:
        dialog = getattr(self, "common_commands_dialog", None)
        if dialog is None:
            return False
        menu = wx.Menu()
        add_id = wx.NewIdRef()
        menu.Append(add_id, "添加")
        menu.Bind(wx.EVT_MENU, lambda _evt: self._add_common_command(), id=add_id)
        if dialog.selected_command() is not None:
            edit_id = wx.NewIdRef()
            delete_id = wx.NewIdRef()
            pin_id = wx.NewIdRef()
            move_up_id = wx.NewIdRef()
            move_down_id = wx.NewIdRef()
            menu.Append(edit_id, "编辑")
            menu.Append(delete_id, "删除")
            menu.Append(pin_id, "取消置顶" if bool(getattr(dialog.selected_command(), "pinned", False)) else "置顶")
            menu.Append(move_up_id, "向上移动")
            menu.Append(move_down_id, "向下移动")
            menu.Bind(wx.EVT_MENU, lambda _evt: self._edit_selected_common_command(), id=edit_id)
            menu.Bind(wx.EVT_MENU, lambda _evt: self._delete_selected_common_command(), id=delete_id)
            menu.Bind(wx.EVT_MENU, lambda _evt: self._toggle_selected_common_command_pin(), id=pin_id)
            menu.Bind(wx.EVT_MENU, lambda _evt: self._move_selected_common_command_up(), id=move_up_id)
            menu.Bind(wx.EVT_MENU, lambda _evt: self._move_selected_common_command_down(), id=move_down_id)
        dialog.PopupMenu(menu)
        menu.Destroy()
        return True

    def _on_common_commands_context(self, _event) -> None:
        self._show_common_commands_menu()

    def _on_common_commands_key_down(self, event) -> None:
        if self._on_any_key_down_escape_minimize(event):
            return
        self._touch_navigation_quiet_window(event)
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if self._send_selected_common_command():
                return
        if self._is_context_menu_key(key):
            if self._show_common_commands_menu():
                return
        if key == wx.WXK_DELETE:
            if self._delete_selected_common_command():
                return
        modifiers = event.GetModifiers() if hasattr(event, "GetModifiers") else 0
        if modifiers == wx.MOD_ALT and key == wx.WXK_UP:
            if self._move_selected_common_command_up():
                return
        if modifiers == wx.MOD_ALT and key == wx.WXK_DOWN:
            if self._move_selected_common_command_down():
                return
        event.Skip()

    def _on_common_commands_char(self, event) -> None:
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if self._send_selected_common_command():
                return
        event.Skip()

    def _remote_common_commands_snapshot_payload(self, snapshot: CommonCommandsSnapshot) -> dict:
        return {
            "accepted": True,
            "revision": int(getattr(snapshot, "revision", 0) or 0),
            "commands": [dict(command.to_dict()) for command in list(getattr(snapshot, "commands", []) or [])],
        }

    def _remote_api_common_commands_list_ui(self, _payload: dict | None = None) -> tuple[int, dict]:
        try:
            snapshot = self.common_commands_store.read_snapshot()
        except CommonCommandsReadError:
            return 500, {"accepted": False, "error": "common_commands_unavailable"}
        return 200, self._remote_common_commands_snapshot_payload(snapshot)

    def _remote_common_commands_unavailable(self) -> tuple[int, dict]:
        return 500, {"accepted": False, "error": "common_commands_unavailable"}

    def _remote_common_commands_stale_state(
        self,
        *,
        current_revision: int,
        observed_revision: int | None = None,
        current_version: int | None = None,
        observed_version: int | None = None,
    ) -> tuple[int, dict]:
        body = {
            "accepted": False,
            "error": "stale_state",
            "current_revision": int(current_revision),
        }
        if observed_revision is not None:
            body["observed_revision"] = int(observed_revision)
        if current_version is not None:
            body["current_version"] = int(current_version)
        if observed_version is not None:
            body["observed_version"] = int(observed_version)
        return 409, body

    def _remote_common_commands_optional_int(self, payload: dict, key: str) -> int | None:
        value = payload.get(key)
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return int(value)

    def _remote_api_common_commands_create_ui(self, payload: dict | None = None) -> tuple[int, dict]:
        payload = payload or {}
        content = str(payload.get("content") or "").strip()
        title = str(payload.get("title") or "").strip()
        if not content:
            return 400, {"accepted": False, "error": "invalid_payload"}
        try:
            observed_revision = self._remote_common_commands_optional_int(payload, "observed_revision")
            device_id = str(payload.get("device_id") or "").strip()
            request_id = str(payload.get("request_id") or "").strip()
            snapshot = self.common_commands_store.read_snapshot()
        except ValueError:
            return 400, {"accepted": False, "error": "invalid_payload"}
        except CommonCommandsReadError:
            return self._remote_common_commands_unavailable()
        except Exception:
            return self._remote_common_commands_unavailable()
        existing_command = None
        if device_id and request_id:
            existing_command = next(
                (
                    command
                    for command in snapshot.commands
                    if command.device_id == device_id and command.request_id == request_id
                ),
                None,
            )
        if existing_command is not None:
            return 200, {
                **self._remote_common_commands_snapshot_payload(snapshot),
                "result": "replayed",
            }
        if observed_revision is not None and observed_revision != snapshot.revision:
            return self._remote_common_commands_stale_state(
                current_revision=snapshot.revision,
                observed_revision=observed_revision,
            )
        try:
            self.common_commands_store.create_command(
                CommonCommandCreate(
                    device_id=device_id,
                    request_id=request_id,
                    title=title,
                    content=content,
                )
            )
            snapshot = self.common_commands_store.read_snapshot()
        except CommonCommandsReadError:
            return self._remote_common_commands_unavailable()
        except Exception:
            return self._remote_common_commands_unavailable()
        return 200, {
            **self._remote_common_commands_snapshot_payload(snapshot),
            "result": "created",
        }

    def _remote_api_common_commands_update_ui(self, payload: dict | None = None) -> tuple[int, dict]:
        payload = payload or {}
        command_id = str(payload.get("command_id") or payload.get("id") or "").strip()
        title = payload.get("title")
        content = payload.get("content")
        if not command_id or (title is None and content is None):
            return 400, {"accepted": False, "error": "invalid_payload"}
        try:
            observed_revision = self._remote_common_commands_optional_int(payload, "observed_revision")
            observed_version = self._remote_common_commands_optional_int(payload, "observed_version")
            snapshot = self.common_commands_store.read_snapshot()
        except ValueError:
            return 400, {"accepted": False, "error": "invalid_payload"}
        except CommonCommandsReadError:
            return self._remote_common_commands_unavailable()
        except Exception:
            return self._remote_common_commands_unavailable()
        if observed_revision is not None and observed_revision != snapshot.revision:
            return self._remote_common_commands_stale_state(
                current_revision=snapshot.revision,
                observed_revision=observed_revision,
            )
        current_command = next((command for command in snapshot.commands if command.id == command_id), None)
        if current_command is None:
            return 404, {"accepted": False, "error": "not_found"}
        expected_version = current_command.version if observed_version is None else observed_version
        if observed_version is not None and observed_version != current_command.version:
            return self._remote_common_commands_stale_state(
                current_revision=snapshot.revision,
                observed_revision=observed_revision,
                current_version=current_command.version,
                observed_version=observed_version,
            )
        try:
            self.common_commands_store.update_command(
                command_id,
                CommonCommandUpdate(
                    expected_version=expected_version,
                    title=None if title is None else str(title),
                    content=None if content is None else str(content),
                ),
            )
            updated_snapshot = self.common_commands_store.read_snapshot()
        except KeyError:
            return 404, {"accepted": False, "error": "not_found"}
        except CommonCommandsVersionConflictError as exc:
            try:
                latest_snapshot = self.common_commands_store.read_snapshot()
                current_revision = latest_snapshot.revision
            except Exception:
                current_revision = snapshot.revision
            return self._remote_common_commands_stale_state(
                current_revision=current_revision,
                observed_revision=observed_revision,
                current_version=exc.current_version,
                observed_version=exc.expected_version,
            )
        except CommonCommandsReadError:
            return self._remote_common_commands_unavailable()
        except Exception:
            return self._remote_common_commands_unavailable()
        return 200, self._remote_common_commands_snapshot_payload(updated_snapshot)

    def _remote_api_common_commands_delete_ui(self, payload: dict | None = None) -> tuple[int, dict]:
        payload = payload or {}
        command_id = str(payload.get("command_id") or payload.get("id") or "").strip()
        if not command_id:
            return 400, {"accepted": False, "error": "invalid_payload"}
        try:
            observed_revision = self._remote_common_commands_optional_int(payload, "observed_revision")
            observed_version = self._remote_common_commands_optional_int(payload, "observed_version")
            snapshot = self.common_commands_store.read_snapshot()
        except ValueError:
            return 400, {"accepted": False, "error": "invalid_payload"}
        except CommonCommandsReadError:
            return self._remote_common_commands_unavailable()
        except Exception:
            return self._remote_common_commands_unavailable()
        if observed_revision is not None and observed_revision != snapshot.revision:
            return self._remote_common_commands_stale_state(
                current_revision=snapshot.revision,
                observed_revision=observed_revision,
            )
        current_command = next((command for command in snapshot.commands if command.id == command_id), None)
        if current_command is None:
            return 404, {"accepted": False, "error": "not_found"}
        expected_version = current_command.version if observed_version is None else observed_version
        if observed_version is not None and observed_version != current_command.version:
            return self._remote_common_commands_stale_state(
                current_revision=snapshot.revision,
                observed_revision=observed_revision,
                current_version=current_command.version,
                observed_version=observed_version,
            )
        try:
            self.common_commands_store.delete_command(
                command_id,
                expected_version=expected_version,
            )
            updated_snapshot = self.common_commands_store.read_snapshot()
        except KeyError:
            return 404, {"accepted": False, "error": "not_found"}
        except CommonCommandsVersionConflictError as exc:
            try:
                latest_snapshot = self.common_commands_store.read_snapshot()
                current_revision = latest_snapshot.revision
            except Exception:
                current_revision = snapshot.revision
            return self._remote_common_commands_stale_state(
                current_revision=current_revision,
                observed_revision=observed_revision,
                current_version=exc.current_version,
                observed_version=exc.expected_version,
            )
        except CommonCommandsReadError:
            return self._remote_common_commands_unavailable()
        except Exception:
            return self._remote_common_commands_unavailable()
        return 200, self._remote_common_commands_snapshot_payload(updated_snapshot)

    def _remote_api_common_commands_pin_ui(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_common_commands_pin_state_ui(payload, pinned=True)

    def _remote_api_common_commands_unpin_ui(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_common_commands_pin_state_ui(payload, pinned=False)

    def _remote_api_common_commands_pin_state_ui(
        self,
        payload: dict | None,
        *,
        pinned: bool,
    ) -> tuple[int, dict]:
        payload = payload or {}
        command_id = str(payload.get("command_id") or payload.get("id") or "").strip()
        if not command_id:
            return 400, {"accepted": False, "error": "invalid_payload"}
        try:
            observed_revision = self._remote_common_commands_optional_int(payload, "observed_revision")
            observed_version = self._remote_common_commands_optional_int(payload, "observed_version")
            snapshot = self.common_commands_store.read_snapshot()
        except ValueError:
            return 400, {"accepted": False, "error": "invalid_payload"}
        except CommonCommandsReadError:
            return self._remote_common_commands_unavailable()
        except Exception:
            return self._remote_common_commands_unavailable()
        if observed_revision is not None and observed_revision != snapshot.revision:
            return self._remote_common_commands_stale_state(
                current_revision=snapshot.revision,
                observed_revision=observed_revision,
            )
        current_command = next((command for command in snapshot.commands if command.id == command_id), None)
        if current_command is None:
            return 404, {"accepted": False, "error": "not_found"}
        expected_version = current_command.version if observed_version is None else observed_version
        if observed_version is not None and observed_version != current_command.version:
            return self._remote_common_commands_stale_state(
                current_revision=snapshot.revision,
                observed_revision=observed_revision,
                current_version=current_command.version,
                observed_version=observed_version,
            )
        try:
            if pinned:
                self.common_commands_store.pin_command(command_id, expected_version=expected_version)
            else:
                self.common_commands_store.unpin_command(command_id, expected_version=expected_version)
            updated_snapshot = self.common_commands_store.read_snapshot()
        except KeyError:
            return 404, {"accepted": False, "error": "not_found"}
        except CommonCommandsVersionConflictError as exc:
            try:
                latest_snapshot = self.common_commands_store.read_snapshot()
                current_revision = latest_snapshot.revision
            except Exception:
                current_revision = snapshot.revision
            return self._remote_common_commands_stale_state(
                current_revision=current_revision,
                observed_revision=observed_revision,
                current_version=exc.current_version,
                observed_version=exc.expected_version,
            )
        except CommonCommandsReadError:
            return self._remote_common_commands_unavailable()
        except Exception:
            return self._remote_common_commands_unavailable()
        return 200, {
            **self._remote_common_commands_snapshot_payload(updated_snapshot),
            "result": "updated",
        }

    def _remote_api_common_commands_move_up_ui(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_common_commands_move_ui(payload, direction="up")

    def _remote_api_common_commands_move_down_ui(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_common_commands_move_ui(payload, direction="down")

    def _remote_api_common_commands_move_ui(
        self,
        payload: dict | None,
        *,
        direction: str,
    ) -> tuple[int, dict]:
        payload = payload or {}
        command_id = str(payload.get("command_id") or payload.get("id") or "").strip()
        if not command_id:
            return 400, {"accepted": False, "error": "invalid_payload"}
        try:
            observed_revision = self._remote_common_commands_optional_int(payload, "observed_revision")
            observed_version = self._remote_common_commands_optional_int(payload, "observed_version")
            snapshot = self.common_commands_store.read_snapshot()
        except ValueError:
            return 400, {"accepted": False, "error": "invalid_payload"}
        except CommonCommandsReadError:
            return self._remote_common_commands_unavailable()
        except Exception:
            return self._remote_common_commands_unavailable()
        if observed_revision is not None and observed_revision != snapshot.revision:
            return self._remote_common_commands_stale_state(
                current_revision=snapshot.revision,
                observed_revision=observed_revision,
            )
        current_command = next((command for command in snapshot.commands if command.id == command_id), None)
        if current_command is None:
            return 404, {"accepted": False, "error": "not_found"}
        expected_version = current_command.version if observed_version is None else observed_version
        if observed_version is not None and observed_version != current_command.version:
            return self._remote_common_commands_stale_state(
                current_revision=snapshot.revision,
                observed_revision=observed_revision,
                current_version=current_command.version,
                observed_version=observed_version,
            )
        try:
            if direction == "up":
                self.common_commands_store.move_up(command_id, expected_version=expected_version)
            else:
                self.common_commands_store.move_down(command_id, expected_version=expected_version)
            updated_snapshot = self.common_commands_store.read_snapshot()
        except KeyError:
            return 404, {"accepted": False, "error": "not_found"}
        except CommonCommandsVersionConflictError as exc:
            try:
                latest_snapshot = self.common_commands_store.read_snapshot()
                current_revision = latest_snapshot.revision
            except Exception:
                current_revision = snapshot.revision
            return self._remote_common_commands_stale_state(
                current_revision=current_revision,
                observed_revision=observed_revision,
                current_version=exc.current_version,
                observed_version=exc.expected_version,
            )
        except CommonCommandsReadError:
            return self._remote_common_commands_unavailable()
        except Exception:
            return self._remote_common_commands_unavailable()
        return 200, {
            **self._remote_common_commands_snapshot_payload(updated_snapshot),
            "result": "updated",
        }
