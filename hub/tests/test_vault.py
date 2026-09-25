import pytest

from control.errors import ControlError
from control.vault import Vault


def fake_encrypt(data):
    return bytes(reversed(data))     # trivially reversible "encryption" for tests only


def fake_decrypt(data):
    return bytes(reversed(data))


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "vault", encrypt=fake_encrypt, decrypt=fake_decrypt)


def test_set_and_get(vault):
    vault.set_secret("gmes", "hunter2")
    assert vault.get_secret("gmes") == "hunter2"


def test_plaintext_never_touches_disk(vault, tmp_path):
    vault.set_secret("gmes", "hunter2")
    raw = (tmp_path / "vault" / "gmes.dat").read_bytes()
    assert b"hunter2" not in raw


def test_missing_secret_raises(vault):
    with pytest.raises(ControlError) as e:
        vault.get_secret("nope")
    assert e.value.code == "SECRET_NOT_FOUND"


def test_has_and_list_and_delete(vault):
    assert vault.has_secret("a") is False
    vault.set_secret("a", "1")
    vault.set_secret("b", "2")
    assert vault.has_secret("a") is True
    assert vault.list_secrets() == ["a", "b"]
    assert vault.delete_secret("a") is True
    assert vault.delete_secret("a") is False
    assert vault.list_secrets() == ["b"]


@pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", ""])
def test_bad_names_rejected(vault, bad):
    with pytest.raises(ControlError):
        vault.set_secret(bad, "x")


def test_real_backend_unavailable_off_windows(tmp_path):
    v = Vault(tmp_path / "vault2")
    with pytest.raises(ControlError) as e:
        v.set_secret("x", "y")
    assert e.value.code == "VAULT_UNAVAILABLE"
