from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from esphome.core import CORE

CODEOWNERS = ["@esphome/core"]
DOMAIN = "epaper_spi"

# The source file that every model needs.
BASE_SOURCE_FILE = "epaper_spi.cpp"


@dataclass
class EpaperSpiData:
    needed_sources: set[str] = field(default_factory=set)
    rtc_store_claimed: bool = False


def _get_data() -> EpaperSpiData:
    if DOMAIN not in CORE.data:
        CORE.data[DOMAIN] = EpaperSpiData()
    return CORE.data[DOMAIN]


def register_sources(sources: Iterable[str]) -> None:
    """Record the source files that a configured model's C++ driver class needs."""
    _get_data().needed_sources.update(sources)


def claim_rtc_store() -> bool:
    """Record that a display owns the single RTC image store; returns False if one already does."""
    data = _get_data()
    if data.rtc_store_claimed:
        return False
    data.rtc_store_claimed = True
    return True


def FILTER_SOURCE_FILES() -> list[str]:  # noqa: N802
    """Exclude the driver sources that no configured model needs."""
    if not (needed := _get_data().needed_sources):
        return []
    keep = needed | {BASE_SOURCE_FILE}
    return sorted(
        path.name
        for path in Path(__file__).parent.glob("*.cpp")
        if path.name not in keep
    )
