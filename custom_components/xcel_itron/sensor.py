"""Sensor platform for the Xcel Itron integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import DOMAIN
from .meter import EndpointConfig, HardwareInfo, endpoint_data_key

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class SensorSpec:
    """Description of a sensor parsed from an endpoint YAML file."""

    data_key: str
    name: str
    endpoint: str
    tag: str
    device_class: SensorDeviceClass | None
    unit: str | None
    state_class: SensorStateClass | None
    scale: float | None
    precision: int | None
    value_type: str


def _parse_device_class(value: Any) -> SensorDeviceClass | None:
    if not value:
        return None
    try:
        return SensorDeviceClass(value)
    except ValueError:
        _LOGGER.warning("Unknown sensor device class %s in endpoint config", value)
        return None


def _parse_state_class(value: Any) -> SensorStateClass | None:
    if not value:
        return None
    try:
        return SensorStateClass(value)
    except ValueError:
        _LOGGER.warning("Unknown sensor state class %s in endpoint config", value)
        return None


def _build_spec(
    endpoint_name: str,
    url: str,
    tag_key: str,
    display_tag: str,
    definition: dict[str, Any],
) -> SensorSpec:
    device_class = _parse_device_class(definition.get("device_class"))

    if device_class == SensorDeviceClass.TIMESTAMP:
        value_type = "datetime"
    elif device_class == SensorDeviceClass.DURATION:
        value_type = "duration"
    else:
        value_type = "number"

    scale = definition.get("scale")
    precision = definition.get("precision")

    return SensorSpec(
        data_key=endpoint_data_key(url, tag_key),
        name=f"{endpoint_name} {display_tag}",
        endpoint=endpoint_name,
        tag=tag_key,
        device_class=device_class,
        unit=definition.get("unit_of_measurement"),
        state_class=_parse_state_class(definition.get("state_class")),
        scale=float(scale) if scale is not None else None,
        precision=int(precision) if precision is not None else None,
        value_type=value_type,
    )


def build_sensor_specs(
    endpoints: list[EndpointConfig],
) -> list[SensorSpec]:
    """Build sensor descriptions from the endpoint configuration."""
    specs: list[SensorSpec] = []

    for endpoint in endpoints:
        for tag_name, tag_definition in endpoint.tags.items():
            if isinstance(tag_definition, list):
                for item in tag_definition:
                    for sub_name, sub_definition in item.items():
                        specs.append(
                            _build_spec(
                                endpoint.name,
                                endpoint.url,
                                f"{tag_name}_{sub_name}",
                                sub_name,
                                sub_definition,
                            )
                        )
            else:
                specs.append(
                    _build_spec(
                        endpoint.name,
                        endpoint.url,
                        tag_name,
                        tag_name,
                        tag_definition,
                    )
                )

    return specs


def convert_value(raw: str, spec: SensorSpec) -> Any:
    """Convert a raw XML string into a native Home Assistant value."""
    if raw is None:
        return None

    raw = raw.strip()
    if not raw:
        return None

    if spec.value_type == "datetime":
        parsed = dt_util.parse_datetime(raw)
        if parsed is None:
            try:
                parsed = dt_util.parse_datetime(raw.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                parsed = None
        if parsed is None:
            _LOGGER.debug("Unable to parse timestamp %s for %s", raw, spec.name)
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed

    try:
        value = float(raw)
    except (TypeError, ValueError):
        _LOGGER.debug("Unable to parse numeric value %s for %s", raw, spec.name)
        return None

    if spec.scale is not None:
        value *= spec.scale
    if spec.precision is not None:
        value = round(value, spec.precision)
    if spec.value_type == "duration":
        return int(value)

    return value


class XcelItronSensor(CoordinatorEntity, SensorEntity):
    """A single reading from the Xcel meter."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: Any,
        spec: SensorSpec,
        hardware: HardwareInfo,
        entry_title: str,
    ) -> None:
        super().__init__(coordinator)
        self._spec = spec
        self._attr_unique_id = f"{hardware.lfdi}_{slugify(spec.name)}"
        self._attr_name = spec.name
        self._attr_device_class = spec.device_class
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_state_class = spec.state_class
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hardware.lfdi)},
            name=entry_title,
            manufacturer=hardware.manufacturer,
            model=hardware.manufacturer,
            sw_version=hardware.sw_version,
            serial_number=hardware.lfdi,
        )

    @property
    def native_value(self) -> Any:
        """Return the latest reading."""
        return self.coordinator.data.get(self._spec.data_key)

    @property
    def available(self) -> bool:
        """Return whether a reading is available."""
        return super().available and self._spec.data_key in self.coordinator.data


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Xcel meter sensors from a config entry."""
    runtime = entry.runtime_data
    async_add_entities(
        XcelItronSensor(
            runtime.coordinator,
            spec,
            runtime.hardware,
            entry.title,
        )
        for spec in runtime.specs
    )
