"""Runtime data models for the Xcel Itron integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .coordinator import XcelItronCoordinator
    from .meter import EndpointConfig, HardwareInfo, XcelMeterClient
    from .sensor import SensorSpec


@dataclass
class XcelItronRuntimeData:
    """Objects shared by the integration platforms."""

    client: XcelMeterClient
    coordinator: XcelItronCoordinator
    hardware: HardwareInfo
    specs: list[SensorSpec]
    endpoints: list[EndpointConfig]
    client_lfdi: str
