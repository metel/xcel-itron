"""Certificate helpers for the Xcel Itron integration."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .const import CERT_DIR

# Xcel requires this certificate policy on the client certificate.
CLIENT_CERT_POLICY = x509.ObjectIdentifier("1.3.6.1.4.1.40732.2.2")

CLIENT_CERT_COMMON_NAME = "MeterReaderHanClient"
CERT_VALIDITY_DAYS = 7300


def _lfdi_from_certificate(certificate: x509.Certificate) -> str:
    """Return the LFDI for a certificate.

    The LFDI is the first 20 bytes of the SHA-256 fingerprint of the DER
    encoded certificate, formatted as 40 uppercase hex characters.
    """
    digest = hashlib.sha256(
        certificate.public_bytes(serialization.Encoding.DER)
    ).hexdigest()
    return digest.upper()[:40]


def generate_client_certificate() -> tuple[bytes, bytes, str]:
    """Generate a new client certificate, private key and LFDI.

    Returns a tuple of (certificate PEM, private key PEM, LFDI).
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, CLIENT_CERT_COMMON_NAME)]
    )
    now = datetime.now(UTC)

    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=CERT_VALIDITY_DAYS))
        .add_extension(
            x509.CertificatePolicies(
                [x509.PolicyInformation(CLIENT_CERT_POLICY, None)]
            ),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )

    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    return certificate_pem, private_key_pem, _lfdi_from_certificate(certificate)


def compute_lfdi_from_certificate(cert_path: str | Path) -> str:
    """Return the LFDI for a certificate stored on disk."""
    certificate = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    return _lfdi_from_certificate(certificate)


def write_credentials(
    config_dir: str | Path,
    lfdi: str,
    certificate_pem: bytes,
    private_key_pem: bytes,
) -> tuple[str, str]:
    """Write generated credentials below the Home Assistant config directory.

    Returns the certificate and key paths relative to the config directory so
    the config entry keeps working if Home Assistant's config path changes.
    """
    config_path = Path(config_dir)
    credential_dir = config_path / CERT_DIR / lfdi
    credential_dir.mkdir(parents=True, exist_ok=True)

    cert_path = credential_dir / "cert.pem"
    key_path = credential_dir / "key.pem"
    cert_path.write_bytes(certificate_pem)
    key_path.write_bytes(private_key_pem)
    key_path.chmod(0o600)

    return (
        str(cert_path.relative_to(config_path)),
        str(key_path.relative_to(config_path)),
    )


def resolve_path(config_dir: str | Path, path: str) -> Path:
    """Resolve a user supplied path against the Home Assistant config directory."""
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return Path(config_dir) / candidate
