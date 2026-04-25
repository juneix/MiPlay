"""小米账号认证管理"""

import asyncio
import logging
import os
import re

import aiohttp
from miservice import MiAccount, MiIOService, MiNAService

from miair.config import Config

log = logging.getLogger("miair")


def parse_cookie_string(cookie_str: str) -> dict:
    """解析 cookie 字符串，提取 userId 和 passToken"""
    result = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key in ("userId", "passToken"):
                result[key] = value
    return result


class DeviceListError(RuntimeError):
    """获取小米设备列表失败。"""


class AuthManager:
    """管理小米账号认证和设备服务"""

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

    async def login(self):
        """登录小米账号并初始化服务"""
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

            # 创建 aiohttp session（必须设置超时，否则 miservice HTTP 调用可能无限挂起导致卡死）
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
                )

            token_store = self.config.mi_token_home

            # 如果有 cookie，使用 cookie 中的信息创建 MiAccount
            token_data = {}
            if self.config.cookie:
                token_data = parse_cookie_string(self.config.cookie)
            
            # 创建 MiAccount，如果使用 cookie 登录，传入空的账号密码
            if token_data.get("userId") and token_data.get("passToken"):
                # 使用 cookie 登录，传入空的账号密码，避免触发密码登录流程
                self.account = MiAccount(
                    self.session,
                    "",  # 空账号
                    "",  # 空密码
                    token_store=token_store,
                )
                # 设置 token，包含所有必要字段
                self.account.token = {
                    "userId": token_data["userId"],
                    "passToken": token_data["passToken"],
                    "deviceId": "miair_device",
                    "ssecurity": "",
                    "serviceToken": "",
                }
                self._cookie_loaded = True
                self._logged_in = False
                log.info("已载入 cookie，等待 API 验证")
            else:
                # 使用账号密码登录
                self._cookie_loaded = False
                self.account = MiAccount(
                    self.session,
                    self.config.account,
                    self.config.password,
                    token_store=token_store,
                )

            # 显式调用 login
            # 如果使用 cookie 登录，跳过 login 调用，等待实际 API 验证 cookie 可用性
            if token_data.get("userId") and token_data.get("passToken"):
                pass
            else:
                try:
                    await self.account.login("micoapi")
                    self._logged_in = True
                    log.info("小米账号登录成功")
                except Exception as e:
                    self._logged_in = False
                    err_msg = str(e)
                    err_code = self._extract_error_code(err_msg)
                    if err_code == "87001" or "captcha" in err_msg.lower():
                        log.error(
                            "登录需要验证码! 请在浏览器访问 https://account.xiaomi.com 完成验证后重试，"
                            "或使用 cookie 方式登录"
                        )
                    elif err_code == "70016":
                        log.error(
                            "登录验证失败! 可能原因：密码错误、需要关闭二次验证、"
                            "或需要在 https://www.mi.com 完成人机验证。"
                            "建议使用 cookie 方式登录。"
                        )
                    elif "userId" in err_msg:
                        log.error(
                            "登录失败(缺少userId)! 小米账号可能需要额外验证。"
                            "请尝试以下方法：\n"
                            "  1. 在浏览器登录 https://account.xiaomi.com 完成验证\n"
                            "  2. 使用 cookie 方式登录（在设置中填入 cookie）\n"
                            "  3. 确保关闭了代理/VPN"
                        )
                    else:
                        log.error(f"登录失败: {e}")

            # 无论是否登录成功，都设置 service (方便后续重试)
            self.mina_service = MiNAService(self.account)
            self.miio_service = MiIOService(self.account)

    async def ensure_login(self):
        """确保已登录，未登录则尝试登录"""
        if self.mina_service is None:
            await self.login()

    @staticmethod
    def _extract_error_code(err_msg: str) -> str:
        """从异常消息中提取数字错误码"""
        m = re.search(r'\b(\d{4,6})\b', err_msg)
        return m.group(1) if m else ""

    async def get_device_list(self) -> list[dict]:
        """获取账号下所有设备列表"""
        await self.ensure_login()
        if self.mina_service is None:
            raise DeviceListError("小米服务尚未初始化，无法获取设备列表")
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
        """执行一次真实的设备列表拉取；并发调用应共享同一任务。"""
        try:
            devices = await self.mina_service.device_list()
            devices = devices or []
            self._logged_in = True
            if self._cookie_loaded:
                log.info("cookie API 验证成功")
                self._cookie_loaded = False
            return devices
        except Exception as e:
            self._logged_in = False
            log.warning(f"获取设备列表失败: {e}")
            # 可能 token 过期，尝试重新登录
            # 但如果使用 cookie 登录，不要重新调用 login（避免 KeyError）
            if self.config.cookie:
                self._cookie_loaded = False
                raise DeviceListError(f"Cookie 可能已过期，请重新获取: {e}") from e
            await self.close()
            await self.login()
            if self.mina_service is None or not self._logged_in:
                raise DeviceListError("重新登录失败，请检查账号密码或完成人机验证")
            try:
                devices = await self.mina_service.device_list()
                devices = devices or []
                self._logged_in = True
                return devices
            except Exception as e2:
                self._logged_in = False
                raise DeviceListError(f"重新登录后仍然失败: {e2}") from e2

    async def update_speakers_info(self) -> set[str]:
        """从云端获取设备信息，更新 speakers 配置"""
        devices = await self.get_device_list()
        did_list = set(self.config.get_did_list())
        synced_dids: set[str] = set()
        changed = False

        # 严格模式：每次真实刷新前先清空当前选中音箱的关键运行字段，
        # 避免旧缓存 device_id 冒充本次云端成功结果。
        for did in did_list:
            speaker = self.config.get_speaker(did)
            if speaker.device_id:
                speaker.device_id = ""
                changed = True
            if speaker.hardware:
                speaker.hardware = ""
                changed = True

        for device in devices:
            miot_did = device.get("miotDID", "")
            if miot_did in did_list:
                speaker = self.config.get_speaker(miot_did)
                device_id = device.get("deviceID", "") or ""
                hardware = device.get("hardware", "") or ""
                name = device.get("name", "") or ""

                if speaker.device_id != device_id:
                    speaker.device_id = device_id
                    changed = True
                if speaker.hardware != hardware:
                    speaker.hardware = hardware
                    changed = True
                if name and speaker.name != name:
                    speaker.name = name
                    changed = True

                speaker.ensure_udn()
                if speaker.device_id:
                    synced_dids.add(miot_did)
                    log.info(
                        f"已同步设备信息: {speaker.name} "
                        f"(did={miot_did}, device_id={speaker.device_id}, "
                        f"hardware={speaker.hardware})"
                    )

        if changed:
            self.config.save()
        return synced_dids

    def is_logged_in(self) -> bool:
        """是否已成功登录"""
        return self._logged_in

    async def close(self):
        """关闭 session"""
        if self._device_list_task and not self._device_list_task.done():
            self._device_list_task.cancel()
            try:
                await self._device_list_task
            except asyncio.CancelledError:
                pass
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
