import pytest

from control import registry


@pytest.fixture(autouse=True)
def control_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("CONTROL_HOME", str(home))
    monkeypatch.delenv("CONTROL_APPROVAL", raising=False)
    return home


@pytest.fixture
def clean_registry(monkeypatch):
    monkeypatch.setattr(registry, "ACTIONS", {})
    monkeypatch.setattr(registry, "AREAS", {})
