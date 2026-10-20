"""Offline setup utilities. Never enrolls, connects, or prints a credential."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from muse.call import (EXPECTED_ALGORITHM, EXPECTED_FORMAT, TOKEN_SHAPE, Fail,
                       bounded_read, unique_object, validate_destination)


def write_exclusive(path, data, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def fingerprint(public):
    der = public.public_bytes(serialization.Encoding.DER,
                              serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


def generate_key(private_path, public_path):
    # Refuse existing files before creating either one. Still use O_EXCL at write.
    if private_path.exists() or public_path.exists() or private_path.resolve() == public_path.resolve():
        raise ValueError("key destination already exists or paths are equal")
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    write_exclusive(private_path, key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    write_exclusive(public_path, key.public_key().public_bytes(serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo))
    return {"recipient_spki_sha256": fingerprint(key.public_key()),
            "private_key_printed": False, "transmitted": False}


def encrypt_credential(public_path, expected_fingerprint, credential_path,
                       output, endpoint, peer_ip, purpose):
    validate_destination(endpoint, peer_ip)
    public = serialization.load_pem_public_key(bounded_read(public_path, 16384, "public key"))
    if not isinstance(public, rsa.RSAPublicKey) or public.key_size != 3072:
        raise ValueError("recipient key must be RSA-3072")
    digest = fingerprint(public)
    if digest != expected_fingerprint.lower().replace(":", ""):
        raise ValueError("recipient fingerprint does not match")
    credential = json.loads(bounded_read(credential_path, 4096, "credential"),
                            object_pairs_hook=unique_object)
    if (not isinstance(credential, dict) or credential.get("enabled") is not True
            or not isinstance(credential.get("token"), str)
            or not TOKEN_SHAPE.fullmatch(credential["token"])):
        raise ValueError("scoped credential disabled or malformed")
    if not isinstance(purpose, str) or not 1 <= len(purpose) <= 500:
        raise ValueError("purpose must be 1..500 characters")
    encrypted = public.encrypt(credential["token"].encode("ascii"), padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    envelope = {"format": EXPECTED_FORMAT, "algorithm": EXPECTED_ALGORITHM,
                "recipient_spki_sha256": digest, "endpoint": endpoint,
                "peer_ip": peer_ip, "purpose": purpose,
                "revocation": "Operator-controlled enabled flag and rotation; this metadata is not expiry enforcement",
                "ciphertext_base64": base64.b64encode(encrypted).decode("ascii")}
    raw = (json.dumps(envelope, indent=2) + "\n").encode("utf-8")
    write_exclusive(output, raw)
    return {"envelope_sha256": hashlib.sha256(raw).hexdigest(),
            "recipient_spki_sha256": digest, "ciphertext_bytes": len(encrypted),
            "transmitted": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    keygen = actions.add_parser("keygen")
    keygen.add_argument("--private-key", type=Path, required=True)
    keygen.add_argument("--public-key", type=Path, required=True)
    encrypt = actions.add_parser("encrypt")
    encrypt.add_argument("--public-key", type=Path, required=True)
    encrypt.add_argument("--expected-fingerprint", required=True)
    encrypt.add_argument("--credential-file", type=Path, required=True)
    encrypt.add_argument("--output", type=Path, required=True)
    encrypt.add_argument("--endpoint", required=True)
    encrypt.add_argument("--peer-ip", required=True)
    encrypt.add_argument("--purpose", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "keygen":
            result = generate_key(args.private_key, args.public_key)
        else:
            result = encrypt_credential(args.public_key, args.expected_fingerprint,
                args.credential_file, args.output, args.endpoint, args.peer_ip, args.purpose)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, TypeError, Fail):
        # Do not render exceptions from parsers or cryptography with private data.
        print(json.dumps({"error": "setup failed; check local inputs and exclusive output paths",
                          "transmitted": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
