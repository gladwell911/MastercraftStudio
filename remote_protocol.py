import json


DEFAULT_REMOTE_MODEL = "codex/main"


def strip_remote_message_prefix(text: str) -> str:
    return str(text or "").strip()


def format_remote_user_input_request(params: dict) -> str:
    questions = params.get("questions") if isinstance(params.get("questions"), list) else []
    if not questions:
        return "Codex 需要你的输入，请直接回复内容。"

    lines = ["Codex 需要你的输入。"]
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


def _parse_single_question_answer(question: dict, text: str) -> tuple[list[str] | None, str]:
    clean = strip_remote_message_prefix(text)
    options = question.get("options") if isinstance(question.get("options"), list) else []
    if not options:
        return ([clean] if clean else []), ""

    labels = [str((item or {}).get("label") or "").strip() for item in options]
    lower = clean.lower()
    if clean.isdigit():
        idx = int(clean)
        if 1 <= idx <= len(labels):
            label = labels[idx - 1]
            return ([label] if label else []), ""
        if bool(question.get("isOther")) and idx == len(labels) + 1:
            return None, "选择“其他”时，请回复 `其他:内容`。"
    if bool(question.get("isOther")) and (lower.startswith("其他:") or lower.startswith("other:")):
        value = clean.split(":", 1)[1].strip()
        return ([value] if value else []), ""
    for label in labels:
        if clean == label:
            return ([label] if label else []), ""
    return None, "未识别的选项，请回复序号、选项文本，或按提示格式回复。"


def format_remote_approval_request(method: str, params: dict) -> str:
    method_name = str(method or "").strip()
    lines = []
    if method_name == "item/permissions/requestApproval":
        lines.append("Codex 请求额外权限。")
        reason = str(params.get("reason") or "").strip()
        if reason:
            lines.append(f"原因：{reason}")
        permissions = params.get("permissions")
        if isinstance(permissions, dict) and permissions:
            lines.append("权限：")
            lines.append(json.dumps(permissions, ensure_ascii=False, indent=2))
        lines.append("回复 1 允许本次，2 允许本会话，3 拒绝。")
        return "\n".join(lines).strip()
    if method_name == "item/fileChange/requestApproval":
        lines.append("Codex 请求文件变更权限。")
        grant_root = str(params.get("grantRoot") or "").strip()
        if grant_root:
            lines.append(f"授权目录：{grant_root}")
        reason = str(params.get("reason") or "").strip()
        if reason:
            lines.append(f"原因：{reason}")
        lines.append("回复 1 允许本次，2 允许本会话，3 拒绝，4 取消当前轮。")
        return "\n".join(lines).strip()
    lines.append("Codex 请求执行命令。")
    command = str(params.get("command") or "").strip()
    cwd = str(params.get("cwd") or "").strip()
    reason = str(params.get("reason") or "").strip()
    if command:
        lines.append(f"命令：{command}")
    if cwd:
        lines.append(f"工作目录：{cwd}")
    if reason:
        lines.append(f"原因：{reason}")
    lines.append("回复 1 允许本次，2 允许本会话，3 拒绝，4 取消当前轮。")
    return "\n".join(lines).strip()


def parse_remote_approval_reply(method: str, text: str) -> str | None:
    clean = strip_remote_message_prefix(text).lower()
    if not clean:
        return None
    mapping = {
        "1": "accept",
        "允许": "accept",
        "允许本次": "accept",
        "accept": "accept",
        "2": "acceptForSession",
        "允许本会话": "acceptForSession",
        "session": "acceptForSession",
        "acceptforsession": "acceptForSession",
        "3": "decline",
        "拒绝": "decline",
        "decline": "decline",
    }
    if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
        mapping.update({"4": "cancel", "取消": "cancel", "cancel": "cancel"})
    return mapping.get(clean)
