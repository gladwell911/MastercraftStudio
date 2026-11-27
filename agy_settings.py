"""Map AGY's advertised ACP settings to the desktop's pending settings."""

import re
import subprocess

from agy_acp import AgyError


DEFAULTS = {"model": "gemini-4-neon", "effort": "high", "speed": "standard", "mode": "auto"}
MODES = {"default": "默认", "accept-edits": "接受编辑", "auto": "自动", "plan": "计划"}
FIELDS = {"model": "model", "thought_level": "effort", "mode": "mode"}


def read_catalog(executable, effort_names):
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    response = subprocess.run([executable, "models"], check=True, capture_output=True, encoding="utf-8",
                              timeout=15, creationflags=creationflags)
    models = {}
    for line in response.stdout.splitlines():
        model_id, separator, label = line.strip().partition("\t")
        if not separator:
            continue
        base, _, effort = model_id.rpartition("-")
        suffix = f" ({effort.title()})"
        if effort in effort_names and label.endswith(suffix):
            label = label.removesuffix(suffix)
            model_id = base
        else:
            effort = ""
        model = models.setdefault(model_id, {"value": model_id, "label": label, "efforts": []})
        if effort:
            model["efforts"].append(effort)
    if not models:
        raise AgyError("AGY 未返回可用模型")
    catalog = [{"category": "model", "options": list(models.values())}]
    try:
        help_result = subprocess.run([executable, "--help"], check=True, capture_output=True,
                                     encoding="utf-8", timeout=5, creationflags=creationflags)
    except (OSError, subprocess.SubprocessError):
        return catalog
    help_text = f"{help_result.stdout}\n{help_result.stderr}"
    advertised = re.search(r"^\s*--mode[ \t]+[^(\r\n]+\(([^()\r\n]+)\)", help_text, re.MULTILINE)
    if advertised:
        values = [value.strip() for value in advertised[1].split(",")]
        modes = [{"value": value, "label": MODES[value]} for value in values if value in MODES]
        if modes:
            catalog.append({"category": "mode", "options": modes})
    return catalog


def _values(field):
    return [option["value"] for option in field.get("options", ())]


def _efforts(model, field):
    return model.get("efforts") or _values(field)


def control_options(options, effort_names):
    fields = {option["category"]: option for option in options}
    models = fields.get("model", {}).get("options", ())
    effort_field = fields.get("thought_level", {})
    modes = fields.get("mode", {}).get("options", ())
    effort_order = {level: index for index, level in enumerate(effort_names)}
    model_options = {}
    for model in models:
        levels = _efforts(model, effort_field)
        ordered = sorted(levels, key=lambda level: effort_order.get(level, len(effort_order)))
        efforts = [(level, effort_names.get(level, level)) for level in ordered]
        if efforts and DEFAULTS["effort"] not in levels:
            efforts.insert(0, ("", "请选择推理强度"))
        model_options[model["value"]] = {"efforts": efforts, "default_effort": DEFAULTS["effort"]}
    labels = [(model["value"], model["label"]) for model in models]
    if models and DEFAULTS["model"] not in model_options:
        labels.insert(0, ("", "请选择模型"))
    return {"models": labels, "model_options": model_options,
            "modes": [(mode["value"], MODES.get(mode["value"], mode["label"])) for mode in modes],
            "defaults": DEFAULTS}


def native_config(options, settings, effort_names):
    fields = {option["category"]: option for option in options}
    selected_model = settings.get("model") or DEFAULTS["model"]
    model = next((entry for entry in fields.get("model", {}).get("options", ())
                  if entry["value"] == selected_model), {})
    config = {}
    for category, key in FIELDS.items():
        value = settings.get(key) or DEFAULTS[key]
        field = fields.get(category, {})
        choices = _efforts(model, field) if key == "effort" else _values(field)
        if not field and key != "model":
            continue
        if value not in choices:
            label = effort_names.get(value, MODES.get(value, value))
            raise AgyError(f"AGY 未提供所需设置（{label}），请选择可用配置后重试。")
        config[field["id"]] = value
    return config
