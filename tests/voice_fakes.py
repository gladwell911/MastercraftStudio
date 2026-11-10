"""Gated device and actual protocol transport; never uses hardware or the network."""
import asyncio
import gzip
import json
import struct
import threading
import time
import types

import realtime_asr
import speech_input


def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition did not converge")
        time.sleep(.002)


class VoiceRig:
    def __init__(self, monkeypatch):
        self.connect = threading.Event()
        self.open_gate = threading.Event()
        self.open_gate.set()
        self.started = threading.Event()
        self.stopped = threading.Event()
        self.final_gate = threading.Event()
        self.final_gate.set()
        self.first = b'A' * 6400
        self.tail = b''
        self.text = 'opening phrase opening phrase'
        self.streams = []
        self.sockets = []
        self.sessions = []
        self.packets = []
        self.fail_connect = False
        self.fail_send = False
        self.status = None
        self.early_final = False
        self.send_gate = threading.Event()
        self.send_gate.set()
        self.terminal_return_gate = threading.Event()
        self.terminal_return_gate.set()
        self.terminal_received = threading.Event()
        self.final_delivered = threading.Event()
        self.close_gate = threading.Event()
        self.close_gate.set()
        self.close_entered = threading.Event()
        self.fail_terminal = False
        self.fail_ws_close = False
        self.device_error = None
        self.receive_fault = None
        rig = self

        class Stream:
            def __init__(self, **kwargs):
                rig.open_gate.wait(3)
                self.callback = kwargs['callback']
                self.closed = False
                self.owner = threading.get_ident()
                rig.streams.append(self)

            def start(self):
                assert threading.get_ident() == self.owner
                if rig.device_error == 'start':
                    raise OSError('controlled device start failure')
                if rig.first:
                    self.callback(rig.first, 0, None, rig.status)
                rig.started.set()

            def stop(self):
                assert threading.get_ident() == self.owner
                if rig.device_error == 'stop':
                    raise OSError('controlled device stop failure')
                if rig.tail:
                    self.callback(rig.tail, 0, None, None)
                rig.stopped.set()

            def close(self):
                assert threading.get_ident() == self.owner
                self.closed = True
                if rig.device_error == 'close':
                    # Driver reports an error after releasing its handle.
                    raise OSError('controlled device close failure')

        def final():
            body = gzip.compress(json.dumps({'result': {'text': rig.text}}).encode())
            return types.SimpleNamespace(type='binary', data=b'\x11\x93\x11\x00' + struct.pack('>iI', -1, len(body)) + body)

        class WebSocket:
            def __init__(self):
                self.closed = False
                self.messages = asyncio.Queue()
                rig.sockets.append(self)

            async def send_bytes(self, data):
                sequence = struct.unpack('>i', data[4:8])[0]
                if data[1] >> 4 == 2:
                    while not rig.send_gate.is_set():
                        await asyncio.sleep(.001)
                    if rig.fail_send:
                        raise OSError('controlled send failure')
                    rig.packets.append((sequence, gzip.decompress(data[12:])))
                    if sequence < 0:
                        await self.messages.put(final())
                        rig.terminal_received.set()
                        while not rig.terminal_return_gate.is_set():
                            await asyncio.sleep(.001)
                        if rig.fail_terminal:
                            raise OSError('controlled terminal send failure')
                elif rig.early_final:
                    await self.messages.put(final())
                elif rig.receive_fault:
                    await self.messages.put(rig.receive_fault)

            def __aiter__(self):
                return self

            async def __anext__(self):
                message = await self.messages.get()
                if message == 'eof':
                    raise StopAsyncIteration
                if message == 'error':
                    return types.SimpleNamespace(type='error')
                while not rig.final_gate.is_set():
                    await asyncio.sleep(.001)
                rig.final_delivered.set()
                return message

            async def close(self):
                rig.close_entered.set()
                while not rig.close_gate.is_set():
                    await asyncio.sleep(.001)
                if rig.fail_ws_close:
                    raise OSError('controlled websocket close failure')
                self.closed = True

        class HttpSession:
            def __init__(self):
                self.closed = False
                rig.sessions.append(self)

            async def ws_connect(self, *_args, **_kwargs):
                while not rig.connect.is_set():
                    await asyncio.sleep(.001)
                if rig.fail_connect:
                    raise OSError('controlled connection failure')
                return WebSocket()

            async def close(self):
                self.closed = True

        monkeypatch.setattr(speech_input.sd, 'RawInputStream', Stream)
        monkeypatch.setattr(realtime_asr, 'aiohttp', types.SimpleNamespace(
            ClientSession=HttpSession, WSMsgType=types.SimpleNamespace(BINARY='binary', ERROR='error')))

    def frame(self, data, status=None):
        self.streams[-1].callback(data, 0, None, status)

    def assert_closed(self):
        assert all(s.closed for s in self.streams + self.sockets + self.sessions)
