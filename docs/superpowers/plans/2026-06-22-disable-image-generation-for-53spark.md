# Disable Image Generation for 5.3 Spark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent `codex/gpt-5.3-codex-spark-high` from sending the runtime `image_generation` tool while keeping `gpt-5.4` and `gpt-5.5` behavior unchanged.

**Architecture:** Apply the fix at Codex app-server launch time instead of mutating per-turn payloads. Add a small model-to-disabled-features mapping in `codex_client.py`, use it from `build_codex_app_server_command(...)`, and verify through targeted unit tests plus a log-based smoke check that `5.3spark` no longer emits `{"type":"image_generation"}` in `response.create`.

**Tech Stack:** Python, pytest, Codex app-server launcher, sqlite-backed runtime logs

---

### Task 1: Add Model-Specific Runtime Feature Gating

**Files:**
- Modify: `codex_client.py`
- Modify: `tests/test_codex_client_unit.py`

- [ ] **Step 1: Write the failing unit tests**

Add these tests to `tests/test_codex_client_unit.py` near the existing `build_codex_app_server_command` coverage:

```python
def test_codex_disabled_features_for_53_spark():
    assert codex_client.codex_disabled_features_for_model(
        "codex/gpt-5.3-codex-spark-high"
    ) == ("image_generation",)


def test_codex_disabled_features_for_non_53_spark_models():
    assert codex_client.codex_disabled_features_for_model("codex/gpt-5.4-medium") == ()
    assert codex_client.codex_disabled_features_for_model("codex/main") == ()


def test_build_codex_app_server_command_disables_image_generation_for_53_spark(monkeypatch):
    monkeypatch.setattr(codex_client, "resolve_codex_launch_command", lambda: ["codex.cmd"])

    command = codex_client.build_codex_app_server_command(
        r"C:\code\codex",
        codex_model="codex/gpt-5.3-codex-spark-high",
    )

    assert command == [
        "codex.cmd",
        "app-server",
        "--listen",
        "stdio://",
        "--analytics-default-enabled",
        "-c",
        'model="gpt-5.3-codex-spark"',
        "-c",
        'model_reasoning_effort="high"',
        "--disable",
        "image_generation",
    ]


def test_build_codex_app_server_command_does_not_disable_image_generation_for_54(monkeypatch):
    monkeypatch.setattr(codex_client, "resolve_codex_launch_command", lambda: ["codex.cmd"])

    command = codex_client.build_codex_app_server_command(
        r"C:\code\codex",
        codex_model="codex/gpt-5.4-medium",
    )

    assert "--disable" not in command
    assert "image_generation" not in command
```

- [ ] **Step 2: Run the focused tests to confirm they fail first**

Run:

```bash
pytest tests/test_codex_client_unit.py -k "disabled_features_for_model or build_codex_app_server_command" -v
```

Expected: FAIL with `AttributeError` or assertion failures because `codex_disabled_features_for_model` does not exist and the command does not yet include `--disable image_generation`.

- [ ] **Step 3: Implement the minimal gating helper and command wiring**

Update `codex_client.py` with a small helper and use it from `build_codex_app_server_command(...)`:

```python
def codex_disabled_features_for_model(model: str) -> tuple[str, ...]:
    normalized = str(model or "").strip()
    if normalized == "codex/gpt-5.3-codex-spark-high":
        return ("image_generation",)
    return ()


def build_codex_app_server_command(cwd: str | None = None, codex_model: str = DEFAULT_CODEX_MODEL) -> list[str]:
    command = [
        *resolve_codex_launch_command(),
        "app-server",
        "--listen",
        "stdio://",
        "--analytics-default-enabled",
    ]
    for key, value in codex_cli_config_for_model(codex_model).items():
        command.extend(["-c", f'{key}="{value}"'])
    for feature_name in codex_disabled_features_for_model(codex_model):
        command.extend(["--disable", feature_name])
    return command
```

Constraints for this step:

- Keep the logic launch-time only; do not add per-request `tools` filtering in this task.
- Match only `codex/gpt-5.3-codex-spark-high`; do not broaden scope to uploads, `localImage`, or other models.
- Do not change `start_thread(...)`, `start_turn_items(...)`, or `steer_turn_items(...)` payload structure.

- [ ] **Step 4: Run the focused tests again**

Run:

```bash
pytest tests/test_codex_client_unit.py -k "disabled_features_for_model or build_codex_app_server_command" -v
```

Expected: PASS for all four tests.

- [ ] **Step 5: Run the existing request-shape regressions**

Run:

```bash
pytest tests/test_codex_client_unit.py -k "start_turn_items or thread_requests_send_service_tier" -v
```

Expected: PASS, confirming the fix did not alter `thread/start` or `turn/start` app-server request payloads.

- [ ] **Step 6: Commit the launcher-level fix**

```bash
git add codex_client.py tests/test_codex_client_unit.py
git commit -m "fix: disable image generation for 5.3 spark"
```


### Task 2: Verify Runtime Behavior Against the Original Failure Mode

**Files:**
- Modify: none
- Inspect: `.codex-home/logs_2.sqlite`

- [ ] **Step 1: Reproduce a `5.3spark` session after the code change**

Run the app locally, select `gpt5.3spark`, start a new chat, and send a plain text message such as `你好`.

Expected: no `400 invalid_request_error` is shown in the app UI.

- [ ] **Step 2: Confirm the new `5.3spark` websocket request no longer includes `image_generation`**

Run:

```bash
@'
import sqlite3
path = r'C:\code\sj\mc\.codex-home\logs_2.sqlite'
conn = sqlite3.connect(path)
cur = conn.cursor()
cur.execute("""
select id, feedback_log_body
from logs
where feedback_log_body like '%websocket request:%'
  and feedback_log_body like '%model=gpt-5.3-codex-spark%'
order by id desc
limit 10
""")
for log_id, body in cur.fetchall():
    text = body or ""
    print("ID", log_id, "HAS_IMAGE_GENERATION", "image_generation" in text)
conn.close()
'@ | python -
```

Expected: the newest `gpt-5.3-codex-spark` request prints `HAS_IMAGE_GENERATION False`.

- [ ] **Step 3: Confirm `5.4` still keeps `image_generation` enabled**

Run the app locally, select `gpt5.4`, send a plain text message, then run:

```bash
@'
import sqlite3
path = r'C:\code\sj\mc\.codex-home\logs_2.sqlite'
conn = sqlite3.connect(path)
cur = conn.cursor()
cur.execute("""
select id, feedback_log_body
from logs
where feedback_log_body like '%websocket request:%'
  and feedback_log_body like '%model=gpt-5.4%'
order by id desc
limit 5
""")
for log_id, body in cur.fetchall():
    text = body or ""
    print("ID", log_id, "HAS_IMAGE_GENERATION", "image_generation" in text)
conn.close()
'@ | python -
```

Expected: the newest `gpt-5.4` request prints `HAS_IMAGE_GENERATION True`.

- [ ] **Step 4: Confirm `5.5` still keeps `image_generation` enabled**

Run the app locally, select `gpt5.5`, send a plain text message, then run:

```bash
@'
import sqlite3
path = r'C:\code\sj\mc\.codex-home\logs_2.sqlite'
conn = sqlite3.connect(path)
cur = conn.cursor()
cur.execute("""
select id, feedback_log_body
from logs
where feedback_log_body like '%websocket request:%'
  and feedback_log_body like '%model=gpt-5.5%'
order by id desc
limit 5
""")
for log_id, body in cur.fetchall():
    text = body or ""
    print("ID", log_id, "HAS_IMAGE_GENERATION", "image_generation" in text)
conn.close()
'@ | python -
```

Expected: the newest `gpt-5.5` request prints `HAS_IMAGE_GENERATION True`.

- [ ] **Step 5: Record the acceptance result in the final handoff**

Include these outcomes in the completion note:

```text
- 5.3spark plain-text chat no longer returns param="tools" / image_generation 400
- 5.3spark websocket request no longer includes image_generation
- 5.4 websocket request still includes image_generation
- 5.5 websocket request still includes image_generation
```


### Task 3: Guard Scope and Avoid Unintended Product Changes

**Files:**
- Modify: none

- [ ] **Step 1: Verify no UI model capability text was changed**

Run:

```bash
git diff --stat HEAD~1..HEAD
```

Expected: only `codex_client.py` and `tests/test_codex_client_unit.py` are listed for the fix commit.

- [ ] **Step 2: Verify image upload behavior was intentionally left untouched**

Run:

```bash
pytest tests/test_chat_attachments_acceptance.py tests/test_chat_attachments_ui_automation.py -q
```

Expected: PASS, confirming that the `5.3spark` fix did not accidentally alter attachment UI behavior.

- [ ] **Step 3: Do not expand scope during implementation**

Keep these out of this fix:

```text
- No disabling of localImage uploads
- No model capability UI redesign
- No runtime request-body rewriting for tools
- No tool filtering for 5.4 or 5.5
- No changes to main.py chat submission flow
```

