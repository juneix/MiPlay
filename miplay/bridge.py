"""AirPlay-to-Xiaomi bridge runtime."""

from __future__ import annotations

import asyncio
import logging
import time

from zeroconf import IPVersion, Zeroconf

from miplay.airplay.server import AirPlayServer
from miplay.config import Config
from miplay.xiaomi import XiaomiTargetController

log = logging.getLogger("miplay")


class AirPlayBridge:
    def __init__(
        self,
        host: str,
        controller: XiaomiTargetController,
        shared_zeroconf: Zeroconf | None = None,
        config: Config | None = None,
    ):
        self.host = host
        self.controller = controller
        self.target = controller.target
        self.device_name = self.target.airplay_name
        self.shared_zeroconf = shared_zeroconf
        self.config = config
        self.airplay_server: AirPlayServer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream_url = ""
        self._airplay_active = False
        self._poll_task: asyncio.Task | None = None
        self._play_grace_until = 0.0

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self.airplay_server = AirPlayServer(
            self.host,
            self.device_name,
            self.shared_zeroconf,
            speaker_hardware=self.target.hardware,
        )
        self.airplay_server.on_play_start = self._on_play_start
        self.airplay_server.on_play_stop = self._on_play_stop
        self.airplay_server.on_volume_change = self._on_volume_change
        await self.airplay_server.start()
        log.info("Started AirPlay bridge %s on rtsp=%s", self.device_name, self.airplay_server.rtsp_port)

    async def stop(self):
        self._airplay_active = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self.airplay_server:
            await self.airplay_server.stop()
            self.airplay_server = None

    def _on_play_start(self, stream_url: str):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._play_on_target(stream_url), self._loop)

    def _on_play_stop(self):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._stop_target(), self._loop)

    def _on_volume_change(self, vol_db: float):
        if vol_db <= -144:
            volume = 0
        elif vol_db >= 0:
            volume = 100
        else:
            volume = int(pow(10, vol_db / 20) * 100)
            if volume == 0 and vol_db > -144:
                volume = 1
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.controller.set_volume(volume), self._loop)

    async def _play_on_target(self, stream_url: str):
        self._stream_url = stream_url
        self._airplay_active = True
        self._play_grace_until = time.time() + 10.0
        if await self.controller.play_url(stream_url):
            self._start_poll()
            log.info("AirPlay stream attached to Xiaomi target %s", self.device_name)
        else:
            log.warning("Xiaomi target rejected AirPlay stream for %s", self.device_name)

    async def _stop_target(self):
        self._airplay_active = False
        self._stream_url = ""
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        try:
            await self.controller.stop()
        except Exception:
            pass

    def _start_poll(self):
        if self._poll_task and not self._poll_task.done():
            return
        self._poll_task = asyncio.create_task(self._poll_target_state())

    async def _poll_target_state(self):
        try:
            while self._airplay_active and self._stream_url:
                await asyncio.sleep(3)
                if not self._airplay_active or not self._stream_url:
                    break
                if self.airplay_server and not self.airplay_server.is_playing:
                    break
                if time.time() < self._play_grace_until:
                    continue
                try:
                    status = await asyncio.wait_for(self.controller.get_status(), timeout=10)
                    if status.get("status", 0) == 1:
                        continue
                    await asyncio.sleep(5)
                    if not self._airplay_active or not self._stream_url:
                        break
                    if self.airplay_server and not self.airplay_server.is_playing:
                        break
                    base_url = self._stream_url.split("?")[0]
                    fresh_url = f"{base_url}?sid={int(time.time())}"
                    self._play_grace_until = time.time() + 10.0
                    await self.controller.play_url(fresh_url)
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    def snapshot(self) -> dict:
        return {
            "id": self.target.id,
            "did": self.target.did,
            "name": self.target.name,
            "airplay_name": self.device_name,
            "hardware": self.target.hardware,
            "active": bool(self.airplay_server and self.airplay_server.is_playing),
            "client_name": self.airplay_server.client_name if self.airplay_server else "",
            "metadata": self.airplay_server.metadata if self.airplay_server else {},
            "artwork": self.airplay_server.artwork if self.airplay_server else None,
            "rtsp_port": self.airplay_server.rtsp_port if self.airplay_server else 0,
            "stream_url": self._stream_url,
        }


class AirPlayBridgeManager:
    def __init__(self, host: str, config: Config):
        self.host = host
        self.config = config
        self.bridges: dict[str, AirPlayBridge] = {}
        self._shared_zeroconf: Zeroconf | None = None

    async def start_for_targets(self, controllers: dict[str, XiaomiTargetController]):
        if self._shared_zeroconf is None:
            self._shared_zeroconf = Zeroconf(ip_version=IPVersion.All)
        for target_id, controller in controllers.items():
            if target_id in self.bridges:
                continue
            bridge = AirPlayBridge(self.host, controller, self._shared_zeroconf, self.config)
            await bridge.start()
            self.bridges[target_id] = bridge
        log.info("Started %s AirPlay bridge endpoint(s)", len(self.bridges))

    async def stop(self):
        for bridge in list(self.bridges.values()):
            try:
                await bridge.stop()
            except Exception as exc:
                log.error(f"Failed to stop bridge: {exc}")
        self.bridges.clear()
        if self._shared_zeroconf:
            self._shared_zeroconf.close()
            self._shared_zeroconf = None

    def snapshot(self) -> list[dict]:
        return [bridge.snapshot() for bridge in self.bridges.values()]
