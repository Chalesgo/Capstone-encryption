"""Create a short-lived development certificate for the laptop's LAN IP."""
from argparse import ArgumentParser
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


parser = ArgumentParser()
parser.add_argument('--host', required=True)
parser.add_argument('--cert', type=Path, required=True)
parser.add_argument('--key', type=Path, required=True)
args = parser.parse_args()

args.cert.parent.mkdir(parents=True, exist_ok=True)
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'SealGuard Local Demo'),
    x509.NameAttribute(NameOID.COMMON_NAME, args.host),
])
certificate = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
    .not_valid_after(datetime.now(timezone.utc) + timedelta(days=30))
    .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address(args.host))]), critical=False)
    .sign(key, hashes.SHA256())
)
args.key.write_bytes(key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
))
args.cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
print(f'Created development certificate for {args.host}')
