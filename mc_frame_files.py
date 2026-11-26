"""Desktop file library, attachments, and transfer operations."""

import base64
import re
import uuid
import wx
from file_transfer import FileDirection, FileTransferStatus, extract_windows_file_paths
from pathlib import Path
from remote_nats_protocol import build_file_command_event
from urllib.parse import quote

import mc_frame_bindings as bindings
from agy_acp import AGY_MODEL_ID


class FileManagerMixin:
    def _show_file_manager(self) -> None:
        if not hasattr(self, "file_manager_list"):
            return
        self.detail_title_label.SetLabel("文件管理：")
        self.answer_list.Hide()
        self.execution_list.Hide()
        self.extracted_file_list.Hide()
        self.file_manager_list.Show()
        self._refresh_file_manager_list()
        self.chat_root_panel.Layout()
        self.file_manager_list.SetFocus()

    def _refresh_file_manager_list(self, selected_id: str | None = None) -> bool:
        if hasattr(self.file_library, "sync_storage_dir"):
            self.file_library.sync_storage_dir()
        records = self.file_library.list_records()
        rows = [(record.id, record.label()) for record in records]
        changed = self.file_manager_list_model.replace_visible_page(rows, selected_id=selected_id)
        self.file_manager_ids = list(self.file_manager_list_model.visible_ids)
        if changed:
            self._request_listbox_repaint(self.file_manager_list)
        return changed

    def _selected_file_manager_record(self):
        record_id = self.file_manager_list_model.selected_id()
        if not record_id:
            return None
        return self.file_library.get_record(record_id)

    def _open_path_with_default_app(self, path) -> bool:
        target = Path(path)
        if not target.exists():
            self.SetStatusText(f"文件不存在：{target}")
            return False
        try:
            bindings.app.os.startfile(str(target))
            return True
        except Exception as exc:
            self.SetStatusText(f"打开文件失败：{exc}")
            return False

    def _open_selected_file_manager_record(self) -> bool:
        record = self._selected_file_manager_record()
        if record is None:
            return False
        return self._open_path_with_default_app(record.stored_path)

    def _delete_selected_file_manager_record(self) -> bool:
        record = self._selected_file_manager_record()
        if record is None:
            return False
        if not self._confirm(f"确定删除 {record.name} 吗？"):
            return False
        if not self.file_library.delete_record(record.id):
            self.SetStatusText("删除文件失败")
            return False
        self._refresh_file_manager_list()
        self.SetStatusText("已删除文件")
        return True

    def _send_selected_file_to_phone(self) -> bool:
        record = self._selected_file_manager_record()
        if record is None:
            return False
        return self._publish_file_offer_for_record(record)

    def _publish_file_offer_for_record(self, record) -> bool:
        offer = self._file_offer_for_record(record)
        download_url = str(offer.get("body", {}).get("download_url") or "").strip()
        if not download_url:
            error = str(offer.get("body", {}).get("error") or "文件下载地址不可用").strip()
            self.file_library.update_record_status(
                record.id,
                FileTransferStatus.FAILED,
                error_message=error,
            )
            self._refresh_file_manager_if_visible(selected_id=record.id)
            self.SetStatusText(error)
            return False
        self._last_file_offer_event = offer
        self._broadcast_remote_event(offer)
        self._mark_file_offer_waiting(record.id)
        self.SetStatusText(f"已创建文件发送请求：{record.name}")
        return True

    def _mark_file_offer_waiting(self, record_id: str) -> None:
        record = self.file_library.update_record_status(
            record_id,
            FileTransferStatus.WAITING_CONFIRMATION,
            transferred_bytes=0,
            speed_bytes_per_second=0,
        )
        if record is not None:
            self._refresh_file_manager_if_visible(selected_id=record.id)

    def _file_offer_for_record(self, record, *, public_route_ready=None, chat_id=None) -> dict:
        if public_route_ready is None:
            public_route_ready = self._ensure_public_file_route_ready()
        file_service = getattr(self, "file_service", None)
        error = ""
        if file_service is not None and hasattr(file_service, "download_url_for"):
            if public_route_ready:
                download_url = file_service.download_url_for(record)
            else:
                download_url = ""
                error = "公网文件通道不可用，未向手机下发本地下载地址。请确认已配置的公网入口可访问文件服务后重试。"
        else:
            base_url = str(getattr(self, "file_http_base_url", "") or "").rstrip("/")
            download_url = f"{base_url}/files/{record.id}" if base_url else ""
        event = build_file_command_event(
            request_id=f"file-offer-{uuid.uuid4().hex}",
            event_type="file_offer",
            device_id=str(getattr(self, "notes_device_id", "desktop") or "desktop"),
            body={
                "file_id": record.id,
                "name": record.name,
                "size_bytes": record.size_bytes,
                "download_url": download_url,
            },
            chat_id=str(getattr(self, "active_chat_id", "") or "") if chat_id is None else chat_id,
        )
        if error:
            event["body"]["error"] = error
        return event

    def _ensure_public_file_route_ready(self) -> bool:
        service = getattr(self, "file_service", None)
        public_base_url = str(getattr(service, "public_base_url", "") or "").strip()
        if not public_base_url:
            return True
        if not self._start_cloudflared_origin_proxy():
            return False
        origin_port = self._cloudflared_origin_port()
        if self._remote_tunnel_mode() == "external":
            runtime = self._remote_runtime_config()
            token = self._read_remote_control_token() or self.remote_control_token
            ready, _detail = self._verify_remote_public_ws(f"{runtime['published_base']}?token={quote(token)}")
            return ready
        ready, _mode, _detail = self._ensure_cloudflared_tunnel(origin_port)
        return ready

    def _file_command_body(self, payload: dict) -> dict:
        body = payload.get("body")
        if isinstance(body, dict):
            if "attachment_send_id" not in payload:
                return dict(body)
            attachment_fields = ("attachment_send_id", "attachment_upload_id", "file_id",
                                 "name", "size_bytes", "transferred_bytes")
            return {**{key: payload[key] for key in attachment_fields if key in payload}, **body}
        return {key: value for key, value in dict(payload or {}).items() if key not in {"type", "id", "event_id", "chat_id", "device_id", "created_at"}}

    def _refresh_file_manager_if_visible(self, selected_id: str | None = None) -> None:
        try:
            if getattr(self, "file_manager_list", None) is not None and self.file_manager_list.IsShown():
                self._refresh_file_manager_list(selected_id=selected_id)
        except Exception:
            pass

    def _remote_api_file_command_worker(self, payload: dict, *, source_transport=None) -> tuple[int, dict]:
        if str(payload.get("type") or "") not in ("file_probe", "file_download_request"):
            return self._run_remote_ui_route(self._remote_api_file_command_ui, payload)
        deadline = bindings.app.time.monotonic() + float(getattr(self, "_file_preparation_budget_seconds", 35.0))
        context = {}
        unavailable = (503, {"accepted": False, "error": "file_preparation_unavailable"})
        timed_out = (503, {"accepted": False, "error": "file_preparation_timeout"})
        capture_deadline = min(deadline, bindings.app.time.monotonic() + 5.0)

        def capture():
            if getattr(self, "_closing", False):
                return unavailable
            transport = getattr(self, "_remote_nats_transport", None)
            if source_transport is not None and transport is not source_transport:
                return unavailable
            library = self.file_library
            service = getattr(self, "file_service", None)
            runtime = dict(getattr(self, "remote_nats_runtime_status", {}) or {})
            body = self._file_command_body(payload)
            identity = tuple(str(payload.get(k) or body.get(k) or "") for k in ("device_id", "session_id", "epoch", "id"))
            pair = str(getattr(getattr(transport, "subjects", None), "pair_id", ""))
            key = (id(transport), pair, *identity)
            # Finish configuration reads before publishing an in-flight owner.
            public_base = str(getattr(service, "public_base_url", "") or "").strip()
            public_url = self._build_remote_nats_url() if public_base else ""
            captured = dict(library=library, service=service, transport=transport, runtime=runtime,
                            chat_id=str(payload.get("chat_id") or getattr(self, "active_chat_id", "") or ""),
                            path=str(body.get("path") or "").strip(), public_base=public_base,
                            public_url=public_url)
            if bindings.app.time.monotonic() >= capture_deadline:
                return timed_out
            cache = getattr(self, "_file_preparation_requests", None)
            if cache is None:
                cache = self._file_preparation_requests = {}
            entry = cache.get(key) if identity[-1] else None
            owner = entry is None
            if owner:
                if len(cache) >= 128:
                    for old_key, old_entry in list(cache.items()):
                        if old_entry["done"].is_set():
                            del cache[old_key]
                            break
                    else:
                        return unavailable
                entry = {"done": bindings.app.threading.Event(), "result": None}
            context.update(captured, entry=entry, owner=owner)
            if bindings.app.time.monotonic() >= capture_deadline:
                return timed_out
            if owner and identity[-1]:
                cache[key] = entry
            return 200, {"accepted": True}

        try:
            status, response = self._run_remote_ui_route(capture, deadline=capture_deadline)
        except Exception as exc:
            status, response = 500, {"accepted": False, "error": str(exc)}
        if status != 200:
            # Covers the tiny gap between publishing the entry and signaling UI completion.
            if context.get("owner"):
                context["entry"]["result"] = status, response
                context["entry"]["done"].set()
            return status, response
        entry = context["entry"]
        if not context["owner"]:
            if not entry["done"].wait(max(0.0, deadline - bindings.app.time.monotonic())):
                return timed_out
            return entry["result"]
        def context_available():
            # Plain captured identities only; no wx calls on the worker.
            return (not getattr(self, "_closing", False)
                    and self.file_library is context["library"]
                    and getattr(self, "file_service", None) is context["service"]
                    and getattr(self, "_remote_nats_transport", None) is context["transport"]
                    and (source_transport is None or source_transport is context["transport"])
                    and dict(getattr(self, "remote_nats_runtime_status", {}) or {}) == context["runtime"])

        prepared = None
        result = unavailable
        try:
            library = context["library"]
            service = context["service"]
            if not context_available():
                return result
            if service is not None and callable(getattr(service, "start", None)):
                try:
                    restored = service.start()
                except Exception:
                    result = (503, {"accepted": False, "error": "file_service_unavailable"})
                    return result
                if not context_available():
                    # Close may have stopped this service while start was queued.
                    # Only undo the instance this worker actually restored.
                    if restored:
                        service.stop()
                    return result
            if bindings.app.time.monotonic() >= deadline:
                raise TimeoutError("file_preparation_timeout")
            prepared = library.prepare_local_file(context["path"], deadline=deadline)
            ready = True
            if context["public_base"]:
                remaining = deadline - bindings.app.time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("file_preparation_timeout")
                ready = bool(context["public_url"]) and self._verify_remote_public_ws(
                    context["public_url"], timeout_seconds=min(5.0, remaining))[0]
            if bindings.app.time.monotonic() >= deadline:
                raise TimeoutError("file_preparation_timeout")
            if not ready:
                result = (503, {"accepted": False, "exists": True, "error": "public_file_route_unavailable"})
            else:
                commit_deadline = min(deadline, bindings.app.time.monotonic() + 5.0)
                def commit():
                    if bindings.app.time.monotonic() >= commit_deadline:
                        return timed_out
                    if not context_available():
                        return unavailable
                    record = library.commit_prepared_file(prepared, deadline=commit_deadline)
                    published = False
                    try:
                        offer = self._file_offer_for_record(record, public_route_ready=True, chat_id=context["chat_id"])
                        download_url = str(offer.get("body", {}).get("download_url") or "")
                        if bindings.app.time.monotonic() >= commit_deadline or not download_url:
                            return timed_out if bindings.app.time.monotonic() >= commit_deadline else unavailable
                        if self._broadcast_remote_event(offer, retry_on_failure=False, publication_deadline=commit_deadline) is False:
                            raise RuntimeError("file_offer_unavailable")
                        published = True
                        self._last_file_offer_event = offer
                    finally:
                        if not published:
                            library.delete_record(record.id)
                    self._refresh_file_manager_if_visible(selected_id=record.id)
                    return 200, {"accepted": True, "exists": True, "file_id": record.id,
                                 "name": record.name, "size_bytes": record.size_bytes, "download_url": download_url}
                result = self._run_remote_ui_route(commit, deadline=commit_deadline)
        except FileNotFoundError:
            result = (404, {"accepted": False, "exists": False, "path": context["path"]})
        except TimeoutError:
            result = timed_out
        except Exception as exc:
            result = (500, {"accepted": False, "error": str(exc)})
        finally:
            try:
                if prepared is not None:
                    prepared[0].unlink(missing_ok=True)
            finally:
                entry["result"] = result
                entry["done"].set()
        return result

    def _remote_attachment_scope(self, payload: dict, send_id: str) -> tuple:
        source = payload.get("_attachment_source")
        chat_id = str(payload.get("chat_id") or "").strip()
        if (not isinstance(source, tuple) or len(source) != 2 or not all(source)
                or not chat_id or not send_id or len(send_id) > 128):
            return ()
        if chat_id not in {self.active_chat_id, self.current_chat_id} and not self._find_archived_chat(chat_id):
            return ()
        store = getattr(self, "chat_store", None)
        revision = max(1, store.get_clear_reconciliation_authority(chat_id)["revision"]) if store else 1
        return (*source, chat_id, send_id, revision)

    def _resolve_remote_chat_attachments(
        self, file_ids: list, scope: tuple, *, require_confirmation: bool = True,
    ) -> list[dict] | None:
        attachments = []
        root = self.file_library.storage_dir.resolve()
        for file_id in file_ids:
            record = self.file_library.get_record(file_id)
            if (record is None or record.attachment_scope != scope
                    or record.direction != FileDirection.PHONE_TO_DESKTOP
                    or (require_confirmation and not record.attachment_confirmed)
                    or record.status != FileTransferStatus.COMPLETED):
                return None
            try:
                path = record.stored_path.resolve(strict=True)
                if path.parent != root or not path.is_file() or path.stat().st_size != record.size_bytes:
                    return None
                with path.open("rb"):
                    pass
            except OSError:
                return None
            attachments.append({"name": record.name, "path": str(path)})
        successful, failed = self._normalize_outgoing_attachments(attachments)
        return None if failed else successful

    def _remote_api_file_command_ui(self, payload: dict) -> tuple[int, dict]:
        command_type = str((payload or {}).get("type") or "").strip()
        body = self._file_command_body(payload or {})
        file_id = str(body.get("file_id") or "").strip()
        existing = self.file_library.get_record(file_id) if file_id else None
        if existing is not None and existing.attachment_scope:
            scope = self._remote_attachment_scope(payload, str(body.get("attachment_send_id") or ""))
            if scope != existing.attachment_scope:
                return 403, {"accepted": False, "error": "attachment_owner_mismatch"}
            if command_type == "file_complete":
                if (type(body.get("size_bytes")) is not int or body["size_bytes"] != existing.size_bytes
                        or ("transferred_bytes" in body and
                            (type(body["transferred_bytes"]) is not int
                             or body["transferred_bytes"] != existing.size_bytes))):
                    return 400, {"accepted": False, "error": "unexpected_upload_size"}
                if not self._resolve_remote_chat_attachments([file_id], scope, require_confirmation=False):
                    return 409, {"accepted": False, "error": "attachment_upload_incomplete"}
                if not self.file_library.confirm_attachment_upload(file_id, scope):
                    return 409, {"accepted": False, "error": "attachment_upload_incomplete"}
                return 200, {"accepted": True, "file_id": file_id}
            if command_type != "file_cancel":
                return 400, {"accepted": False, "error": "invalid_attachment_file_command"}
        if command_type == "file_list":
            self.file_library.sync_storage_dir()
            files = [
                {
                    "file_id": record.id,
                    "name": record.name,
                    "size_bytes": record.size_bytes,
                    "local_path": str(record.stored_path),
                    "direction": record.direction.value,
                    "status": record.status.value,
                    "transferred_bytes": record.transferred_bytes,
                    "speed_bytes_per_second": record.speed_bytes_per_second,
                    "error": record.error_message,
                }
                for record in self.file_library.list_records()
            ]
            return 200, {"accepted": True, "files": files}
        if command_type == "file_add":
            path = str(body.get("path") or "").strip()
            try:
                record = self.file_library.add_local_file(Path(path))
            except FileNotFoundError:
                return 404, {"accepted": False, "exists": False, "path": path}
            except Exception as exc:
                return 500, {"accepted": False, "error": str(exc)}
            self._refresh_file_manager_if_visible(selected_id=record.id)
            return 200, {
                "accepted": True,
                "file_id": record.id,
                "name": record.name,
                "size_bytes": record.size_bytes,
                "local_path": str(record.stored_path),
            }
        if command_type == "file_delete":
            deleted = self.file_library.delete_record(file_id)
            self._refresh_file_manager_if_visible()
            return (200 if deleted else 404), {"accepted": bool(deleted), "file_id": file_id}
        if command_type == "file_accept":
            record = self.file_library.update_record_status(file_id, FileTransferStatus.ACCEPTED)
            self._refresh_file_manager_if_visible(selected_id=file_id)
            return (200 if record else 404), {"accepted": bool(record), "file_id": file_id}
        if command_type == "file_reject":
            record = self.file_library.update_record_status(file_id, FileTransferStatus.REJECTED)
            self._refresh_file_manager_if_visible(selected_id=file_id)
            return (200 if record else 404), {"accepted": bool(record), "file_id": file_id}
        if command_type == "file_progress":
            record = self.file_library.get_record(file_id)
            if record is None:
                return 404, {"accepted": False, "file_id": file_id}
            if record.status in {
                FileTransferStatus.COMPLETED,
                FileTransferStatus.CANCELED,
                FileTransferStatus.REJECTED,
                FileTransferStatus.FAILED,
                FileTransferStatus.PAUSED,
            }:
                # Delayed progress acknowledges the existing record without
                # reviving a terminal/paused transfer or refreshing its UI.
                return 200, {"accepted": True, "file_id": file_id}
            record = self.file_library.update_record_status(
                file_id,
                FileTransferStatus.TRANSFERRING,
                transferred_bytes=int(body.get("transferred_bytes") or 0),
                speed_bytes_per_second=int(body.get("speed_bytes_per_second") or 0),
                size_bytes=int(body.get("size_bytes") or 0) or record.size_bytes,
            )
            self._refresh_file_manager_if_visible(selected_id=file_id)
            return (200 if record else 404), {"accepted": bool(record), "file_id": file_id}
        if command_type in ("file_pause", "file_paused"):
            record, changed = self._apply_file_transfer_control_state(
                file_id,
                allowed_from={FileTransferStatus.ACCEPTED, FileTransferStatus.TRANSFERRING},
                status=FileTransferStatus.PAUSED,
            )
            if changed:
                self._refresh_file_manager_if_visible(selected_id=file_id)
                self._broadcast_file_transfer_state("file_paused", record)
            return (200 if record else 404), {"accepted": bool(record), "file_id": file_id}
        if command_type in ("file_resume", "file_resumed"):
            record, changed = self._apply_file_transfer_control_state(
                file_id,
                allowed_from={FileTransferStatus.PAUSED},
                status=FileTransferStatus.TRANSFERRING,
            )
            if changed:
                self._refresh_file_manager_if_visible(selected_id=file_id)
                self._broadcast_file_transfer_state("file_resumed", record)
            return (200 if record else 404), {"accepted": bool(record), "file_id": file_id}
        if command_type in ("file_cancel", "file_canceled"):
            record, changed = self._apply_file_transfer_control_state(
                file_id,
                allowed_from={
                    FileTransferStatus.WAITING_CONFIRMATION,
                    FileTransferStatus.ACCEPTED,
                    FileTransferStatus.TRANSFERRING,
                    FileTransferStatus.PAUSED,
                },
                status=FileTransferStatus.CANCELED,
            )
            if changed:
                self._refresh_file_manager_if_visible(selected_id=file_id)
                self._broadcast_file_transfer_state("file_canceled", record)
            return (200 if record else 404), {"accepted": bool(record), "file_id": file_id}
        if command_type == "file_complete":
            if "attachment_send_id" in body:
                return 409, {"accepted": False, "error": "attachment_reference_unavailable"}
            existing = self.file_library.get_record(file_id)
            size = int(body.get("size_bytes") or (existing.size_bytes if existing else 0) or 0)
            transferred = int(body.get("transferred_bytes") or size)
            record = self.file_library.update_record_status(
                file_id,
                FileTransferStatus.COMPLETED,
                size_bytes=size,
                transferred_bytes=transferred,
                speed_bytes_per_second=0,
            )
            self.file_library.sync_storage_dir()
            self._refresh_file_manager_if_visible(selected_id=file_id)
            return (200 if record else 404), {"accepted": bool(record), "file_id": file_id}
        if command_type == "file_error":
            record = self.file_library.update_record_status(
                file_id,
                FileTransferStatus.FAILED,
                error_message=str(body.get("error") or body.get("message") or "error"),
            )
            self._refresh_file_manager_if_visible(selected_id=file_id)
            return (200 if record else 404), {"accepted": bool(record), "file_id": file_id}
        if command_type in ("file_probe", "file_download_request"):
            path = str(body.get("path") or "").strip()
            info = self.file_library.probe_path(path)
            if not info.get("exists"):
                return 404, {"accepted": False, **info}
            try:
                record = self.file_library.add_local_file(Path(str(info["path"])), direction=FileDirection.DESKTOP_TO_PHONE)
            except Exception as exc:
                return 500, {"accepted": False, "exists": True, "error": str(exc)}
            offer = self._file_offer_for_record(record)
            download_url = str(offer.get("body", {}).get("download_url") or "").strip()
            if not download_url:
                error = str(offer.get("body", {}).get("error") or "文件下载地址不可用").strip()
                self.file_library.update_record_status(
                    record.id,
                    FileTransferStatus.FAILED,
                    error_message=error,
                )
                self._refresh_file_manager_if_visible(selected_id=record.id)
                return 503, {
                    "accepted": False,
                    "exists": True,
                    "file_id": record.id,
                    "name": record.name,
                    "size_bytes": record.size_bytes,
                    "download_url": "",
                    "error": error,
                }
            self._last_file_offer_event = offer
            self._broadcast_remote_event(offer)
            self._refresh_file_manager_if_visible(selected_id=record.id)
            return 200, {
                "accepted": True,
                "exists": True,
                "file_id": record.id,
                "name": record.name,
                "size_bytes": record.size_bytes,
                "download_url": download_url,
            }
        if command_type == "file_upload_request":
            name = str(body.get("name") or "upload.bin").strip() or "upload.bin"
            size = int(body.get("size_bytes") or 0)
            attachment_scope = ()
            upload_id = ""
            record = None
            if "attachment_send_id" in body:
                attachment_scope = self._remote_attachment_scope(payload, str(body["attachment_send_id"] or ""))
                upload_id = str(body.get("attachment_upload_id") or "").strip()
                if not attachment_scope or not upload_id or len(upload_id) > 180:
                    return 403, {"accepted": False, "error": "attachment_owner_mismatch"}
                if (size < 0 or len(name) > 240 or name.endswith((".", " "))
                        or Path(name).is_reserved()
                        or any(ord(ch) < 32 or ch in '<>:"/\\|?*' for ch in name)):
                    return 400, {"accepted": False, "error": "invalid_attachment_file"}
                store = getattr(self, "chat_store", None)
                if store is None or not getattr(self, "_chat_store_enabled", False):
                    return 503, {"accepted": False, "error": "attachment_upload_unavailable"}
                receipt = store.remote_attachment_send(attachment_scope, reserve=True)
                if receipt["revision"] != attachment_scope[-1] or receipt["state"] != "uploading":
                    return 409, {"accepted": False, "error": "attachment_send_expired"}
                for candidate in self.file_library.list_records():
                    if candidate.attachment_scope != attachment_scope or candidate.attachment_upload_id != upload_id:
                        continue
                    if candidate.attachment_original_name != name or candidate.size_bytes != size:
                        return 409, {"accepted": False, "error": "attachment_upload_conflict"}
                    if self._resolve_remote_chat_attachments([candidate.id], attachment_scope, require_confirmation=False):
                        return 200, {"accepted": True, "file_id": candidate.id, "upload_url": ""}
                    if candidate.status == FileTransferStatus.ACCEPTED and candidate.size_bytes == size:
                        record = candidate
                        break
            if record is None:
                record = self.file_library.prepare_incoming_upload(
                    name, size_bytes=size, attachment_scope=attachment_scope, attachment_upload_id=upload_id,
                )
            service = getattr(self, "file_service", None)
            upload_url = ""
            if service is not None and hasattr(service, "upload_url_for"):
                upload_url = service.upload_url_for(record if attachment_scope else record.name)
            if attachment_scope and not upload_url:
                return 503, {"accepted": False, "error": "attachment_upload_unavailable"}
            self._refresh_file_manager_if_visible(selected_id=record.id)
            return 200, {"accepted": True, "file_id": record.id, "name": record.name, "size_bytes": record.size_bytes, "upload_url": upload_url}
        return 404, {"accepted": False, "error": "unknown_file_command"}

    def _apply_file_transfer_control_state(
        self,
        file_id: str,
        *,
        allowed_from: set[FileTransferStatus],
        status: FileTransferStatus,
    ) -> tuple[object | None, bool]:
        """Apply one valid control transition and report whether it changed.

        Commands are intentionally idempotent: a known record in an
        inapplicable or terminal state is an accepted no-op. This prevents
        delayed pause/resume/cancel commands from emitting events that claim a
        state the persisted record never entered.
        """
        record = self.file_library.get_record(file_id)
        if record is None or record.status not in allowed_from:
            return record, False
        updated = self.file_library.update_record_status(
            file_id,
            status,
            allowed_from=allowed_from,
            speed_bytes_per_second=0,
        )
        return updated, updated is not None and updated.status == status

    def _broadcast_file_transfer_state(self, event_type: str, record) -> None:
        self._broadcast_remote_event(
            build_file_command_event(
                request_id=f"{event_type}-{uuid.uuid4().hex}",
                event_type=event_type,
                device_id=str(getattr(self, "notes_device_id", "desktop") or "desktop"),
                body={
                    "file_id": record.id,
                    "size_bytes": record.size_bytes,
                    "transferred_bytes": record.transferred_bytes,
                    "speed_bytes_per_second": record.speed_bytes_per_second,
                },
            )
        )

    def _start_file_service_if_configured(self) -> bool:
        if str(bindings.app.os.getenv("DESKTOP_FILE_SERVICE_AUTOSTART", "1")).strip().lower() in {"0", "false", "no"}:
            return False
        service = getattr(self, "file_service", None)
        if service is None:
            return False
        try:
            service.start()
        except Exception as exc:
            self.SetStatusText(f"copyparty 文件服务启动失败：{exc}")
            return False
        self.file_http_base_url = service.base_url
        return True

    def _add_file_to_manager_from_dialog(self) -> bool:
        dlg = wx.FileDialog(self, "添加文件", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            record = self.file_library.add_local_file(Path(dlg.GetPath()))
        finally:
            dlg.Destroy()
        self._refresh_file_manager_list(selected_id=record.id)
        return True

    def _show_file_manager_menu(self) -> None:
        menu = wx.Menu()
        add_id = wx.NewIdRef()
        send_id = wx.NewIdRef()
        delete_id = wx.NewIdRef()
        menu.Append(int(add_id), "添加文件")
        menu.Append(int(send_id), "发送到手机")
        menu.Append(int(delete_id), "删除")
        self.Bind(wx.EVT_MENU, lambda _evt: self._add_file_to_manager_from_dialog(), id=int(add_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._send_selected_file_to_phone(), id=int(send_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._delete_selected_file_manager_record(), id=int(delete_id))
        self.PopupMenu(menu)
        menu.Destroy()

    def _on_file_manager_context(self, _event) -> None:
        self._show_file_manager_menu()

    def _on_file_manager_key_down(self, event) -> None:
        key = event.GetKeyCode()
        if self._is_context_menu_key(key):
            self._show_file_manager_menu()
            return
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if self._open_selected_file_manager_record():
                return
        if key == wx.WXK_DELETE:
            if self._delete_selected_file_manager_record():
                return
        event.Skip()

    def _extract_files_from_selected_answer(self) -> bool:
        paths = extract_windows_file_paths(self._selected_answer_source_text())
        rows = [(path, Path(path).name or path) for path in paths]
        self.detail_title_label.SetLabel("提取文件：")
        self.answer_list.Hide()
        self.execution_list.Hide()
        self.file_manager_list.Hide()
        self.extracted_file_list.Show()
        changed = self.extracted_file_list_model.replace_visible_page(rows, selected_id=paths[0] if paths else None)
        self.extracted_file_ids = list(self.extracted_file_list_model.visible_ids)
        if changed:
            self._request_listbox_repaint(self.extracted_file_list)
        self.chat_root_panel.Layout()
        if paths:
            self.extracted_file_list.SetFocus()
            return True
        self.SetStatusText("当前内容未提取到文件路径")
        return False

    def _selected_extracted_file_path(self) -> str:
        return self.extracted_file_list_model.selected_id()

    def _add_selected_extracted_file_to_manager(self) -> bool:
        path = self._selected_extracted_file_path()
        if not path:
            return False
        try:
            record = self.file_library.add_local_file(Path(path))
        except Exception as exc:
            self.SetStatusText(f"添加到文件管理失败：{exc}")
            return False
        self._show_file_manager()
        self._refresh_file_manager_list(selected_id=record.id)
        return True

    def _send_selected_extracted_file_to_phone(self) -> bool:
        path = self._selected_extracted_file_path()
        if not path:
            return False
        try:
            record = self.file_library.add_local_file(Path(path))
        except Exception as exc:
            self.SetStatusText(f"发送到手机失败：{exc}")
            return False
        return self._publish_file_offer_for_record(record)

    def _show_extracted_file_menu(self) -> None:
        menu = wx.Menu()
        add_id = wx.NewIdRef()
        send_id = wx.NewIdRef()
        menu.Append(int(add_id), "添加到文件管理")
        menu.Append(int(send_id), "发送到手机")
        self.Bind(wx.EVT_MENU, lambda _evt: self._add_selected_extracted_file_to_manager(), id=int(add_id))
        self.Bind(wx.EVT_MENU, lambda _evt: self._send_selected_extracted_file_to_phone(), id=int(send_id))
        self.PopupMenu(menu)
        menu.Destroy()

    def _on_extracted_file_context(self, _event) -> None:
        self._show_extracted_file_menu()

    def _on_extracted_file_key_down(self, event) -> None:
        if self._is_context_menu_key(event.GetKeyCode()):
            self._show_extracted_file_menu()
            return
        event.Skip()

    def _attachment_label(self, attachment: dict, *, incoming: bool = False) -> str:
        name = str((attachment or {}).get("name") or Path(str((attachment or {}).get("path") or "")).name or "未知附件").strip()
        is_image = str((attachment or {}).get("kind") or "").strip() == "image"
        if incoming:
            kind = "图片" if is_image else "文件"
            return f"CLI 发来{kind}：{name}"
        success = str((attachment or {}).get("status") or "").strip() == "success"
        if is_image:
            return "图片上传成功" if success else "图片上传失败"
        return f"{name} {'上传成功' if success else '上传失败'}"

    def _turn_attachment_rows(self, turn_idx: int, attachments: list[dict], *, incoming: bool = False) -> list[tuple[str, tuple]]:
        rows: list[tuple[str, tuple]] = []
        for attachment in attachments or []:
            label = self._attachment_label(attachment, incoming=incoming)
            open_path = str((attachment or {}).get("open_path") or (attachment or {}).get("path") or "").strip()
            rows.append((label, ("attachment", turn_idx, label, open_path)))
        return rows

    def _append_turn_attachment_rows(self, turn_idx: int, attachments: list[dict], *, incoming: bool = False) -> None:
        for label, meta in self._turn_attachment_rows(turn_idx, attachments, incoming=incoming):
            if hasattr(self, "answer_list_model"):
                row_id = self._answer_row_id(meta)
                if row_id in self.answer_list_model.labels_by_id:
                    continue
                self.answer_list_model.append(row_id, label)
            else:
                self.answer_list.Append(label)
            self.answer_meta.append(meta)

    def _input_attachment_marker_text(self, attachments: list[dict]) -> str:
        names = [str((item or {}).get("name") or "").strip() for item in attachments or [] if str((item or {}).get("name") or "").strip()]
        if not names:
            return ""
        return f"【附件】{'、'.join(names)}"

    def _strip_attachment_markers(self, text: str) -> str:
        lines = []
        for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = raw_line.strip()
            if line.startswith("【附件】"):
                continue
            lines.append(raw_line)
        return "\n".join(lines).strip()

    def _normalize_attachment_kind(self, path: str) -> str:
        suffix = Path(str(path or "")).suffix.lower()
        return "image" if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"} else "file"

    def _normalize_outgoing_attachments(self, attachments: list[dict]) -> tuple[list[dict], list[dict]]:
        success = []
        failed = []
        for item in attachments or []:
            path = str((item or {}).get("path") or "").strip()
            name = str((item or {}).get("name") or Path(path).name or "未知附件").strip()
            exists = bool(path) and Path(path).is_file()
            normalized = {
                "name": name,
                "path": path,
                "kind": str((item or {}).get("kind") or self._normalize_attachment_kind(path)).strip() or "file",
                "direction": "outgoing",
                "status": "success" if exists else "failed",
                "open_path": path if exists else "",
            }
            (success if exists else failed).append(normalized)
        return success, failed

    def _record_received_attachment(self, turn: dict, attachment: dict) -> bool:
        if not isinstance(turn, dict) or not isinstance(attachment, dict):
            return False
        attachments = turn.setdefault("received_attachments", [])
        if not isinstance(attachments, list):
            attachments = []
            turn["received_attachments"] = attachments
        open_path = str(attachment.get("open_path") or attachment.get("path") or "").strip()
        for existing in attachments:
            if str((existing or {}).get("open_path") or (existing or {}).get("path") or "").strip() == open_path:
                return False
        attachments.append(attachment)
        return True

    def _extract_existing_file_attachments_from_text(self, text: str, source: str) -> list[dict]:
        matches = []
        for candidate in re.findall(r"(?:[A-Za-z]:[\\/][^\s<>\|\?\*\"']+|/[^\s<>\|\?\*\"']+)", str(text or "")):
            path = str(candidate or "").strip().rstrip(".,;:)]}")
            if not path:
                continue
            try:
                file_path = Path(path)
            except Exception:
                continue
            if not file_path.is_file():
                continue
            matches.append(
                {
                    "name": file_path.name,
                    "path": str(file_path),
                    "kind": self._normalize_attachment_kind(str(file_path)),
                    "direction": "incoming",
                    "status": "success",
                    "open_path": str(file_path),
                    "source": source,
                }
            )
        deduped = []
        seen = set()
        for item in matches:
            key = str(item.get("open_path") or "")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _summarize_attachment_send_text(self, success: list[dict], failed: list[dict]) -> str:
        outgoing = success + failed
        if not outgoing:
            return ""
        names = "、".join(str(item.get("name") or "").strip() for item in outgoing if str(item.get("name") or "").strip())
        has_only_one_image = len(outgoing) == 1 and str(outgoing[0].get("kind") or "") == "image"
        suffix = "图片" if has_only_one_image else "文件"
        status = "已成功上传" if success and not failed else ("上传失败" if failed and not success else "已部分上传")
        return f"{names} {suffix}{status}".strip()

    def _build_cli_attachment_context(self, attachments: list[dict]) -> str:
        lines = []
        for item in attachments or []:
            path = str((item or {}).get("path") or "").strip()
            if not path:
                continue
            kind = "图片" if str((item or {}).get("kind") or "") == "image" else "文件"
            lines.append(f"- {kind}：{path}")
        if not lines:
            return ""
        return "请结合以下本地附件继续处理：\n" + "\n".join(lines)

    def _build_codex_input_items(self, question: str, attachments: list[dict]) -> list[dict]:
        items = []
        text = str(question or "").strip()
        file_context = self._build_cli_attachment_context([item for item in attachments or [] if str((item or {}).get("kind") or "") != "image"])
        if text and file_context:
            items.append({"type": "text", "text": f"{text}\n\n{file_context}"})
        elif text:
            items.append({"type": "text", "text": text})
        elif file_context:
            items.append({"type": "text", "text": file_context})
        for item in attachments or []:
            if str((item or {}).get("kind") or "") == "image":
                path = str((item or {}).get("path") or "").strip()
                if path:
                    items.append({"type": "localImage", "path": path})
        return items or [{"type": "text", "text": ""}]

    @staticmethod
    def _kimi_image_media_type(path: str) -> str:
        suffix = Path(str(path or "")).suffix.lower()
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }.get(suffix, "image/png")

    def _build_kimi_content_blocks(self, question: str, attachments: list[dict]) -> list[dict]:
        blocks = []
        text = str(question or "").strip()
        file_context = self._build_cli_attachment_context([item for item in attachments or [] if str((item or {}).get("kind") or "") != "image"])
        if text and file_context:
            blocks.append({"type": "text", "text": f"{text}\n\n{file_context}"})
        elif text:
            blocks.append({"type": "text", "text": text})
        elif file_context:
            blocks.append({"type": "text", "text": file_context})
        for item in attachments or []:
            if str((item or {}).get("kind") or "") != "image":
                continue
            path = str((item or {}).get("path") or "").strip()
            if not path:
                continue
            try:
                data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            except OSError:
                continue
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "kind": "base64",
                        "media_type": self._kimi_image_media_type(path),
                        "data": data,
                    },
                }
            )
        return blocks or [{"type": "text", "text": ""}]

    def _queue_input_attachments(self, attachments: list[dict], *, update_input: bool = True) -> bool:
        success, failed = self._normalize_outgoing_attachments(attachments)
        self._pending_input_attachments.extend(success + failed)
        if update_input and (success or failed):
            marker = self._input_attachment_marker_text(success + failed)
            current = self.input_edit.GetValue()
            if marker and marker not in current:
                next_value = f"{current}\n{marker}".strip() if current.strip() else marker
                self.input_edit.SetValue(next_value)
                self.input_edit.SetInsertionPointEnd()
            self.input_edit.SetFocus()
        return bool(success or failed)

    def _load_chat_attachments_via_dialog(self) -> bool:
        dlg = wx.FileDialog(
            self,
            "选择图片或文件",
            wildcard="所有文件 (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
        )
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            paths = [str(path or "").strip() for path in dlg.GetPaths() if str(path or "").strip()]
        finally:
            dlg.Destroy()
        attachments = [{"name": Path(path).name, "path": path, "kind": self._normalize_attachment_kind(path)} for path in paths]
        if not attachments:
            return False
        self._pending_input_attachments = []
        self._queue_input_attachments(attachments, update_input=False)
        chat, _, _ = self._chat_target_for_request(self._visible_clear_owner())
        question = self.input_edit.GetValue() if (chat or {}).get("model") == AGY_MODEL_ID else ""
        ok, message = self._submit_question(question, source="local")
        if not ok and message:
            wx.MessageBox(message, "提示", wx.OK | wx.ICON_WARNING)
        return ok

    def _read_clipboard_attachments(self) -> list[dict]:
        attachments = []
        if not wx.TheClipboard.Open():
            return attachments
        try:
            file_data = wx.FileDataObject()
            if wx.TheClipboard.GetData(file_data):
                for path in file_data.GetFilenames():
                    text = str(path or "").strip()
                    if text:
                        attachments.append({"name": Path(text).name, "path": text, "kind": self._normalize_attachment_kind(text)})
            if attachments:
                return attachments
            bitmap_data = wx.BitmapDataObject()
            if wx.TheClipboard.GetData(bitmap_data):
                bitmap = bitmap_data.GetBitmap()
                if bitmap and bitmap.IsOk():
                    file_path = self.chat_uploads_dir / f"clipboard_{int(bindings.app.time.time() * 1000)}_{uuid.uuid4().hex[:6]}.png"
                    if bitmap.SaveFile(str(file_path), wx.BITMAP_TYPE_PNG):
                        attachments.append({"name": file_path.name, "path": str(file_path), "kind": "image"})
        finally:
            wx.TheClipboard.Close()
        return attachments

    def _try_paste_clipboard_attachments_to_input(self) -> bool:
        attachments = self._read_clipboard_attachments()
        if not attachments:
            return False
        self._queue_input_attachments(attachments, update_input=True)
        self.SetStatusText("已添加附件到输入框")
        return True
