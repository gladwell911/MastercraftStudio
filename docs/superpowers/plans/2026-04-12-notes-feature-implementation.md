# Notes Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build desktop and mobile notes with offline-first local storage, desktop-hosted cross-device sync, conflict-copy preservation, import, voice capture, and UI state restoration.

**Architecture:** Desktop (`c:\code\codex1`) owns a local SQLite notes store, wxPython notes UI state machine, and notes sync API layered onto the existing remote control transport. Mobile (`c:\code\rc`) adds a local SQLite-backed notes repository, notes pages under the existing bottom tab shell, and a sync service that pushes outbox mutations and pulls server changes over the existing remote socket. Conflict resolution is server-mediated and preserves both versions by creating conflict copies instead of overwriting data.

**Tech Stack:** Python 3 + sqlite3 + wxPython + pytest on desktop; Flutter + Dart + sqflite + path + flutter_test on mobile.

---

### Task 1: Desktop Notes Data Layer

**Files:**
- Create: `c:\code\codex1\notes_models.py`
- Create: `c:\code\codex1\notes_store.py`
- Create: `c:\code\codex1\tests\test_notes_store_unit.py`
- Modify: `c:\code\codex1\main.py`

- [ ] **Step 1: Write the failing desktop notes store tests**

```python
from pathlib import Path

from notes_store import NotesStore


def test_notes_store_creates_schema_and_round_trips_notebook(tmp_path: Path):
    store = NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()

    notebook = store.create_notebook("收件箱")
    fetched = store.get_notebook(notebook.id)

    assert fetched is not None
    assert fetched.title == "收件箱"
    assert fetched.version == 1


def test_notes_store_creates_entries_and_imports_lines(tmp_path: Path):
    store = NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    notebook = store.create_notebook("导入测试")

    created = store.import_entries(
        notebook_id=notebook.id,
        lines=["第一行", "", "第二行"],
        source="import_clipboard",
    )

    assert [entry.content for entry in created] == ["第一行", "第二行"]
    assert all(entry.source == "import_clipboard" for entry in created)


def test_notes_store_preserves_soft_delete_and_outbox(tmp_path: Path):
    store = NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    notebook = store.create_notebook("待删除")

    store.delete_notebook(notebook.id)

    assert store.get_notebook(notebook.id, include_deleted=True) is not None
    pending = store.list_pending_ops(limit=10)
    assert pending[-1].action == "delete"
    assert pending[-1].entity_type == "notebook"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_notes_store_unit.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'notes_store'`

- [ ] **Step 3: Add focused notes models**

```python
from dataclasses import dataclass


@dataclass(slots=True)
class Notebook:
    id: str
    title: str
    created_at: str
    updated_at: str
    deleted_at: str | None
    pinned: bool
    sort_order: int
    version: int
    device_id: str
    last_modified_by: str
    is_conflict_copy: bool
    origin_notebook_id: str | None = None


@dataclass(slots=True)
class NoteEntry:
    id: str
    notebook_id: str
    content: str
    created_at: str
    updated_at: str
    deleted_at: str | None
    pinned: bool
    sort_order: int
    version: int
    device_id: str
    last_modified_by: str
    is_conflict_copy: bool
    source: str
    origin_entry_id: str | None = None


@dataclass(slots=True)
class SyncOp:
    op_id: str
    entity_type: str
    entity_id: str
    action: str
    payload_json: str
    base_version: int
    created_at: str
    retry_count: int
    status: str
```

- [ ] **Step 4: Implement the desktop SQLite store and schema**

```python
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from notes_models import NoteEntry, Notebook, SyncOp


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NotesStore:
    def __init__(self, db_path: Path, device_id: str) -> None:
        self.db_path = Path(db_path)
        self.device_id = device_id

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS notebooks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    device_id TEXT NOT NULL,
                    last_modified_by TEXT NOT NULL,
                    is_conflict_copy INTEGER NOT NULL DEFAULT 0,
                    origin_notebook_id TEXT
                );
                CREATE TABLE IF NOT EXISTS note_entries (
                    id TEXT PRIMARY KEY,
                    notebook_id TEXT NOT NULL REFERENCES notebooks(id),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    device_id TEXT NOT NULL,
                    last_modified_by TEXT NOT NULL,
                    is_conflict_copy INTEGER NOT NULL DEFAULT 0,
                    origin_entry_id TEXT,
                    source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sync_outbox (
                    op_id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    base_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL
                );
                """
            )

    def create_notebook(self, title: str) -> Notebook:
        now = _utc_now()
        notebook = Notebook(
            id=str(uuid.uuid4()),
            title=title.strip() or "未命名笔记",
            created_at=now,
            updated_at=now,
            deleted_at=None,
            pinned=False,
            sort_order=int(datetime.now(timezone.utc).timestamp() * 1000),
            version=1,
            device_id=self.device_id,
            last_modified_by="desktop",
            is_conflict_copy=False,
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO notebooks (
                    id, title, created_at, updated_at, deleted_at, pinned,
                    sort_order, version, device_id, last_modified_by,
                    is_conflict_copy, origin_notebook_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notebook.id,
                    notebook.title,
                    notebook.created_at,
                    notebook.updated_at,
                    notebook.deleted_at,
                    int(notebook.pinned),
                    notebook.sort_order,
                    notebook.version,
                    notebook.device_id,
                    notebook.last_modified_by,
                    int(notebook.is_conflict_copy),
                    notebook.origin_notebook_id,
                ),
            )
            self._append_outbox(conn, "notebook", notebook.id, "create", notebook.version, {"id": notebook.id})
        return notebook
```

- [ ] **Step 5: Wire the store path into desktop startup**

```python
self.notes_db_path = self.app_data_dir / "notes.db"
self.notes_device_id = f"desktop-{platform.node().strip().lower() or 'local'}"
self.notes_store = NotesStore(self.notes_db_path, device_id=self.notes_device_id)
self.notes_store.initialize()
```

- [ ] **Step 6: Run the desktop notes store tests until they pass**

Run: `python -m pytest tests/test_notes_store_unit.py -q`

Expected: PASS

- [ ] **Step 7: Commit the data layer**

```bash
git add notes_models.py notes_store.py tests/test_notes_store_unit.py main.py
git commit -m "feat: add desktop notes data layer"
```

### Task 2: Desktop Notes UI, Import, and State Restore

**Files:**
- Create: `c:\code\codex1\notes_ui.py`
- Create: `c:\code\codex1\notes_import.py`
- Modify: `c:\code\codex1\main.py`
- Modify: `c:\code\codex1\tests\test_main_unit.py`

- [ ] **Step 1: Add failing UI state and import tests**

```python
def test_load_state_restores_notes_editor_draft(chat_frame, tmp_path):
    chat_frame._current_notes_state = {}
    chat_frame.state_path.write_text(
        json.dumps(
            {
                "notes_ui_state": {
                    "active_root_tab": "notes",
                    "notes_view": "note_edit",
                    "active_notebook_id": "nb-1",
                    "active_entry_id": "entry-1",
                    "entry_editor_draft": "未保存草稿",
                    "entry_editor_dirty": True,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    chat_frame._load_state()

    assert chat_frame._current_notes_state["notes_view"] == "note_edit"
    assert chat_frame._current_notes_state["entry_editor_draft"] == "未保存草稿"


def test_import_lines_skips_blank_rows(notes_store, tmp_path):
    file_path = tmp_path / "notes.txt"
    file_path.write_text("甲\n\n乙\n", encoding="utf-8")
    notebook = notes_store.create_notebook("导入")

    created = import_note_entries_from_file(notes_store, notebook.id, file_path)

    assert [item.content for item in created] == ["甲", "乙"]
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `python -m pytest tests/test_main_unit.py -k notes -q`

Expected: FAIL with `AttributeError` or `NameError` because the notes UI state and import helpers have not been added

- [ ] **Step 3: Add the desktop notes UI controller**

```python
class DesktopNotesController:
    def __init__(self, frame, store):
        self.frame = frame
        self.store = store
        self.root_tab = "chat"
        self.notes_view = "notes_list"
        self.active_notebook_id = ""
        self.active_entry_id = ""
        self.entry_editor_dirty = False

    def to_state_dict(self) -> dict:
        return {
            "active_root_tab": self.root_tab,
            "notes_view": self.notes_view,
            "active_notebook_id": self.active_notebook_id,
            "active_entry_id": self.active_entry_id,
            "entry_editor_draft": self.frame.notes_editor.GetValue() if getattr(self.frame, "notes_editor", None) else "",
            "entry_editor_dirty": self.entry_editor_dirty,
        }

    def restore_state(self, state: dict) -> None:
        self.root_tab = str(state.get("active_root_tab") or "chat")
        self.notes_view = str(state.get("notes_view") or "notes_list")
        self.active_notebook_id = str(state.get("active_notebook_id") or "")
        self.active_entry_id = str(state.get("active_entry_id") or "")
        self.entry_editor_dirty = bool(state.get("entry_editor_dirty", False))
```

- [ ] **Step 4: Implement import helpers and menu actions**

```python
from pathlib import Path


def import_note_entries_from_file(store, notebook_id: str, file_path: Path):
    raw = ""
    for encoding in ("utf-8", "gbk", "utf-16"):
        try:
            raw = file_path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    return store.import_entries(notebook_id=notebook_id, lines=lines, source="import_file")


def import_note_entries_from_clipboard(store, notebook_id: str, text: str):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return store.import_entries(notebook_id=notebook_id, lines=lines, source="import_clipboard")
```

- [ ] **Step 5: Integrate notes widgets, menus, keyboard flow, and state persistence into `ChatFrame`**

```python
self.notes_controller = DesktopNotesController(self, self.notes_store)
self.notes_list = wx.ListBox(panel, style=wx.LB_SINGLE)
self.note_entry_list = wx.ListBox(panel, style=wx.LB_SINGLE)
self.notes_editor = wx.TextCtrl(panel, style=wx.TE_MULTILINE)

self.notes_list.Bind(wx.EVT_KEY_DOWN, self._on_notes_list_key_down)
self.note_entry_list.Bind(wx.EVT_KEY_DOWN, self._on_note_entry_key_down)
self.notes_editor.Bind(wx.EVT_KEY_DOWN, self._on_notes_editor_key_down)

data["notes_ui_state"] = self.notes_controller.to_state_dict()
```

- [ ] **Step 6: Run the desktop unit tests covering notes state and import**

Run: `python -m pytest tests/test_notes_store_unit.py tests/test_main_unit.py -k "notes or import" -q`

Expected: PASS

- [ ] **Step 7: Commit the desktop notes UI**

```bash
git add notes_ui.py notes_import.py main.py tests/test_main_unit.py
git commit -m "feat: add desktop notes UI and restore state"
```

### Task 3: Desktop Notes Sync API and Conflict Copy Logic

**Files:**
- Create: `c:\code\codex1\notes_sync.py`
- Modify: `c:\code\codex1\main.py`
- Modify: `c:\code\codex1\tests\test_remote_ws_unit.py`
- Modify: `c:\code\codex1\tests\test_remote_http_unit.py`

- [ ] **Step 1: Add failing remote sync tests**

```python
def test_notes_pull_since_returns_incremental_changes(remote_client):
    status, body = remote_client.frame._remote_api_notes_pull_since({"cursor": "0"})

    assert status == 200
    assert "cursor" in body
    assert "ops" in body


def test_notes_conflict_creates_conflict_copy(frame_with_notes_store):
    notebook = frame_with_notes_store.notes_store.create_notebook("冲突笔记")
    entry = frame_with_notes_store.notes_store.create_entry(notebook.id, "桌面版本")

    frame_with_notes_store.notes_sync.apply_remote_ops(
        [
            {
                "entity_type": "entry",
                "action": "update",
                "entity_id": entry.id,
                "base_version": 1,
                "payload": {"content": "手机版本", "updated_at": "2026-04-12T00:00:00+00:00"},
            }
        ]
    )

    entries = frame_with_notes_store.notes_store.list_entries(notebook.id, include_deleted=True)
    assert len(entries) == 2
    assert any(item.is_conflict_copy for item in entries)
```

- [ ] **Step 2: Run the remote notes tests to verify they fail**

Run: `python -m pytest tests/test_remote_ws_unit.py tests/test_remote_http_unit.py -k notes -q`

Expected: FAIL because notes API handlers and conflict logic are missing

- [ ] **Step 3: Add a desktop notes sync service**

```python
class DesktopNotesSyncService:
    def __init__(self, store, broadcaster) -> None:
        self.store = store
        self._broadcaster = broadcaster

    def snapshot(self) -> dict:
        return {
            "notebooks": [item.__dict__ for item in self.store.list_notebooks(include_deleted=True)],
            "entries": [item.__dict__ for item in self.store.list_all_entries(include_deleted=True)],
            "cursor": self.store.current_cursor(),
        }

    def pull_since(self, cursor: str) -> dict:
        ops, next_cursor = self.store.list_ops_since(cursor)
        return {"ops": ops, "cursor": next_cursor}

    def apply_remote_ops(self, ops: list[dict]) -> dict:
        applied = []
        conflicts = []
        for op in ops:
            result = self.store.apply_remote_op(op)
            applied.append(result.applied)
            conflicts.extend(result.conflicts)
        return {"applied": applied, "conflicts": conflicts, "cursor": self.store.current_cursor()}
```

- [ ] **Step 4: Extend the existing remote control handlers in `main.py`**

```python
def _remote_api_notes_snapshot(self, _payload: dict | None = None) -> tuple[int, dict]:
    return 200, self.notes_sync.snapshot()


def _remote_api_notes_pull_since(self, payload: dict | None = None) -> tuple[int, dict]:
    payload = payload or {}
    return 200, self.notes_sync.pull_since(str(payload.get("cursor") or "0"))


def _remote_api_notes_push_ops(self, payload: dict | None = None) -> tuple[int, dict]:
    payload = payload or {}
    result = self.notes_sync.apply_remote_ops(list(payload.get("ops") or []))
    self._broadcast_notes_changed(result)
    return 200, result
```

- [ ] **Step 5: Register notes events on the WebSocket server**

```python
self._remote_ws_server = RemoteWSServer(
    host=runtime["bind_host"],
    port=runtime["bind_port"],
    token=runtime["token"],
    on_state=self._remote_api_state_ui,
    on_history_list=self._remote_api_history_list_ui,
    on_history_read=self._remote_api_history_read_ui,
    on_notes_snapshot=self._remote_api_notes_snapshot,
    on_notes_pull_since=self._remote_api_notes_pull_since,
    on_notes_push_ops=self._remote_api_notes_push_ops,
)
```

- [ ] **Step 6: Run the remote sync tests**

Run: `python -m pytest tests/test_remote_ws_unit.py tests/test_remote_http_unit.py -k notes -q`

Expected: PASS

- [ ] **Step 7: Commit the desktop sync layer**

```bash
git add notes_sync.py main.py tests/test_remote_ws_unit.py tests/test_remote_http_unit.py
git commit -m "feat: add desktop notes sync endpoints"
```

### Task 4: Mobile Notes Data Layer and Local Repository

**Files:**
- Modify: `c:\code\rc\pubspec.yaml`
- Create: `c:\code\rc\lib\notes_models.dart`
- Create: `c:\code\rc\lib\notes_store.dart`
- Create: `c:\code\rc\lib\notes_repository.dart`
- Create: `c:\code\rc\test\notes_store_test.dart`
- Create: `c:\code\rc\test\notes_repository_test.dart`

- [ ] **Step 1: Add failing mobile notes data tests**

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:zhuge_qa/notes_models.dart';

void main() {
  test('NotebookRecord round-trips json', () {
    final NotebookRecord notebook = NotebookRecord(
      id: 'nb-1',
      title: '移动端',
      createdAt: DateTime.utc(2026, 4, 12),
      updatedAt: DateTime.utc(2026, 4, 12),
      version: 1,
      deviceId: 'mobile-test',
      lastModifiedBy: 'mobile',
    );

    final Map<String, dynamic> json = notebook.toJson();
    expect(NotebookRecord.fromJson(json).title, '移动端');
  });
}
```

- [ ] **Step 2: Run the mobile tests to verify they fail**

Run: `flutter test test/notes_store_test.dart test/notes_repository_test.dart`

Expected: FAIL because the notes files and dependencies do not exist

- [ ] **Step 3: Add the SQLite dependencies**

```yaml
dependencies:
  flutter:
    sdk: flutter
  sqflite: ^2.4.1
  path: ^1.9.1
  path_provider: ^2.1.5
```

- [ ] **Step 4: Add mobile notes models and store**

```dart
class NotebookRecord {
  const NotebookRecord({
    required this.id,
    required this.title,
    required this.createdAt,
    required this.updatedAt,
    required this.version,
    required this.deviceId,
    required this.lastModifiedBy,
    this.deletedAt,
    this.pinned = false,
    this.sortOrder = 0,
    this.isConflictCopy = false,
    this.originNotebookId,
  });

  final String id;
  final String title;
  final DateTime createdAt;
  final DateTime updatedAt;
  final int version;
  final String deviceId;
  final String lastModifiedBy;
}

class NotesStore {
  Future<void> open() async {
    final Directory dir = await getApplicationDocumentsDirectory();
    final String dbPath = p.join(dir.path, 'notes.db');
    _db = await openDatabase(
      dbPath,
      version: 1,
      onCreate: (Database db, int version) async {
        await db.execute('CREATE TABLE notebooks (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, pinned INTEGER NOT NULL, sort_order INTEGER NOT NULL, version INTEGER NOT NULL, device_id TEXT NOT NULL, last_modified_by TEXT NOT NULL, is_conflict_copy INTEGER NOT NULL, origin_notebook_id TEXT)');
        await db.execute('CREATE TABLE note_entries (id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, pinned INTEGER NOT NULL, sort_order INTEGER NOT NULL, version INTEGER NOT NULL, device_id TEXT NOT NULL, last_modified_by TEXT NOT NULL, is_conflict_copy INTEGER NOT NULL, origin_entry_id TEXT, source TEXT NOT NULL)');
        await db.execute('CREATE TABLE sync_outbox (op_id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, action TEXT NOT NULL, payload_json TEXT NOT NULL, base_version INTEGER NOT NULL, created_at TEXT NOT NULL, retry_count INTEGER NOT NULL, status TEXT NOT NULL)');
      },
    );
  }

  Future<NotebookRecord> createNotebook(String title) async {
    final DateTime now = DateTime.now().toUtc();
    final NotebookRecord notebook = NotebookRecord(
      id: const Uuid().v4(),
      title: title.trim().isEmpty ? '未命名笔记' : title.trim(),
      createdAt: now,
      updatedAt: now,
      version: 1,
      deviceId: _deviceId,
      lastModifiedBy: 'mobile',
    );
    await _db.insert('notebooks', notebook.toSqlMap());
    return notebook;
  }

  Future<List<NotebookRecord>> listNotebooks() async {
    final List<Map<String, Object?>> rows = await _db.query(
      'notebooks',
      where: 'deleted_at IS NULL',
      orderBy: 'pinned DESC, sort_order DESC, updated_at DESC',
    );
    return rows.map(NotebookRecord.fromSqlMap).toList();
  }

  Future<void> enqueueSyncOp(NotesSyncOp op) async {
    await _db.insert('sync_outbox', op.toSqlMap());
  }
}
```

- [ ] **Step 5: Add a repository that hides store details from the UI**

```dart
class NotesRepository {
  NotesRepository({
    required this.store,
    required this.deviceId,
  });

  final NotesStore store;
  final String deviceId;

  Future<NotebookRecord> createNotebook(String title) async {
    return store.createNotebook(title.trim().isEmpty ? '未命名笔记' : title.trim());
  }

  Future<NoteEntryRecord> addEntry({
    required String notebookId,
    required String content,
    required String source,
  }) async {
    return store.createEntry(notebookId: notebookId, content: content, source: source);
  }
}
```

- [ ] **Step 6: Run the mobile data tests**

Run: `flutter test test/notes_store_test.dart test/notes_repository_test.dart`

Expected: PASS

- [ ] **Step 7: Commit the mobile data layer**

```bash
git -C c:\code\rc add pubspec.yaml lib/notes_models.dart lib/notes_store.dart lib/notes_repository.dart test/notes_store_test.dart test/notes_repository_test.dart
git -C c:\code\rc commit -m "feat: add mobile notes data layer"
```

### Task 5: Mobile Notes Pages, Selection, Editor, and Voice Input

**Files:**
- Create: `c:\code\rc\lib\notes_voice_service.dart`
- Create: `c:\code\rc\lib\notes_list_page.dart`
- Create: `c:\code\rc\lib\note_detail_page.dart`
- Create: `c:\code\rc\lib\note_entry_editor_page.dart`
- Modify: `c:\code\rc\lib\main.dart`
- Create: `c:\code\rc\test\notes_pages_test.dart`

- [ ] **Step 1: Add failing widget tests for the notes pages**

```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:zhuge_qa/notes_list_page.dart';

void main() {
  testWidgets('notes tab shows create button and opens notebook', (WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: NotesListPage(
          notebooks: const [],
          onCreate: () {},
          onOpen: (_) {},
          onDeleteSelected: (_) {},
        ),
      ),
    );

    expect(find.text('新建笔记'), findsOneWidget);
  });
}
```

- [ ] **Step 2: Run the notes widget tests to verify they fail**

Run: `flutter test test/notes_pages_test.dart`

Expected: FAIL with `Target of URI doesn't exist` for the new notes page imports

- [ ] **Step 3: Add a notes voice polishing service**

```dart
class NotesVoiceService {
  NotesVoiceService({required this.chatClient});

  final ChatCompletionClient chatClient;

  Future<String> polish(String transcript) async {
    final String prompt = '''
请整理下面的笔记口述文本：
1. 保留原意，不补充事实
2. 去掉明显口头禅
3. 补充基本标点
4. 输出纯文本

$transcript
''';
    final String raw = await chatClient.completeConversation(
      systemPrompt: '你负责整理中文口述笔记。',
      messages: <ChatMessage>[ChatMessage(role: ChatRole.user, text: prompt)],
      model: 'doubao-seed-2-0-mini-260215',
    );
    return raw.trim().isEmpty ? transcript.trim() : raw.trim();
  }
}
```

- [ ] **Step 4: Add the notes list, detail, and editor pages**

```dart
enum RootTab { chat, notes, message, settings }

class NotesListPage extends StatelessWidget {
  const NotesListPage({
    super.key,
    required this.notebooks,
    required this.onCreate,
    required this.onOpen,
    required this.onDeleteSelected,
  });

  final List<NotebookRecord> notebooks;
  final VoidCallback onCreate;
  final ValueChanged<NotebookRecord> onOpen;
  final ValueChanged<Set<String>> onDeleteSelected;
}

class NoteDetailPage extends StatelessWidget {
  const NoteDetailPage({
    super.key,
    required this.notebook,
    required this.entries,
    required this.onCreateTextEntry,
    required this.onCreateVoiceEntry,
  });

  final NotebookRecord notebook;
  final List<NoteEntryRecord> entries;
  final ValueChanged<String> onCreateTextEntry;
  final Future<void> Function() onCreateVoiceEntry;
}

class NoteEntryEditorPage extends StatefulWidget {
  const NoteEntryEditorPage({
    super.key,
    required this.entry,
    required this.onSave,
  });

  final NoteEntryRecord entry;
  final ValueChanged<String> onSave;
}
```

- [ ] **Step 5: Integrate the pages into `main.dart` and replace the auto-task tab**

```dart
final List<Widget> pages = <Widget>[
  ChatListPage(
    key: ValueKey<int>(chatListRefreshVersion),
    sessions: sessions,
    selectedIds: selectedIds,
    selectMode: selectMode,
    remoteStore: codexChatService.store,
    onCreate: () => openSession(),
    onOpen: openSession,
    onRename: _renameSession,
    onEnterSelection: () => setState(() {
      selectMode = true;
      selectedIds.clear();
    }),
    onToggleSelected: (ChatSession session) {},
    onTogglePinned: (ChatSession session) {},
    onDeleteOne: _deleteSingleSession,
    onSelectAll: () {},
    onDeleteSelected: deleteSelected,
    onClearAll: () => confirmClear(keepPinned: false),
    onClearUnpinned: () => confirmClear(keepPinned: true),
  ),
  NotesListPage(
    notebooks: notesRepositoryState.notebooks,
    onCreate: _createNotebook,
    onOpen: _openNotebook,
    onDeleteSelected: _deleteSelectedNotebooks,
  ),
  const PlaceholderPage(title: T.message),
  SettingsPage(
    store: remoteControlSettingsStore,
    codexAnswerEnglishFilterEnabled:
        codexChatService.store.settings.codexAnswerEnglishFilterEnabled,
    onAnnounce: (String message) {
      announce(message);
      unawaited(_initializeRemoteConnection());
    },
    onCodexAnswerEnglishFilterChanged: (bool value) async {
      await codexChatService.updateSettings(
        codexAnswerEnglishFilterEnabled: value,
      );
    },
  ),
];

_BottomTabButton(
  icon: Icons.note_alt_outlined,
  label: '笔记',
  selected: tab == RootTab.notes,
  onTap: () => setState(() => tab = RootTab.notes),
),
```

- [ ] **Step 6: Run the notes page tests**

Run: `flutter test test/notes_pages_test.dart`

Expected: PASS

- [ ] **Step 7: Commit the mobile notes UI**

```bash
git -C c:\code\rc add lib/notes_voice_service.dart lib/notes_list_page.dart lib/note_detail_page.dart lib/note_entry_editor_page.dart lib/main.dart test/notes_pages_test.dart
git -C c:\code\rc commit -m "feat: add mobile notes pages and voice input"
```

### Task 6: Mobile Notes Sync Integration and Cross-Device Conflict Coverage

**Files:**
- Create: `c:\code\rc\lib\notes_sync_service.dart`
- Modify: `c:\code\rc\lib\remote_socket_client.dart`
- Modify: `c:\code\rc\lib\main.dart`
- Create: `c:\code\rc\test\notes_sync_service_test.dart`
- Create: `c:\code\rc\test\notes_conflict_resolution_test.dart`

- [ ] **Step 1: Add failing sync tests**

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:zhuge_qa/notes_sync_service.dart';

void main() {
  test('pushPendingOps sends notes_push_ops payload', () async {
    final FakeRemoteSocketClient socketClient = FakeRemoteSocketClient();
    final FakeNotesRepository repository = FakeNotesRepository(
      pendingOps: <Map<String, dynamic>>[
        <String, dynamic>{'entity_type': 'entry', 'action': 'update', 'entity_id': 'entry-1'}
      ],
    );
    final NotesSyncService service = NotesSyncService(
      socketClient: socketClient,
      repository: repository,
    );

    await service.pushPendingOps();

    expect(socketClient.sent.single['type'], 'notes_push_ops');
  });

  test('conflicting remote update creates conflict copy instead of overwrite', () async {
    final FakeRemoteSocketClient socketClient = FakeRemoteSocketClient();
    final FakeNotesRepository repository = FakeNotesRepository.withEntry(
      notebookId: 'nb-1',
      entryId: 'entry-1',
      content: '桌面版本',
      version: 2,
    );
    final NotesSyncService service = NotesSyncService(
      socketClient: socketClient,
      repository: repository,
    );

    await service.applyRemotePayload(<String, dynamic>{
      'conflicts': <Map<String, dynamic>>[
        <String, dynamic>{
          'entity_type': 'entry',
          'entity_id': 'entry-1',
          'notebook_id': 'nb-1',
          'content': '手机版本',
          'base_version': 1,
          'updated_at': '2026-04-12T00:00:00Z',
        }
      ],
    });

    expect(repository.entries.length, 2);
    expect(repository.entries.any((FakeEntry entry) => entry.isConflictCopy), isTrue);
  });
}
```

- [ ] **Step 2: Run the sync tests to verify they fail**

Run: `flutter test test/notes_sync_service_test.dart test/notes_conflict_resolution_test.dart`

Expected: FAIL with `Target of URI doesn't exist` because the sync service has not been created

- [ ] **Step 3: Add the mobile notes sync service**

```dart
class NotesSyncService {
  NotesSyncService({
    required this.socketClient,
    required this.repository,
  });

  final RemoteSocketClient socketClient;
  final NotesRepository repository;

  Future<void> pullSince(String cursor) async {
    await socketClient.send(<String, dynamic>{
      'id': 'notes-pull-${DateTime.now().microsecondsSinceEpoch}',
      'type': 'notes_pull_since',
      'cursor': cursor,
    });
  }

  Future<void> pushPendingOps() async {
    final List<NotesSyncOp> pending = await repository.listPendingOps(limit: 100);
    if (pending.isEmpty) return;
    await socketClient.send(<String, dynamic>{
      'id': 'notes-push-${DateTime.now().microsecondsSinceEpoch}',
      'type': 'notes_push_ops',
      'ops': pending.map((NotesSyncOp op) => op.toJson()).toList(),
    });
  }
}
```

- [ ] **Step 4: Handle notes events from the existing remote socket**

```dart
socketClient.messages.listen((Map<String, dynamic> event) async {
  switch ('${event['type'] ?? ''}') {
    case 'notes_changed':
      await notesSyncService.pullSince(repository.lastSyncCursor);
      break;
    case 'notes_conflict':
      await repository.applyRemotePayload(event['payload'] as Map<String, dynamic>);
      break;
    case 'notes_sync_status':
      notesSyncStatus.value = '${event['status'] ?? ''}';
      break;
  }
});
```

- [ ] **Step 5: Wire sync triggers into app lifecycle and notes mutations**

```dart
@override
void didChangeAppLifecycleState(AppLifecycleState state) {
  if (state == AppLifecycleState.resumed) {
    unawaited(notesSyncService.pullSince(notesRepository.lastSyncCursor));
    unawaited(notesSyncService.pushPendingOps());
  }
}

Future<void> _saveNoteEntry({
  required String entryId,
  required String notebookId,
  required String content,
}) async {
  await notesRepository.updateEntry(
    entryId: entryId,
    notebookId: notebookId,
    content: content,
  );
  unawaited(notesSyncService.pushPendingOps());
}
```

- [ ] **Step 6: Run the sync and conflict tests**

Run: `flutter test test/notes_sync_service_test.dart test/notes_conflict_resolution_test.dart`

Expected: PASS

- [ ] **Step 7: Run the combined verification suites**

Run: `python -m pytest tests/test_notes_store_unit.py tests/test_main_unit.py tests/test_remote_ws_unit.py tests/test_remote_http_unit.py -k notes -q`

Expected: PASS

Run: `flutter test test/notes_store_test.dart test/notes_repository_test.dart test/notes_pages_test.dart test/notes_sync_service_test.dart test/notes_conflict_resolution_test.dart`

Expected: PASS

- [ ] **Step 8: Commit the sync integration**

```bash
git -C c:\code\rc add lib/notes_sync_service.dart lib/main.dart lib/remote_socket_client.dart test/notes_sync_service_test.dart test/notes_conflict_resolution_test.dart
git -C c:\code\rc commit -m "feat: add mobile notes sync"
```
