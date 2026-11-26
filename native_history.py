"""Identity and snapshots shared by native-session discovery adapters."""

from dataclasses import dataclass, field
import hashlib
import json


class NativeTitleRejected(RuntimeError):
    """A provider or identity check explicitly rejected a title update."""


class NativeDeletionRejected(RuntimeError):
    """The selected provider session cannot be safely removed."""


@dataclass(frozen=True)
class NativeSessionIdentity:
    pair_id: str
    provider: str
    storage_id: str
    session_id: str

    @property
    def chat_id(self) -> str:
        return "native-" + hashlib.sha256(json.dumps(
            [self.pair_id, self.provider, self.storage_id, self.session_id],
            ensure_ascii=False, separators=(",", ":"),
        ).encode()).hexdigest()


@dataclass
class NativeSessionSnapshot:
    identity: NativeSessionIdentity
    account: str
    model: str
    cwd: str
    title: str
    updated_at: float = 0
    chat_fields: dict = field(default_factory=dict)
    turns: list | None = None
    execution_steps: list = field(default_factory=list)
    status: str = ""
    legacy_fields: dict = field(default_factory=dict)
    title_observed_at: float = 0
    title_explicit: bool = True


CODEX_STORAGE_FIELDS = ("codex_home", "codex_storage_id", "codex_user_home")


NATIVE_CHAT_FIELDS = (
    "native_identity", "native_account", "native_cwd", "native_title",
    "native_updated_at", "native_replayed_at", "native_history_status", "native_open_command",
    "native_title_revision", "native_title_observed_at", "kimi_storage_id",
)

TITLE_FIELDS = ("title", "title_source", "title_manual", "title_updated_at", "title_revision")


def merge_native_title(chat, provider, title, observed_at, *, explicit=True, app_manual=False):
    """Keep native title evidence and the application display title independently."""
    if not title or observed_at < chat.get("native_title_observed_at", 0):
        return
    if provider != "agy" and not explicit and chat.get("title_manual"):
        return
    previous_native = chat.get("native_title")
    if previous_native != title:
        chat.update(native_title=title, native_title_observed_at=observed_at,
                    native_title_revision=int(chat.get("native_title_revision") or 0) + 1)
    if chat.get("title_manual") and not app_manual and (
            provider == "agy" or not previous_native or previous_native == title):
        return
    if (provider != "agy" and not explicit and chat.get("title_source") not in (None, "", "default", "native")):
        return
    if app_manual or chat.get("title") != title:
        chat.update(title=title, title_source="manual" if app_manual or chat.get("title_manual") else "native",
                    title_manual=app_manual or bool(chat.get("title_manual")),
                    title_updated_at=observed_at, title_revision=int(chat.get("title_revision") or 0) + 1,
                    native_title_observed_at=observed_at)
