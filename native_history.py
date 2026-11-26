"""Identity and snapshots shared by native-session discovery adapters."""

from dataclasses import dataclass, field
import hashlib
import json


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


NATIVE_CHAT_FIELDS = (
    "native_identity", "native_account", "native_cwd", "native_title",
    "native_updated_at", "native_replayed_at", "native_history_status",
)
