import asyncio
import json

import pytest

from remote_nats import RemoteNatsTransport


@pytest.mark.parametrize("pair_id", ["default", "laptop"])
def test_readiness_preserves_epoch_and_bypasses_ui(pair_id):
    class Broker:
        def __init__(self):
            self.events = []

        async def publish(self, subject, payload):
            self.events.append((subject, json.loads(payload)))

    async def run():
        broker = Broker()
        transport = RemoteNatsTransport(pair_id=pair_id, token="test", jetstream=broker)
        transport._sessions[("device", "session")] = (2, "current")

        def forbidden(_payload):
            raise AssertionError("Readiness must not invoke UI or history")

        transport._invoke_route_command = forbidden
        payload = {"id": "probe", "request_id": "probe", "type": "readiness",
                   "device_id": "device", "session_id": "session",
                   "protocol_version": 2, "epoch": "current", "chat_id": "", "body": {}}
        await transport.handle_command(payload)
        assert broker.events[-1][1]["body"] == {"ready": True}
        assert broker.events[-1][1]["status"] == 200
        assert broker.events[-1][1]["epoch"] == "current"
        assert transport._sessions[("device", "session")] == (2, "current")
        await transport.handle_command({**payload, "epoch": "obsolete"})
        assert broker.events[-1][1]["status"] == 500
        assert broker.events[-1][1]["body"]["error"] == "STALE_EPOCH"
        assert transport._sessions[("device", "session")] == (2, "current")
        assert all(pair_id in subject for subject, _ in broker.events)

    asyncio.run(run())
