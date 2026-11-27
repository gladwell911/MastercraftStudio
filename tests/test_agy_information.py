from datetime import datetime, timedelta, timezone
import subprocess
import time

import agy_information


def test_agy_native_quota_reports_partial_fields_and_expiration(monkeypatch):
    reset = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    stdout = (
        f"Gemini Models\tWeekly Limit Remaining\t98%\t{reset}\n"
        "Gemini Models\tFive Hour Limit Remaining\t99%\t\n"
        "Claude and GPT models\tWeekly Limit Remaining\t100%\t1970-01-01T00:00:00Z\n"
        "Claude and GPT models\tFive Hour Limit Remaining\t10%\t1970-01-01T00:00:00Z\n"
    )
    monkeypatch.setattr(agy_information, "find_agy_executable", lambda: "agy")

    def run(argv, **kwargs):
        assert argv[:3] == ["agy", "--print", "/usage"]
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", run)
    status, quota, stamp = agy_information.read_agy_quota()
    assert not status
    assert quota["周限额"] == ("98%", reset)
    rows = agy_information.agy_quota_rows((status, quota, stamp))
    assert "周限额：剩余 98%，重置 " in rows[1]
    assert rows[2:] == ["5 小时限额：剩余 99%，重置时间暂不可用",
                        "Claude 和 GPT 共用周限额：剩余 100%，重置时间暂不可用"]
    stale = agy_information.agy_quota_rows((status, quota, time.monotonic() - 120))
    assert all(row.endswith("：已过期") for row in stale[1:])


def test_agy_native_quota_requires_login(monkeypatch):
    monkeypatch.setattr(agy_information, "find_agy_executable", lambda: "agy")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 1, "", "Authentication required"))
    assert agy_information.read_agy_quota()[:2] == ("未登录", {})
