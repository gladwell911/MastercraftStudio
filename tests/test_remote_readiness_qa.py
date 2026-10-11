"""Readiness request boundary acceptance through the production command handler."""
import asyncio
import json

import pytest

from remote_nats import RemoteNatsTransport


@pytest.mark.parametrize("pair_id", ["default", "laptop"])
@pytest.mark.parametrize("body,expected_status", [({}, 200), ([], 500), (None, 500)])
def test_readiness_invalid_body_then_valid_request_preserves_session(pair_id, body, expected_status):
    class Broker:
        def __init__(self):
            self.events = []

        async def publish(self, subject, payload):
            self.events.append((subject, json.loads(payload)))

    async def run():
        broker = Broker()
        transport = RemoteNatsTransport(pair_id=pair_id, token="qa", jetstream=broker)
        transport._sessions[("device", "session")] = (2, "current")

        def forbidden(_payload):
            raise AssertionError("Readiness must bypass GUI/history")

        transport._invoke_route_command = forbidden
        request = {"id": "qa", "request_id": "qa", "type": "readiness",
                   "device_id": "device", "session_id": "session",
                   "protocol_version": 2, "epoch": "current", "chat_id": "", "body": body}
        await transport.handle_command(request)
        subject, response = broker.events[-1]
        assert subject == f"zgwd.{pair_id}.events"
        assert response["request_id"] == "qa"
        assert response["status"] == expected_status
        assert response["ok"] is (expected_status == 200)
        if expected_status == 500:
            assert response["body"]["error"] == "INVALID_BODY"
        await transport.handle_command({**request, "id": "recovered", "request_id": "recovered", "body": {}})
        assert broker.events[-1][1]["body"] == {"ready": True}
        assert broker.events[-1][1]["status"] == 200
        assert transport._sessions[("device", "session")] == (2, "current")

    asyncio.run(run())
