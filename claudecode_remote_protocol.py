import json


DEFAULT_REMOTE_CLAUDECODE_MODEL = "claudecode/default"


def strip_remote_message_prefix(text: str) -> str:
    return str(text or "").strip()


def format_remote_user_input_request(params: dict) -> str:
    """格式化用户输入请求消息"""
    questions = params.get("questions") if isinstance(params.get("questions"), list) else []
    if not questions:
        return "Claude Code 需要你的输入，请直接回复内容。"

    lines = ["Claude Code 需要你的输入。"]
    if len(questions) == 1:
        question = questions[0] if isinstance(questions[0], dict) else {}
        lines.extend(_format_single_question(question, include_id=False))
    else:
        lines.append("请按 `问题ID=答案` 每行回复。")
        for question in questions:
            question = question if isinstance(question, dict) else {}
            lines.append("")
            lines.extend(_format_single_question(question, include_id=True))
    return "\n".join(lines).strip()


def format_remote_approval_request(params: dict) -> str:
    """格式化批准请求消息"""
    request_type = str(params.get("type") or "").strip()
    message = str(params.get("message") or "").strip()

    if request_type == "command_approval":
        return f"Claude Code 需要批准执行命令：\n{message}\n请回复 'yes' 或 'no'。"
    elif request_type == "file_change_approval":
        return f"Claude Code 需要批准文件变更：\n{message}\n请回复 'yes' 或 'no'。"
    elif request_type == "permissions_approval":
        return f"Claude Code 需要权限批准：\n{message}\n请回复 'yes' 或 'no'。"
    else:
        return f"Claude Code 需要批准：\n{message}\n请回复 'yes' 或 'no'。"


def _format_single_question(question: dict, include_id: bool) -> list[str]:
    lines = []
    question_id = str(question.get("id") or "").strip()
    header = str(question.get("header") or "").strip()
    prompt = str(question.get("question") or "").strip()
    title = header or question_id or "问题"
    if include_id and question_id:
        title = f"{title} ({question_id})"
    lines.append(title)
    if prompt:
        lines.append(prompt)
    options = question.get("options") if isinstance(question.get("options"), list) else []
    if options:
        lines.append("可选项：")
        for idx, option in enumerate(options, start=1):
            label = str((option or {}).get("label") or "").strip() or f"选项{idx}"
            lines.append(f"{idx}. {label}")
        if bool(question.get("isOther")):
            lines.append(f"{len(options) + 1}. 其他（回复 `其他:内容`）")
        lines.append("可直接回复序号或选项文本。")
    elif bool(question.get("isSecret")):
        lines.append("请直接回复秘密内容。")
    else:
        lines.append("请直接回复文本内容。")
    return lines


def parse_remote_user_input_reply(params: dict, text: str) -> tuple[dict[str, list[str]] | None, str]:
    """解析用户输入回复"""
    clean = strip_remote_message_prefix(text)
    questions = params.get("questions") if isinstance(params.get("questions"), list) else []
    if not questions:
        return {}, ""
    if not clean:
        return None, "回复内容为空。"
    if len(questions) == 1:
        question = questions[0] if isinstance(questions[0], dict) else {}
        values, error = _parse_single_question_answer(question, clean)
        if error:
            return None, error
        return {str(question.get("id") or "").strip(): values}, ""

    answers = {}
    by_id = {str((question or {}).get("id") or "").strip(): question for question in questions}
    for raw_line in clean.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "=" not in line:
            return None, "多问题回复格式错误，请按 `问题ID=答案` 每行回复。"
        question_id, value = line.split("=", 1)
        question_id = question_id.strip()
        if question_id not in by_id:
            return None, f"未知的问题ID：{question_id}"
        parsed, error = _parse_single_question_answer(by_id[question_id], value.strip())
        if error:
            return None, error
        answers[question_id] = parsed
    if not answers:
        return None, "未解析到任何有效答案。"
    return answers, ""


def parse_remote_approval_reply(text: str) -> tuple[str | None, str]:
    """解析批准回复"""
    clean = strip_remote_message_prefix(text).lower().strip()
    if clean in ("yes", "y", "是", "同意", "批准"):
        return "approved", ""
    elif clean in ("no", "n", "否", "拒绝", "不同意"):
        return "rejected", ""
    else:
        return None, "请回复 'yes' 或 'no'。"


def _parse_single_question_answer(question: dict, text: str) -> tuple[list[str] | None, str]:
    clean = strip_remote_message_prefix(text)
    options = question.get("options") if isinstance(question.get("options"), list) else []
    if not options:
        return ([clean] if clean else []), ""

    labels = [str((item or {}).get("label") or "").strip() for item in options]
    lower = clean.lower()
    if clean.isdigit():
        idx = int(clean)
        if 1 <= idx <= len(options):
            return ([str((options[idx - 1] or {}).get("value") or labels[idx - 1])], "")
        if bool(question.get("isOther")) and idx == len(options) + 1:
            return ([clean], "")
        return None, f"选项序号超出范围（1-{len(options)}）。"

    for idx, label in enumerate(labels):
        if lower == label.lower():
            return ([str((options[idx] or {}).get("value") or label)], "")

    if bool(question.get("isOther")):
        if clean.startswith("其他:"):
            return ([clean[3:].strip()], "")
        return ([clean], "")

    return None, f"无效的选项。可选项：{', '.join(labels)}"
