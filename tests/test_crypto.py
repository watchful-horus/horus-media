import pytest
from cryptography.fernet import InvalidToken

from horus_media.crypto import TokenCipher


def test_round_trip_and_key_reuse(tmp_path):
    key = tmp_path / "horus.key"
    c1 = TokenCipher(key)
    enc = c1.encrypt("hello")
    assert enc != "hello"
    assert TokenCipher(key).decrypt(enc) == "hello"  # key persisted


def test_different_key_cannot_decrypt(tmp_path):
    enc = TokenCipher(tmp_path / "a.key").encrypt("hello")
    with pytest.raises(InvalidToken):
        TokenCipher(tmp_path / "b.key").decrypt(enc)
