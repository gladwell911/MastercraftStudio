import requests

from remote_http import RemoteControlHttpServer


def test_remote_http_server_routes_and_auth():
    seen = {"message": None, "new_chat": 0, "reply": None, "state": 0}
    server = RemoteControlHttpServer(
        host="127.0.0.1",
        port=0,
        token="secret",
        on_message=lambda payload: (200, {"accepted": True, "echo": payload.get("text")}),
        on_new_chat=lambda payload: (200, {"accepted": True}),
        on_reply_request=lambda payload: (200, {"accepted": True, "kind": payload.get("kind")}),
        on_state=lambda: (200, {"accepted": True, "status": "idle"}),
    )
    server.start()
    try:
        base = f"http://127.0.0.1:{server.bound_port}"

        response = requests.get(f"{base}/api/remote/state", timeout=5)
        assert response.status_code == 401

        headers = {"X-Remote-Token": "secret"}
        response = requests.get(f"{base}/api/remote/state", headers=headers, timeout=5)
        assert response.status_code == 200
        assert response.json()["status"] == "idle"

        response = requests.post(
            f"{base}/api/remote/message",
            headers=headers,
            json={"text": "hello"},
            timeout=5,
        )
        assert response.status_code == 200
        assert response.json()["echo"] == "hello"

        response = requests.post(f"{base}/api/remote/new-chat", headers=headers, json={}, timeout=5)
        assert response.status_code == 200

        response = requests.post(
            f"{base}/api/remote/reply-request",
            headers=headers,
            json={"kind": "approval", "text": "1"},
            timeout=5,
        )
        assert response.status_code == 200
        assert response.json()["kind"] == "approval"
    finally:
        server.stop()


def test_remote_http_server_accepts_non_utf8_json_when_charset_is_declared():
    server = RemoteControlHttpServer(
        host="127.0.0.1",
        port=0,
        token="secret",
        on_message=lambda payload: (200, {"accepted": True, "echo": payload.get("text")}),
        on_new_chat=lambda payload: (200, {"accepted": True}),
        on_reply_request=lambda payload: (200, {"accepted": True}),
        on_state=lambda: (200, {"accepted": True, "status": "idle"}),
    )
    server.start()
    try:
        base = f"http://127.0.0.1:{server.bound_port}"
        body = '{"text":"中文远程测试"}'.encode("gb18030")
        response = requests.post(
            f"{base}/api/remote/message",
            headers={
                "X-Remote-Token": "secret",
                "Content-Type": "application/json; charset=gb18030",
            },
            data=body,
            timeout=5,
        )
        assert response.status_code == 200
        assert response.json()["echo"] == "中文远程测试"
    finally:
        server.stop()
