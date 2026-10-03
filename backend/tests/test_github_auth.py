import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.github.auth import app_jwt, normalize_private_key


def _throwaway_pem() -> tuple[str, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode("ascii")
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return pem, public


def _accepts(private_key: str, public: bytes) -> None:
    now = int(time.time())
    token = app_jwt(Settings(github_app_id="4242", github_app_private_key=private_key), now=now)
    claims = jwt.decode(token, public, algorithms=["RS256"])
    assert claims["iss"] == "4242"


def test_literal_backslash_n_pem_is_normalized_for_jwt():
    pem, public = _throwaway_pem()
    literal = pem.replace("\n", "\\n")
    assert "\n" not in literal

    normalized = normalize_private_key(literal)
    assert normalized == pem
    assert normalized.startswith("-----BEGIN")
    assert "\n" in normalized
    assert "\\n" not in normalized
    _accepts(literal, public)

    quoted = f'"{literal}"'
    assert normalize_private_key(quoted) == pem
    _accepts(quoted, public)

    assert normalize_private_key(pem) == pem
    _accepts(pem, public)


def test_private_key_file_is_loaded_and_parsed(tmp_path: Path):
    pem, public = _throwaway_pem()
    path = tmp_path / "github-app.pem"
    path.write_text(pem, encoding="utf-8")
    settings = Settings(
        github_app_id="4242",
        github_app_private_key="",
        github_app_private_key_file=str(path),
    )
    token = app_jwt(settings, now=int(time.time()))
    claims = jwt.decode(token, public, algorithms=["RS256"])
    assert claims["iss"] == "4242"


def test_missing_private_key_file_names_the_path(tmp_path: Path):
    missing = tmp_path / "missing.pem"
    settings = Settings(
        github_app_id="4242",
        github_app_private_key="not-a-key",
        github_app_private_key_file=str(missing),
    )
    with pytest.raises(RuntimeError) as exc:
        app_jwt(settings)
    message = str(exc.value)
    assert str(missing) in message
    assert "BEGIN" not in message
    assert "not-a-key" not in message
