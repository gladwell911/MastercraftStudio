from __future__ import annotations

from typing import Any

import requests


class CouchDbClient:
    def __init__(self, base_url: str, database: str, session: requests.Session | None = None) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.database = str(database or "").strip().strip("/")
        self.session = session or requests.Session()

    def fetch_changes(self, since: str) -> dict[str, Any]:
        response = self.session.get(
            f"{self.base_url}/{self.database}/_changes",
            params={"since": str(since or "0"), "include_docs": "true"},
            timeout=10,
        )
        response.raise_for_status()
        return dict(response.json() or {})

    def write_documents(self, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not docs:
            return []
        response = self.session.post(
            f"{self.base_url}/{self.database}/_bulk_docs",
            json={"docs": list(docs)},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json() or []
        if not isinstance(payload, list):
            return []
        return [dict(item) for item in payload if isinstance(item, dict)]

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()
