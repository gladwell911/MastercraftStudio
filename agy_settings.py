"""Map AGY's advertised ACP settings to the desktop's pending settings."""

import subprocess

from agy_acp import AgyError


DEFAULTS = {"model": "gemini-4-neon", "effort": "high", "speed": "standard", "mode": "auto"}
MODES = {"default": "默认", "accept-edits": "接受编辑", "auto": "自动", "plan": "计划"}
FIELDS = {"model": "model", "thought_level": "effort", "mode": "mode"}


def read_models(executable, effort_names):
    response = subprocess.run([executable, "models"], check=True, capture_output=True, encoding="utf-8",
                              timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
    return [{"category": "model", "options": list(models.values())}]


def _values(field):
    return [option["value"] for option in field.get("options", ())]


def _efforts(model, field):
    return model.get("efforts", _values(field))


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
        model_options[model["value"]] = {
            "efforts": [(level, effort_names.get(level, level)) for level in ordered],
            "default_effort": "high" if "high" in levels else effort_field.get("current"),
        }
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
        if key == "effort" and model.get("efforts") == [] and selected_model != DEFAULTS["model"] and not settings.get(key):
            continue
        if value not in choices or not field:
            label = effort_names.get(value, MODES.get(value, value))
            raise AgyError(f"AGY 未提供所需设置（{label}），请选择可用配置后重试。")
        config[field["id"]] = value
    return config
