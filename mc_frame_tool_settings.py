"""Keyboard-accessible settings for the next desktop question."""

import threading

import wx

import mc_frame_bindings as bindings
from agy_acp import AGY_MODEL_ID
from codex_client import (
    DEFAULT_CODEX_MODEL,
    CodexAppServerClient,
    codex_cli_config_for_model,
    is_codex_model,
)
from kimi_server_client import KimiServerClient, is_kimi_model, kimi_model_to_server_alias
from mc_frame_settings import VISIBLE_MODEL_IDS


TOOLS = (("codex", "Codex"), ("agy", "AGY"), ("kimi", "Kimi Code"), ("other", "其他"))
EFFORT_NAMES = {"none": "无", "minimal": "最低", "low": "低", "medium": "中",
                "high": "高", "xhigh": "极高", "max": "最高", "ultra": "超高"}
MODE_NAMES = {"default": "默认协作模式", "plan": "计划模式"}
KIMI_MODES = (("default", "关闭计划模式"), ("plan", "计划模式"))
KIMI_PERMISSIONS = (("auto", "自动审批"), ("manual", "逐次询问"), ("yolo", "免审批"))
KIMI_EFFORT_NAMES = {**EFFORT_NAMES, "off": "关闭", "on": "开启"}
UNAVAILABLE = "此工具不支持"


def tool_for_route(model):
    if is_codex_model(model):
        return "codex"
    if model == AGY_MODEL_ID:
        return "agy"
    if is_kimi_model(model):
        return "kimi"
    return "other"


class ChatModelCombo(wx.ComboBox):
    """Accept the application's legacy route-display writes on chat switches."""

    def __init__(self, parent, frame):
        super().__init__(parent, style=wx.CB_READONLY, size=(235, -1), name="模型")
        self.frame = frame

    def SetValue(self, value):
        route = bindings.app.normalize_model_id(value, default="")
        if route:
            self.frame._select_tool_route(route)
        else:
            super().SetValue(value)

    def SetStringSelection(self, value, *args):
        if self.FindString(value) == wx.NOT_FOUND:
            route = bindings.app.normalize_model_id(value, default="")
            if route in VISIBLE_MODEL_IDS:
                self.frame._select_tool_route(route)
                return True
        return super().SetStringSelection(value, *args)


class ChatToolSettingsMixin:
    def _build_tool_settings_controls(self, panel, row):
        self._tool_mode = ""
        self._codex_settings_explicit = False
        self._tool_mode_menu_ids = []
        self._tool_route = DEFAULT_CODEX_MODEL
        self._codex_settings_catalog = {}
        self._codex_settings_loading = False
        self._kimi_settings_catalog = {}
        self._kimi_settings_loading = False
        self._kimi_permission = "auto"
        self._kimi_permission_items = {}
        self.tool_combo = wx.ComboBox(panel, style=wx.CB_READONLY, size=(100, -1), name="工具")
        self.model_combo = ChatModelCombo(panel, self)
        self.reasoning_combo = wx.ComboBox(panel, style=wx.CB_READONLY, size=(104, -1), name="推理强度")
        self.codex_speed_combo = wx.ComboBox(panel, style=wx.CB_READONLY, size=(100, -1), name="速度")
        self._set_tool_choices(self.tool_combo, TOOLS, "codex")
        for name, combo in (("工具", self.tool_combo), ("模型", self.model_combo),
                            ("推理强度", self.reasoning_combo), ("速度", self.codex_speed_combo)):
            row.Add(wx.StaticText(panel, label=name + "："), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
            row.Add(combo, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self._refresh_tool_setting_choices()

    def _tool_settings_options(self, tool):
        if tool == "codex":
            if self._codex_settings_catalog:
                return self._codex_settings_catalog
            config = codex_cli_config_for_model(self._tool_route)
            model = config.get("model", "")
            effort = config.get("model_reasoning_effort", "")
            return {
                "models": [(model, model or "codex")],
                "model_options": {model: {"efforts": [(effort, EFFORT_NAMES[effort])] if effort else [],
                                          "speeds": [("standard", "标准"), ("fast", "快速")]}},
                "modes": [("default", MODE_NAMES["default"])],
                "defaults": {"model": model, "effort": effort, "speed": "standard", "mode": "default"},
            }
        if tool == "kimi":
            return self._kimi_settings_catalog or {
                "modes": KIMI_MODES, "defaults": {"speed": "standard", "mode": "default"},
            }
        if tool == "agy":
            return {"models": [(self._tool_route, bindings.app.model_display_name(self._tool_route))],
                    "defaults": {"speed": "standard"}}
        return {"models": [(model, bindings.app.model_display_name(model))
                           for model in VISIBLE_MODEL_IDS if tool_for_route(model) == "other"]}

    @staticmethod
    def _set_tool_choices(combo, choices, selected):
        choices = list(choices) or [("", UNAVAILABLE)]
        ids = tuple(key for key, _ in choices)
        labels = [label for _, label in choices]
        if list(combo.GetItems()) != labels:
            combo.SetItems(labels)
        combo.setting_ids = ids
        selection = ids.index(selected) if selected in ids else 0
        if combo.GetSelection() != selection:
            combo.SetSelection(selection)

    @staticmethod
    def _selected_tool_value(combo):
        index = combo.GetSelection()
        ids = getattr(combo, "setting_ids", ())
        return ids[index] if 0 <= index < len(ids) else ""

    def _current_tool(self):
        return self._selected_tool_value(self.tool_combo)

    def _pending_tool_settings(self):
        tool = self._current_tool()
        settings = {"tool": tool, "model": self._selected_tool_value(self.model_combo),
                    "effort": self._selected_tool_value(self.reasoning_combo),
                    "speed": self._selected_tool_value(self.codex_speed_combo), "mode": self._tool_mode}
        if tool == "codex" and self._codex_settings_explicit:
            settings["explicit"] = True
        if tool == "kimi":
            settings["permission"] = self._kimi_permission
        return settings

    def _refresh_tool_setting_choices(self, selected=None):
        selected = selected or {}
        tool = self._current_tool()
        options = self._tool_settings_options(tool)
        defaults = options.get("defaults", {})
        if tool == "other":
            defaults = {"model": self._tool_route}
        self._set_tool_choices(self.model_combo, options.get("models", ()), selected.get("model", defaults.get("model")))
        model = self._selected_tool_value(self.model_combo)
        model_options = options.get("model_options", {}).get(model, {})
        efforts = model_options.get("efforts", ())
        effort = selected.get("effort")
        if effort not in dict(efforts):
            effort = model_options.get("default_effort", defaults.get("effort"))
        self._set_tool_choices(self.reasoning_combo, efforts, effort)
        speeds = model_options.get("speeds")
        if speeds is None and tool in {"agy", "kimi", "codex"}:
            speeds = [("standard", "标准")]
        self._set_tool_choices(self.codex_speed_combo, speeds or (), selected.get("speed", defaults.get("speed")))
        modes = dict(options.get("modes", ()))
        mode = selected.get("mode")
        self._tool_mode = mode if mode in modes else defaults.get("mode", "")
        if tool == "kimi":
            permission = selected.get("permission")
            self._kimi_permission = permission if permission in dict(KIMI_PERMISSIONS) else "auto"
        self._refresh_tool_mode_menu()

    def _select_tool_route(self, route):
        tool = tool_for_route(route)
        if self._current_tool() == tool and self._tool_route == route:
            return
        self._tool_route = route
        self._codex_settings_explicit = False
        self._set_tool_choices(self.tool_combo, TOOLS, tool)
        self._refresh_tool_setting_choices()

    def _sync_tool_settings_from_chat(self, chat, route):
        self._select_tool_route(route)
        self._codex_settings_explicit = False
        selected = next((turn["tool_settings"] for turn in reversed((chat or {}).get("turns") or [])
                         if isinstance(turn, dict) and isinstance(turn.get("tool_settings"), dict)
                         and turn["tool_settings"].get("tool") == self._current_tool()), None)
        selected = dict(selected or {})
        if is_codex_model(route):
            for key, value in codex_cli_config_for_model(route).items():
                selected.setdefault("effort" if key == "model_reasoning_effort" else key, value)
            selected.setdefault("speed", self._codex_service_tier_for_chat(chat) or "standard")
        elif is_kimi_model(route):
            if route != "kimi/main":
                selected.setdefault("model", kimi_model_to_server_alias(route))
            self._request_kimi_settings_catalog()
        self._refresh_tool_setting_choices(selected)

    def _on_tool_changed(self, _event):
        tool = self._current_tool()
        chat = self._visible_codex_speed_chat()
        route = str(chat.get("model") or "")
        if tool == tool_for_route(route) and route:
            self._sync_tool_settings_from_chat(chat, route)
        else:
            self._tool_route = {"codex": DEFAULT_CODEX_MODEL, "agy": AGY_MODEL_ID,
                                "kimi": "kimi/main", "other": "claudecode/default"}[tool]
            self._codex_settings_explicit = False
            self._refresh_tool_setting_choices()
        self._on_model_changed(None)
        if tool == "codex":
            self._request_codex_settings_catalog()
        elif tool == "kimi":
            self._request_kimi_settings_catalog()

    def _on_tool_model_changed(self, event):
        self._on_tool_setting_changed(event)
        self._refresh_tool_setting_choices(self._pending_tool_settings())
        self._on_model_changed(event)
        if self._current_tool() == "kimi":
            choice = self._selected_tool_value(self.model_combo)
            label = self.model_combo.GetStringSelection()
            if choice == "kimi-code/kimi-for-coding-highspeed" and "K2.7" in label:
                self.SetStatusText("K2.7 Code Highspeed 约消耗 3 倍用量")
            elif choice == "kimi-code/k3.1-pro-highspeed" and "K3.1" in label:
                self.SetStatusText("K3.1 Pro Highspeed 约消耗 2 倍用量")

    def _on_tool_setting_changed(self, _event):
        if self._current_tool() == "codex":
            self._codex_settings_explicit = True

    def _on_tool_model_focus(self, event):
        event.Skip()
        if self._current_tool() == "codex":
            self._request_codex_settings_catalog()
        elif self._current_tool() == "kimi":
            self._request_kimi_settings_catalog()

    def _request_kimi_settings_catalog(self):
        if self._kimi_settings_catalog or self._kimi_settings_loading:
            return
        self._kimi_settings_loading = True

        def read():
            client = KimiServerClient()
            try:
                client.start()
                result = client.read_tool_settings_catalog()
            except Exception as exc:
                self._call_after_if_alive(self._apply_kimi_settings_catalog, {}, str(exc))
            else:
                self._call_after_if_alive(self._apply_kimi_settings_catalog, result, "")
            finally:
                client.close()

        threading.Thread(target=read, daemon=True, name="kimi-model-catalog").start()

    def _apply_kimi_settings_catalog(self, result, error):
        self._kimi_settings_loading = False
        visible = self._current_tool() == "kimi"
        if error:
            if visible:
                self.SetStatusText(f"无法读取 Kimi Code 模型目录：{error}")
            return
        models = []
        model_options = {}
        for row in result["models"]:
            model = row.get("model")
            if not model or model in model_options:
                continue
            models.append((model, row.get("display_name") or model))
            capabilities = row.get("capabilities") or ()
            efforts = row.get("support_efforts") or ()
            default_effort = row.get("default_effort")
            if not efforts:
                if "always_thinking" in capabilities:
                    efforts = ("on",)
                elif "thinking" in capabilities:
                    efforts = ("on", "off")
                default_effort = "on"
            model_options[model] = {
                "efforts": [(name, KIMI_EFFORT_NAMES.get(name, name)) for name in efforts],
                "default_effort": default_effort,
            }
        default = result["default_model"]
        if default not in model_options:
            default = ""
            models.insert(0, ("", "请选择模型" if model_options else UNAVAILABLE))
        self._kimi_settings_catalog = {
            "models": models, "model_options": model_options, "modes": KIMI_MODES,
            "defaults": {"model": default, "speed": "standard", "mode": "default"},
        }
        if visible:
            selected = self._pending_tool_settings()
            chat = self._visible_codex_speed_chat()
            turns = (chat or {}).get("turns") or ()
            prior = next((turn["tool_settings"] for turn in reversed(turns)
                          if (turn.get("tool_settings") or {}).get("tool") == "kimi"), {})
            selected["model"] = prior.get("model") or (
                kimi_model_to_server_alias(self._tool_route) if self._tool_route != "kimi/main" else default)
            selected["effort"] = prior.get("effort")
            self._refresh_tool_setting_choices(selected)
            if not default:
                self.SetStatusText("Kimi Code 没有可用的默认模型，请选择模型或检查 Kimi CLI 配置")

    def _request_codex_settings_catalog(self):
        if self._codex_settings_catalog or self._codex_settings_loading:
            return
        self._codex_settings_loading = True

        def read():
            client = CodexAppServerClient(timeout=10)
            try:
                result = client.read_tool_settings_catalog()
            except Exception as exc:
                self._call_after_if_alive(self._apply_codex_settings_catalog, {}, str(exc))
            else:
                self._call_after_if_alive(self._apply_codex_settings_catalog, result, "")
            finally:
                client.close()

        threading.Thread(target=read, daemon=True).start()

    def _apply_codex_settings_catalog(self, result, error):
        self._codex_settings_loading = False
        if error:
            if self._current_tool() == "codex":
                self.SetStatusText(f"无法读取 Codex 模型目录：{error}")
            return
        rows = result["models"]
        models = []
        model_options = {}
        for item in rows:
            model = item["model"]
            if model in model_options:
                continue
            models.append((model, item.get("displayName") or model))
            efforts = [entry["reasoningEffort"] for entry in item.get("supportedReasoningEfforts", ())
                       if entry.get("reasoningEffort")]
            speeds = [("standard", "标准")]
            tiers = item.get("serviceTiers") or ()
            for tier in tiers:
                tier_id = tier.get("id")
                if tier_id and tier_id != "standard":
                    name = tier.get("name")
                    speeds.append((tier_id, "快速" if name == "Fast" else name or tier_id))
            if not tiers and "fast" in (item.get("additionalSpeedTiers") or ()):
                speeds.append(("fast", "快速"))
            model_options[model] = {
                "efforts": [(effort, EFFORT_NAMES.get(effort, effort)) for effort in efforts],
                "default_effort": item.get("defaultReasoningEffort", ""),
                "speeds": speeds,
            }
        modes = [("default", MODE_NAMES["default"])]
        modes.extend((mode, MODE_NAMES.get(mode, entry.get("name") or mode))
                     for entry in result["modes"] if (mode := entry.get("mode")) and mode != "default")
        self._codex_settings_catalog = {
            "models": models, "model_options": model_options, "modes": modes,
            "defaults": {"model": result["default_model"], "speed": "standard", "mode": "default"},
        }
        if self._current_tool() == "codex":
            current = self._pending_tool_settings()
            if not current["model"]:
                current.pop("model")
            if not current["effort"]:
                current.pop("effort")
            self._refresh_tool_setting_choices(current)

    def _append_tool_modes(self, menu, id_refs):
        options = self._tool_settings_options(self._current_tool())
        modes = options.get("modes", ())
        if not modes:
            return
        submenu = wx.Menu()
        self._populate_tool_modes(submenu, modes, id_refs)
        if self._current_tool() == "kimi":
            self._append_kimi_permissions(submenu, id_refs)
        menu.AppendSubMenu(submenu, "工具模式")

    def _refresh_tool_mode_menu(self):
        menu = self._tool_mode_menu
        modes = self._tool_settings_options(self._current_tool()).get("modes", ())
        if tuple(modes) == getattr(self, "_listed_tool_modes", None):
            for item, (mode, _label) in zip(menu.GetMenuItems(), modes):
                item.Check(mode == self._tool_mode)
            for mode, item in self._kimi_permission_items.items():
                item.Check(mode == self._kimi_permission)
            return
        for item in menu.GetMenuItems():
            menu.Unbind(wx.EVT_MENU, id=item.GetId())
            menu.DestroyItem(item)
        self._tool_mode_menu_ids.clear()
        self._kimi_permission_items = {}
        self._listed_tool_modes = tuple(modes)
        self._populate_tool_modes(menu, modes, self._tool_mode_menu_ids)
        if self._current_tool() == "kimi":
            self._kimi_permission_items = self._append_kimi_permissions(menu, self._tool_mode_menu_ids)
        if not modes:
            menu.Append(wx.ID_ANY, UNAVAILABLE).Enable(False)

    def _populate_tool_modes(self, menu, modes, id_refs):
        for value, label in modes:
            menu_id = wx.NewIdRef()
            id_refs.append(menu_id)
            item = menu.AppendRadioItem(menu_id, label)
            item.Check(self._tool_mode == value)
            menu.Bind(wx.EVT_MENU, lambda _event, mode=value, name=label: self._select_tool_mode(mode, name),
                      id=menu_id)

    def _select_tool_mode(self, mode, label):
        self._tool_mode = mode
        self._refresh_tool_mode_menu()
        self.SetStatusText(f"下一回合的工具模式：{label}")

    def _append_kimi_permissions(self, menu, id_refs):
        submenu = wx.Menu()
        items = {}
        for mode, label in KIMI_PERMISSIONS:
            menu_id = wx.NewIdRef()
            id_refs.append(menu_id)
            item = submenu.AppendRadioItem(menu_id, label)
            item.Check(mode == self._kimi_permission)
            items[mode] = item
            submenu.Bind(wx.EVT_MENU, lambda _event, key=mode, name=label: self._select_kimi_permission(key, name),
                         id=menu_id)
        menu.AppendSubMenu(submenu, "Kimi 执行审批")
        return items

    def _select_kimi_permission(self, mode, label):
        self._kimi_permission = mode
        self._refresh_tool_mode_menu()
        self.SetStatusText(f"下一回合的 Kimi 执行审批：{label}")
