from unittest.mock import patch

from miplay.config import TargetConfig, detect_name_conflicts, is_port_available


def test_detect_duplicate_target_names_and_wired_conflict():
    targets = [
        TargetConfig(id="a", did="1", name="One", airplay_name="Kitchen"),
        TargetConfig(id="b", did="2", name="Two", airplay_name="Kitchen"),
        TargetConfig(id="c", did="3", name="Three", airplay_name="Wired"),
    ]

    warnings = detect_name_conflicts(targets, wired_airplay_name="Wired")

    assert any("duplicate AirPlay names" in warning for warning in warnings)
    assert any("conflicts with external wired AirPlay name" in warning for warning in warnings)


def test_is_port_available_detects_bind_failure():
    with patch("socket.socket.bind", side_effect=OSError("busy")):
        assert is_port_available(8300, host="127.0.0.1") is False

    with patch("socket.socket.bind", return_value=None):
        assert is_port_available(8300, host="127.0.0.1") is True
