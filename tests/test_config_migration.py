from miplay.config import Config, TargetConfig


def test_migrate_legacy_config_to_targets():
    legacy = {
        "account": "user@example.com",
        "password": "secret",
        "cookie": "userId=1; passToken=2",
        "hostname": "192.168.1.5",
        "web_port": 8300,
        "mi_did": "123,456",
        "speakers": {
            "123": {
                "name": "Living Room Speaker",
                "dlna_name": "Living Room AirPlay",
                "device_id": "dev-123",
                "hardware": "OH2",
                "enabled": True,
            },
            "456": {
                "name": "Bedroom Speaker",
                "enabled": False,
            },
        },
        "plex_port": 32500,
        "dlna_port": 8200,
    }

    migrated = Config._migrate(legacy)

    assert migrated["host"] == "192.168.1.5"
    assert migrated["xiaomi"]["account"] == "user@example.com"
    assert migrated["xiaomi"]["cookie"] == "userId=1; passToken=2"
    assert migrated["legacy"]["plex_port"] == 32500
    assert len(migrated["targets"]) == 2
    assert migrated["targets"][0]["did"] == "123"
    assert migrated["targets"][0]["airplay_name"] == "Living Room AirPlay"
    assert migrated["targets"][1]["enabled"] is False


def test_target_config_generates_default_names():
    target = TargetConfig(id="", did="789", name="", airplay_name="")

    assert target.id == "789"
    assert target.name == "Xiaomi Speaker 789"
    assert target.airplay_name == "Xiaomi Speaker 789"
    assert target.slug == "789"


def test_config_coerces_nested_dicts():
    config = Config(
        host="127.0.0.1",
        xiaomi={"account": "demo@example.com", "cookie": "x"},
        external={"wired_airplay_name": "Living Room Wired"},
        targets=[{"did": "123", "name": "Speaker", "airplay_name": "Speaker AP"}],
    )

    assert config.xiaomi.account == "demo@example.com"
    assert config.external.wired_airplay_name == "Living Room Wired"
    assert config.targets[0].did == "123"
