import asyncio
import logging
import socket
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.sax.saxutils import escape

import aiohttp
from aiohttp import web
from zeroconf import ServiceInfo, Zeroconf

from miair.const import (
    TRANSPORT_STATE_PAUSED,
    TRANSPORT_STATE_PLAYING,
    TRANSPORT_STATE_STOPPED,
)

log = logging.getLogger("miair.plex")


class PlexError(Exception):
    """Plex 协议处理错误。"""


@dataclass
class PlexServerContext:
    protocol: str
    address: str
    fetch_address: str
    port: int
    token: str

    @property
    def verify_ssl(self) -> bool:
        return self.protocol != "https"

    @property
    def base_url(self) -> str:
        return f"{self.protocol}://{self.fetch_address}:{self.port}"


@dataclass
class PlexMedia:
    key: str
    rating_key: str
    title: str
    duration_ms: int
    stream_url: str


@dataclass(frozen=True)
class PlexPersona:
    product: str
    platform: str
    device: str
    device_class: str
    version: str = "1.0.0"
    platform_version: str = ""


ROKU_PERSONA = PlexPersona(
    product="Plex for Roku",
    platform="Roku",
    device="Roku",
    device_class="stb",
    version="1.0.0",
    platform_version="14.0",
)


@dataclass
class PlexSession:
    command_id: str = "0"
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: str = "stopped"
    rating_key: str = ""
    key: str = ""
    title: str = ""
    duration_ms: int = 0
    position_ms: int = 0
    updated_at: float = field(default_factory=time.monotonic)
    server_protocol: str = "http"
    server_address: str = ""
    server_port: int = 32400
    token: str = ""
    play_queue_id: str = ""
    play_queue_version: str = ""
    play_queue_item_id: str = ""
    container_key: str = ""
    controllable: str = "playPause,stop,seekTo,skipPrevious,skipNext"
    controller_updated_at: float = field(default_factory=time.monotonic)

    def replace_media(
        self,
        media: PlexMedia,
        context: PlexServerContext,
        command_id: str,
        play_queue_id: str = "",
        play_queue_version: str = "",
        play_queue_item_id: str = "",
        container_key: str = "",
    ):
        self.command_id = command_id
        self.session_id = str(uuid.uuid4())
        self.state = "playing"
        self.rating_key = media.rating_key
        self.key = media.key
        self.title = media.title
        self.duration_ms = media.duration_ms
        self.position_ms = 0
        self.updated_at = time.monotonic()
        self.server_protocol = context.protocol
        self.server_address = context.fetch_address
        self.server_port = context.port
        self.token = context.token
        self.play_queue_id = play_queue_id
        self.play_queue_version = play_queue_version
        self.play_queue_item_id = play_queue_item_id
        self.container_key = container_key
        self.controller_updated_at = time.monotonic()

    def set_state(self, state: str, position_ms: int | None = None):
        self.position_ms = self.current_position_ms() if position_ms is None else max(0, position_ms)
        self.state = state
        self.updated_at = time.monotonic()
        self.controller_updated_at = time.monotonic()

    def touch_controller(self):
        self.controller_updated_at = time.monotonic()

    def current_position_ms(self) -> int:
        position = self.position_ms
        if self.state == "playing":
            position += int((time.monotonic() - self.updated_at) * 1000)
        if self.duration_ms > 0:
            position = min(position, self.duration_ms)
        return max(0, position)

    def has_media(self) -> bool:
        return bool(self.key and self.rating_key)

    def is_active(self) -> bool:
        return self.state in {"playing", "paused", "buffering"} and self.has_media()

    def clear(self):
        self.command_id = "0"
        self.session_id = str(uuid.uuid4())
        self.state = "stopped"
        self.rating_key = ""
        self.key = ""
        self.title = ""
        self.duration_ms = 0
        self.position_ms = 0
        self.updated_at = time.monotonic()
        self.server_protocol = "http"
        self.server_address = ""
        self.server_port = 32400
        self.token = ""
        self.play_queue_id = ""
        self.play_queue_version = ""
        self.play_queue_item_id = ""
        self.container_key = ""
        self.controllable = "playPause,stop,seekTo,skipPrevious,skipNext"
        self.controller_updated_at = time.monotonic()


class PlexPlayer:
    """Plex Companion 假播放器，复用现有 MiAir 播放链路。"""

    def __init__(self, miair):
        self.miair = miair
        self.config = miair.config
        self.port = self.config.plex_port
        self.uuid = self.config.plex_client_id
        self.persona = ROKU_PERSONA
        self.running = False
        self.local_ip = self.config.hostname
        self.server_runner: web.AppRunner | None = None
        self.zc: Zeroconf | None = None
        self.zc_info: ServiceInfo | None = None
        self._session = PlexSession()
        self._tasks: list[asyncio.Task] = []
        self._gdm_transports = []

    async def start(self):
        self.running = True
        app = web.Application()
        app.router.add_get("/player/playback/playMedia", self.handle_play_media)
        app.router.add_get("/player/playback/{action}", self.handle_control)
        app.router.add_get("/player/timeline/poll", self.handle_poll)
        app.router.add_get("/resources", self.handle_resources)
        app.router.add_route("OPTIONS", "/{tail:.*}", self.handle_options)
        app.router.add_get("/{tail:.*}", self.handle_fallback)

        self.server_runner = web.AppRunner(app, access_log=None)
        await self.server_runner.setup()
        site = web.TCPSite(self.server_runner, "0.0.0.0", self.port)
        await site.start()

        display_name = self.config.plex_name or "miPlay"
        log.info(f"Plex 假播放器启动 [{display_name}]，端口: {self.port}")

        self._tasks = [
            asyncio.create_task(self.gdm_announcer()),
            asyncio.create_task(self.gdm_responder()),
            asyncio.create_task(self._start_mdns()),
            asyncio.create_task(self._report_timeline_loop()),
        ]

    async def stop(self):
        self.running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        for transport in self._gdm_transports:
            transport.close()
        self._gdm_transports.clear()

        if self.zc:
            try:
                if self.zc_info:
                    self.zc.unregister_service(self.zc_info)
            except Exception:
                pass
            self.zc.close()
            self.zc = None
            self.zc_info = None

        if self.server_runner:
            await self.server_runner.cleanup()
            self.server_runner = None

        log.info("Plex 模拟器已停止")

    def get_status_snapshot(self) -> dict:
        session = self._session
        return {
            "active": session.is_active(),
            "state": session.state,
            "title": session.title,
            "duration_ms": session.duration_ms,
            "position_ms": session.current_position_ms(),
            "bound_did": self.config.get_plex_target_did(),
        }

    async def handle_play_media(self, request: web.Request):
        try:
            log.info(f"Plex playMedia: key={request.query.get('key', '')} target={self.config.get_plex_target_did()}")
            renderer = self._require_target_renderer()
            context = self._parse_server_context(request.query)
            command_id = request.query.get("commandID", self._session.command_id)
            offset_ms = self._parse_offset_ms(request.query)
            media = await self._fetch_media(request.query.get("key", ""), context)

            await self._prepare_renderer(renderer, media, offset_ms)
            success = await renderer.play()
            if not success:
                raise PlexError("Target speaker rejected playback")

            self._session.replace_media(
                media,
                context,
                command_id,
                play_queue_id=request.query.get("playQueueID", ""),
                play_queue_version=request.query.get("playQueueVersion", ""),
                play_queue_item_id=request.query.get("playQueueItemID", ""),
                container_key=request.query.get("containerKey", ""),
            )
            if offset_ms > 0:
                self._session.set_state("playing", offset_ms)
            await self._sync_session_from_renderer(renderer)
            await self._report_timeline()
            return self._make_response(self._build_poll_xml())
        except PlexError as exc:
            log.warning(f"Plex 播放请求失败: {exc}")
            return web.Response(status=412, text=str(exc))
        except Exception as exc:
            log.error(f"Plex 播放请求异常: {exc}")
            return web.Response(status=500, text="Playback failed")

    async def handle_control(self, request: web.Request):
        action = request.match_info.get("action", "")
        if not action:
            action = request.path.rstrip("/").split("/")[-1]
        self._session.command_id = request.query.get("commandID", self._session.command_id)
        self._session.touch_controller()

        try:
            renderer = self._require_target_renderer(require_media=action not in {"play", "resume"})
        except PlexError as exc:
            if action in {"play", "resume"}:
                return web.Response(status=412, text=str(exc))
            return self._make_response(self._build_poll_xml())

        try:
            if action == "pause":
                await renderer.pause()
                self._session.set_state("paused")
            elif action in {"play", "resume"}:
                success = await renderer.play()
                if not success:
                    raise PlexError("Resume playback failed")
                self._session.set_state("playing")
            elif action == "stop":
                await renderer.stop()
                self._session.clear()
            elif action == "seekTo":
                offset_ms = self._parse_offset_ms(request.query)
                duration_ms = self._session.duration_ms
                if duration_ms > 0 and offset_ms >= duration_ms:
                    offset_ms = max(duration_ms - 1000, 0)
                success = await renderer.seek("REL_TIME", self._format_seconds(offset_ms / 1000))
                if not success:
                    raise PlexError("Seek failed")
                self._session.set_state(self._session.state if self._session.state != "stopped" else "paused", offset_ms)
            elif action == "skipNext":
                await renderer.next_track()
                if request.query.get("key"):
                    self._session.key = request.query.get("key", self._session.key)
            elif action == "skipPrevious":
                await renderer.previous_track()
            elif action == "setParameters":
                await self._handle_set_parameters(renderer, request.query)
            else:
                log.info(f"Plex 收到未实现控制指令: {action} query={dict(request.query)}")

            await self._sync_session_from_renderer(renderer)
            if action in {"pause", "play", "resume", "stop", "seekTo", "skipNext", "skipPrevious", "setParameters"}:
                await self._report_timeline()
            return self._make_response(self._build_poll_xml())
        except PlexError as exc:
            log.warning(f"Plex 控制失败 [{action}]: {exc}")
            return web.Response(status=412, text=str(exc))
        except Exception as exc:
            log.error(f"Plex 控制异常 [{action}]: {exc}")
            return web.Response(status=500, text="Control failed")

    async def handle_poll(self, request: web.Request):
        self._session.command_id = request.query.get("commandID", self._session.command_id)
        self._session.touch_controller()
        renderer = self._get_target_renderer()
        if renderer:
            await self._sync_session_from_renderer(renderer)
        return self._make_response(self._build_poll_xml())

    async def handle_resources(self, request: web.Request):
        persona = self.persona
        display_name = escape(self.config.plex_name or "miPlay")
        protocol_caps = escape("playback,timeline,navigation,playqueues,music")
        content = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<MediaContainer size="1">'
            f'<Player title="{display_name}" '
            f'machineIdentifier="{self.uuid}" '
            f'product="{escape(persona.product)}" '
            f'version="{escape(persona.version)}" '
            f'platform="{escape(persona.platform)}" '
            f'platformVersion="{escape(persona.platform_version)}" '
            'protocol="plex" '
            'protocolVersion="1" '
            f'protocolCapabilities="{protocol_caps}" '
            f'deviceClass="{escape(persona.device_class)}" '
            f'device="{escape(persona.device)}" '
            f'address="{self.local_ip}" '
            f'port="{self.port}" '
            'provides="player,pubsub-player" '
            'publicAddressMatches="1" '
            'owned="1" '
            'relayed="0" '
            'httpsRequired="0">'
            f'<Connection protocol="http" address="{self.local_ip}" port="{self.port}" '
            f'uri="http://{self.local_ip}:{self.port}" local="1" relay="0" secure="0" />'
            "</Player>"
            "</MediaContainer>"
        )
        return self._make_response(content)

    async def handle_options(self, request: web.Request):
        return web.Response(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS, PUT, DELETE",
                "Access-Control-Allow-Headers": (
                    "X-Plex-Client-Identifier, X-Plex-Target-Client-Identifier, "
                    "X-Plex-Token, X-Plex-Session-Identifier, Content-Type, Accept"
                ),
            },
        )

    async def handle_fallback(self, request: web.Request):
        return self._make_response('<MediaContainer size="0"></MediaContainer>')

    async def _prepare_renderer(self, renderer, media: PlexMedia, offset_ms: int):
        if renderer.transport_state in {TRANSPORT_STATE_PLAYING, TRANSPORT_STATE_PAUSED}:
            await renderer.stop()

        metadata = self._build_track_metadata(media)
        await renderer.set_av_transport_uri(media.stream_url, metadata)
        async with renderer._lock:
            renderer._track_duration = media.duration_ms / 1000 if media.duration_ms > 0 else 0.0
            renderer._accumulated_time = offset_ms / 1000 if offset_ms > 0 else 0.0
            renderer._play_start_time = 0.0

    def _require_target_renderer(self, require_media: bool = False):
        target_did = self.config.get_plex_target_did()
        if not target_did:
            raise PlexError("Plex target speaker is not configured")
        renderer = self.miair.get_renderer_by_did(target_did)
        if not renderer or not self.miair.device_server:
            raise PlexError("Target speaker is not ready; start MiAir renderer services first")
        if require_media and not renderer.current_uri:
            raise PlexError("No active Plex media session")
        return renderer

    def _get_target_renderer(self):
        target_did = self.config.get_plex_target_did()
        if not target_did:
            return None
        return self.miair.get_renderer_by_did(target_did)

    def _parse_server_context(self, query) -> PlexServerContext:
        key = query.get("key", "")
        if not key:
            raise PlexError("Missing media key")

        protocol = (query.get("protocol") or "http").lower()
        address = query.get("address") or self.config.plex_server
        if not address:
            raise PlexError("Missing Plex server address")

        try:
            port = int(query.get("port") or 32400)
        except ValueError as exc:
            raise PlexError("Invalid Plex server port") from exc

        token = query.get("token") or query.get("X-Plex-Token") or self.config.plex_token
        if not token:
            raise PlexError("Missing Plex token")

        fetch_address = self.config.plex_server if ".plex.direct" in address and self.config.plex_server else address
        return PlexServerContext(
            protocol=protocol,
            address=address,
            fetch_address=fetch_address,
            port=port,
            token=token,
        )

    async def _fetch_media(self, key: str, context: PlexServerContext) -> PlexMedia:
        metadata_url = self._build_url(context.base_url, key, context.token)
        request_kwargs = self._get_request_kwargs(context)

        async with aiohttp.ClientSession() as session:
            async with session.get(metadata_url, **request_kwargs) as response:
                if response.status != 200:
                    raise PlexError(f"Metadata fetch failed ({response.status})")
                xml_data = await response.text()

        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError as exc:
            raise PlexError("Invalid Plex metadata response") from exc

        item = root.find(".//Track")
        if item is None:
            if root.find(".//Video") is not None or root.find(".//Photo") is not None:
                raise PlexError("Only audio tracks are supported")
            raise PlexError("Track metadata not found")

        part = item.find(".//Part")
        if part is None:
            raise PlexError("Track part missing in metadata")

        part_key = part.get("key")
        if not part_key:
            raise PlexError("Track stream key missing in metadata")

        stream_url = self._build_url(context.base_url, part_key, context.token)
        title = item.get("title") or "Unknown"
        duration_ms = self._safe_int(item.get("duration"))
        rating_key = item.get("ratingKey") or ""

        return PlexMedia(
            key=key,
            rating_key=rating_key,
            title=title,
            duration_ms=duration_ms,
            stream_url=stream_url,
        )

    def _get_request_kwargs(self, context: PlexServerContext) -> dict:
        kwargs = {
            "headers": self._build_plex_headers(self._session.session_id),
        }
        if context.protocol == "https":
            kwargs["ssl"] = False
        return kwargs

    def _build_plex_headers(self, session_id: str) -> dict:
        display_name = self.config.plex_name or "miPlay"
        persona = self.persona
        return {
            "Accept": "application/xml",
            "X-Plex-Client-Identifier": self.uuid,
            "X-Plex-Session-Identifier": session_id,
            "X-Plex-Device-Name": display_name,
            "X-Plex-Platform": persona.platform,
            "X-Plex-Platform-Version": persona.platform_version,
            "X-Plex-Product": persona.product,
            "X-Plex-Device": persona.device,
            "X-Plex-Provides": "player,pubsub-player",
        }

    async def _sync_session_from_renderer(self, renderer):
        if not self._session.has_media():
            return

        state_map = {
            TRANSPORT_STATE_PLAYING: "playing",
            TRANSPORT_STATE_PAUSED: "paused",
            TRANSPORT_STATE_STOPPED: "stopped",
        }
        new_state = state_map.get(renderer.transport_state, self._session.state)
        position_ms = int(renderer._get_elapsed_time() * 1000)
        if renderer._track_duration > 0:
            self._session.duration_ms = int(renderer._track_duration * 1000)
        if new_state == "stopped":
            self._session.position_ms = position_ms
            self._session.state = new_state
            self._session.updated_at = time.monotonic()
            return
        self._session.set_state(new_state, position_ms)

    async def _report_timeline_loop(self):
        try:
            while self.running:
                await asyncio.sleep(10)
                if self._session.has_media() and (
                    time.monotonic() - self._session.controller_updated_at > 30
                ):
                    await self._handle_stale_session()
                if not self._session.has_media() or self._session.state != "playing":
                    continue
                renderer = self._get_target_renderer()
                if renderer:
                    await self._sync_session_from_renderer(renderer)
                await self._report_timeline()
        except asyncio.CancelledError:
            pass

    async def _handle_stale_session(self):
        renderer = self._get_target_renderer()
        if renderer and renderer.transport_state in {TRANSPORT_STATE_PLAYING, TRANSPORT_STATE_PAUSED}:
            log.info("Plex 控制端超时，自动停止当前播放")
            await renderer.stop()
        self._session.clear()

    async def _report_timeline(self):
        session = self._session
        if not session.has_media() or not session.server_address or not session.token:
            return

        base_url = f"{session.server_protocol}://{session.server_address}:{session.server_port}"
        url = f"{base_url}/:/timeline"
        headers = self._build_plex_headers(session.session_id)
        headers["X-Plex-Token"] = session.token
        params = {
            "ratingKey": session.rating_key,
            "key": session.key,
            "state": session.state,
            "time": str(session.current_position_ms()),
            "duration": str(session.duration_ms),
        }
        if session.play_queue_id:
            params["playQueueID"] = session.play_queue_id
        if session.play_queue_version:
            params["playQueueVersion"] = session.play_queue_version
        if session.play_queue_item_id:
            params["playQueueItemID"] = session.play_queue_item_id
        if session.container_key:
            params["containerKey"] = session.container_key
        request_kwargs = {"headers": headers, "params": params}
        if session.server_protocol == "https":
            request_kwargs["ssl"] = False

        try:
            async with aiohttp.ClientSession() as client:
                async with client.post(url, **request_kwargs):
                    pass
        except Exception as exc:
            log.debug(f"Timeline 同步失败: {exc}")

    def _build_poll_xml(self) -> str:
        session = self._session
        music_time = session.current_position_ms() if session.has_media() else 0
        music_duration = session.duration_ms if session.has_media() else 0
        music_state = session.state if session.has_media() else "stopped"
        key = escape(session.key)
        rating_key = escape(session.rating_key)
        title = escape(session.title)
        controllable = escape(session.controllable)
        play_queue_attrs = ""
        if session.play_queue_id:
            play_queue_attrs += f' playQueueID="{escape(session.play_queue_id)}"'
        if session.play_queue_version:
            play_queue_attrs += f' playQueueVersion="{escape(session.play_queue_version)}"'
        if session.play_queue_item_id:
            play_queue_attrs += f' playQueueItemID="{escape(session.play_queue_item_id)}"'
        if session.container_key:
            play_queue_attrs += f' containerKey="{escape(session.container_key)}"'
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<MediaContainer size="3" commandID="{escape(self._session.command_id)}" '
            f'machineIdentifier="{self.uuid}">'
            f'<Timeline type="music" state="{music_state}" time="{music_time}" '
            f'duration="{music_duration}" key="{key}" ratingKey="{rating_key}" '
            f'title="{title}" machineIdentifier="{self.uuid}" protocol="plex" '
            f'controllable="{controllable}" volume="100"{play_queue_attrs} />'
            '<Timeline type="video" state="stopped" time="0" duration="0" />'
            '<Timeline type="photo" state="stopped" time="0" duration="0" />'
            "</MediaContainer>"
        )

    async def _handle_set_parameters(self, renderer, query):
        volume = query.get("volume")
        if volume not in (None, ""):
            try:
                await renderer.set_volume(int(float(volume)))
            except ValueError:
                log.debug(f"忽略无效 Plex 音量参数: {volume}")

        if query.get("shuffle") is not None or query.get("repeat") is not None:
            log.info(f"Plex setParameters: {dict(query)}")

    def _make_response(self, content: str, content_type: str = "text/xml") -> web.Response:
        return web.Response(
            text=content,
            content_type=content_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Expose-Headers": "X-Plex-Client-Identifier, X-Plex-Session-Identifier",
                "X-Plex-Client-Identifier": self.uuid,
                "Plex-Client-Identifier": self.uuid,
                "X-Plex-Session-Identifier": self._session.session_id,
                "X-Plex-Protocol": "1.0",
            },
        )

    def _build_track_metadata(self, media: PlexMedia) -> str:
        duration = self._format_seconds(media.duration_ms / 1000)
        title = escape(media.title)
        uri = escape(media.stream_url)
        return (
            '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            f'<item id="{escape(media.rating_key or media.key)}" parentID="0" restricted="1">'
            f"<dc:title>{title}</dc:title>"
            "<upnp:class>object.item.audioItem.musicTrack</upnp:class>"
            f'<res protocolInfo="http-get:*:audio/*:*" duration="{duration}">{uri}</res>'
            "</item>"
            "</DIDL-Lite>"
        )

    async def gdm_announcer(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        msg = self._build_gdm_payload(hello=True)
        try:
            while self.running:
                try:
                    sock.sendto(msg, ("255.255.255.255", 32412))
                    sock.sendto(msg, ("255.255.255.255", 32414))
                    if self.config.plex_server:
                        sock.sendto(msg, (self.config.plex_server, 32412))
                except Exception:
                    pass
                await asyncio.sleep(15)
        except asyncio.CancelledError:
            pass
        finally:
            sock.close()

    async def gdm_responder(self):
        player = self

        class GDMSProtocol(asyncio.DatagramProtocol):
            def connection_made(self, transport):
                player._gdm_transports.append(transport)

            def datagram_received(self, data, addr):
                if b"PLAYER" not in data and b"M-SEARCH" not in data and b"HELLO" not in data:
                    return
                response = player._build_gdm_payload(hello=False)
                if player._gdm_transport:
                    player._gdm_transport.sendto(response, addr)

        loop = asyncio.get_running_loop()
        sockets = []
        try:
            for port in (32412, 32414):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
                sock.bind(("0.0.0.0", port))
                sockets.append(sock)
                await loop.create_datagram_endpoint(lambda: GDMSProtocol(), sock=sock)
            while self.running:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            for sock in sockets:
                sock.close()

    def _build_gdm_payload(self, hello: bool) -> bytes:
        status = "HELLO * HTTP/1.0" if hello else "HTTP/1.0 200 OK"
        display_name = self.config.plex_name or "miPlay"
        persona = self.persona
        payload = (
            f"{status}\r\n"
            f"Name: {display_name}\r\n"
            f"Host: {self.local_ip}\r\n"
            f"Port: {self.port}\r\n"
            f"Location: http://{self.local_ip}:{self.port}\r\n"
            f"Resource-Identifier: {self.uuid}\r\n"
            "Content-Type: plex/media-player\r\n"
            f"Product: {persona.product}\r\n"
            f"Version: {persona.version}\r\n"
            f"Updated-At: {int(time.time())}\r\n"
            "Protocol: plex\r\n"
            "Protocol-Version: 1\r\n"
            "Protocol-Capabilities: playback,timeline,navigation,playqueues,music\r\n"
            f"Device-Class: {persona.device_class}\r\n"
            "\r\n"
        )
        return payload.encode()

    async def _start_mdns(self):
        try:
            self.zc = Zeroconf()
            display_name = self.config.plex_name or "miPlay"
            self.zc_info = ServiceInfo(
                "_plexclient._tcp.local.",
                f"{display_name}.{self.uuid}._plexclient._tcp.local.",
                addresses=[socket.inet_aton(self.local_ip)],
                port=self.port,
                properties=self._build_mdns_properties(),
                server=f"{self.uuid}.local.",
            )
            self.zc.register_service(self.zc_info)
            log.info(f"Plex mDNS 广播已启动: {display_name}")
            while self.running:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.debug(f"Plex mDNS 启动失败: {exc}")

    def _build_mdns_properties(self) -> dict[str, str]:
        persona = self.persona
        display_name = self.config.plex_name or "miPlay"
        return {
            "machineIdentifier": self.uuid,
            "name": display_name,
            "product": persona.product,
            "version": persona.version,
            "platform": persona.platform,
            "device": persona.device,
            "deviceClass": persona.device_class,
            "protocol": "plex",
            "protocolVersion": "1",
            "protocolCapabilities": "playback,timeline,navigation,playqueues,music",
            "resourceIdentifier": self.uuid,
            "port": str(self.port),
        }

    @staticmethod
    def _build_url(base_url: str, path: str, token: str) -> str:
        url = f"{base_url}{path}"
        parts = list(urlsplit(url))
        query = parse_qsl(parts[3], keep_blank_values=True)
        if token and not any(key == "X-Plex-Token" for key, _ in query):
            query.append(("X-Plex-Token", token))
        parts[3] = urlencode(query)
        return urlunsplit(parts)

    @staticmethod
    def _parse_offset_ms(query) -> int:
        for key in ("offset", "time", "viewOffset"):
            value = query.get(key)
            if value in (None, ""):
                continue
            try:
                return max(0, int(float(value)))
            except ValueError:
                continue
        return 0

    @staticmethod
    def _format_seconds(seconds: float) -> str:
        total = max(0, int(seconds))
        hours = total // 3600
        minutes = (total % 3600) // 60
        secs = total % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    @staticmethod
    def _safe_int(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
