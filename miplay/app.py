"""MiPlay runtime."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys

from aiohttp import web

from miplay.bridge import AirPlayBridgeManager
from miplay.config import Config, build_external_status, detect_name_conflicts
from miplay.web.api import create_web_app
from miplay.xiaomi import XiaomiAuthManager, XiaomiTargetManager

log = logging.getLogger("miplay")


class MiPlay:
    def __init__(self, config: Config):
        self.config = config
        self.auth = XiaomiAuthManager(config)
        self.target_manager = XiaomiTargetManager(config, self.auth)
        self.bridge_manager: AirPlayBridgeManager | None = None
        self._web_runner: web.AppRunner | None = None
        self.running = False
        self.status_message = ""
        self.warnings: list[str] = []

    async def get_all_devices(self) -> list[dict]:
        if not self.config.xiaomi.account and not self.config.xiaomi.cookie:
            return []
        await self.auth.ensure_login()
        return await self.auth.get_device_list()

    def _refresh_warnings(self):
        self.warnings = detect_name_conflicts(
            self.config.targets,
            self.config.external.wired_airplay_name,
        )

    async def start(self):
        self._setup_logging()
        self._refresh_warnings()
        web_app = create_web_app(self.config, self)
        self._web_runner = web.AppRunner(web_app, access_log=None)
        await self._web_runner.setup()
        web_site = web.TCPSite(self._web_runner, "0.0.0.0", self.config.web_port)
        await web_site.start()
        log.info("MiPlay Web UI: http://%s:%s", self.config.host, self.config.web_port)

        if (self.config.xiaomi.account or self.config.xiaomi.cookie) and self.config.get_enabled_targets():
            await self._start_bridges()
        else:
            if not (self.config.xiaomi.account or self.config.xiaomi.cookie):
                self.status_message = "Configure Xiaomi credentials to enable wireless bridge targets."
            elif not self.config.get_enabled_targets():
                self.status_message = "Select at least one Xiaomi target to start AirPlay bridge endpoints."
            log.info(self.status_message)

    async def _start_bridges(self):
        try:
            await self.auth.login()
            await self.target_manager.init_targets()
            if not self.target_manager.controllers:
                self.status_message = "No Xiaomi targets are ready; sync devices and verify credentials."
                log.warning(self.status_message)
                return
            self.bridge_manager = AirPlayBridgeManager(self.config.host, self.config)
            await self.bridge_manager.start_for_targets(self.target_manager.controllers)
            self.running = True
            self.status_message = f"MiPlay running with {len(self.target_manager.controllers)} Xiaomi AirPlay target(s)."
            log.info(self.status_message)
        except Exception as exc:
            self.status_message = f"Startup failed: {exc}"
            log.error(self.status_message)

    async def stop(self):
        self.running = False
        if self.bridge_manager:
            await self.bridge_manager.stop()
            self.bridge_manager = None
        if self._web_runner:
            try:
                await asyncio.wait_for(self._web_runner.cleanup(), timeout=3.0)
            except asyncio.TimeoutError:
                log.warning("Web cleanup timed out")
        await self.auth.close()

    async def run_forever(self):
        await self.start()
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    def get_runtime_targets(self) -> list[dict]:
        snapshots = {}
        if self.bridge_manager:
            for item in self.bridge_manager.snapshot():
                snapshots[item["id"]] = item

        result = []
        for target in self.config.targets:
            item = {
                "id": target.id,
                "did": target.did,
                "name": target.name,
                "airplay_name": target.airplay_name,
                "enabled": target.enabled,
                "device_id": target.device_id,
                "hardware": target.hardware,
                "active": False,
                "client_name": "",
                "metadata": {},
                "artwork": None,
                "rtsp_port": 0,
            }
            item.update(snapshots.get(target.id, {}))
            result.append(item)
        return result

    def get_status_snapshot(self) -> dict:
        external = build_external_status(self.config)
        return {
            "version": "0.2.0",
            "running": self.running,
            "host": self.config.host,
            "web_port": self.config.web_port,
            "targets_count": len(self.config.get_enabled_targets()),
            "bridges_count": len(self.bridge_manager.bridges) if self.bridge_manager else 0,
            "status_message": self.status_message,
            "warnings": self.warnings,
            "external": external,
        }

    async def control_target(self, target_id: str, action: str) -> bool:
        controller = self.target_manager.controllers.get(target_id)
        if not controller:
            raise ValueError(f"Target {target_id} not active")
        
        if action == "pause":
            # AirPlay bridge does not support reverse control reliably
            return False
        elif action == "play":
            # AirPlay bridge does not support reverse control reliably
            return False
        return False

    def _setup_logging(self):
        logger = logging.getLogger("miplay")
        logger.setLevel(logging.DEBUG if self.config.verbose else logging.INFO)
        if logger.handlers:
            return
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d: %(message)s",
            datefmt="[%Y-%m-%d %H:%M:%S]",
        )
        if sys.platform == "win32":
            stream = io.TextIOWrapper(
                sys.stderr.buffer,
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
            )
            console = logging.StreamHandler(stream)
        else:
            console = logging.StreamHandler()
        console.setFormatter(formatter)
        logger.addHandler(console)
        log_path = os.path.join(self.config.conf_path, "miplay.log")
        os.makedirs(self.config.conf_path, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
