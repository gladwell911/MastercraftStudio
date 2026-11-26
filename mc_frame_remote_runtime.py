"""Remote server and tunnel configuration and lifecycle."""

from __future__ import annotations

import asyncio
import ctypes
import json
import re
import socket
import sys
import wx
from aiohttp import ClientTimeout, WSMsgType
from mc_frame_settings import (
    DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT,
    DEFAULT_REMOTE_CONTROL_DOMAIN,
    DEFAULT_REMOTE_CONTROL_TOKEN,
    DEFAULT_REMOTE_NATS_PORT,
    DEFAULT_REMOTE_NATS_WEBSOCKET_PORT,
    REMOTE_CLOUDFLARED_ORIGIN_PORT_FALLBACKS,
    REMOTE_NATS_WEBSOCKET_PORT_FALLBACKS,
)
from nats_runtime import NatsRuntimeConfig
from pathlib import Path
from remote_machine_config import load_machine_config, load_machine_config_with_restore
from typing import Callable
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

import mc_frame_bindings as bindings


class RemoteRuntimeMixin:
    def _schedule_remote_nats_autostart(self) -> None:
        if not self.remote_control_autostart:
            return

        def _worker() -> None:
            try:
                self._start_remote_nats_runtime_if_configured(ensure_connectivity=True)
            except Exception as exc:
                bindings.app.wx_call_after_if_alive(self.SetStatusText, f"远程 NATS 启动失败：{exc}")
            finally:
                self._start_remote_health_monitor()

        bindings.app.threading.Thread(target=_worker, daemon=True).start()

    def _read_remote_control_setting(self, *names: str, default: str = "") -> str:
        for name in names:
            value = str(bindings.app.os.getenv(name) or "").strip()
            if value:
                return value
        return default

    def _has_remote_control_env(self, *names: str) -> bool:
        for name in names:
            if str(bindings.app.os.getenv(name) or "").strip():
                return True
        return False

    def _read_remote_control_bool_setting(self, *names: str, default: bool = True) -> bool:
        raw = self._read_remote_control_setting(*names, default="")
        if not raw:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

    def _initialize_remote_control_settings(self) -> None:
        machine_path = self.app_data_dir / "remote_machine.json"
        machine, machine_hints = load_machine_config_with_restore(
            machine_path,
            default_domain=self.remote_control_domain or DEFAULT_REMOTE_CONTROL_DOMAIN,
            default_token=self.remote_control_token or DEFAULT_REMOTE_CONTROL_TOKEN)
        for hint in machine_hints:
            bindings.app.wx_call_after_if_alive(self.SetStatusText, hint)
        changed = self.remote_control_domain != machine["domain"]
        self.remote_control_pair_id = machine["pair_id"]
        self.remote_control_domain = machine["domain"]
        self.remote_control_token = machine["token"]
        has_domain_env = self._has_remote_control_env(
            "REMOTE_CONTROL_DOMAIN",
            "CLAUDECODE_REMOTE_CONTROL_DOMAIN",
        )
        has_local_binding_env = self._has_remote_control_env(
            "REMOTE_CONTROL_HOST",
            "CLAUDECODE_REMOTE_CONTROL_HOST",
            "REMOTE_CONTROL_PORT",
            "CLAUDECODE_REMOTE_CONTROL_PORT",
        )
        has_autostart_env = self._has_remote_control_env(
            "REMOTE_CONTROL_AUTOSTART",
            "CLAUDECODE_REMOTE_CONTROL_AUTOSTART",
        )

        token = self._read_remote_control_setting(
            "REMOTE_CONTROL_TOKEN",
            "CLAUDECODE_REMOTE_CONTROL_TOKEN",
            default=self.remote_control_token,
        )
        if not token and self.remote_control_pair_id == "default":
            token = DEFAULT_REMOTE_CONTROL_TOKEN
        if token != self.remote_control_token:
            self.remote_control_token = token
            changed = True

        domain = self._read_remote_control_setting(
            "REMOTE_CONTROL_DOMAIN",
            "CLAUDECODE_REMOTE_CONTROL_DOMAIN",
            default=self.remote_control_domain,
        )
        if not has_domain_env and has_local_binding_env:
            domain = ""
        if domain:
            domain = bindings.app.normalize_remote_nats_endpoint(domain, default_scheme="wss")
        if (
            not domain
            and not has_local_binding_env and self._read_pair_id() == "default"
        ):
            domain = DEFAULT_REMOTE_CONTROL_DOMAIN
        if domain != self.remote_control_domain:
            self.remote_control_domain = domain
            changed = True

        fixed_domain_mode = bool(domain)
        host = self._read_remote_control_setting(
            "REMOTE_CONTROL_HOST",
            "CLAUDECODE_REMOTE_CONTROL_HOST",
            default=self.remote_control_host or "127.0.0.1",
        ).strip() or "127.0.0.1"
        if not has_domain_env and has_local_binding_env and not self._has_remote_control_env(
            "REMOTE_CONTROL_HOST",
            "CLAUDECODE_REMOTE_CONTROL_HOST",
        ):
            host = "127.0.0.1"
        if fixed_domain_mode and bindings.app.is_loopback_remote_host(host):
            host = "0.0.0.0"
        if host != self.remote_control_host:
            self.remote_control_host = host
            changed = True

        port_text = self._read_remote_control_setting(
            "REMOTE_CONTROL_PORT",
            "CLAUDECODE_REMOTE_CONTROL_PORT",
            default=str(self.remote_control_port or DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT),
        ).strip() or str(DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT)
        try:
            port = int(port_text)
        except Exception:
            port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        if fixed_domain_mode:
            if port <= 0:
                port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        if port < 0:
            port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        if port == 0 and port_text != "0":
            port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        if port != self.remote_control_port:
            self.remote_control_port = port
            changed = True

        autostart = self._read_remote_control_bool_setting(
            "REMOTE_CONTROL_AUTOSTART",
            "CLAUDECODE_REMOTE_CONTROL_AUTOSTART",
            default=self.remote_control_autostart,
        )
        if fixed_domain_mode and not has_autostart_env:
            autostart = True
        if autostart != self.remote_control_autostart:
            self.remote_control_autostart = autostart
            changed = True

        if changed:
            self._defer_chat_state_save()

    def _remote_tunnel_mode(self) -> str:
        mode = self._read_remote_control_setting(
            "REMOTE_CONTROL_TUNNEL_MODE", "CLAUDECODE_REMOTE_CONTROL_TUNNEL_MODE",
            default="cloudflared",
        ).strip().lower()
        if mode not in {"cloudflared", "external"}:
            raise ValueError(f"Invalid REMOTE_CONTROL_TUNNEL_MODE: {mode!r}")
        return mode

    def _remote_runtime_config(self) -> dict:
        self._remote_tunnel_mode()
        has_domain_env = self._has_remote_control_env(
            "REMOTE_CONTROL_DOMAIN",
            "CLAUDECODE_REMOTE_CONTROL_DOMAIN",
        )
        has_local_binding_env = self._has_remote_control_env(
            "REMOTE_CONTROL_HOST",
            "CLAUDECODE_REMOTE_CONTROL_HOST",
            "REMOTE_CONTROL_PORT",
            "CLAUDECODE_REMOTE_CONTROL_PORT",
        )
        domain = bindings.app.normalize_remote_nats_endpoint(
            self._read_remote_control_setting(
                "REMOTE_CONTROL_DOMAIN",
                "CLAUDECODE_REMOTE_CONTROL_DOMAIN",
                default=self.remote_control_domain,
            ),
            default_scheme="wss",
        )
        if not has_domain_env and has_local_binding_env:
            domain = ""
        if (
            not domain
            and not has_local_binding_env and self._read_pair_id() == "default"
        ):
            domain = DEFAULT_REMOTE_CONTROL_DOMAIN
        fixed_domain_mode = bool(domain)
        host = self._read_remote_control_setting(
            "REMOTE_CONTROL_HOST",
            "CLAUDECODE_REMOTE_CONTROL_HOST",
            default=self.remote_control_host or "127.0.0.1",
        ).strip() or "127.0.0.1"
        if not has_domain_env and has_local_binding_env and not self._has_remote_control_env(
            "REMOTE_CONTROL_HOST",
            "CLAUDECODE_REMOTE_CONTROL_HOST",
        ):
            host = "127.0.0.1"
        port_text = self._read_remote_control_setting(
            "REMOTE_CONTROL_PORT",
            "CLAUDECODE_REMOTE_CONTROL_PORT",
            default=str(self.remote_control_port or DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT),
        ).strip() or str(DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT)
        try:
            port = int(port_text)
        except Exception:
            port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        if fixed_domain_mode:
            if bindings.app.is_loopback_remote_host(host):
                host = "0.0.0.0"
            if port <= 0:
                port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
            published_base = domain
            publish_port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        else:
            if port < 0:
                port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
            publish_port = port if port > 0 else DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
            published_host = "127.0.0.1" if host == "0.0.0.0" else host
            published_base = bindings.app.normalize_remote_nats_endpoint(
                f"ws://{published_host}:{DEFAULT_REMOTE_NATS_WEBSOCKET_PORT}/nats",
                default_scheme="ws",
            )
        return {
            "domain": domain,
            "fixed_domain_mode": fixed_domain_mode,
            "host": host,
            "port": port,
            "published_base": published_base,
            "published_bind": f"ws://{host}:{DEFAULT_REMOTE_NATS_WEBSOCKET_PORT}/nats",
        }

    def _read_remote_control_token(self) -> str:
        return self._read_remote_control_setting(
            "REMOTE_CONTROL_TOKEN",
            "CLAUDECODE_REMOTE_CONTROL_TOKEN",
            default=getattr(self, "remote_control_token", DEFAULT_REMOTE_CONTROL_TOKEN),
        )

    def _is_process_elevated(self) -> bool:
        if bindings.app.os.name != "nt":
            return False
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def _runtime_environment_summary(self) -> dict:
        runtime = self._remote_runtime_config()
        return {
            "is_frozen": bool(getattr(sys, "frozen", False)),
            "is_elevated": self._is_process_elevated(),
            "is_fixed_domain_remote_mode": bool(runtime["fixed_domain_mode"]),
        }

    def _set_remote_runtime_status(
        self,
        *,
        local_listener_ready: bool | None = None,
        public_ws_ready: bool | None = None,
        last_remote_error: str | None = None,
        published_url: str | None = None,
    ) -> None:
        status = dict(getattr(self, "remote_control_runtime_status", {}) or {})
        status.setdefault("local_listener_ready", False)
        status.setdefault("public_ws_ready", False)
        status.setdefault("last_remote_error", "")
        status.setdefault("published_url", "")
        if local_listener_ready is not None:
            status["local_listener_ready"] = bool(local_listener_ready)
        if public_ws_ready is not None:
            status["public_ws_ready"] = bool(public_ws_ready)
        if last_remote_error is not None:
            status["last_remote_error"] = str(last_remote_error or "")
        if published_url is not None:
            status["published_url"] = str(published_url or "")
        self.remote_control_runtime_status = status
        self.remote_control_runtime_url = status["published_url"] if status["public_ws_ready"] else ""

    def _format_remote_startup_error(self, message: str) -> str:
        env = self._runtime_environment_summary()
        return (
            f"{message} "
            f"(frozen={env['is_frozen']}, elevated={env['is_elevated']}, fixed_domain={env['is_fixed_domain_remote_mode']})"
        )

    def _set_remote_nats_runtime_status(
        self,
        *,
        enabled: bool | None = None,
        tcp_url: str | None = None,
        websocket_url: str | None = None,
        cloudflared_url: str | None = None,
        last_error: str | None = None,
    ) -> None:
        status = dict(getattr(self, "remote_nats_runtime_status", {}) or {})
        status.setdefault("enabled", False)
        status.setdefault("tcp_url", "")
        status.setdefault("websocket_url", "")
        status.setdefault("cloudflared_url", "")
        status.setdefault("last_error", "")
        if enabled is not None:
            status["enabled"] = bool(enabled)
        if tcp_url is not None:
            status["tcp_url"] = str(tcp_url or "")
        if websocket_url is not None:
            status["websocket_url"] = str(websocket_url or "")
        if cloudflared_url is not None:
            status["cloudflared_url"] = str(cloudflared_url or "")
        if last_error is not None:
            status["last_error"] = str(last_error or "")
        self.remote_nats_runtime_status = status
        self.remote_nats_runtime_url = status["websocket_url"] if status["enabled"] else ""
        self._configure_file_service_public_base_from_remote_status()

    def _cloudflare_file_public_base_url(self) -> str:
        status = getattr(self, "remote_nats_runtime_status", {}) or {}
        if not status.get("enabled"):
            return ""
        configured_url = self._remote_runtime_config()["domain"]
        remote_url = str(configured_url if self._remote_tunnel_mode() == "external" else
                         status.get("cloudflared_url") or configured_url or "").strip()
        if not remote_url:
            return ""
        try:
            parsed = urlsplit(remote_url)
        except Exception:
            return ""
        scheme = "https" if parsed.scheme in {"wss", "https"} else "http"
        if not parsed.netloc:
            return ""
        return urlunsplit((scheme, parsed.netloc, "", "", ""))

    def _configure_file_service_public_base_from_remote_status(self) -> None:
        service = getattr(self, "file_service", None)
        if service is None or not hasattr(service, "set_public_base_url"):
            return
        service.set_public_base_url(self._cloudflare_file_public_base_url())
        self.file_http_base_url = service.base_url

    def _remote_nats_call_ui(self, callback: Callable[[], tuple[int, dict]]) -> tuple[int, dict]:
        if getattr(self, "_closing", False):
            return 503, {"accepted": False, "error": "closing"}
        if bindings.app.threading.current_thread() is bindings.app.threading.main_thread():
            return callback()

        done = bindings.app.threading.Event()
        result: dict[str, object] = {}

        def _run() -> None:
            try:
                if getattr(self, "_closing", False):
                    result["value"] = (503, {"accepted": False, "error": "closing"})
                else:
                    result["value"] = callback()
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

        if not bindings.app.wx_call_after_if_alive(_run):
            raise RuntimeError("UI is not available for NATS command handling")
        if not done.wait(30):
            raise TimeoutError("NATS command handling timed out on UI thread")
        if "error" in result:
            raise result["error"]  # type: ignore[misc]
        value = result.get("value")
        if isinstance(value, tuple):
            return value
        raise RuntimeError("NATS command handler returned invalid response")

    def _stop_remote_servers(self) -> None:
        self._remote_health_stop.set()
        with self._remote_health_lock:
            self._remote_health_generation += 1
        if self._remote_tunnel_mode() != "external":
            self._stop_managed_cloudflared_process()
        self._stop_cloudflared_origin_proxy()
        transport = getattr(self, "_remote_nats_transport", None)
        if transport is not None:
            try:
                transport.stop()
            except Exception:
                pass
        self._remote_nats_transport = None
        process = getattr(self, "_remote_nats_process", None)
        if process is not None:
            try:
                process.stop()
            except Exception:
                pass
        self._remote_nats_process = None
        self._set_remote_nats_runtime_status(enabled=False)

    def _start_remote_health_monitor(self) -> None:
        if getattr(self, "_closing", False) or self._remote_nats_transport is None:
            return
        if not self._remote_runtime_config()["fixed_domain_mode"]:
            return
        with self._remote_health_lock:
            if (
                getattr(self, "_closing", False)
                or self._remote_nats_transport is None
            ):
                return
            if self._remote_health_thread is not None and self._remote_health_thread.is_alive():
                return
            self._remote_health_stop = bindings.app.threading.Event()
            generation = self._remote_health_generation
            stop = self._remote_health_stop
            self._remote_health_thread = bindings.app.threading.Thread(
                target=self._remote_health_worker, args=(stop, generation), daemon=True
            )
            self._remote_health_thread.start()

    def _remote_health_current(self, stop, generation: int) -> bool:
        return (
            not stop.is_set()
            and generation == self._remote_health_generation
            and not getattr(self, "_closing", False)
            and self._remote_nats_transport is not None
        )

    def _remote_health_worker(self, stop, generation: int) -> None:
        from tunnel_policy import TunnelRestartBudget
        restart_budget = TunnelRestartBudget()
        failures = 0
        last_restart = float("-inf")
        while not stop.wait(30):
            if not self._remote_health_current(stop, generation):
                return
            runtime = self._remote_runtime_config()
            if not runtime["fixed_domain_mode"]:
                return
            token = self._read_remote_control_token() or self.remote_control_token
            published_url = f"{runtime['published_base']}?token={quote(token)}"
            local_url = str(self.remote_nats_runtime_status.get("websocket_url") or "")
            port = int(urlsplit(local_url).port or DEFAULT_REMOTE_NATS_WEBSOCKET_PORT)
            local_ok, detail = self._verify_remote_local_health(token, port)
            local_ok = local_ok and self._remote_local_listener_ready(self._cloudflared_origin_port())
            if not local_ok:
                restart_budget.allow(bindings.app.time.monotonic(), origin_healthy=False,
                    network_available=False, channels_healthy=False)
                failures = 0
                public_ok = False
                detail = detail or "cloudflared origin 未监听。"
            else:
                public_ok, detail = self._verify_remote_public_ws(published_url)
                if not self._remote_health_current(stop, generation):
                    return
                failures = 0 if public_ok else failures + 1
                if self._remote_tunnel_mode() != "external":
                    channels = self._owned_cloudflared_channel_count()
                    network_available = public_ok or self._remote_external_route_ready(published_url)
                    if restart_budget.allow(bindings.app.time.monotonic(), origin_healthy=local_ok,
                            network_available=network_available, channels_healthy=public_ok or channels is None or channels > 0):
                        with self._remote_health_lock:
                            if not self._remote_health_current(stop, generation):
                                return
                            last_restart = bindings.app.time.monotonic()
                        owned = getattr(self, "_managed_cloudflared_process", None)
                        if owned is not None or getattr(self, "_managed_cloudflared_start_failed", False):
                            self._stop_managed_cloudflared_process()
                            restarted = self._start_managed_cloudflared_process(self._cloudflared_origin_port())
                        elif bindings.app.os.environ.get("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE") == "service":
                            restarted = self._restart_cloudflared_service(stop_event=stop)
                        else:
                            restarted = False
                        if not self._remote_health_current(stop, generation):
                            return
                        if restarted:
                            public_ok, detail = self._verify_remote_public_ws(published_url)
                            if public_ok:
                                failures = 0
                        else:
                            detail = f"cloudflared 服务重启失败；{detail}"
            detail = str(detail or "").replace(token, "<redacted>") if token else str(detail or "")
            with self._remote_health_lock:
                if not self._remote_health_current(stop, generation):
                    return
                previous = dict(self.remote_control_runtime_status)
                status = dict(
                    local_listener_ready=local_ok, public_ws_ready=public_ok,
                    last_remote_error="" if public_ok else detail, published_url=published_url,
                )
                if all(previous.get(key) == value for key, value in status.items()):
                    continue
                self._set_remote_runtime_status(**status)

                def notify(ready=public_ok, error=detail, expected=status):
                    if self._remote_health_current(stop, generation) and self.remote_control_runtime_status == expected:
                        self.SetStatusText("远程 NATS 公网连接已恢复" if ready else f"远程 NATS 连接降级：{error}")

                self._call_after_if_alive(notify)

    def _remote_external_route_ready(self, published_url: str) -> bool:
        """A DNS/TCP route probe gates restart; it never asserts UDP health."""
        try:
            parsed = urlsplit(published_url)
            if parsed.scheme not in {"wss", "https", "ws", "http"} or not parsed.hostname:
                return False
            port = parsed.port if parsed.port is not None else (443 if parsed.scheme in {"wss", "https"} else 80)
            if port <= 0:
                return False
            with socket.create_connection((parsed.hostname, port), timeout=2):
                return True
        except (OSError, ValueError):
            return False

    def _owned_cloudflared_channel_count(self) -> int | None:
        process = getattr(self, "_managed_cloudflared_process", None)
        if process is None:
            if getattr(self, "_managed_cloudflared_start_failed", False):
                return 0
            if bindings.app.os.environ.get("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE") != "service":
                return None
            return None if self._query_cloudflared_service().get("running") else 0
        if process.poll() is not None:
            return 0
        log_reader = getattr(self, "_managed_cloudflared_log_handle", None)
        if hasattr(log_reader, "channel_count"):
            return log_reader.channel_count
        # A positive registration is never negated by a generic HTTP failure.
        # Missing channel evidence is unknown and cannot authorize restart.
        process_id = getattr(process, "pid", None)
        if getattr(self, "_managed_cloudflared_channel_pid", None) != process_id:
            self._managed_cloudflared_channel_pid = process_id
            self._managed_cloudflared_channels = {}
            self._managed_cloudflared_channel_offset = 0
        channels = getattr(self, "_managed_cloudflared_channels", {})
        offset = int(getattr(self, "_managed_cloudflared_channel_offset", 0))
        try:
            with self._managed_cloudflared_log_path().open("rb") as log:
                log.seek(0, bindings.app.os.SEEK_END)
                size = log.tell()
                if size < offset or size - offset > 1024 * 1024:
                    self._managed_cloudflared_channels = {}
                    self._managed_cloudflared_channel_offset = size
                    return None
                log.seek(offset)
                fresh = log.read().decode("utf-8", errors="replace")
                self._managed_cloudflared_channel_offset = size
        except OSError:
            return None
        for line in fresh.splitlines():
            index = re.search(r"connIndex=(\d+)", line)
            if index is None:
                continue
            if "Registered tunnel connection" in line:
                channels[index.group(1)] = True
            elif "Unregistered tunnel connection" in line or "Connection terminated" in line:
                channels[index.group(1)] = False
        self._managed_cloudflared_channels = channels
        return sum(channels.values()) if channels else None

    def _stop_cloudflared_origin_proxy(self) -> None:
        proxy = getattr(self, "_cloudflared_origin_proxy", None)
        self._cloudflared_origin_proxy = None
        self._cloudflared_origin_proxy_port = 0
        if proxy is None:
            return
        try:
            proxy.stop()
        except Exception:
            pass

    def _cloudflared_origin_port(self) -> int:
        active_port = int(getattr(self, "_cloudflared_origin_proxy_port", 0) or 0)
        if active_port > 0:
            return active_port
        return int(getattr(self, "remote_control_port", 0) or DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT)

    def _resolve_cloudflared_origin_port(self) -> int:
        preferred_port = int(getattr(self, "remote_control_port", 0) or DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT)
        existing_bridge_targets = self._cloudflared_origin_bridge_target_ports()
        candidates = [
            *existing_bridge_targets,
            preferred_port,
            DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT,
            *REMOTE_CLOUDFLARED_ORIGIN_PORT_FALLBACKS,
        ]
        seen = set()
        current_proxy = getattr(self, "_cloudflared_origin_proxy", None)
        current_proxy_port = int(getattr(current_proxy, "listen_port", 0) or 0)
        for port in candidates:
            if port in seen or port <= 0:
                continue
            seen.add(port)
            if current_proxy_port == port:
                return port
            if self._can_bind_loopback_tcp_port(port):
                return port
        return preferred_port

    def _start_cloudflared_origin_proxy(self) -> bool:
        if not self._start_file_service_if_configured():
            return False
        websocket_url = str((getattr(self, "remote_nats_runtime_status", {}) or {}).get("websocket_url") or "").strip()
        if not websocket_url:
            websocket_url = f"ws://127.0.0.1:{int(getattr(self, '_remote_nats_websocket_port', DEFAULT_REMOTE_NATS_WEBSOCKET_PORT))}/nats"
        service = getattr(self, "file_service", None)
        file_base_url = str(getattr(service, "local_base_url", "") or getattr(self, "file_http_base_url", "") or "").rstrip("/")
        proxy = getattr(self, "_cloudflared_origin_proxy", None)
        listen_port = self._resolve_cloudflared_origin_port()
        if proxy is not None and getattr(proxy, "listen_port", None) == listen_port:
            self._cloudflared_origin_proxy_port = listen_port
            return True
        self._stop_cloudflared_origin_proxy()
        proxy = bindings.app.CloudflaredOriginProxy(
            listen_host="127.0.0.1",
            listen_port=listen_port,
            nats_ws_url=websocket_url,
            file_base_url=file_base_url,
        )
        try:
            proxy.start()
        except Exception as exc:
            self._set_status_text_safe(f"cloudflared origin 代理启动失败：{exc}")
            return False
        self._cloudflared_origin_proxy = proxy
        self._cloudflared_origin_proxy_port = listen_port
        self._ensure_cloudflared_origin_bridge(listen_port)
        return True

    def _start_remote_servers(self, *, token: str, host: str, port: int) -> None:
        self._start_remote_nats_server_if_configured(token=token, host=host)

    def _build_remote_nats_url(self) -> str:
        token = self._read_remote_control_token()
        if not token:
            return ""
        configured_url = self._remote_runtime_config()["domain"]
        base = (configured_url if self._remote_tunnel_mode() == "external" else
                getattr(self, "remote_nats_runtime_status", {}).get("cloudflared_url") or configured_url)
        if not base:
            return ""
        return f"{base}?token={token}"

    def _on_copy_remote_nats_url(self, _event) -> None:
        url = self._build_remote_nats_url()
        if not url:
            wx.MessageBox("未配置远程控制令牌", "提示", wx.OK | wx.ICON_WARNING)
            return
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(url))
            finally:
                wx.TheClipboard.Close()
        self.SetStatusText("已复制远程控制地址")

    def _start_remote_nats_server_if_configured(self, *, token: str, host: str) -> None:
        if getattr(self, "_remote_nats_transport", None) is not None:
            return
        explicit_port_text = self._read_remote_control_setting(
            "REMOTE_CONTROL_NATS_PORT", "CLAUDECODE_REMOTE_CONTROL_NATS_PORT", default="",
        ).strip()
        explicit_port = None
        if explicit_port_text:
            try:
                explicit_port = int(explicit_port_text)
            except ValueError:
                raise ValueError("REMOTE_CONTROL_NATS_PORT must be an integer between 1 and 65535") from None
            if not 1 <= explicit_port <= 65535:
                raise ValueError("REMOTE_CONTROL_NATS_PORT must be an integer between 1 and 65535")
        preferred_websocket_port = self._resolve_remote_nats_websocket_port(
            DEFAULT_REMOTE_NATS_WEBSOCKET_PORT
        )
        if preferred_websocket_port == self._cloudflared_origin_port():
            preferred_websocket_port = self._resolve_remote_nats_websocket_port(
                REMOTE_NATS_WEBSOCKET_PORT_FALLBACKS[0]
            )
        websocket_port = preferred_websocket_port
        self._remote_nats_websocket_port = websocket_port
        preferred_ports = [explicit_port] if explicit_port is not None else [DEFAULT_REMOTE_NATS_PORT, *bindings.app.REMOTE_NATS_PORT_FALLBACKS]
        seen_ports = set()
        last_error = ""
        for attempt_idx, tcp_port in enumerate(preferred_ports):
            if tcp_port in seen_ports or tcp_port <= 0:
                continue
            seen_ports.add(tcp_port)
            config = NatsRuntimeConfig(
                app_data_dir=bindings.app.resolve_app_data_dir(),
                token=token,
                host=host,
                port=tcp_port,
                websocket_host="127.0.0.1",
                websocket_port=websocket_port,
                role_permissions=True,
                pair_id=self._read_pair_id(),
            )
            tcp_url = f"nats://127.0.0.1:{config.port}"
            websocket_url = f"ws://127.0.0.1:{config.websocket_port}/nats"
            process = None
            reused_existing_runtime = False
            try:
                process = bindings.app.NatsServerProcess(config)
                try:
                    process.start()
                except Exception as exc:
                    if explicit_port is not None:
                        raise
                    message = str(exc or "").lower()
                    port_in_use = "already in use" in message
                    port_unavailable = (
                        "access permissions" in message
                        or "forbidden by its access permissions" in message
                        or "unavailable for binding" in message
                    )
                    if not port_in_use and not port_unavailable:
                        raise
                    process = None
                    if attempt_idx > 0:
                        continue
                    if port_unavailable:
                        continue
                    reused_existing_runtime = True
                    websocket_port = self._probe_existing_remote_nats_websocket_port(token)
                    if websocket_port is None:
                        raise RuntimeError("Could not determine websocket port for existing NATS runtime")
                    self._remote_nats_websocket_port = websocket_port
                    websocket_url = f"ws://127.0.0.1:{websocket_port}/nats"
                transport = bindings.app.RemoteNatsTransport(
                    pair_id=self._read_pair_id(),
                    token=token,
                    desktop_password=config.desktop_password,
                    on_message=self._remote_api_message,
                    on_new_chat=self._remote_api_new_chat,
                    on_agy_configure=lambda payload: self._run_remote_ui_route(self._remote_api_agy_configure_ui, payload),
                    on_reply_request=lambda payload: self._run_remote_ui_route(self._remote_api_reply_request_ui, payload),
                    on_state=lambda payload: self._run_remote_ui_route(self._remote_api_state_ui, payload),
                    on_rename_chat=self._remote_api_rename_chat,
                    on_history_mutation=self._remote_api_history_mutation,
                    on_update_settings=lambda payload: self._run_remote_ui_route(self._remote_api_update_settings_ui, payload),
                    on_speed_options=lambda payload: self._run_remote_ui_route(self._remote_api_speed_options_ui, payload),
                    on_set_speed=lambda payload: self._run_remote_ui_route(self._remote_api_set_speed_ui, payload),
                    on_clear_context=lambda payload: self._run_remote_ui_route(self._remote_api_clear_context_ui, payload),
                    on_model_list=self._remote_api_model_list_ui,
                    on_common_commands_list=lambda: self._run_remote_ui_route(self._remote_api_common_commands_list_ui),
                    on_common_commands_create=lambda payload: self._run_remote_ui_route(self._remote_api_common_commands_create_ui, payload),
                    on_common_commands_update=lambda payload: self._run_remote_ui_route(self._remote_api_common_commands_update_ui, payload),
                    on_common_commands_delete=lambda payload: self._run_remote_ui_route(self._remote_api_common_commands_delete_ui, payload),
                    on_common_commands_pin=lambda payload: self._run_remote_ui_route(self._remote_api_common_commands_pin_ui, payload),
                    on_common_commands_unpin=lambda payload: self._run_remote_ui_route(self._remote_api_common_commands_unpin_ui, payload),
                    on_common_commands_move_up=lambda payload: self._run_remote_ui_route(self._remote_api_common_commands_move_up_ui, payload),
                    on_common_commands_move_down=lambda payload: self._run_remote_ui_route(self._remote_api_common_commands_move_down_ui, payload),
                    on_history_list=lambda: self._run_remote_ui_route(self._remote_api_history_list_ui),
                    on_history_page=self._remote_api_history_collection_worker,
                    on_history_read=lambda payload: self._run_remote_ui_route(self._remote_api_history_read_ui, payload),
                    on_chat_read_changed=lambda payload: wx.CallAfter(self._on_chat_read_changed, payload),
                    on_execution_page=lambda payload: self._remote_api_execution_page_ui(payload, secret=token),
                    on_chat_information=lambda payload: self._run_remote_ui_route(self._remote_api_chat_information_ui, payload),
                    on_notes_changes=self._remote_api_notes_changes,
                    on_notes_bulk_docs=self._remote_api_notes_bulk_docs,
                    on_file_command=None,
                    chat_attachment_support=not getattr(
                        self.file_service, "use_copyparty_process", False
                    ),
                    durable_store=getattr(self, "chat_store", None),
                )
                transport.on_file_command = lambda payload, source_transport=transport: self._remote_api_file_command_worker(
                    payload, source_transport=source_transport)
                transport.start_threaded(tcp_url)
            except Exception as exc:
                last_error = str(exc)
                if process is not None:
                    try:
                        process.stop()
                    except Exception:
                        pass
                message = str(exc or "").lower()
                if explicit_port is None and (
                    reused_existing_runtime
                    or "already in use" in message
                    or "access permissions" in message
                    or "forbidden by its access permissions" in message
                    or "unavailable for binding" in message
                ):
                    websocket_port = preferred_websocket_port
                    self._remote_nats_websocket_port = websocket_port
                    continue
                self._set_remote_nats_runtime_status(
                    enabled=False,
                    tcp_url=tcp_url,
                    websocket_url=websocket_url,
                    cloudflared_url=self._remote_runtime_config()["domain"],
                    last_error=last_error,
                )
                return
            self._remote_nats_process = process
            self._remote_nats_transport = transport
            self._ensure_cloudflared_origin_bridge()
            self._set_remote_nats_runtime_status(
                enabled=True,
                tcp_url=tcp_url,
                websocket_url=websocket_url,
                cloudflared_url=self._remote_runtime_config()["domain"],
                last_error="",
            )
            if reused_existing_runtime:
                self.SetStatusText("远程 NATS 已复用现有本地运行时")
            return
        self._set_remote_nats_runtime_status(
            enabled=False,
            tcp_url=f"nats://127.0.0.1:{DEFAULT_REMOTE_NATS_PORT}",
            websocket_url=f"ws://127.0.0.1:{self._remote_nats_websocket_port}/nats",
            cloudflared_url=self._remote_runtime_config()["domain"],
            last_error=last_error or "远程 NATS 运行时未成功启动。",
        )
        return

    def _resolve_remote_nats_websocket_port(self, preferred_port: int) -> int:
        candidates = [preferred_port, *REMOTE_NATS_WEBSOCKET_PORT_FALLBACKS]
        seen = set()
        for port in candidates:
            if port in seen or port <= 0:
                continue
            seen.add(port)
            if self._can_bind_loopback_tcp_port(port):
                return port
        return preferred_port

    def _can_bind_loopback_tcp_port(self, port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
                return False
        except Exception:
            pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind(("127.0.0.1", int(port)))
                return True
        except Exception:
            return False

    def _probe_existing_remote_nats_websocket_port(self, token: str) -> int | None:
        candidates = []
        runtime_url = str((getattr(self, "remote_nats_runtime_status", {}) or {}).get("websocket_url") or "").strip()
        if runtime_url:
            try:
                parsed = urlsplit(runtime_url)
                if parsed.port:
                    candidates.append(int(parsed.port))
            except Exception:
                pass
        candidates.extend(
            [
                getattr(self, "_remote_nats_websocket_port", DEFAULT_REMOTE_NATS_WEBSOCKET_PORT),
                DEFAULT_REMOTE_NATS_WEBSOCKET_PORT,
                *REMOTE_NATS_WEBSOCKET_PORT_FALLBACKS,
            ]
        )
        seen = set()
        for port in candidates:
            try:
                port = int(port)
            except Exception:
                continue
            if port in seen or port <= 0:
                continue
            seen.add(port)
            if self._probe_remote_nats_websocket_port(port, token):
                return port
        return None

    def _probe_remote_nats_websocket_port(self, port: int, token: str) -> bool:
        if port <= 0 or not self._remote_local_listener_ready(port):
            return False
        if not str(token or ""):
            return False
        url = f"ws://127.0.0.1:{int(port)}/nats?token={quote(str(token))}"
        ok, _detail = self._verify_remote_public_ws(url)
        return ok

    def _cloudflared_origin_bridge_target_ports(self) -> list[int]:
        listen_port = int(getattr(self, "remote_control_port", 0) or DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT)
        try:
            result = self._run_remote_check_command(
                ["netsh", "interface", "portproxy", "show", "all"],
                timeout=10.0,
            )
        except Exception:
            return []
        if getattr(result, "returncode", 1) != 0:
            return []
        ports: list[int] = []
        for line in str(getattr(result, "stdout", "") or "").splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                row_listen_port = int(parts[1])
                row_connect_port = int(parts[3])
            except Exception:
                continue
            if row_listen_port == listen_port and row_connect_port > 0:
                ports.append(row_connect_port)
        return ports

    def _ensure_cloudflared_origin_bridge(self, target_port: int | None = None) -> None:
        listen_port = int(getattr(self, "remote_control_port", 0) or DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT)
        connect_port = int(target_port or self._cloudflared_origin_port())
        for delete_args in (
            [
                "netsh",
                "interface",
                "portproxy",
                "delete",
                "v4tov4",
                f"listenport={listen_port}",
                "listenaddress=127.0.0.1",
            ],
            [
                "netsh",
                "interface",
                "portproxy",
                "delete",
                "v6tov4",
                f"listenport={listen_port}",
                "listenaddress=::1",
            ],
        ):
            self._run_remote_check_command(delete_args, timeout=10.0)
        if connect_port <= 0:
            return
        for add_args in (
            [
                "netsh",
                "interface",
                "portproxy",
                "add",
                "v4tov4",
                f"listenport={listen_port}",
                "listenaddress=127.0.0.1",
                f"connectport={connect_port}",
                "connectaddress=127.0.0.1",
            ],
            [
                "netsh",
                "interface",
                "portproxy",
                "add",
                "v6tov4",
                f"listenport={listen_port}",
                "listenaddress=::1",
                f"connectport={connect_port}",
                "connectaddress=127.0.0.1",
            ],
        ):
            # The IPv4 origin already owns this port; IPv6 still needs its alias.
            if connect_port == listen_port and "v4tov4" in add_args:
                continue
            self._run_remote_check_command(add_args, timeout=10.0)

    def _run_remote_check_command(self, args: list[str], timeout: float = 10.0) -> bindings.app.subprocess.CompletedProcess | None:
        kwargs = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": timeout,
        }
        if bindings.app.os.name == "nt":
            kwargs["creationflags"] = getattr(bindings.app.subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return bindings.app.subprocess.run(args, **kwargs)
        except Exception:
            return None

    def _query_cloudflared_service(self) -> dict:
        result = self._run_remote_check_command(["sc.exe", "query", "cloudflared"])
        config_result = self._run_remote_check_command(["sc.exe", "qc", "cloudflared"])
        text = ""
        if result is not None:
            text = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
        config_text = ""
        if config_result is not None:
            config_text = f"{config_result.stdout or ''}\n{config_result.stderr or ''}".strip()
        normalized = text.lower()
        binary_path = ""
        for line in config_text.splitlines():
            match = re.match(r"\s*BINARY_PATH_NAME\s*:\s*(.+)", line, flags=re.IGNORECASE)
            if match:
                binary_path = match.group(1).strip()
                break
        exists = bool(
            (result and (result.returncode == 0 or "state" in normalized))
            or (config_result and config_result.returncode == 0 and bool(binary_path))
        )
        running = "running" in normalized
        detail = "\n".join(part for part in (text, config_text) if part)
        return {"exists": exists, "running": running, "detail": detail, "binary_path": binary_path}

    def _query_cloudflared_process_command_lines(self) -> list[str]:
        if bindings.app.os.name != "nt":
            return []
        result = self._run_remote_check_command(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | Select-Object -ExpandProperty CommandLine",
            ],
            timeout=10.0,
        )
        if result is None or result.returncode != 0:
            return []
        return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]

    def _query_process_command_line(self, process_id: int) -> str:
        if bindings.app.os.name != "nt" or int(process_id or 0) <= 0:
            return ""
        result = self._run_remote_check_command(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId={int(process_id)}\").CommandLine",
            ],
            timeout=10.0,
        )
        if result is None or result.returncode != 0:
            return ""
        return str(result.stdout or "").strip()

    def _cloudflared_process_targets_port(self, websocket_port: int) -> bool:
        desired_url = f"http://127.0.0.1:{int(websocket_port)}"
        for command_line in self._query_cloudflared_process_command_lines():
            rewritten, changed = self._replace_cloudflared_service_url(command_line, websocket_port)
            if rewritten is not None and not changed:
                return True
            if desired_url in command_line:
                return True
        return False

    @staticmethod
    def _replace_cloudflared_service_url(bin_path: str, websocket_port: int) -> tuple[str, bool] | tuple[None, bool]:
        match = re.search(r"--url(?P<separator>\s+|=)(?:\"([^\"]*)\"|'([^']*)'|([^\s]+))", bin_path, flags=re.IGNORECASE)
        if not match:
            return None, False
        current_url = match.group(2) or match.group(3) or match.group(4)
        desired_url = f"http://127.0.0.1:{int(websocket_port)}"
        same_target = False
        try:
            parsed = urlsplit(current_url)
            same_target = parsed.hostname in {"127.0.0.1", "localhost"} and parsed.port == int(websocket_port)
        except Exception:
            try:
                same_target = current_url.strip() == desired_url
            except Exception:
                same_target = False
        if same_target:
            return bin_path, False
        quote = "\"" if match.group(2) is not None else ("'" if match.group(3) is not None else "")
        replacement = f"--url{match.group('separator')}{quote}{desired_url}{quote}"
        return f"{bin_path[:match.start()]}{replacement}{bin_path[match.end():]}", True

    def _set_cloudflared_service_bin_path(self, bin_path: str) -> bool:
        if not bin_path:
            return False
        result = self._run_remote_check_command(["sc.exe", "config", "cloudflared", "binPath=", bin_path], timeout=15.0)
        return bool(result and result.returncode == 0)

    def _ensure_cloudflared_service_url(self, websocket_port: int) -> tuple[bool, bool]:
        state = self._query_cloudflared_service()
        if not state.get("exists"):
            return self._cloudflared_process_targets_port(websocket_port), False
        current_bin_path = str(state.get("binary_path") or "").strip()
        if not current_bin_path:
            return self._cloudflared_process_targets_port(websocket_port), False
        rewritten, changed = self._replace_cloudflared_service_url(current_bin_path, websocket_port)
        if rewritten is None:
            return self._cloudflared_process_targets_port(websocket_port), False
        requested_protocol = bindings.app.os.environ.get("REMOTE_CONTROL_CLOUDFLARED_PROTOCOL")
        if requested_protocol is not None:
            from tunnel_policy import connector_argv
            rewritten = bindings.app.subprocess.list2cmdline(connector_argv(rewritten, origin_port=websocket_port,
                protocol=requested_protocol.strip().lower()))
            changed = rewritten != current_bin_path
        if not changed:
            return True, False
        if not self._set_cloudflared_service_bin_path(rewritten):
            return False, True
        updated_state = self._query_cloudflared_service()
        updated_bin_path = str(updated_state.get("binary_path") or "").strip()
        verified, still_stale = self._replace_cloudflared_service_url(updated_bin_path, websocket_port)
        return bool(verified and not still_stale), True

    def _cloudflared_running_process_targets_origin(self, origin_port: int) -> bool:
        command_lines = self._query_cloudflared_process_command_lines()
        if not command_lines:
            return True
        return self._cloudflared_process_targets_port(origin_port)

    def _start_cloudflared_service(self, *, stop_event=None) -> bool:
        def cancelled():
            return stop_event is not None and (stop_event.is_set() or getattr(self, "_closing", False))

        if cancelled():
            return False
        state = self._query_cloudflared_service()
        if cancelled():
            return False
        if not state["exists"]:
            return False
        if state["running"]:
            return True
        deadline = bindings.app.time.time() + bindings.app.REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS
        while bindings.app.time.time() < deadline:
            if cancelled():
                return False
            result = self._run_remote_check_command(["sc.exe", "start", "cloudflared"], timeout=15.0)
            detail = ""
            if result is not None:
                detail = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
            if result is not None and (result.returncode == 0 or "already running" in detail):
                break
            bindings.app.time.sleep(0.2)
        deadline = bindings.app.time.time() + bindings.app.REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS
        while bindings.app.time.time() < deadline:
            if cancelled():
                return False
            if self._query_cloudflared_service()["running"]:
                return not cancelled()
            bindings.app.time.sleep(0.2)
        return not cancelled() and self._query_cloudflared_service()["running"] and not cancelled()

    def _kill_cloudflared_processes(self) -> None:
        # Only the child handle created by this window conveys ownership. A
        # process name or matching origin never authorizes killing another tunnel.
        self._stop_managed_cloudflared_process()

    def _replace_stale_cloudflared_with_managed(self, origin_port: int) -> bool:
        self._kill_cloudflared_processes()
        return self._start_managed_cloudflared_process(origin_port)

    def _restart_cloudflared_service(self, *, stop_event=None) -> bool:
        def cancelled():
            return stop_event is not None and (stop_event.is_set() or getattr(self, "_closing", False))

        if cancelled():
            return False
        state = self._query_cloudflared_service()
        if not state["exists"]:
            return False
        if cancelled():
            return False
        self._run_remote_check_command(["sc.exe", "stop", "cloudflared"], timeout=15.0)
        deadline = bindings.app.time.time() + bindings.app.REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS
        while bindings.app.time.time() < deadline:
            if cancelled():
                return False
            if not self._query_cloudflared_service()["running"]:
                break
            bindings.app.time.sleep(0.2)
        if cancelled() or self._query_cloudflared_service()["running"]:
            return False
        if stop_event is not None:
            return self._start_cloudflared_service(stop_event=stop_event)
        return self._start_cloudflared_service()

    def _managed_cloudflared_command_line(self, websocket_port: int) -> str:
        state = self._query_cloudflared_service()
        command_line = str(bindings.app.os.environ.get("REMOTE_CONTROL_CLOUDFLARED_COMMAND") or state.get("binary_path") or "").strip()
        if not command_line:
            for candidate in self._query_cloudflared_process_command_lines():
                matched, changed = self._replace_cloudflared_service_url(candidate, websocket_port)
                if matched is not None and not changed and " tunnel run " in f" {candidate} ".lower():
                    command_line = candidate
                    break
        if not command_line:
            return ""
        from tunnel_policy import connector_argv
        argv = connector_argv(command_line, origin_port=websocket_port,
            protocol=bindings.app.os.environ.get("REMOTE_CONTROL_CLOUDFLARED_PROTOCOL", "auto").strip().lower())
        return bindings.app.subprocess.list2cmdline(argv)

    def _start_managed_cloudflared_process(self, websocket_port: int) -> bool:
        existing = getattr(self, "_managed_cloudflared_process", None)
        if existing is not None and existing.poll() is None:
            return True
        from tunnel_policy import ConnectorLease
        try:
            command_line = self._managed_cloudflared_command_line(websocket_port)
            if not command_line:
                return False
            lease = ConnectorLease(bindings.app.resolve_app_data_dir() / "cloudflared" / "owners", self._read_pair_id(), command_line)
        except (ValueError, OSError):
            self._set_status_text_safe("cloudflared connector configuration or credential unavailable")
            return False
        try:
            lease.acquire()
        except OSError:
            self._set_status_text_safe("cloudflared connector owner already active")
            return False
        self._managed_cloudflared_lease = lease
        try:
            log_path = self._managed_cloudflared_log_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._close_managed_cloudflared_log()
            from owned_logs import RotatingLog
            self._managed_cloudflared_log_handle = RotatingLog(log_path, track_channels=True)
        except OSError:
            lease.close()
            self._managed_cloudflared_lease = None
            return False
        popen_kwargs = {
            "stdin": bindings.app.subprocess.DEVNULL,
            "stdout": bindings.app.subprocess.PIPE,
            "stderr": bindings.app.subprocess.STDOUT,
        }
        if bindings.app.os.name == "nt":
            startupinfo = bindings.app.subprocess.STARTUPINFO()
            startupinfo.dwFlags |= bindings.app.subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = bindings.app.subprocess.SW_HIDE
            popen_kwargs["startupinfo"] = startupinfo
            popen_kwargs["creationflags"] = getattr(bindings.app.subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._managed_cloudflared_start_failed = True
            self._managed_cloudflared_process = bindings.app.subprocess.Popen(command_line, **popen_kwargs)
            self._managed_cloudflared_log_handle.attach(self._managed_cloudflared_process.stdout)
            deadline = bindings.app.time.monotonic() + 0.5
            while bindings.app.time.monotonic() < deadline:
                if self._managed_cloudflared_process.poll() is not None:
                    self._managed_cloudflared_log_handle.wait()
                    detail = self._managed_cloudflared_diagnostic()
                    self._set_status_text_safe(f"cloudflared process exited during startup; {detail}")
                    self._managed_cloudflared_process = None
                    lease.close()
                    self._close_managed_cloudflared_log()
                    return False
                bindings.app.time.sleep(0.05)
            self._managed_cloudflared_start_failed = False
            return True
        except Exception as exc:
            detail = self._managed_cloudflared_diagnostic()
            self._stop_managed_cloudflared_process()
            self._set_status_text_safe(f"cloudflared process failed to start: {exc}; {detail}")
            return False

    def _verify_managed_cloudflared_process(self, origin_port: int) -> tuple[bool, str]:
        """Verify the tunnel owned by this window, not an unrelated service."""
        process = getattr(self, "_managed_cloudflared_process", None)
        if process is None or process.poll() is not None:
            return False, f"cloudflared 桌面托管进程未运行；{self._managed_cloudflared_diagnostic()}"
        desired = f"http://127.0.0.1:{int(origin_port)}"
        command_line = self._query_process_command_line(int(getattr(process, "pid", 0) or 0))
        if command_line and desired not in command_line:
            return False, f"cloudflared 桌面托管进程未指向 {desired}；{self._managed_cloudflared_diagnostic()}"
        if not self._remote_local_listener_ready(origin_port):
            return False, f"cloudflared origin {desired} 未监听"
        return True, ""

    def _ensure_cloudflared_tunnel(self, origin_port: int) -> tuple[bool, str, str]:
        """Start exactly one connector for the fixed domain.

        A named Cloudflare tunnel may load-balance across all connected
        connectors.  Starting a managed connector while the Windows service
        is still connected to a stale origin therefore leaves an intermittent
        502 path.  If the service exists, make it authoritative; use a child
        process only when no service is installed.
        """
        if self._remote_tunnel_mode() == "external":
            ready = self._remote_local_listener_ready(origin_port)
            return ready, "external", "" if ready else "external origin 未监听。"
        state = self._query_cloudflared_service()
        if state.get("exists"):
            if bindings.app.os.environ.get("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE") != "service":
                return False, "service", "cloudflared service ownership requires explicit REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE=service deployment configuration"
            configured, reconfigured = self._ensure_cloudflared_service_url(origin_port)
            if not configured:
                return (
                    False,
                    "service",
                    "cloudflared 服务仍连接旧 origin，且无法更新；为避免同一 tunnel 同时连接两个不同 origin，未启动桌面托管进程。",
                )
            started = self._restart_cloudflared_service() if reconfigured else self._start_cloudflared_service()
            if not started:
                return False, "service", "cloudflared 服务未能以当前 origin 启动。"
            return True, "service", ""

        if not self._start_managed_cloudflared_process(origin_port):
            return False, "managed", "cloudflared 桌面托管进程启动失败。"
        managed_ok, managed_detail = self._verify_managed_cloudflared_process(origin_port)
        return managed_ok, "managed", managed_detail

    def _managed_cloudflared_log_path(self) -> Path:
        return bindings.app.resolve_app_data_dir() / "cloudflared" / "cloudflared.log"

    def _managed_cloudflared_log_tail(self, max_bytes: int = 4096) -> str:
        handle = getattr(self, "_managed_cloudflared_log_handle", None)
        failure_note = getattr(handle, "failure_note", "")
        if handle is not None:
            try:
                handle.flush()
            except Exception:
                pass
        try:
            path = self._managed_cloudflared_log_path()
            with path.open("rb") as stream:
                stream.seek(0, bindings.app.os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - max_bytes), bindings.app.os.SEEK_SET)
                text = stream.read().decode("utf-8", errors="replace")
        except OSError:
            return ("<unavailable> " + failure_note).strip()
        token = str(self._read_remote_control_token() or "")
        if token:
            text = text.replace(token, "<redacted>")
        text = re.sub(r"(?i)(--token\s+|[?&]token=|token\s*[:=]\s*)\S+", r"\1<redacted>", text)
        tail = " | ".join(text.splitlines()[-20:]).strip() or "<empty>"
        return (tail + " | " + failure_note) if failure_note else tail

    def _managed_cloudflared_diagnostic(self) -> str:
        process = getattr(self, "_managed_cloudflared_process", None)
        exit_code = process.poll() if process is not None else None
        return (
            f"owner=mc:{getattr(self, 'remote_nats_pair_id', 'default')}; pid={getattr(process, 'pid', None)}; "
            f"protocol={bindings.app.os.environ.get('REMOTE_CONTROL_CLOUDFLARED_PROTOCOL', 'auto')}; "
            f"exit_code={exit_code}; log: {self._managed_cloudflared_log_path()}; "
            f"tail: {self._managed_cloudflared_log_tail()}"
        )

    def _close_managed_cloudflared_log(self) -> None:
        handle = getattr(self, "_managed_cloudflared_log_handle", None)
        self._managed_cloudflared_log_handle = None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass

    def _stop_managed_cloudflared_process(self) -> None:
        lease = getattr(self, "_managed_cloudflared_lease", None)
        self._managed_cloudflared_lease = None
        process = getattr(self, "_managed_cloudflared_process", None)
        self._managed_cloudflared_process = None
        if process is None or process.poll() is not None:
            if lease is not None:
                lease.close()
            self._close_managed_cloudflared_log()
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:
                pass
        finally:
            if lease is not None:
                lease.close()
            self._close_managed_cloudflared_log()

    def _remote_runtime_probe_host(self) -> str:
        host = str(self.remote_control_host or "127.0.0.1").strip() or "127.0.0.1"
        return "127.0.0.1" if host in {"0.0.0.0", "::", "::0"} else host

    def _remote_local_listener_ready(self, port: int) -> bool:
        try:
            with socket.create_connection((self._remote_runtime_probe_host(), int(port)), timeout=2.0):
                return True
        except Exception:
            return False

    def _verify_remote_local_health(self, token: str, port: int) -> tuple[bool, str]:
        runtime = getattr(self, "remote_nats_runtime_status", {}) or {}
        websocket_url = str(runtime.get("websocket_url") or "").strip()
        if not websocket_url:
            return False, "本地 NATS websocket 地址未配置。"
        parsed = urlsplit(websocket_url)
        probe_host = parsed.hostname or self._remote_runtime_probe_host()
        probe_port = int(parsed.port or DEFAULT_REMOTE_NATS_WEBSOCKET_PORT)
        probe_url = urlunsplit(
            (parsed.scheme or "ws", f"{probe_host}:{probe_port}", parsed.path or "/nats", f"token={quote(token)}", "")
        )
        return self._verify_remote_public_ws(probe_url)

    async def _verify_remote_public_ws_async(self, url: str, *, timeout_seconds=None) -> tuple[bool, str]:
        timeout_seconds = bindings.app.REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        timeout = ClientTimeout(total=timeout_seconds)
        parsed_url = urlsplit(str(url or ""))
        path = (parsed_url.path or "").strip().lower()
        nats_token = str((parse_qs(parsed_url.query).get("token") or [""])[0] or "")
        try:
            async with bindings.app.ClientSession(timeout=timeout) as session:
                async with session.ws_connect(url, heartbeat=timeout_seconds) as ws:
                    if path.endswith("/nats"):
                        message = await ws.receive(timeout=timeout_seconds)
                        info_text = ""
                        if message.type == WSMsgType.TEXT:
                            info_text = str(message.data or "")
                        elif message.type == WSMsgType.BINARY:
                            try:
                                info_text = bytes(message.data or b"").decode("utf-8", errors="replace")
                            except Exception:
                                info_text = ""
                        if not info_text.startswith("INFO "):
                            return False, f"公网 NATS 隧道响应异常：{message.type} {message.data!r}"
                        if not nats_token:
                            return False, "公网 NATS 隧道认证令牌缺失。"
                        await ws.send_str(
                            f"CONNECT {json.dumps({'user': 'mobile', 'pass': nats_token, 'verbose': False}, separators=(',', ':'))}\r\nPING\r\n"
                        )
                        while True:
                            message = await ws.receive(timeout=timeout_seconds)
                            if message.type == WSMsgType.TEXT:
                                response = str(message.data or "")
                            elif message.type == WSMsgType.BINARY:
                                response = bytes(message.data or b"").decode("utf-8", errors="replace")
                            else:
                                return False, f"公网 NATS 隧道认证连接关闭：{message.type}"
                            for protocol_line in response.splitlines():
                                protocol_line = protocol_line.strip()
                                if protocol_line == "PONG":
                                    return True, ""
                                if protocol_line == "PING":
                                    await ws.send_str("PONG\r\n")
                                elif protocol_line.startswith("-ERR"):
                                    return False, f"公网 NATS 隧道认证失败：{protocol_line}"
                    message = await ws.receive(timeout=timeout_seconds)
        except Exception as exc:
            return False, f"公网隧道握手失败：{exc}"
        if message.type != WSMsgType.TEXT:
            return False, f"公网隧道返回了非文本消息：{message.type}"
        try:
            payload = json.loads(message.data)
        except Exception as exc:
            return False, f"公网隧道返回了无效响应：{exc}"
        ok = payload.get("type") == "connected" and bool(payload.get("ok")) and bool((payload.get("body") or {}).get("accepted"))
        return ok, ("" if ok else f"公网隧道响应异常：{payload}")

    def _verify_remote_public_ws(self, url: str, *, timeout_seconds=None) -> tuple[bool, str]:
        async def verify():
            if timeout_seconds is None:
                return await self._verify_remote_public_ws_async(url)
            return await asyncio.wait_for(self._verify_remote_public_ws_async(url, timeout_seconds=timeout_seconds), timeout_seconds)
        try:
            return asyncio.run(verify())
        except TimeoutError:
            return False, "public_route_timeout"

    def _verify_remote_public_ws_with_retries(self, url: str) -> tuple[bool, str]:
        last_detail = ""
        attempts = max(1, int(bindings.app.REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS * 2))
        for attempt_idx in range(attempts):
            ok, detail = self._verify_remote_public_ws(url)
            if ok:
                return True, ""
            last_detail = detail
            if attempt_idx + 1 < attempts:
                bindings.app.time.sleep(0.5)
        return False, last_detail

    def _ensure_remote_nats_startup_connectivity(self, *, token: str, published_url: str) -> None:
        runtime = self._remote_runtime_config()
        if not runtime["fixed_domain_mode"] or getattr(self, "_remote_nats_transport", None) is None:
            return
        self._set_remote_runtime_status(
            local_listener_ready=False,
            public_ws_ready=False,
            last_remote_error="",
            published_url=published_url,
        )
        websocket_url = str((getattr(self, "remote_nats_runtime_status", {}) or {}).get("websocket_url") or "").strip()
        parsed = urlsplit(websocket_url)
        port = int(parsed.port or DEFAULT_REMOTE_NATS_WEBSOCKET_PORT)
        if port <= 0:
            raise RuntimeError(self._format_remote_startup_error("远程控制本地监听端口无效。"))
        if not self._remote_local_listener_ready(port):
            raise RuntimeError(self._format_remote_startup_error(f"远程控制端口 {port} 未正常监听。"))
        self._set_remote_runtime_status(local_listener_ready=True, published_url=published_url)
        local_ok, local_detail = self._verify_remote_local_health(token, port)
        if not local_ok:
            raise RuntimeError(self._format_remote_startup_error(local_detail or "远程控制本地健康检查失败。"))
        if not self._start_cloudflared_origin_proxy():
            raise RuntimeError(self._format_remote_startup_error("cloudflared origin 代理启动失败。"))
        origin_port = self._cloudflared_origin_port()
        tunnel_ok, tunnel_mode, tunnel_detail = self._ensure_cloudflared_tunnel(origin_port)
        if not tunnel_ok:
            raise RuntimeError(self._format_remote_startup_error(tunnel_detail))
        public_ok, public_detail = self._verify_remote_public_ws_with_retries(published_url)
        if public_ok:
            self._set_remote_runtime_status(
                local_listener_ready=True,
                public_ws_ready=True,
                last_remote_error="",
                published_url=published_url,
            )
            connector = ("external 公网入口" if tunnel_mode == "external" else
                         "cloudflared 桌面托管进程" if tunnel_mode == "managed" else "cloudflared 系统服务")
            self._set_status_text_safe(
                f"远程 NATS 已启动：监听 {self.remote_control_runtime_bind}；发布 {published_url}；{connector}已验证"
            )
            return
        detail = public_detail
        if tunnel_mode == "managed":
            detail = f"{detail}; {self._managed_cloudflared_diagnostic()}"
        raise RuntimeError(self._format_remote_startup_error(detail))

    def _start_remote_nats_runtime_if_configured(self, *, ensure_connectivity: bool = False) -> None:
        token = self._read_remote_control_token() or self.remote_control_token
        if not token or getattr(self, "_remote_nats_transport", None) is not None:
            return
        runtime = self._remote_runtime_config()
        host = runtime["host"]
        port = runtime["port"]
        published_url = f"{runtime['published_base']}?token={token}"
        self._set_remote_runtime_status(
            local_listener_ready=False,
            public_ws_ready=False,
            last_remote_error="",
            published_url=published_url,
        )
        attempts = 2 if (ensure_connectivity and runtime["fixed_domain_mode"]) else 1
        last_error = ""
        for attempt_idx in range(attempts):
            self._stop_remote_servers()
            self._start_remote_servers(token=token, host=host, port=port)
            runtime_status = dict(getattr(self, "remote_nats_runtime_status", {}) or {})
            if getattr(self, "_remote_nats_transport", None) is None or not runtime_status.get("enabled"):
                last_error = str(runtime_status.get("last_error") or "").strip()
                if not last_error:
                    last_error = "远程 NATS 运行时未成功启动。"
                raise RuntimeError(last_error)
            self.remote_control_runtime_mode = "fixed_domain" if runtime["fixed_domain_mode"] else "local"
            self.remote_control_runtime_bind = (
                getattr(self, "remote_nats_runtime_status", {}).get("websocket_url")
                or f"ws://127.0.0.1:{DEFAULT_REMOTE_NATS_WEBSOCKET_PORT}/nats"
            )
            self._set_remote_runtime_status(
                local_listener_ready=True,
                public_ws_ready=not runtime["fixed_domain_mode"],
                last_remote_error="",
                published_url=published_url,
            )
            if not ensure_connectivity:
                if runtime["fixed_domain_mode"]:
                    self._set_status_text_safe(
                        f"远程 NATS 本地监听已启动：监听 {self.remote_control_runtime_bind}；等待公网验证 {published_url}"
                    )
                else:
                    self._set_status_text_safe(
                        f"远程 NATS 已启动：监听 {self.remote_control_runtime_bind}；发布 {published_url}"
                    )
                return
            try:
                if runtime["fixed_domain_mode"]:
                    self._ensure_remote_nats_startup_connectivity(token=token, published_url=published_url)
                    if self.remote_control_runtime_status.get("public_ws_ready"):
                        self._set_remote_runtime_status(
                            local_listener_ready=True,
                            public_ws_ready=True,
                            last_remote_error="",
                            published_url=published_url,
                        )
                else:
                    self._set_status_text_safe(
                        f"远程 NATS 已启动：监听 {self.remote_control_runtime_bind}；发布 {published_url}"
                    )
                if not runtime["fixed_domain_mode"]:
                    self._set_remote_runtime_status(
                        local_listener_ready=True,
                        public_ws_ready=True,
                        last_remote_error="",
                        published_url=published_url,
                    )
                return
            except Exception as exc:
                last_error = str(exc)
                self._set_remote_runtime_status(
                    local_listener_ready=True,
                    public_ws_ready=False,
                    last_remote_error=last_error,
                    published_url=published_url,
                )
                if attempt_idx + 1 >= attempts:
                    self._set_status_text_safe(f"远程 NATS 启动失败：{last_error}")
                    raise

    def _start_claudecode_remote_nats_runtime_if_configured(self) -> None:
        return
