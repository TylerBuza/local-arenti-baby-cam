"""Private home CA and paired-client identity, separately protected by DPAPI.

Public certificates may be files. Private keys are either inside DPAPI or an
encrypted PEM whose random password stays in DPAPI. No camera password export.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
import ssl
import threading
import time
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

import protected_store

ENTROPY = b'Arenti-Home-Connector-Identity-v1'
FAMILY_ENTROPY = b'Private-Camera-Family-APK-v1'


def checked_url(value, local=False):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment
            or any(ord(c) < 33 for c in value) or len(value) > 512):
        raise ValueError('A credential-free HTTPS origin is required')
    port = parsed.port if parsed.port is not None else 443
    if not 1 <= port <= 65535:
        raise ValueError('Invalid port')
    if local:
        address = ipaddress.ip_address(parsed.hostname)
        ranges = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8')
        if address.version != 4 or not any(address in ipaddress.ip_network(network) for network in ranges):
            raise ValueError('A local IPv4 connector origin is required')
    return value.rstrip('/')


def checked_funnel_url(value):
    value = checked_url(value)
    parsed = urlsplit(value)
    if (not parsed.hostname.endswith('.ts.net') or len(parsed.hostname) <= len('.ts.net')
            or (parsed.port or 443) not in (443, 8443, 10000)):
        raise ValueError('A Tailscale Funnel HTTPS origin on 443, 8443 or 10000 is required')
    return value


def private_pem(key, password=None):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(password) if password else serialization.NoEncryption())


def certificate(ca_key, ca_cert, public_key, name, *, server_hosts=None, days=365):
    now = datetime.now(timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(ca_cert.subject).public_key(public_key).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(min(now + timedelta(days=days), ca_cert.not_valid_after_utc))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()), critical=False)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if server_hosts else ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False))
    if server_hosts:
        names = []
        for host in dict.fromkeys(server_hosts):
            try:
                names.append(x509.IPAddress(ipaddress.ip_address(host)))
            except ValueError:
                names.append(x509.DNSName(host))
        builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False)
    return builder.sign(ca_key, hashes.SHA256())


class Identity:
    def __init__(self, directory=None):
        self.directory = Path(directory or Path(os.environ['LOCALAPPDATA']) / 'LocalArentiBabyCam' / 'connector' / '.private')
        self.path = self.directory / 'identity.dpapi'
        self.lock = threading.RLock()
        self.pair_hash = self.pair_deadline = None
        self.pair_failures = 0
        self.family_attempts = []
        self.value = self._load() if self.path.exists() else None

    def _load(self):
        import win32crypt
        _, plain = win32crypt.CryptUnprotectData(self.path.read_bytes(), ENTROPY, None, None, 0)
        value = json.loads(plain)
        if value.get('schema') != 1:
            raise ValueError('Unknown connector identity format')
        return value

    def _save(self):
        import win32crypt
        self.directory.mkdir(parents=True, exist_ok=True)
        protected_store.restrict_acl(self.directory, directory=True)
        encrypted = win32crypt.CryptProtectData(json.dumps(self.value).encode(), 'Home connector identity', ENTROPY, None, None, 0)
        temporary = self.path.with_suffix('.new')
        with temporary.open('xb') as output:
            output.write(encrypted)
        protected_store.restrict_acl(temporary)
        os.replace(temporary, self.path)
        protected_store.restrict_acl(self.path)

    def initialize(self, local_url, remote_url='', *, funnel=False):
        with self.lock:
            local_url = checked_url(local_url, local=True)
            remote_url = checked_url(remote_url) if remote_url else ''
            if funnel:
                remote_url = checked_funnel_url(remote_url)
            if self.value is None:
                ca_key = ec.generate_private_key(ec.SECP256R1())
                now = datetime.now(timezone.utc)
                name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Private Camera Home CA')])
                ca_cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                    .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=3650))
                    .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                    .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
                    .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                    .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                        key_encipherment=False, data_encipherment=False, key_agreement=False,
                        key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
                    .sign(ca_key, hashes.SHA256()))
                self.value = {'schema': 1, 'gatewayId': secrets.token_hex(16),
                    'caKey': private_pem(ca_key).decode(),
                    'caCert': ca_cert.public_bytes(serialization.Encoding.PEM).decode(),
                    'serverKey': private_pem(ec.generate_private_key(ec.SECP256R1())).decode(),
                    'pemPassword': secrets.token_urlsafe(48), 'clients': {}}
            self.value.update(localUrl=local_url, remoteUrl=remote_url, funnelMode=bool(funnel))
            self._write_server_cert()
            self._save()

    def _ca(self):
        return (serialization.load_pem_private_key(self.value['caKey'].encode(), None),
                x509.load_pem_x509_certificate(self.value['caCert'].encode()))

    def _write_server_cert(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        protected_store.restrict_acl(self.directory, directory=True)
        ca_key, ca_cert = self._ca()
        key = serialization.load_pem_private_key(self.value['serverKey'].encode(), None)
        hosts = [urlsplit(self.value['localUrl']).hostname, '127.0.0.1', 'localhost']
        if self.value['remoteUrl']:
            hosts.append(urlsplit(self.value['remoteUrl']).hostname)
        cert = certificate(ca_key, ca_cert, key.public_key(), 'Private Camera Home', server_hosts=hosts)
        self.value['serverCert'] = cert.public_bytes(serialization.Encoding.PEM).decode()
        for name, data in (('server-chain.pem', (self.value['serverCert'] + self.value['caCert']).encode()),
                           ('server-key.encrypted.pem', private_pem(key, self.value['pemPassword'].encode())),
                           ('home-ca.pem', self.value['caCert'].encode())):
            target = self.directory / name
            target.write_bytes(data)
            protected_store.restrict_acl(target)

    def tls_context(self, *, require_client=False):
        if not self.value:
            raise ValueError('Initialize the connector explicitly first')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(self.directory / 'server-chain.pem'),
            str(self.directory / 'server-key.encrypted.pem'), self.value['pemPassword'])
        context.load_verify_locations(cadata=self.value['caCert'])
        # Pairing has no certificate yet. All non-pair APIs enforce a verified,
        # enrolled and non-revoked peer certificate at the application layer.
        context.verify_mode = ssl.CERT_REQUIRED if require_client else ssl.CERT_OPTIONAL
        return context

    def pairing_payload(self):
        with self.lock:
            if not self.value:
                raise ValueError('Connector identity unavailable')
            token = secrets.token_urlsafe(32)
            self.pair_hash = hashlib.sha256(token.encode()).digest()
            self.pair_deadline = time.monotonic() + 300
            self.pair_failures = 0
            return {'version': 1, 'gatewayId': self.value['gatewayId'],
                    'localUrl': self.value['localUrl'], 'remoteUrl': self.value['remoteUrl'],
                    'caDer': base64.b64encode(x509.load_pem_x509_certificate(self.value['caCert'].encode())
                        .public_bytes(serialization.Encoding.DER)).decode(), 'pairToken': token}

    def close_pairing(self):
        with self.lock:
            self.pair_hash = self.pair_deadline = None

    def enroll(self, token, csr_pem, name):
        with self.lock:
            valid = (isinstance(token, str) and len(token) <= 128 and self.pair_hash is not None
                and time.monotonic() < self.pair_deadline and self.pair_failures < 5
                and hmac.compare_digest(hashlib.sha256(token.encode()).digest(), self.pair_hash))
            if not valid:
                self.pair_failures += 1
                raise PermissionError('Pairing is absent, expired or invalid')
            result = self._issue_phone(csr_pem, name)
            self.close_pairing()
            return result

    def _issue_phone(self, csr_pem, name, *, parent=None):
        if not isinstance(csr_pem, str) or not 1 <= len(csr_pem) <= 4096:
            raise ValueError('Invalid CSR size')
        csr = x509.load_pem_x509_csr(csr_pem.encode())
        public = csr.public_key()
        if (not csr.is_signature_valid or not isinstance(public, ec.EllipticCurvePublicKey)
                or not isinstance(public.curve, ec.SECP256R1)):
            raise ValueError('A signed P-256 client CSR is required')
        public_hash = hashlib.sha256(public.public_bytes(serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
        if parent:
            for client in self.value['clients'].values():
                if client.get('parent') == parent and client.get('publicKeySha256') == public_hash:
                    if client.get('revoked'):
                        raise PermissionError('Phone identity revoked')
                    return {'clientId': client['id'], 'gatewayId': self.value['gatewayId'],
                            'clientCert': client['clientCert'], 'caCert': self.value['caCert']}
            if sum(client.get('parent') == parent and not client.get('revoked')
                   for client in self.value['clients'].values()) >= 64:
                raise PermissionError('Family phone limit reached')
        client_id = secrets.token_hex(16)
        ca_key, ca_cert = self._ca()
        cert = certificate(ca_key, ca_cert, public, 'Android-' + client_id)
        fingerprint = cert.fingerprint(hashes.SHA256()).hex()
        safe_name = ''.join(c for c in str(name) if c.isprintable())[:40] or 'Android phone'
        record = {'id': client_id, 'name': safe_name, 'revoked': False}
        pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        if parent:
            record.update(parent=parent, publicKeySha256=public_hash, clientCert=pem)
        self.value['clients'][fingerprint] = record
        self._save()
        return {'clientId': client_id, 'gatewayId': self.value['gatewayId'],
                'clientCert': pem, 'caCert': self.value['caCert']}

    def family_status(self):
        with self.lock:
            fingerprint = (self.value or {}).get('familyBootstrapFingerprint')
            client = (self.value or {}).get('clients', {}).get(fingerprint, {})
            present = client.get('role') == 'family-bootstrap'
            return {'available': present, 'revoked': bool(client.get('revoked')),
                    'enabled': present and client.get('enabled') is True and not client.get('revoked')}

    def set_family_access(self, enabled):
        with self.lock:
            status = self.family_status()
            if not status['available'] or status['revoked']:
                raise PermissionError('A new private family APK is required')
            fingerprint = self.value['familyBootstrapFingerprint']
            self.value['clients'][fingerprint]['enabled'] = bool(enabled)
            self._save()

    def prepare_family_credential(self):
        """Explicit owner provisioning only; caller must keep this payload private."""
        import win32crypt
        with self.lock:
            if not self.value:
                raise ValueError('Existing home identity required')
            path = self.directory / 'family-apk.dpapi'
            if path.exists():
                _, plain = win32crypt.CryptUnprotectData(path.read_bytes(), FAMILY_ENTROPY, None, None, 0)
                payload = json.loads(plain)
                cert = x509.load_pem_x509_certificate(payload['clientCert'].encode())
                fingerprint = cert.fingerprint(hashes.SHA256()).hex()
                if (payload.get('gatewayId') != self.value['gatewayId']
                        or fingerprint != self.value.get('familyBootstrapFingerprint')
                        or not self.family_status()['enabled']):
                    raise PermissionError('Existing family credential is not enabled; preserve for review')
                cert.verify_directly_issued_by(x509.load_pem_x509_certificate(self.value['caCert'].encode()))
                now = datetime.now(timezone.utc)
                if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
                    raise PermissionError('Family credential expired; preserve for review')
            else:
                if self.family_status()['available']:
                    raise ValueError('Incomplete family provisioning; preserve for review')
                key = ec.generate_private_key(ec.SECP256R1())
                ca_key, ca_cert = self._ca()
                cert = certificate(ca_key, ca_cert, key.public_key(), 'Private Camera Family APK', days=3650)
                fingerprint = cert.fingerprint(hashes.SHA256()).hex()
                self.value['clients'][fingerprint] = {'id': secrets.token_hex(16),
                    'name': 'Family APK access (all copies)', 'role': 'family-bootstrap',
                    'enabled': True, 'revoked': False}
                self.value['familyBootstrapFingerprint'] = fingerprint
                self._save()
                payload = {'version': 1, 'mode': 'family-v1', 'gatewayId': self.value['gatewayId'],
                    'clientCert': cert.public_bytes(serialization.Encoding.PEM).decode(),
                    'clientKey': private_pem(key).decode()}
                encrypted = win32crypt.CryptProtectData(json.dumps(payload).encode(),
                    'Private family APK credential', FAMILY_ENTROPY, None, None, 0)
                with path.open('xb') as output:
                    output.write(encrypted)
                protected_store.restrict_acl(path)
            ca = x509.load_pem_x509_certificate(self.value['caCert'].encode())
            return {**payload, 'localUrl': self.value['localUrl'], 'remoteUrl': self.value['remoteUrl'],
                    'caDer': base64.b64encode(ca.public_bytes(serialization.Encoding.DER)).decode()}

    def is_family_bootstrap(self, fingerprint):
        with self.lock:
            return (self.value or {}).get('clients', {}).get(fingerprint, {}).get('role') == 'family-bootstrap'

    def activate_family(self, fingerprint, csr_pem, name):
        with self.lock:
            parent = (self.value or {}).get('clients', {}).get(fingerprint, {})
            if (parent.get('role') != 'family-bootstrap' or parent.get('revoked')
                    or parent.get('enabled') is not True):
                raise PermissionError('Enabled family credential required')
            now = time.monotonic()
            self.family_attempts = [value for value in self.family_attempts if now - value < 60]
            if len(self.family_attempts) >= 12:
                raise PermissionError('Family activation rate limit')
            self.family_attempts.append(now)
            return self._issue_phone(csr_pem, name, parent=fingerprint)

    def authenticate(self, der):
        if not der or not self.value:
            return None
        fingerprint = hashlib.sha256(der).hexdigest()
        with self.lock:
            client = self.value['clients'].get(fingerprint)
            if not client or client.get('revoked'):
                return None
            if client.get('role') == 'family-bootstrap' and client.get('enabled') is not True:
                return None
            if client.get('parent'):
                parent = self.value['clients'].get(client['parent'], {})
                if (parent.get('role') != 'family-bootstrap' or parent.get('revoked')
                        or parent.get('enabled') is not True):
                    return None
            return fingerprint

    def revoke(self, fingerprint):
        with self.lock:
            if fingerprint not in self.value['clients']:
                raise ValueError('Unknown enrolled device')
            self.value['clients'][fingerprint]['revoked'] = True
            self._save()
