import io

import pytest

from control import vault


@pytest.fixture
def patched_vault(tmp_path, monkeypatch):
    v = vault.Vault(tmp_path / "vault", encrypt=lambda d: d, decrypt=lambda d: d)
    monkeypatch.setattr(vault, "default", lambda: v)
    return v


def test_set_prompts_and_stores(patched_vault, monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "hunter2")
    code = vault.main(["set", "gmes"])
    assert code == 0
    assert patched_vault.get_secret("gmes") == "hunter2"
    out = capsys.readouterr().out
    assert "hunter2" not in out


def test_set_empty_value_refused(patched_vault, monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "")
    assert vault.main(["set", "gmes"]) == 1
    assert patched_vault.has_secret("gmes") is False


def test_list_and_delete(patched_vault, capsys):
    patched_vault.set_secret("a", "1")
    vault.main(["list"])
    assert "a" in capsys.readouterr().out
    assert vault.main(["delete", "a"]) == 0
    assert patched_vault.has_secret("a") is False
    assert vault.main(["delete", "a"]) == 1


def test_bad_usage():
    assert vault.main([]) == 1
    assert vault.main(["bogus"]) == 1
    assert vault.main(["set"]) == 1
