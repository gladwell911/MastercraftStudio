# Remote Fixed Domain Auto Connect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the mobile app launched from VSCode `Ctrl+F5` connect to the desktop app over `wss://rc.tingyou.cc/nats` without USB or `adb reverse`, while preserving bidirectional chat, message, and notes sync.

**Architecture:** Tighten the desktop fixed-domain startup contract so public websocket readiness is the source of truth, then force the mobile startup path to converge stale local/test settings to the fixed public endpoint and token before remote bootstrap. Extend regression tests on both sides and finish with strict real-device verification against the public endpoint.

**Tech Stack:** Python + wxPython desktop app, local NATS runtime, cloudflared service, Dart/Flutter mobile app, Flutter widget/integration tests, pytest.

---

## File Map

- Modify: `c:\code\mc\main.py`
  - Desktop fixed-domain startup, runtime status, public readiness handling, cloudflared startup contract.
- Modify: `c:\code\mc\tests\test_main_unit.py`
  - Desktop unit coverage for public readiness and startup failure states.
- Modify: `c:\code\rc\lib\remote_control_settings.dart`
  - Mobile settings normalization and migration toward the fixed public endpoint.
- Modify: `c:\code\rc\lib\main.dart`
  - Mobile startup remote settings resolution and bootstrap path.
- Modify: `c:\code\rc\test\remote_control_settings_test.dart`
  - Mobile normalization and migration regression tests.
- Modify: `c:\code\rc\test\remote_connection_bootstrap_test.dart`
  - Startup resolution and bootstrap regression tests.
- Modify: `c:\code\rc\integration_test\real_remote_visibility_e2e_test.dart`
  - Public-endpoint real-device visibility and sync validation.
- Modify: `c:\code\mc\scripts\real_desktop_remote_e2e_runtime.py`
  - Real-device desktop runtime harness for public endpoint validation.

### Task 1: Harden Desktop Fixed-Domain Public Readiness

**Files:**
- Modify: `c:\code\mc\tests\test_main_unit.py`
- Modify: `c:\code\mc\main.py`

- [ ] **Step 1: Write the failing desktop public-readiness tests**

```python
def test_fixed_domain_remote_runtime_url_stays_empty_when_public_probe_is_not_ready(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_DOMAIN", "rc.tingyou.cc")
    monkeypatch.setattr(frame, "_ensure_cloudflared_origin_bridge", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(
        frame,
        "_ensure_remote_nats_startup_connectivity",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("public probe failed")),
    )

    with pytest.raises(RuntimeError, match="public probe failed"):
        frame._start_remote_nats_runtime_if_configured(ensure_connectivity=True)

    assert frame.remote_control_runtime_status["public_ws_ready"] is False
    assert frame.remote_control_runtime_url == ""


def test_fixed_domain_remote_runtime_url_is_published_only_after_public_probe(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_DOMAIN", "rc.tingyou.cc")
    monkeypatch.setattr(frame, "_ensure_cloudflared_origin_bridge", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)

    def _fake_connectivity(**_kwargs):
        frame.remote_control_runtime_status["public_ws_ready"] = True

    monkeypatch.setattr(frame, "_ensure_remote_nats_startup_connectivity", _fake_connectivity)

    frame._start_remote_nats_runtime_if_configured(ensure_connectivity=True)

    assert frame.remote_control_runtime_status["public_ws_ready"] is True
    assert frame.remote_control_runtime_url == "wss://rc.tingyou.cc/nats?token=secret"
```

- [ ] **Step 2: Run the desktop tests to verify the new assertions fail before the code change**

Run: `pytest tests/test_main_unit.py -q -k "fixed_domain_remote_runtime_url_stays_empty_when_public_probe_is_not_ready or fixed_domain_remote_runtime_url_is_published_only_after_public_probe"`

Expected: FAIL because the current startup path still allows partial success states or does not enforce the new invariant exactly.

- [ ] **Step 3: Implement the minimal desktop fixed-domain readiness changes**

```python
def _start_remote_nats_runtime_if_configured(self, *, ensure_connectivity: bool = False) -> None:
    token = self._read_remote_control_token() or self.remote_control_token
    if not token or getattr(self, "_remote_nats_transport", None) is not None:
        return
    runtime = self._remote_runtime_config()
    published_url = f"{runtime['published_base']}?token={token}"
    self._set_remote_runtime_status(
        local_listener_ready=False,
        public_ws_ready=False,
        last_remote_error="",
        published_url=published_url,
    )
    self.remote_control_runtime_url = ""
    # existing runtime startup continues here
    if ensure_connectivity and runtime["fixed_domain_mode"]:
        self._ensure_remote_nats_startup_connectivity(token=token, published_url=published_url)
        if not self.remote_control_runtime_status.get("public_ws_ready"):
            raise RuntimeError("public probe failed")
        self._set_remote_runtime_status(
            local_listener_ready=True,
            public_ws_ready=True,
            last_remote_error="",
            published_url=published_url,
        )
    else:
        self._set_remote_runtime_status(
            local_listener_ready=True,
            public_ws_ready=not runtime["fixed_domain_mode"],
            last_remote_error="",
            published_url=published_url,
        )
```

- [ ] **Step 4: Run the focused desktop tests to verify they pass**

Run: `pytest tests/test_main_unit.py -q -k "fixed_domain_remote_runtime_url_stays_empty_when_public_probe_is_not_ready or fixed_domain_remote_runtime_url_is_published_only_after_public_probe"`

Expected: PASS

- [ ] **Step 5: Run the broader desktop remote startup suite**

Run: `pytest tests/test_main_unit.py tests/test_main_remote_nats_unit.py -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main_unit.py tests/test_main_remote_nats_unit.py
git commit -m "fix: require public readiness for fixed-domain remote startup"
```

### Task 2: Converge Mobile Startup to the Fixed Public Endpoint

**Files:**
- Modify: `c:\code\rc\test\remote_control_settings_test.dart`
- Modify: `c:\code\rc\test\remote_connection_bootstrap_test.dart`
- Modify: `c:\code\rc\lib\remote_control_settings.dart`
- Modify: `c:\code\rc\lib\main.dart`

- [ ] **Step 1: Write the failing mobile settings migration tests**

```dart
test('loopback endpoint is migrated to fixed public endpoint on load', () async {
  SharedPreferences.setMockInitialValues(<String, Object>{
    'remote_control_endpoint': 'ws://127.0.0.1:18081/nats',
    'remote_control_token': 'token',
  });

  final RemoteControlSettings settings = await RemoteControlSettingsStore().load();

  expect(settings.endpoint, RemoteControlSettings.defaultEndpoint);
  expect(settings.token, RemoteControlSettings.defaultToken);
});

test('startup remote settings prefer fixed public config when persisted settings are loopback based', () async {
  final RemoteControlSettings resolved = await resolveStartupRemoteControlSettings(
    _FakeBootstrapChatService(
      const RemoteControlSettings(
        endpoint: 'ws://127.0.0.1:18081/nats',
        backupEndpoint: '',
        token: 'token',
        authMode: RemoteAuthMode.queryToken,
      ),
    ),
  );

  expect(resolved.endpoint, RemoteControlSettings.defaultEndpoint);
  expect(resolved.token, RemoteControlSettings.defaultToken);
});
```

- [ ] **Step 2: Run the focused mobile tests to verify they fail before implementation**

Run: `flutter test test/remote_control_settings_test.dart test/remote_connection_bootstrap_test.dart --plain-name "loopback endpoint is migrated to fixed public endpoint on load|startup remote settings prefer fixed public config when persisted settings are loopback based"`

Expected: FAIL because loopback settings still survive normal startup resolution.

- [ ] **Step 3: Implement the minimal mobile settings convergence**

```dart
static bool shouldForceFixedDomainDefaults(String endpoint) {
  final String normalized = normalizeEndpoint(endpoint);
  if (normalized.isEmpty) {
    return true;
  }
  if (normalized.startsWith('ws://127.0.0.1:') || normalized.startsWith('ws://localhost:')) {
    return true;
  }
  if (legacyQuickTunnelEndpoints.contains(endpoint.trim())) {
    return true;
  }
  return false;
}

Future<RemoteControlSettings> resolveStartupRemoteControlSettings(
  CodexChatService service,
) async {
  try {
    final RemoteControlSettings settings = (await service.loadSettings()).copyWith();
    if (RemoteControlSettings.shouldForceFixedDomainDefaults(settings.endpoint)) {
      return RemoteControlSettings.empty;
    }
    if (settings.isComplete || settings.connectionEndpoints.isNotEmpty) {
      return settings;
    }
  } catch (_) {}
  return RemoteControlSettings.empty;
}
```

- [ ] **Step 4: Run the focused mobile tests to verify they pass**

Run: `flutter test test/remote_control_settings_test.dart test/remote_connection_bootstrap_test.dart --plain-name "loopback endpoint is migrated to fixed public endpoint on load|startup remote settings prefer fixed public config when persisted settings are loopback based"`

Expected: PASS

- [ ] **Step 5: Run the broader mobile startup test suites**

Run: `flutter test test/remote_control_settings_test.dart test/remote_connection_bootstrap_test.dart`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add lib/remote_control_settings.dart lib/main.dart test/remote_control_settings_test.dart test/remote_connection_bootstrap_test.dart
git commit -m "fix: force mobile startup to fixed public remote settings"
```

### Task 3: Upgrade the Public-End-Only Real-Device Harness and Integration Test

**Files:**
- Modify: `c:\code\mc\scripts\real_desktop_remote_e2e_runtime.py`
- Modify: `c:\code\rc\integration_test\real_remote_visibility_e2e_test.dart`

- [ ] **Step 1: Write the failing harness and integration expectations**

```dart
const String _endpoint = String.fromEnvironment('REAL_REMOTE_E2E_ENDPOINT');

testWidgets('real desktop and mobile can see each other notes and chats', (WidgetTester tester) async {
  expect(_endpoint, startsWith('wss://rc.tingyou.cc/nats'));
  // existing visibility assertions remain
});
```

```python
ready_payload = {
    "endpoint": str(runtime_status.get("published_url") or ""),
    "token": token,
}
assert ready_payload["endpoint"].startswith("wss://rc.tingyou.cc/nats")
```

- [ ] **Step 2: Run the desktop harness script in dry verification mode and the integration test locally to capture the failure**

Run: `c:\code\mc\.venv\Scripts\python.exe scripts/real_desktop_remote_e2e_runtime.py`

Expected: FAIL or emit a local `ws://127.0.0.1:*` endpoint rather than the fixed public endpoint.

- [ ] **Step 3: Implement the minimal public-endpoint harness behavior**

```python
runtime_status = dict(getattr(frame, "remote_control_runtime_status", {}) or {})
published_url = str(runtime_status.get("published_url") or "").strip()
if not published_url:
    raise RuntimeError(runtime_status.get("last_remote_error") or "desktop public endpoint did not become ready")

ready_payload = {
    "endpoint": published_url,
    "token": token,
    "desktop_chat_title": desktop_chat_title,
    "desktop_note_title": desktop_note_title,
    "desktop_note_body": desktop_note_body,
    "mobile_chat_title": mobile_chat_title,
    "mobile_note_title": mobile_note_title,
    "mobile_note_body": mobile_note_body,
}
```

- [ ] **Step 4: Run the desktop harness self-check again**

Run: `c:\code\mc\.venv\Scripts\python.exe scripts/real_desktop_remote_e2e_runtime.py`

Expected: Ready payload contains `wss://rc.tingyou.cc/nats?...` instead of a loopback websocket URL.

- [ ] **Step 5: Commit**

```bash
git add scripts/real_desktop_remote_e2e_runtime.py integration_test/real_remote_visibility_e2e_test.dart
git commit -m "test: switch real-device visibility flow to public endpoint"
```

### Task 4: Strict Public-Endpoint Real-Device Verification

**Files:**
- Modify: `c:\code\mc\scripts\real_desktop_remote_e2e_runtime.py` if verification reveals missing harness assertions
- Modify: `c:\code\rc\integration_test\real_remote_visibility_e2e_test.dart` if verification reveals missing public-state assertions

- [ ] **Step 1: Start the desktop app or desktop runtime harness with fixed-domain startup enabled**

Run: `c:\code\mc\.venv\Scripts\python.exe scripts/real_desktop_remote_e2e_runtime.py`

Expected: The ready file contains an endpoint beginning with `wss://rc.tingyou.cc/nats`.

- [ ] **Step 2: Run the mobile integration test on the real phone without `adb reverse`**

Run: `flutter drive --driver=test_driver/integration_test.dart --target=integration_test/real_remote_visibility_e2e_test.dart -d 93206cc7 --dart-define=REAL_REMOTE_E2E_ENDPOINT=wss://rc.tingyou.cc/nats --dart-define=REAL_REMOTE_E2E_TOKEN=h9k2m7p4q8x1z6v3t5n9c2r7d4s8j1f6 --dart-define=REAL_REMOTE_E2E_DESKTOP_CHAT_TITLE=<desktop title> --dart-define=REAL_REMOTE_E2E_DESKTOP_NOTE_TITLE=<desktop note title> --dart-define=REAL_REMOTE_E2E_DESKTOP_NOTE_BODY=<desktop note body> --dart-define=REAL_REMOTE_E2E_MOBILE_CHAT_TITLE=<mobile chat title> --dart-define=REAL_REMOTE_E2E_MOBILE_NOTE_TITLE=<mobile note title> --dart-define=REAL_REMOTE_E2E_MOBILE_NOTE_BODY=<mobile note body>`

Expected: PASS with no USB port reversal involved in the business connection.

- [ ] **Step 3: Verify the desktop result file confirms mobile-created content is visible on desktop**

Run: `Get-Content c:\code\mc\.tmp-real-e2e\<run-id>\result.json`

Expected:

```json
{
  "mobile_chat_visible_on_desktop": true,
  "mobile_note_visible_on_desktop": true
}
```

- [ ] **Step 4: Run the desktop and mobile regression suites after the real-device pass**

Run: `pytest tests/test_main_unit.py tests/test_main_remote_nats_unit.py -q`

Expected: PASS

Run: `flutter test test/remote_control_settings_test.dart test/remote_connection_bootstrap_test.dart`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main_unit.py tests/test_main_remote_nats_unit.py lib/main.dart lib/remote_control_settings.dart test/remote_control_settings_test.dart test/remote_connection_bootstrap_test.dart integration_test/real_remote_visibility_e2e_test.dart scripts/real_desktop_remote_e2e_runtime.py
git commit -m "feat: enable fixed-domain mobile auto-connect without usb"
```

## Self-Review

### Spec coverage

- Desktop public readiness hardening is covered by Task 1.
- Mobile startup convergence and stale-setting migration are covered by Task 2.
- Public-endpoint real-device harness changes are covered by Task 3.
- Strict real-device public verification is covered by Task 4.

No spec section is left without a task.

### Placeholder scan

- No `TBD`, `TODO`, or deferred placeholders remain.
- Every code-changing task includes an explicit code block.
- Every validation step includes an explicit command and expected result.

### Type consistency

- Desktop status fields use existing names: `public_ws_ready`, `published_url`, `last_remote_error`, `remote_control_runtime_url`.
- Mobile settings use existing names: `RemoteControlSettings.defaultEndpoint`, `RemoteControlSettings.defaultToken`, `resolveStartupRemoteControlSettings`.
- Real-device test names and file paths match the current repository layout.
