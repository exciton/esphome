"""Tests for logger component validation."""

from unittest.mock import patch

import pytest

from esphome import config, yaml_util
from esphome.core import CORE

BASE_CONFIG = """
esphome:
  name: test

esp8266:
  board: nodemcuv2

logger:
{logger_extra}
uart:
  - id: test_uart
    tx_pin: {tx_pin}
    rx_pin: {rx_pin}
    baud_rate: 9600
"""


def _read_config(tmp_path, contents: str):
    test_file = tmp_path / "test.yaml"
    test_file.write_text(contents)
    parsed_yaml = yaml_util.load_yaml(test_file)
    with (
        patch.object(yaml_util, "load_yaml", return_value=parsed_yaml),
        patch.object(CORE, "config_path", test_file),
    ):
        return config.read_config({})


@pytest.mark.parametrize(
    ("logger_extra", "tx_pin", "rx_pin", "uart_name"),
    [
        pytest.param("", "GPIO1", "GPIO3", "UART0", id="uart0"),
        pytest.param(
            "  hardware_uart: UART0_SWAP\n", "GPIO15", "GPIO13", "UART0_SWAP", id="swap"
        ),
    ],
)
def test_logger_pin_conflict_fails(
    tmp_path, capsys, logger_extra: str, tx_pin: str, rx_pin: str, uart_name: str
) -> None:
    """A component on the ESP8266 console pins clashes with the logger."""
    result = _read_config(
        tmp_path,
        BASE_CONFIG.format(logger_extra=logger_extra, tx_pin=tx_pin, rx_pin=rx_pin),
    )

    assert result is None, "Expected validation to fail on the console pins"
    captured = capsys.readouterr()
    assert f"The logger is set to use {uart_name}" in captured.out
    # The message must name both sides so the fix is obvious
    assert "is also used by uart -> 0" in captured.out


def test_logger_no_conflict_on_other_pins(tmp_path) -> None:
    """A component on any other pin is fine."""
    result = _read_config(
        tmp_path,
        BASE_CONFIG.format(logger_extra="", tx_pin="GPIO4", rx_pin="GPIO5"),
    )

    assert result is not None


@pytest.mark.parametrize(
    "logger_extra",
    [
        pytest.param("  baud_rate: 0\n", id="serial_logging_off"),
        pytest.param("  hardware_uart: UART0_SWAP\n", id="logger_moved_off_the_pins"),
    ],
)
def test_logger_elsewhere_allows_reuse(tmp_path, logger_extra: str) -> None:
    """The logger only claims the pins it actually drives."""
    result = _read_config(
        tmp_path,
        BASE_CONFIG.format(logger_extra=logger_extra, tx_pin="GPIO1", rx_pin="GPIO3"),
    )

    assert result is not None
