"""Tests for sensor parsing and value conversion."""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from packaging.version import Version

from custom_components.xcel_itron.meter import (
    load_endpoints,
    normalize_endpoints,
)
from custom_components.xcel_itron.sensor import (
    build_sensor_specs,
    convert_value,
)


def _specs(version: str):
    return build_sensor_specs(normalize_endpoints(load_endpoints(Version(version))))


def test_sensor_count_3_2_50() -> None:
    assert len(_specs("3.2.50")) == 37


def test_sensor_count_3_2_39() -> None:
    assert len(_specs("3.2.39")) == 7


def test_data_keys_are_unique() -> None:
    specs = _specs("3.2.50")
    assert len({spec.data_key for spec in specs}) == len(specs)


def test_energy_spec() -> None:
    spec = next(
        item
        for item in _specs("3.2.50")
        if item.name == "Current Summation Delivered value"
    )

    assert spec.device_class is SensorDeviceClass.ENERGY
    assert spec.unit == "Wh"
    assert spec.state_class is SensorStateClass.TOTAL_INCREASING
    assert spec.value_type == "number"


def test_timestamp_spec_and_conversion() -> None:
    spec = next(
        item
        for item in _specs("3.2.50")
        if item.name == "Current Summation Delivered start"
    )

    assert spec.device_class is SensorDeviceClass.TIMESTAMP
    assert spec.unit is None
    assert spec.value_type == "datetime"
    assert convert_value("2024-01-01T00:00:00Z", spec) == datetime(
        2024, 1, 1, tzinfo=UTC
    )


def test_power_factor_conversion() -> None:
    spec = next(item for item in _specs("3.2.50") if item.name == "Power Factor value")

    assert spec.device_class is SensorDeviceClass.POWER_FACTOR
    assert spec.scale == 0.001
    assert spec.precision == 3
    assert convert_value("950", spec) == 0.95


def test_energy_conversion() -> None:
    spec = next(
        item
        for item in _specs("3.2.50")
        if item.name == "Current Summation Delivered value"
    )
    assert convert_value("123456", spec) == 123456.0


def test_duration_conversion() -> None:
    spec = next(
        item
        for item in _specs("3.2.39")
        if item.name == "Current Summation Received duration"
    )

    assert spec.device_class is SensorDeviceClass.DURATION
    assert spec.value_type == "duration"
    assert convert_value("300", spec) == 300


def test_vah_spec_uses_custom_unit() -> None:
    spec = next(item for item in _specs("3.2.50") if item.name == "VAh Received value")

    assert spec.device_class is None
    assert spec.unit == "VAh"
    assert spec.state_class is SensorStateClass.TOTAL_INCREASING


def test_invalid_values_return_none() -> None:
    spec = next(
        item
        for item in _specs("3.2.50")
        if item.name == "Current Summation Delivered value"
    )

    assert convert_value("not-a-number", spec) is None
    assert convert_value("", spec) is None
