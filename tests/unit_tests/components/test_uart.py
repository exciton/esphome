"""Tests for UART hardware port assignment on ESP32."""

from unittest.mock import patch

import pytest

from esphome import config, yaml_util
from esphome.components.uart import _esp32_assign_ports
from esphome.core import CORE

BASE_CONFIG = """
esphome:
  name: test

esp32:
  board: {board}
  framework:
    type: esp-idf

logger:
{logger_extra}
uart:
{buses}
"""

# Pins that are free to use on both boards under test
BUS_PINS = [(16, 17), (18, 21), (4, 15)]


def _make_config(board: str, logger_extra: str, bus_count: int) -> str:
    buses = "".join(
        f"  - id: bus{i}\n"
        f"    tx_pin: GPIO{tx}\n"
        f"    rx_pin: GPIO{rx}\n"
        f"    baud_rate: 9600\n"
        for i, (tx, rx) in enumerate(BUS_PINS[:bus_count])
    )
    return BASE_CONFIG.format(board=board, logger_extra=logger_extra, buses=buses)


def _read_config(tmp_path, contents: str):
    test_file = tmp_path / "test.yaml"
    test_file.write_text(contents)
    parsed_yaml = yaml_util.load_yaml(test_file)
    with (
        patch.object(yaml_util, "load_yaml", return_value=parsed_yaml),
        patch.object(CORE, "config_path", test_file),
    ):
        return config.read_config({})


def test_ports_run_out_when_logger_holds_one(tmp_path, capsys) -> None:
    """The logger's own port cannot be handed to a bus."""
    result = _read_config(tmp_path, _make_config("esp32dev", "", 3))

    assert result is None, "Expected validation to fail once the ports run out"
    captured = capsys.readouterr()
    assert "only 3 hardware UART ports" in captured.out
    # The message must point at the logger so the fix is obvious
    assert "used by the logger" in captured.out


def test_ports_available_alongside_logger(tmp_path) -> None:
    """Two buses still fit next to a logger on UART0."""
    assert _read_config(tmp_path, _make_config("esp32dev", "", 2)) is not None


def test_bus_on_console_port_warns(tmp_path, caplog) -> None:
    """Turning off serial logging frees a port but leaves the console on it."""
    result = _read_config(tmp_path, _make_config("esp32dev", "  baud_rate: 0\n", 3))

    assert result is not None
    assert "bus2" in caplog.text
    assert "serial console" in caplog.text


def test_logger_on_usb_leaves_every_port_free(tmp_path, caplog) -> None:
    """With the console on USB no port is reserved, so all three are usable."""
    result = _read_config(tmp_path, _make_config("esp32-s3-devkitc-1", "", 3))

    assert result is not None
    assert "serial console" not in caplog.text


@pytest.mark.parametrize(
    ("bus_count", "console_port", "logger_port", "expected"),
    [
        # Logger on UART0: it holds the console port too, so buses start at UART1
        pytest.param(2, 0, 0, [1, 2], id="logger_on_uart0"),
        # Logger on USB: nothing is reserved
        pytest.param(3, None, None, [0, 1, 2], id="logger_on_usb"),
        # Serial logging off: the console still holds UART0, so it goes last
        pytest.param(3, 0, None, [1, 2, 0], id="console_port_used_last"),
        # Logger on UART1: only UART2 is clear, then the console port is reused
        pytest.param(2, 0, 1, [2, 0], id="logger_on_uart1"),
        # The logger's port is never handed out
        pytest.param(3, 0, 1, [2, 0, None], id="logger_port_never_reused"),
    ],
)
def test_port_assignment(
    bus_count: int,
    console_port: int | None,
    logger_port: int | None,
    expected: list[int | None],
) -> None:
    assert _esp32_assign_ports(bus_count, 3, console_port, logger_port) == expected
