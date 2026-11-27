"""Tests for the meter client and endpoint configuration."""

from __future__ import annotations

import ssl
import stat
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from cryptography import x509
from packaging.version import Version

from custom_components.xcel_itron.certificates import (
    CLIENT_CERT_POLICY,
    compute_lfdi_from_certificate,
    generate_client_certificate,
    write_credentials,
)
from custom_components.xcel_itron.meter import (
    MeterProtocolError,
    XcelMeterClient,
    _is_tls_error,
    create_ssl_context,
    endpoint_data_key,
    load_endpoints,
    normalize_endpoints,
    parse_endpoint_response,
    select_config_version,
    supported_config_versions,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _write_cert(tmp_path: Path) -> tuple[Path, Path, str]:
    cert_pem, key_pem, lfdi = generate_client_certificate()
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    return cert_path, key_path, lfdi


def test_generate_client_certificate() -> None:
    cert_pem, key_pem, lfdi = generate_client_certificate()

    assert key_pem.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert len(lfdi) == 40
    assert lfdi == lfdi.upper()
    assert all(character in "0123456789ABCDEF" for character in lfdi)

    certificate = x509.load_pem_x509_certificate(cert_pem)
    policies = certificate.extensions.get_extension_for_class(x509.CertificatePolicies)
    assert policies.critical is True
    assert policies.value[0].policy_identifier == CLIENT_CERT_POLICY

    key_usage = certificate.extensions.get_extension_for_class(x509.KeyUsage)
    assert key_usage.critical is True
    assert key_usage.value.digital_signature is True

    assert certificate.not_valid_after_utc > datetime.now(UTC)


def test_write_credentials(tmp_path: Path) -> None:
    cert_pem, key_pem, lfdi = generate_client_certificate()
    cert_path, key_path = write_credentials(tmp_path, lfdi, cert_pem, key_pem)

    assert cert_path == f"xcel_itron/{lfdi}/cert.pem"
    assert key_path == f"xcel_itron/{lfdi}/key.pem"
    assert (tmp_path / cert_path).is_file()
    assert (tmp_path / key_path).is_file()
    assert stat.S_IMODE((tmp_path / key_path).stat().st_mode) == 0o600
    assert compute_lfdi_from_certificate(tmp_path / cert_path) == lfdi


def test_create_ssl_context(tmp_path: Path) -> None:
    cert_path, key_path, _ = _write_cert(tmp_path)
    context = create_ssl_context(cert_path, key_path)

    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.maximum_version == ssl.TLSVersion.TLSv1_2


def test_supported_config_versions() -> None:
    assert supported_config_versions() == [
        Version("3.2.39"),
        Version("3.2.50"),
    ]


@pytest.mark.parametrize(
    ("meter_version", "expected"),
    [
        ("3.2.50", "3.2.50"),
        ("3.2.51", "3.2.50"),
        ("3.2.39", "3.2.39"),
        ("3.2.38", "3.2.39"),
        ("3.3.0", "3.2.50"),
        ("not-a-version", "3.2.50"),
    ],
)
def test_select_config_version(meter_version: str, expected: str) -> None:
    selected = select_config_version(
        [Version("3.2.39"), Version("3.2.50")], meter_version
    )
    assert selected == Version(expected)


def test_select_config_version_without_configs() -> None:
    with pytest.raises(MeterProtocolError):
        select_config_version([], "3.2.50")


def test_parse_current_summation_response() -> None:
    endpoints = normalize_endpoints(load_endpoints(Version("3.2.50")))
    endpoint = next(
        item for item in endpoints if item.name == "Current Summation Delivered"
    )

    readings = parse_endpoint_response(
        (FIXTURES / "current_summation.xml").read_text(), endpoint
    )

    assert readings == {
        "timePeriod_start": "2024-01-01T00:00:00Z",
        "value": "123456",
    }


def test_parse_instantaneous_demand_response() -> None:
    endpoints = normalize_endpoints(load_endpoints(Version("3.2.50")))
    endpoint = next(item for item in endpoints if item.name == "Instantaneous Demand")

    readings = parse_endpoint_response(
        (FIXTURES / "instantaneous_demand.xml").read_text(), endpoint
    )

    assert readings == {"value": "1234"}


def test_parse_invalid_xml() -> None:
    endpoints = normalize_endpoints(load_endpoints(Version("3.2.50")))
    endpoint = next(item for item in endpoints if item.name == "Instantaneous Demand")

    with pytest.raises(ET.ParseError):
        parse_endpoint_response("<not-xml", endpoint)


def test_endpoint_data_key() -> None:
    assert endpoint_data_key("/upt/1/mr/1/r", "value") == ("/upt/1/mr/1/r#value")


@pytest.mark.asyncio
async def test_hardware_info(tmp_path: Path) -> None:
    cert_path, key_path, _ = _write_cert(tmp_path)
    client = XcelMeterClient("192.168.1.50", 8081, cert_path, key_path)
    client._async_get = AsyncMock(  # type: ignore[method-assign]
        return_value=(FIXTURES / "sdi.xml").read_text()
    )

    hardware = await client.async_get_hardware_info()

    assert hardware.lfdi == "METERLFDI0123456789ABCDEF"
    assert hardware.manufacturer == "Itron"
    assert hardware.sw_version == "3.2.50"


@pytest.mark.asyncio
async def test_hardware_info_missing_field(tmp_path: Path) -> None:
    cert_path, key_path, _ = _write_cert(tmp_path)
    client = XcelMeterClient("192.168.1.50", 8081, cert_path, key_path)
    client._async_get = AsyncMock(  # type: ignore[method-assign]
        return_value=(
            '<DeviceCapability xmlns="urn:ieee:std:2030.5:ns">'
            "<lFDI>abc</lFDI><swVer>3.2.50</swVer>"
            "</DeviceCapability>"
        )
    )

    with pytest.raises(MeterProtocolError):
        await client.async_get_hardware_info()


def test_tls_error_classification() -> None:
    assert _is_tls_error(ssl.SSLError("bad certificate"))
    assert not _is_tls_error(ConnectionRefusedError("Connection refused"))
