import pytest

from control import registry, runner, safety, vault
from control.areas import vault as vault_area


@pytest.fixture(autouse=True)
def area(clean_registry, tmp_path, monkeypatch):
    v = vault.Vault(tmp_path / "vault", encrypt=lambda d: d, decrypt=lambda d: d)
    monkeypatch.setattr(vault, "default", lambda: v)
    vault_area.register_area({})
    return v


def test_area_registered():
    assert registry.AREAS["vault"]["ok"] is True


def test_no_get_or_set_action_exists():
    names = {n for n in registry.ACTIONS if n.startswith("vault.")}
    assert names == {"vault.list", "vault.delete"}


def test_list_shows_names_only(area):
    area.set_secret("gmes", "hunter2")
    payload, text = runner.execute("vault.list", {})
    assert payload["secrets"] == ["gmes"]
    assert "hunter2" not in text


def test_delete_is_risky_and_gated(area):
    area.set_secret("gmes", "hunter2")
    payload, _ = runner.execute("vault.delete", {"name": "gmes"}, approver=lambda *a: safety.NO)
    assert payload["code"] == "APPROVAL_DENIED"
    assert area.has_secret("gmes") is True

    payload, _ = runner.execute("vault.delete", {"name": "gmes"}, approver=lambda *a: safety.YES)
    assert payload["ok"] is True
    assert area.has_secret("gmes") is False


def test_delete_missing_reports_not_found(area):
    payload, _ = runner.execute("vault.delete", {"name": "nope"}, approver=lambda *a: safety.YES)
    assert payload["ok"] is False
