from __future__ import annotations

import json
import os
import re
import socket
import threading
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field


def _slugify(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip()).strip("-").lower()
    return value or "target"


def _detect_local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("1.1.1.1", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


def normalize_service_name(name: str) -> str:
    return " ".join(name.strip().split())


def detect_name_conflicts(
    targets: list["TargetConfig"],
    wired_airplay_name: str = "",
) -> list[str]:
    warnings: list[str] = []
    enabled_targets = [target for target in targets if target.enabled]
    counts = Counter(
        normalize_service_name(target.airplay_name).casefold()
        for target in enabled_targets
        if normalize_service_name(target.airplay_name)
    )
    duplicates = {name for name, count in counts.items() if count > 1}
    if duplicates:
        warnings.append("MiPlay targets contain duplicate AirPlay names; rename them to avoid mDNS ambiguity.")

    wired_name = normalize_service_name(wired_airplay_name)
    if wired_name:
        for target in enabled_targets:
            if normalize_service_name(target.airplay_name).casefold() == wired_name.casefold():
                warnings.append(
                    f"Target '{target.airplay_name}' conflicts with external wired AirPlay name '{wired_name}'."
                )
                break
    return warnings


def build_external_status(config: "Config") -> dict:
    wired_name = normalize_service_name(config.external.wired_airplay_name)
    warnings = detect_name_conflicts(config.targets, wired_name)
    return {
        "wired_airplay_name": wired_name,
        "name_conflicts": warnings,
        "managed_externally": bool(wired_name),
    }


def is_port_available(port: int, host: str = "0.0.0.0") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


@dataclass
class XiaomiConfig:
    account: str = ""
    password: str = ""
    cookie: str = ""

    def __post_init__(self):
        if not self.account:
            self.account = os.getenv("XIAOMI_ACCOUNT", "")
        if not self.password:
            self.password = os.getenv("XIAOMI_PASSWORD", "")
        if not self.cookie:
            self.cookie = os.getenv("XIAOMI_COOKIE", "")


@dataclass
class ExternalServiceConfig:
    wired_airplay_name: str = ""

    def __post_init__(self):
        if not self.wired_airplay_name:
            self.wired_airplay_name = os.getenv("WIRED_AIRPLAY_NAME", "").strip()


@dataclass
class TargetConfig:
    id: str = ""
    did: str = ""
    name: str = ""
    airplay_name: str = ""
    enabled: bool = True
    device_id: str = ""
    hardware: str = ""
    use_music_api: bool = False

    def __post_init__(self):
        self.id = self.id or self.did or str(uuid.uuid4())
        self.name = self.name.strip()
        self.airplay_name = self.airplay_name.strip()
        self.ensure_names()

    def ensure_names(self):
        if not self.name:
            self.name = f"Xiaomi Speaker {self.did or self.id}"
        if not self.airplay_name:
            self.airplay_name = self.name

    @property
    def slug(self) -> str:
        return _slugify(self.id or self.did or self.airplay_name)


@dataclass
class Config:
    host: str = ""
    web_port: int = 8300
    verbose: bool = False
    xiaomi: XiaomiConfig = field(default_factory=XiaomiConfig)
    external: ExternalServiceConfig = field(default_factory=ExternalServiceConfig)
    targets: list[TargetConfig] = field(default_factory=list)
    legacy: dict = field(default_factory=dict)
    conf_path: str = "conf"

    _save_lock = threading.Lock()

    def __post_init__(self):
        if isinstance(self.xiaomi, dict):
            self.xiaomi = XiaomiConfig(**self.xiaomi)
        if isinstance(self.external, dict):
            self.external = ExternalServiceConfig(**self.external)
        if not self.host:
            self.host = os.getenv("MIPLAY_HOST", "").strip()
        if not self.host:
            self.host = _detect_local_ip()
        env_web_port = os.getenv("WEB_PORT")
        if env_web_port:
            try:
                self.web_port = int(env_web_port)
            except ValueError:
                pass
        self.targets = [self._coerce_target(item) for item in self.targets]

    @property
    def config_file(self) -> str:
        return os.path.join(self.conf_path, "config.json")

    def _coerce_target(self, item: TargetConfig | dict) -> TargetConfig:
        if isinstance(item, TargetConfig):
            item.ensure_names()
            return item
        target = TargetConfig(**item)
        target.ensure_names()
        return target

    def get_enabled_targets(self) -> list[TargetConfig]:
        return [target for target in self.targets if target.enabled]

    def get_target(self, target_id: str) -> TargetConfig | None:
        for target in self.targets:
            if target.id == target_id:
                return target
        return None

    def get_target_by_did(self, did: str) -> TargetConfig | None:
        for target in self.targets:
            if target.did == did:
                return target
        return None

    def set_targets(self, targets: list[TargetConfig | dict]):
        self.targets = [self._coerce_target(item) for item in targets]

    def save(self):
        with self._save_lock:
            os.makedirs(self.conf_path, exist_ok=True)
            data = asdict(self)
            with open(self.config_file, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, conf_path: str = "conf") -> "Config":
        if not os.path.isabs(conf_path):
            conf_path = os.path.abspath(conf_path)
        config_file = os.path.join(conf_path, "config.json")
        if not os.path.exists(config_file):
            config = cls(conf_path=conf_path)
            config.save()
            return config

        with open(config_file, encoding="utf-8") as file:
            raw = json.load(file)
        migrated = cls._migrate(raw)
        migrated["conf_path"] = conf_path
        config = cls(**migrated)
        if migrated != raw:
            config.save()
        return config

    @classmethod
    def _migrate(cls, raw: dict) -> dict:
        if "xiaomi" in raw and "targets" in raw:
            return {
                "host": raw.get("host", raw.get("hostname", "")),
                "web_port": raw.get("web_port", 8300),
                "verbose": raw.get("verbose", False),
                "xiaomi": raw.get("xiaomi", {}),
                "external": raw.get("external", {}),
                "targets": raw.get("targets", []),
                "legacy": raw.get("legacy", {}),
            }

        legacy = {}
        legacy_keys = (
            "hostname",
            "dlna_port",
            "plex_port",
            "plex_token",
            "plex_server",
            "plex_name",
            "plex_target_did",
            "plex_client_id",
            "auto_play_on_set_uri",
            "auto_resume_on_interrupt",
            "resume_delay_seconds",
            "enable_voice_control",
            "voice_poll_interval",
            "proxy_enabled",
            "mi_did",
        )
        for key in legacy_keys:
            if key in raw:
                legacy[key] = raw[key]

        speakers = raw.get("speakers", {}) or {}
        dids = []
        if raw.get("mi_did"):
            dids = [item.strip() for item in str(raw["mi_did"]).split(",") if item.strip()]
        elif speakers:
            dids = list(speakers.keys())

        targets = []
        for did in dids:
            source = speakers.get(did, {}) if isinstance(speakers, dict) else {}
            if not isinstance(source, dict):
                source = {}
            name = source.get("name", "").strip()
            airplay_name = source.get("airplay_name", "").strip() or source.get("dlna_name", "").strip() or name
            targets.append(
                {
                    "id": source.get("id", "") or did,
                    "did": did,
                    "name": name or f"Xiaomi Speaker {did}",
                    "airplay_name": airplay_name or name or f"Xiaomi Speaker {did}",
                    "enabled": source.get("enabled", True),
                    "device_id": source.get("device_id", ""),
                    "hardware": source.get("hardware", ""),
                    "use_music_api": source.get("use_music_api", False),
                }
            )

        return {
            "host": raw.get("host", raw.get("hostname", "")),
            "web_port": raw.get("web_port", 8300),
            "verbose": raw.get("verbose", False),
            "xiaomi": {
                "account": raw.get("account", ""),
                "password": raw.get("password", ""),
                "cookie": raw.get("cookie", ""),
            },
            "external": {
                "wired_airplay_name": raw.get("wired_airplay_name", ""),
            },
            "targets": targets,
            "legacy": legacy,
        }
