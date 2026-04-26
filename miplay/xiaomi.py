"""Xiaomi account auth, device sync, and playback target control."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re

import aiohttp
from miservice import MiAccount, MiIOService, MiNAService

from miplay.config import Config, TargetConfig

log = logging.getLogger("miplay")

NEED_USE_PLAY_MUSIC_API = [
    "X08C",
    "X08E",
    "X8F",
    "X4B",
    "LX05",
    "OH2",
    "OH2P",
    "X6A",
]

DEFAULT_AUDIO_ID = "1582971365183456177"


def parse_cookie_string(cookie_str: str) -> dict:
    result = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key.strip() in ("userId", "passToken"):
            result[key.strip()] = value.strip()
    return result


class DeviceListError(RuntimeError):
    """Raised when Xiaomi device discovery fails."""


class XiaomiAuthManager:
    def __init__(self, config: Config):
        self.config = config
        self.session: aiohttp.ClientSession | None = None
        self.account: MiAccount | None = None
        self.mina_service: MiNAService | None = None
        self.miio_service: MiIOService | None = None
        self._logged_in = False
        self._cookie_loaded = False
        self._login_lock = asyncio.Lock()
        self._device_list_lock = asyncio.Lock()
        self._device_list_task: asyncio.Task | None = None

    @property
    def token_store(self) -> str:
        return os.path.join(self.config.conf_path, ".mi.token")

    async def login(self):
        async with self._login_lock:
            if (
                self.session is not None
                and not self.session.closed
                and self.account is not None
                and self.mina_service is not None
                and self.miio_service is not None
            ):
                return

            os.makedirs(self.config.conf_path, exist_ok=True)
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
                )

            token_data = {}
            if self.config.xiaomi.cookie:
                token_data = parse_cookie_string(self.config.xiaomi.cookie)

            if token_data.get("userId") and token_data.get("passToken"):
                self.account = MiAccount(self.session, "", "", token_store=self.token_store)
                self.account.token = {
                    "userId": token_data["userId"],
                    "passToken": token_data["passToken"],
                    "deviceId": "miplay_device",
                    "ssecurity": "",
                    "serviceToken": "",
                }
                self._cookie_loaded = True
                self._logged_in = False
                log.info("Loaded Xiaomi cookie credentials; waiting for API validation")
            else:
                self._cookie_loaded = False
                self.account = MiAccount(
                    self.session,
                    self.config.xiaomi.account,
                    self.config.xiaomi.password,
                    token_store=self.token_store,
                )
                try:
                    await self.account.login("micoapi")
                    self._logged_in = True
                    log.info("Xiaomi account login succeeded")
                except Exception as exc:
                    self._logged_in = False
                    log.error(f"Xiaomi login failed: {exc}")

            self.mina_service = MiNAService(self.account)
            self.miio_service = MiIOService(self.account)

    async def ensure_login(self):
        if self.mina_service is None:
            await self.login()

    def is_logged_in(self) -> bool:
        return self._logged_in

    async def get_device_list(self) -> list[dict]:
        await self.ensure_login()
        if self.mina_service is None:
            raise DeviceListError("Xiaomi service is not ready")

        current_task = self._device_list_task
        if current_task and not current_task.done():
            return await asyncio.shield(current_task)

        async with self._device_list_lock:
            current_task = self._device_list_task
            if current_task and not current_task.done():
                return await asyncio.shield(current_task)
            self._device_list_task = asyncio.create_task(self._fetch_device_list_once())

        try:
            return await asyncio.shield(self._device_list_task)
        finally:
            if self._device_list_task and self._device_list_task.done():
                self._device_list_task = None

    async def _fetch_device_list_once(self) -> list[dict]:
        try:
            devices = await self.mina_service.device_list()
            devices = devices or []
            self._logged_in = True
            if self._cookie_loaded:
                log.info("Xiaomi cookie validation succeeded")
                self._cookie_loaded = False
            return devices
        except Exception as exc:
            self._logged_in = False
            log.warning(f"Fetching Xiaomi device list failed: {exc}")
            if self.config.xiaomi.cookie:
                self._cookie_loaded = False
                raise DeviceListError(f"Cookie may be expired: {exc}") from exc
            await self.close()
            await self.login()
            if self.mina_service is None or not self._logged_in:
                raise DeviceListError("Relogin failed")
            return await self.mina_service.device_list()

    async def update_targets_info(self) -> set[str]:
        devices = await self.get_device_list()
        selected_dids = {target.did for target in self.config.targets if target.did}
        synced_dids: set[str] = set()
        changed = False

        for target in self.config.targets:
            if target.did in selected_dids:
                if target.device_id:
                    target.device_id = ""
                    changed = True
                if target.hardware:
                    target.hardware = ""
                    changed = True

        for device in devices:
            did = device.get("miotDID", "")
            if did not in selected_dids:
                continue
            target = self.config.get_target_by_did(did)
            if target is None:
                continue
            device_id = device.get("deviceID", "") or ""
            hardware = device.get("hardware", "") or ""
            name = device.get("name", "") or ""
            if target.device_id != device_id:
                target.device_id = device_id
                changed = True
            if target.hardware != hardware:
                target.hardware = hardware
                changed = True
            if name and target.name != name and not target.name.startswith("Xiaomi Speaker "):
                target.name = target.name or name
            elif name and target.name.startswith("Xiaomi Speaker "):
                target.name = name
                if not target.airplay_name or target.airplay_name.startswith("Xiaomi Speaker "):
                    target.airplay_name = name
                changed = True
            target.ensure_names()
            if target.device_id:
                synced_dids.add(did)
                log.info(
                    "Synced Xiaomi target %s (did=%s, device_id=%s, hardware=%s)",
                    target.name,
                    did,
                    target.device_id,
                    target.hardware,
                )
        if changed:
            self.config.save()
        return synced_dids

    @staticmethod
    def extract_error_code(err_msg: str) -> str:
        match = re.search(r"\b(\d{4,6})\b", err_msg)
        return match.group(1) if match else ""

    async def close(self):
        if self._device_list_task and not self._device_list_task.done():
            self._device_list_task.cancel()
            try:
                await self._device_list_task
            except Exception:
                pass
        self._device_list_task = None
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None
        self.account = None
        self.mina_service = None
        self.miio_service = None
        self._logged_in = False
        self._cookie_loaded = False


class XiaomiTargetController:
    def __init__(self, target: TargetConfig, auth: XiaomiAuthManager):
        self.target = target
        self.auth = auth
        self._last_volume = 50

    @property
    def id(self) -> str:
        return self.target.id

    @property
    def did(self) -> str:
        return self.target.did

    @property
    def device_id(self) -> str:
        return self.target.device_id

    def _should_use_music_api(self) -> bool:
        return self.target.use_music_api or self.target.hardware in NEED_USE_PLAY_MUSIC_API

    async def play_url(self, url: str) -> bool:
        try:
            await self.auth.ensure_login()
            if self._should_use_music_api():
                result = await self.auth.mina_service.play_by_music_url(
                    self.device_id, url, audio_id=DEFAULT_AUDIO_ID
                )
            else:
                result = await self.auth.mina_service.play_by_url(self.device_id, url)
            log.info("play_url target=%s device_id=%s result=%s", self.target.airplay_name, self.device_id, result)
            return result is not None
        except Exception as exc:
            log.error(f"play_url failed for {self.target.airplay_name}: {exc}")
            return False

    async def stop(self) -> bool:
        try:
            await self.auth.ensure_login()
            result = await self.auth.mina_service.player_stop(self.device_id)
            await self.pause()
            log.info("stop target=%s result=%s", self.target.airplay_name, result)
            return True
        except Exception as exc:
            log.error(f"stop failed for {self.target.airplay_name}: {exc}")
            return False

    async def pause(self) -> bool:
        try:
            await self.auth.ensure_login()
            result = await self.auth.mina_service.player_pause(self.device_id)
            log.info("pause target=%s result=%s", self.target.airplay_name, result)
            return True
        except Exception as exc:
            log.error(f"pause failed for {self.target.airplay_name}: {exc}")
            return False

    async def set_volume(self, volume: int) -> bool:
        volume = max(0, min(100, volume))
        try:
            await self.auth.ensure_login()
            await self.auth.mina_service.player_set_volume(self.device_id, volume)
            if volume > 0:
                self._last_volume = volume
            log.info("set_volume target=%s volume=%s", self.target.airplay_name, volume)
            return True
        except Exception as exc:
            log.error(f"set_volume failed for {self.target.airplay_name}: {exc}")
            return False

    async def get_status(self) -> dict:
        try:
            await self.auth.ensure_login()
            playing_info = await self.auth.mina_service.player_get_status(self.device_id)
            if playing_info.get("code") != 0:
                raise RuntimeError(f"Mina API error: {playing_info}")
            info = json.loads(playing_info.get("data", {}).get("info", "{}"))
            volume = int(info.get("volume", 0))
            if volume > 0:
                self._last_volume = volume
            return {
                "status": info.get("status", 0),
                "volume": volume,
                "cur_time": int(info.get("cur_time", 0)),
                "duration": int(info.get("duration", 0)),
            }
        except Exception as exc:
            raise RuntimeError(f"get_status failed for {self.target.airplay_name}: {exc}") from exc


class XiaomiTargetManager:
    def __init__(self, config: Config, auth: XiaomiAuthManager):
        self.config = config
        self.auth = auth
        self.controllers: dict[str, XiaomiTargetController] = {}

    async def init_targets(self) -> set[str]:
        synced_dids = await self.auth.update_targets_info()
        self.controllers.clear()
        for target in self.config.get_enabled_targets():
            if target.did not in synced_dids:
                log.warning("Skipping target did=%s because it was not found in Xiaomi cloud", target.did)
                continue
            if not target.device_id:
                log.warning("Skipping target did=%s because device_id is missing", target.did)
                continue
            target.ensure_names()
            self.controllers[target.id] = XiaomiTargetController(target, self.auth)
            log.info("Initialized Xiaomi target: %s (did=%s)", target.airplay_name, target.did)
        return synced_dids
