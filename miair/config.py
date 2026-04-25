from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field


@dataclass
class Speaker:
    """单个小爱音箱的配置"""

    did: str = ""
    device_id: str = ""
    hardware: str = ""
    name: str = ""
    dlna_name: str = ""
    udn: str = ""
    use_music_api: bool = False
    enabled: bool = True

    # 不支持无损格式的音箱型号列表
    _NON_LOSSLESS_HARDWARE = {"L05B", "L05C", "LX06", "L16A"}

    def get_dlna_name(self) -> str:
        return self.dlna_name or self.name or f"XiaoAI-{self.did}"

    def ensure_udn(self):
        if not self.udn:
            self.udn = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"miair-{self.did}"))

    def needs_audio_conversion(self, content_type: str = "") -> bool:
        """检查是否需要转换音频格式
        
        部分音箱不支持无损格式，需要转换为 WAV (PCM) 播放
        """
        if self.hardware not in self._NON_LOSSLESS_HARDWARE:
            return False
        
        # 已经是可直接播放的格式则不需要转换
        if content_type:
            ct = content_type.lower()
            if "mp3" in ct or "mpeg" in ct or "wav" in ct or "x-wav" in ct:
                return False
        
        return True


@dataclass
class Config:
    """MiAir 全局配置"""

    account: str = ""
    password: str = ""
    mi_did: str = ""
    cookie: str = ""
    hostname: str = ""
    dlna_port: int = 8200
    web_port: int = 8300
    plex_port: int = 32500
    plex_token: str = ""
    plex_server: str = ""
    plex_name: str = ""
    conf_path: str = "conf"
    verbose: bool = False
    # log_file 不存储，动态计算相对于 conf_path
    proxy_enabled: bool = False
    auto_play_on_set_uri: bool = False
    # 实验性功能：打断后续播
    auto_resume_on_interrupt: bool = False
    resume_delay_seconds: int = 5
    # 语音控制
    enable_voice_control: bool = False
    voice_poll_interval: int = 1
    speakers: dict = field(default_factory=dict)

    # 保存配置的线程锁（类级别共享）
    _save_lock = threading.Lock()

    @property
    def log_file(self) -> str:
        """日志文件路径，动态计算"""
        return os.path.join(self.conf_path, "miair.log")

    def __post_init__(self):
        if not self.account:
            self.account = os.getenv("MI_USER", "")
        if not self.password:
            self.password = os.getenv("MI_PASS", "")
        if not self.mi_did:
            self.mi_did = os.getenv("MI_DID", "")
        if not self.hostname:
            self.hostname = os.getenv("MIAIR_HOSTNAME", "")
        if not self.hostname:
            self.hostname = self._detect_local_ip()
        
        # 端口环境变量支持
        env_dlna_port = os.getenv("DLNA_PORT")
        if env_dlna_port:
            try:
                self.dlna_port = int(env_dlna_port)
            except ValueError:
                pass
        
        env_web_port = os.getenv("WEB_PORT")
        if env_web_port:
            try:
                self.web_port = int(env_web_port)
            except ValueError:
                pass

        env_plex_port = os.getenv("PLEX_PORT")
        if env_plex_port:
            try:
                self.plex_port = int(env_plex_port)
            except ValueError:
                pass
        
        self.plex_token = os.getenv("PLEX_TOKEN", self.plex_token)
        self.plex_server = os.getenv("PLEX_SERVER", self.plex_server)
        self.plex_name = os.getenv("PLEX_NAME", self.plex_name)

    def _detect_local_ip(self) -> str:
        """强制探测物理网卡 IP，排除虚拟网卡 (解决 198.18.0.1 显示 Bug)"""
        import socket
        import subprocess
        import re

        # 尝试 1: 通过路由追踪寻找出站 IP
        target = self.plex_server or "1.1.1.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target, 32400))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("198.18."):
                return ip
        except:
            pass

        # 尝试 2: 在 Mac/Linux 下解析 ifconfig (最保险的物理层搜索)
        try:
            # 执行 ifconfig 获取所有网卡信息
            output = subprocess.check_output(["ifconfig"], text=True)
            # 正则匹配常见的私有 IP 地址，且排除 198.18 和 127.0.0.1
            pattern = r'inet\s+(10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2[0-9]|3[0-1])\.\d+\.\d+)'
            matches = re.findall(pattern, output)
            if matches:
                # 返回第一个匹配到的物理局域网地址
                return matches[0][0]
        except:
            pass

        return "127.0.0.1"

    @property
    def mi_token_home(self) -> str:
        return os.path.join(self.conf_path, ".mi.token")

    @property
    def config_file(self) -> str:
        return os.path.join(self.conf_path, "config.json")

    def get_did_list(self) -> list[str]:
        """获取配置的设备 DID 列表"""
        if not self.mi_did:
            return []
        return [d.strip() for d in self.mi_did.split(",") if d.strip()]

    def get_speaker(self, did: str) -> Speaker:
        """获取或创建指定 DID 的 Speaker 配置"""
        if did not in self.speakers:
            self.speakers[did] = Speaker(did=did)
        speaker = self.speakers[did]
        if isinstance(speaker, dict):
            speaker = Speaker(**speaker)
            self.speakers[did] = speaker
        speaker.ensure_udn()
        return speaker

    def get_enabled_speakers(self) -> list[Speaker]:
        """获取所有已启用的 Speaker"""
        result = []
        for did in self.get_did_list():
            speaker = self.get_speaker(did)
            if speaker.enabled:
                result.append(speaker)
        return result

    def save(self):
        """保存配置到文件（线程安全）"""
        with self._save_lock:
            os.makedirs(self.conf_path, exist_ok=True)
            data = asdict(self)
            # speakers 中的 Speaker 对象转为 dict
            speakers_data = {}
            for did, speaker in data.get("speakers", {}).items():
                if isinstance(speaker, Speaker):
                    speakers_data[did] = asdict(speaker)
                else:
                    speakers_data[did] = speaker
            data["speakers"] = speakers_data

            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, conf_path: str = "conf") -> "Config":
        """从文件加载配置"""
        # 标准化路径为绝对路径，确保无论从哪里运行都能正确定位
        if not os.path.isabs(conf_path):
            conf_path = os.path.abspath(conf_path)
        config_file = os.path.join(conf_path, "config.json")
        if os.path.exists(config_file):
            with open(config_file, encoding="utf-8") as f:
                data = json.load(f)
            data["conf_path"] = conf_path
            # 过滤掉不存在的字段，避免TypeError
            import inspect
            sig = inspect.signature(cls.__init__)
            valid_params = list(sig.parameters.keys())
            filtered_data = {k: v for k, v in data.items() if k in valid_params}
            return cls(**filtered_data)
        return cls(conf_path=conf_path)
