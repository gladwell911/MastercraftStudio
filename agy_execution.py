"""Project public ACP execution and interactions into the existing MC/RC views."""

import difflib
import json
import math


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def _text(content):
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        return content["text"]
    return ""


def _tool_details(tool):
    for field, label in (("rawInput", "输入"), ("rawOutput", "输出")):
        value = tool.get(field)
        if value is not None:
            yield f"{label}\n{value if isinstance(value, str) else _json(value)}"
    for item in tool.get("content") or ():
        if not isinstance(item, dict):
            continue
        if item.get("type") == "diff":
            path = str(item.get("path") or "")
            old = str(item.get("oldText") or "").splitlines()
            new = str(item.get("newText") or "").splitlines()
            yield "\n".join(difflib.unified_diff(old, new, fromfile=path, tofile=path, lineterm=""))
        elif text := _text(item.get("content")):
            yield text


class AgyExecution:
    def __init__(self):
        self.tools = {}
        self.thought = ""
        self.thought_index = 0
        self.in_thought = False

    def step(self, update):
        kind = update.get("sessionUpdate")
        if kind == "agent_thought_chunk":
            if not self.in_thought:
                self.thought_index += 1
                self.thought = ""
                self.in_thought = True
            self.thought += _text(update.get("content"))
            if self.thought:
                return (f"thought:{self.thought_index}", "reasoning",
                        "AGY 思考：" + self.thought.strip()[:150], self.thought)
            return None
        self.in_thought = False
        if kind in {"tool_call", "tool_call_update"}:
            item_id = update.get("toolCallId")
            if not isinstance(item_id, str) or not item_id:
                return None
            tool = self.tools.setdefault(item_id, {})
            if tool.get("status") in {"completed", "failed"} and update.get("status") not in {"completed", "failed"}:
                update = {key: value for key, value in update.items() if key != "status"}
            tool.update({key: value for key, value in update.items() if value is not None})
            status = {"pending": "等待", "in_progress": "执行中", "completed": "已完成", "failed": "失败"}
            title = str(tool.get("title") or tool.get("kind") or "工具")
            label = status.get(tool.get("status"))
            summary = f"{label}：{title}" if label else title
            return f"tool:{item_id}", "tool", summary, "\n\n".join((summary, *_tool_details(tool)))
        if kind == "plan":
            parts = [str(entry["content"]) for entry in update.get("entries") or ()
                     if isinstance(entry, dict) and entry.get("content")]
            if parts:
                return "plan", "plan", "AGY 计划", "\n".join(parts)
        if kind == "steer_error":
            return (f"steer-error:{update['requestId']}", "error",
                    "AGY 追加失败，原请求继续执行", update["text"])
        return None


def interaction_questions(method, params):
    if method == "session/request_permission":
        options = [{"value": option["optionId"], "label": option.get("name") or option["optionId"]}
                   for option in params.get("options") or ()
                   if isinstance(option, dict) and isinstance(option.get("optionId"), str) and option["optionId"]]
        tool = params.get("toolCall")
        tool = tool if isinstance(tool, dict) else {}
        prompt = str(tool.get("title") or "AGY 请求工具审批")
        return prompt, [{"id": "approval", "question": prompt, "options": options}] if options else []
    if params.get("mode") != "form":
        return "", []
    schema = params.get("requestedSchema")
    if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
        return "", []
    properties = schema["properties"]
    questions = []
    for key, field in properties.items():
        field_type = field.get("type") if isinstance(field, dict) else None
        if field_type not in {"string", "boolean", "number", "integer"}:
            return "", []
        choices = [(option["const"], option.get("title") or str(option["const"]))
                   for option in field.get("oneOf") or () if isinstance(option, dict) and "const" in option]
        if not choices:
            labels = field.get("enumNames")
            labels = labels if isinstance(labels, list) else []
            choices = [(value, labels[index] if index < len(labels) else str(value))
                       for index, value in enumerate(field.get("enum") or ())]
        if not choices and field_type == "boolean":
            choices = [(True, "是"), (False, "否")]
        options = [{"label": str(label), "value": str(index)} for index, (_, label) in enumerate(choices)]
        questions.append({
            "id": key, "header": str(field.get("title") or key), "question": str(field.get("description") or ""),
            "options": options, "values": [value for value, _ in choices], "value_type": field_type,
            "required": key in (schema.get("required") or ()),
        })
    return str(params.get("message") or "AGY 需要补充信息"), questions


def interaction_result(method, questions, text, answers):
    if method == "session/request_permission":
        if not any(option["value"] == text for option in questions[0]["options"]):
            raise ValueError("请选择此 AGY 请求提供的审批决定。")
        return {"outcome": {"outcome": "selected", "optionId": text}}
    if not isinstance(answers, dict) or answers.keys() - {question["id"] for question in questions}:
        raise ValueError("请按 AGY 提问表单填写答案。")
    content = {}
    for question in questions:
        key, label = question["id"], question["header"]
        value = answers.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"请输入有效的答案：{label}")
        if not value:
            if question["required"]:
                raise ValueError(f"请填写：{label}")
            continue
        try:
            if question["options"]:
                options = question["options"]
                index = next(index for index, opt in enumerate(options) if opt["value"] == value)
                content[key] = question["values"][index]
            elif question["value_type"] == "integer":
                content[key] = int(value)
            elif question["value_type"] == "number":
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError
                content[key] = number
            else:
                content[key] = value
        except (ValueError, StopIteration):
            raise ValueError(f"请输入有效的答案：{label}") from None
    return {"action": "accept", "content": content}
