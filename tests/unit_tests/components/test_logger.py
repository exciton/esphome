"""Tests for logger component validation."""

from unittest.mock import patch

import pytest

from esphome import config, yaml_util
from esphome.core import CORE

BASE_CONFIG = """
esphome:
  name: test

esp32:
  board: esp32dev
  framework:
    type: esp-idf

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


def test_logger_uart0_pin_conflict_fails(tmp_path, capsys) -> None:
    """A component on the ESP32 console pins clashes with the logger."""
    result = _read_config(
        tmp_path,
        BASE_CONFIG.format(logger_extra="", tx_pin="GPIO1", rx_pin="GPIO3"),
    )

    assert result is None, "Expected validation to fail on the console pins"
    captured = capsys.readouterr()
    assert "The logger is set to use UART0" in captured.out
    # The message must name both sides so the fix is obvious
    assert "Pin 1 is also used by uart -> 0" in captured.out


def test_logger_no_conflict_on_other_pins(tmp_path) -> None:
    """A component on any other pin is fine."""
    result = _read_config(
        tmp_path,
        BASE_CONFIG.format(logger_extra="", tx_pin="GPIO17", rx_pin="GPIO16"),
    )

    assert result is not None


@pytest.mark.parametrize(
    "logger_extra",
    [
        pytest.param("  baud_rate: 0\n", id="serial_logging_off"),
        pytest.param("  hardware_uart: UART1\n", id="uart1_has_no_default_pins"),
    ],
)
def test_logger_without_console_pins_allows_reuse(tmp_path, logger_extra: str) -> None:
    """The logger only claims pins when it actually drives the console UART."""
    result = _read_config(
        tmp_path,
        BASE_CONFIG.format(logger_extra=logger_extra, tx_pin="GPIO1", rx_pin="GPIO3"),
    )

    assert result is not None
