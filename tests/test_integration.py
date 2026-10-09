import asyncio
import inspect
import json
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import aiohttp

from ha_stubs import ConfigFlow, install

install()

from custom_components.minidsp.api import MiniDSPAPI
from custom_components.minidsp.config_flow import MiniDSPConfigFlow, MiniDSPOptionsFlow
from custom_components.minidsp.coordinator import MiniDSPCoordinator
from custom_components.minidsp.number import MiniDSPOutputGain

ROOT = Path(__file__).resolve().parents[1]
STATUS = {
    "master": {"volume": -20.5, "preset": 0, "mute": False, "dirac": True},
    "input_levels": [-51.2, -50.8],
    "output_levels": [-60.1, -127.0],
}


class Response:
    def __init__(self, data, error=None):
        self.data = data
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        if self.error:
            raise self.error

    async def json(self):
        return deepcopy(self.data)


class Socket:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.entered = asyncio.Event()
        self.exited = asyncio.Event()

    async def __aenter__(self):
        self.entered.set()
        return self

    async def __aexit__(self, *args):
        self.exited.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=item)

    def send(self, data):
        self.queue.put_nowait(json.dumps(data))


class Session:
    def __init__(self, sockets=()):
        self.status = deepcopy(STATUS)
        self.sockets = list(sockets)
        self.connections = 0
        self.posts = []
        self.post_error = None
        self.get_error = None

    def get(self, url):
        return Response(self.status, self.get_error)

    def post(self, url, json):
        self.posts.append((url, json))
        return Response(None, self.post_error)

    def ws_connect(self, url, **kwargs):
        self.connections += 1
        if not self.sockets:
            raise aiohttp.ClientConnectionError("server unavailable")
        socket = self.sockets.pop(0)
        if isinstance(socket, Exception):
            raise socket
        return socket


class PackagingTests(unittest.TestCase):
    def test_folder_matches_manifest_and_domain(self):
        component = ROOT / "custom_components/minidsp"
        manifest = json.loads((component / "manifest.json").read_text())
        self.assertEqual(component.name, manifest["domain"])
        self.assertFalse((ROOT / "custom_components/minidsp-rs").exists())
        self.assertEqual(MiniDSPConfigFlow.VERSION, 1)


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.socket = Socket()
        self.session = Session([self.socket])
        self.api = MiniDSPAPI("http://example.test:5380", self.session)
        self.coordinator = MiniDSPCoordinator(None, self.api)
        await self.coordinator.async_request_refresh()
        self.addAsyncCleanup(self.coordinator.async_disconnect)

    async def wait_for_state(self, success):
        if self.coordinator.last_update_success is success:
            return
        ready = asyncio.Event()

        def changed():
            if self.coordinator.last_update_success is success:
                ready.set()

        self.coordinator.listeners.append(changed)
        try:
            await asyncio.wait_for(ready.wait(), 2)
        finally:
            self.coordinator.listeners.remove(changed)

    async def test_http_refresh_rounds_only_meters(self):
        self.assertEqual(self.coordinator.data["input_levels"], [-51, -51])
        self.assertEqual(self.coordinator.data["master"]["volume"], -20.5)
        self.assertIs(self.coordinator.data["master"]["mute"], False)
        self.assertIs(self.coordinator.data["master"]["dirac"], True)
        self.assertEqual(self.session.status, STATUS)

    async def test_websocket_preserves_volume_and_boolean_types(self):
        ready = asyncio.Event()
        self.coordinator.listeners.append(ready.set)
        await self.coordinator.async_start()
        self.socket.send({"master": {"volume": -21.5, "mute": True}})
        await asyncio.wait_for(ready.wait(), 1)
        master = self.coordinator.data["master"]
        self.assertEqual(master["volume"], -21.5)
        self.assertIs(master["mute"], True)
        self.assertIs(master["dirac"], True)

    async def test_disconnect_cancels_idle_receive_and_closes_socket(self):
        await self.coordinator.async_start()
        await self.socket.entered.wait()
        await asyncio.wait_for(self.coordinator.async_disconnect(), 1)
        self.assertTrue(self.socket.exited.is_set())
        self.assertIsNone(self.api._ws_task)

    async def test_disconnect_stops_busy_stream(self):
        await self.coordinator.async_start()
        await self.socket.entered.wait()
        for _ in range(100):
            self.socket.send(STATUS)
        await asyncio.wait_for(self.coordinator.async_disconnect(), 1)
        self.assertTrue(self.socket.exited.is_set())

    async def test_disconnect_cancels_reconnection_backoff(self):
        await self.coordinator.async_start()
        await self.socket.entered.wait()
        self.socket.queue.put_nowait(aiohttp.ClientConnectionError("lost"))
        await self.wait_for_state(False)
        await asyncio.wait_for(self.coordinator.async_disconnect(), 0.5)
        self.assertIsNone(self.api._ws_task)

    async def test_disconnect_cancels_pending_handshake(self):
        started = asyncio.Event()

        class PendingHandshake(Socket):
            async def __aenter__(self):
                started.set()
                await asyncio.Event().wait()

        self.session.sockets = [PendingHandshake()]
        await self.coordinator.async_start()
        await started.wait()
        await asyncio.wait_for(self.coordinator.async_disconnect(), 0.5)

    async def test_clean_socket_close_marks_entities_unavailable(self):
        await self.coordinator.async_start()
        await self.socket.entered.wait()
        self.socket.queue.put_nowait(None)
        await self.wait_for_state(False)

    async def test_initial_connection_failure_marks_entities_unavailable(self):
        self.session.sockets = []
        await self.coordinator.async_start()
        await self.wait_for_state(False)

    async def test_identical_message_restores_availability_after_reconnect(self):
        replacement = Socket()
        self.session.sockets.append(replacement)
        await self.coordinator.async_start()
        await self.socket.entered.wait()
        self.socket.queue.put_nowait(aiohttp.ClientConnectionError("lost"))
        await self.wait_for_state(False)
        await asyncio.wait_for(replacement.entered.wait(), 2)
        # A handshake alone is insufficient to restore availability.
        self.assertFalse(self.coordinator.last_update_success)
        replacement.send(STATUS)
        await self.wait_for_state(True)
        self.assertEqual(self.coordinator.data["master"]["volume"], -20.5)

    async def test_unsubscribe_cancels_last_listener_and_allows_resubscribe(self):
        updates = []

        async def callback(event):
            updates.append(event)

        unsubscribe = await self.api.async_subscribe_levels(callback)
        await self.socket.entered.wait()
        unsubscribe()
        await asyncio.wait_for(self.socket.exited.wait(), 1)
        await self.api.async_disconnect()
        replacement = Socket()
        self.session.sockets.append(replacement)
        await self.api.async_subscribe_levels(callback)
        await asyncio.wait_for(replacement.entered.wait(), 1)
        self.assertFalse(self.api._stop_event.is_set())

    async def test_invalid_messages_do_not_restore_availability(self):
        self.coordinator.async_set_update_error(RuntimeError("offline"))
        await self.coordinator.async_start()
        await self.socket.entered.wait()
        self.socket.queue.put_nowait("bad json")
        self.socket.send([1, 2])
        await asyncio.sleep(0.01)
        self.assertFalse(self.coordinator.last_update_success)
        self.socket.send(STATUS)
        await self.wait_for_state(True)

    def gain_entity(self):
        entity = MiniDSPOutputGain(self.coordinator, 0)
        self.coordinator.listeners.append(entity._handle_coordinator_update)
        return entity

    async def test_gain_is_assumed_and_persists_after_successful_write_and_refresh(self):
        entity = self.gain_entity()
        self.assertIsNone(entity.native_value)
        self.assertTrue(entity._attr_assumed_state)
        await entity.async_set_native_value(-6.5)
        self.assertEqual(entity.native_value, -6.5)
        self.assertEqual(self.session.posts[-1][1], {"outputs": [{"index": 0, "gain": -6.5}]})
        await self.coordinator.async_request_refresh()
        self.assertEqual(entity.native_value, -6.5)

    async def test_failed_gain_write_does_not_overwrite_last_success(self):
        entity = self.gain_entity()
        await entity.async_set_native_value(-6)
        self.session.post_error = aiohttp.ClientConnectionError("write failed")
        with self.assertRaises(aiohttp.ClientConnectionError):
            await entity.async_set_native_value(-12)
        self.assertEqual(entity.native_value, -6)

    async def test_gain_clears_on_preset_change(self):
        entity = self.gain_entity()
        await entity.async_set_native_value(-6)
        self.session.status["master"]["preset"] = 1
        await self.coordinator.async_request_refresh()
        self.assertIsNone(entity.native_value)

    async def test_gain_clears_on_connection_loss(self):
        entity = self.gain_entity()
        await entity.async_set_native_value(-6)
        self.coordinator.async_set_update_error(RuntimeError("offline"))
        self.assertIsNone(entity.native_value)
        await self.coordinator.async_request_refresh()
        self.assertIsNone(entity.native_value)

    async def test_gain_clears_if_refresh_fails_after_successful_write(self):
        entity = self.gain_entity()
        self.session.get_error = aiohttp.ClientConnectionError("offline")
        await entity.async_set_native_value(-6)
        self.assertFalse(self.coordinator.last_update_success)
        self.assertIsNone(entity.native_value)

    async def test_options_hook_is_synchronous_and_saves_new_url(self):
        entry = SimpleNamespace(data={"url": "http://old:5380"}, options={})
        self.assertIsNot(MiniDSPConfigFlow.async_get_options_flow, ConfigFlow.async_get_options_flow)
        flow = MiniDSPConfigFlow.async_get_options_flow(entry)
        self.assertFalse(inspect.isawaitable(flow))
        self.assertIsInstance(flow, MiniDSPOptionsFlow)
        form = await flow.async_step_init()
        self.assertEqual(form["data_schema"]({}), {"url": "http://old:5380"})
        result = await flow.async_step_init({"url": "http://new:5380"})
        self.assertEqual(result["data"], {"url": "http://new:5380"})


if __name__ == "__main__":
    unittest.main()
