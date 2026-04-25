import asyncio
import logging
import socket
import uuid
from aiohttp import web, ClientSession
import plexapi
from plexapi.myplex import MyPlexAccount

# 移除全局 MY_UUID，改为每个实例基于 DID 生成
plexapi.BASE_HEADERS['X-Plex-Product'] = 'Plex for Mac'
plexapi.BASE_HEADERS['X-Plex-Platform'] = 'macOS'
plexapi.BASE_HEADERS['X-Plex-Provides'] = 'player,music,playback,timeline,navigation,pubsub-player'

log = logging.getLogger("miair.plex")

class PlexPlayer:
    """Plex Companion 模拟器 - 深度对齐 Plexamp 投送协议"""

    def __init__(self, miair, controller=None, port=None):
        self.miair = miair
        self.config = miair.config
        self.controller = controller
        self.port = port or self.config.plex_port
        
        # 为每个音箱生成唯一的 UUID
        seed = f"miplay-{controller.did if controller else 'global'}"
        self.uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))
        
        self.running = False
        self.current_command_id = "0"
        self.current_key = ""
        self.server_runner = None
        self.plex_account = None
        self.local_ip = self.config.hostname

    async def start(self):
        self.running = True

        app = web.Application()
        # 补全 Plexamp 可能会探测的所有接口
        app.router.add_get('/player/playback/playMedia', self.handle_play_media)
        app.router.add_get('/player/playback/{action}', self.handle_control)
        app.router.add_get('/player/timeline/poll', self.handle_poll)
        app.router.add_route('OPTIONS', '/{tail:.*}', self.handle_options)
        app.router.add_get('/{tail:.*}', self.handle_fallback)

        self.server_runner = web.AppRunner(app, access_log=None)
        await self.server_runner.setup()
        site = web.TCPSite(self.server_runner, '0.0.0.0', self.port)
        await site.start()
        
        display_name = self.config.plex_name
        if self.controller:
            display_name = self.controller.speaker.get_dlna_name()
        elif not display_name:
            display_name = f"Plex-{socket.gethostname()}"
            
        log.info(f"Plex 模拟播放器启动 [{display_name}]，端口: {self.port}")

        # 启动本地广播、查询响应与云端同步
        asyncio.create_task(self.gdm_announcer())
        asyncio.create_task(self.gdm_responder())
        if self.config.plex_token:
            asyncio.create_task(self.maintain_plex_identity())

    async def maintain_plex_identity(self):
        """维持 Plex 会话，通过定期刷新资源列表向服务器‘刷脸’"""
        while self.running:
            try:
                if not self.plex_account:
                    self.plex_account = MyPlexAccount(token=self.config.plex_token)
                    log.info(f"Plex 账号同步成功: {self.plex_account.username}")
                
                # 更新本地 IP，防止网络切换导致的失效
                self.local_ip = await self._get_local_ip()
                self.plex_account.resources()
            except Exception as e:
                log.error(f"Plex 身份维持失败: {e}")
            await asyncio.sleep(300)

    async def _get_local_ip(self):
        # 内部再次调用 config 的探测逻辑确保实时性
        return self.config._detect_local_ip()

    async def gdm_announcer(self):
        """局域网广播 (Protocol 设为 plex 是进入 Plexamp 的门票)"""
        GDM_ADDR = "239.0.0.250"
        GDM_PORT = 32413
        
        display_name = self.config.plex_name or f"Plex-{socket.gethostname()}"
        
        # 修正：Protocol 必须是 plex，增加 Location 显式指明 IP
        msg = (
            f"HELLO * HTTP/1.0\r\n"
            f"Name: {display_name}\r\n"
            f"Port: {self.port}\r\n"
            f"Location: http://{self.local_ip}:{self.port}\r\n"
            f"Content-Type: plex/media-player\r\n"
            f"Resource-Identifier: {self.uuid}\r\n"
            f"Product: Plex for Mac\r\n"
            f"Protocol: plex\r\n"
            f"Protocol-Version: 1\r\n"
            f"Protocol-Capabilities: timeline,playback,navigation,mirroring,playqueues,music\r\n"
            f"Device-Class: pc\r\n"
            f"Version: 1.83.1\r\n"
            f"\r\n"
        ).encode('utf-8')

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        
        while self.running:
            try:
                sock.sendto(msg, (GDM_ADDR, GDM_PORT))
                if self.config.plex_server:
                    sock.sendto(msg, (self.config.plex_server, GDM_PORT))
            except Exception:
                pass
            await asyncio.sleep(20)

    async def gdm_responder(self):
        """响应局域网内的 GDM 查询 (Plexamp 常用此方式主动探测)"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', 32414))
        except Exception as e:
            log.warning(f"无法绑定 GDM 响应端口 32414: {e}")
            return

        loop = asyncio.get_event_loop()
        display_name = self.config.plex_name or f"Plex-{socket.gethostname()}"
        
        # 响应消息格式与公告一致
        resp_msg = (
            f"HTTP/1.0 200 OK\r\n"
            f"Name: {display_name}\r\n"
            f"Port: {self.port}\r\n"
            f"Location: http://{self.local_ip}:{self.port}\r\n"
            f"Content-Type: plex/media-player\r\n"
            f"Resource-Identifier: {self.uuid}\r\n"
            f"Product: Plex for Mac\r\n"
            f"Protocol: plex\r\n"
            f"Protocol-Version: 1\r\n"
            f"Protocol-Capabilities: timeline,playback,navigation,mirroring,playqueues,music\r\n"
            f"Device-Class: pc\r\n"
            f"Version: 1.83.1\r\n"
            f"\r\n"
        ).encode('utf-8')

        while self.running:
            try:
                data, addr = await loop.run_in_executor(None, sock.recvfrom, 1024)
                if b"M-SEARCH" in data or b"HELLO" in data:
                    log.debug(f"收到 GDM 查询，来自 {addr}")
                    sock.sendto(resp_msg, addr)
            except Exception:
                await asyncio.sleep(1)
        sock.close()

    def _get_xml_response(self, content=""):
        # 强制包含 machineIdentifier 和 protocol="plex"
        xml = (
            f'<?xml version="1.0" encoding="utf-8" ?>\n'
            f'<MediaContainer commandID="{self.current_command_id}" '
            f'machineIdentifier="{self.uuid}" protocol="plex" version="1.0.0">\n'
            f'{content}\n'
            f'</MediaContainer>'
        )
        return xml

    def _make_response(self, text, content_type='application/xml'):
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers': 'X-Plex-Token, X-Plex-Client-Identifier, Content-Type',
            'Access-Control-Max-Age': '86400'
        }
        return web.Response(text=text, content_type=content_type, headers=headers)

    async def handle_play_media(self, request):
        query = request.query
        token = query.get('X-Plex-Token') or self.config.plex_token
        if self.config.plex_token and token != self.config.plex_token:
            return self._make_response("", content_type='text/plain')

        self.current_command_id = query.get('commandID', self.current_command_id)
        key = query.get('key')
        address = query.get('address') or self.config.plex_server
        port = query.get('port') or "32400"
        
        if key and address:
            try:
                # 核心修复：解析 metadata key 为真实的音频流地址
                from plexapi.server import PlexServer
                server = PlexServer(f"http://{address}:{port}", token)
                item = server.fetchItem(key)
                
                # 强制 Direct Play: 获取原始文件路径，避开 M3U8 转码
                # 优先取第一个媒体文件的第一个分片
                part = item.media[0].parts[0]
                stream_url = f"http://{address}:{port}{part.key}?X-Plex-Token={token}"
                
                self.current_key = key
                log.info(f"Plex 媒体解析成功 (Direct): {item.title} -> {stream_url}")
                
                await self._broadcast_to_speakers("play", stream_url)
            except Exception as e:
                log.error(f"Plex 媒体解析失败: {e}")
        
        return self._make_response(self._get_xml_response())

    async def handle_control(self, request):
        token = request.query.get('X-Plex-Token')
        if self.config.plex_token and token != self.config.plex_token:
            return self._make_response("", content_type='text/plain')

        action = request.match_info['action']
        self.current_command_id = request.query.get('commandID', self.current_command_id)

        if action == "pause":
            await self._broadcast_to_speakers("pause")
        elif action in ("play", "resume"):
            await self._broadcast_to_speakers("play")
        elif action == "stop":
            self.current_key = ""
            await self._broadcast_to_speakers("stop")
        
        return self._make_response(self._get_xml_response())

    async def handle_poll(self, request):
        self.current_command_id = request.query.get('commandID', self.current_command_id)
        # 补全 Timeline 状态，确保 Plex Web 不会认为播放失败
        music_state = "playing" if self.current_key else "stopped"
        content = (
            f'<Timeline type="music" state="{music_state}" time="0" duration="0" '
            f'key="{self.current_key}" volume="50" '
            f'machineIdentifier="{self.uuid}" protocol="plex" '
            f'controllable="playPause,stop,volume,seekTo,skipPrevious,skipNext" />'
            f'<Timeline type="video" state="stopped" />'
            f'<Timeline type="photo" state="stopped" />'
        )
        return self._make_response(self._get_xml_response(content))

    async def handle_fallback(self, request):
        return self._make_response(self._get_xml_response())

    async def handle_options(self, request):
        return self._make_response("", content_type="text/plain")

    async def _broadcast_to_speakers(self, action, url=None):
        # 如果绑定了具体控制器，只发给它；否则发给所有（向上兼容）
        target_controllers = [self.controller] if self.controller else self.miair.speaker_manager.controllers.values()
        
        for controller in target_controllers:
            if not controller: continue
            try:
                if action == "play" and url:
                    await controller.play_url(url)
                elif action == "pause":
                    await controller.pause()
                elif action in ("play", "resume"):
                    await controller.play()
                elif action == "stop":
                    await controller.stop()
            except Exception as e:
                log.error(f"小米音箱下发失败: {e}")

    async def stop(self):
        self.running = False
        if self.server_runner:
            await self.server_runner.cleanup()
            self.server_runner = None
        log.info("Plex 模拟器已停止")
