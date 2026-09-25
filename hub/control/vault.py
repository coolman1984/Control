"""A per-secret store for flows: piece 4 of the master roadmap.

Same mechanism as `opening-nerp-tcode/gmes_credentials.py` (Windows DPAPI, `CryptProtectData` /
`CryptUnprotectData`, tied to the signed-in Windows user, plus an application-specific entropy
salt) generalised to any number of named secrets instead of one fixed G-MES login. The plaintext
never touches disk, a log line, or — this is the point — the model: `vault.set`/`vault.get` are
deliberately **not** registered as actions (see `control/areas/vault.py`), so there is no MCP tool
an agent could call with a real secret value as an argument. The only door in is `control vault
set <name>`, typed by the owner at a keyboard (`getpass`, no echo); the only door out is a flow
step's `{{ secret:name }}`, resolved by `control/flow/engine.py` itself, immediately before the
real call, never returned to whoever asked for the run.
"""
import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from . import config
from .errors import ControlError

_ENTROPY = b"control-hub-vault-v1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _from_blob(blob):
    return ctypes.string_at(blob.pbData, blob.cbData)


def _dpapi(func, data):
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    blob_in, _keep_in = _to_blob(data)
    blob_entropy, _keep_entropy = _to_blob(_ENTROPY)
    blob_out = _Blob()
    ok = func(ctypes.byref(blob_in), None, ctypes.byref(blob_entropy), None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError(ctypes.GetLastError(), "Windows DPAPI call failed")
    try:
        return _from_blob(blob_out)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _need_windows():
    raise ControlError("VAULT_UNAVAILABLE", "the secrets vault needs Windows DPAPI",
                        "run Control on the owner's Windows PC")


def _default_encrypt(data):
    if sys.platform != "win32":
        _need_windows()
    return _dpapi(ctypes.windll.crypt32.CryptProtectData, data)


def _default_decrypt(data):
    if sys.platform != "win32":
        _need_windows()
    return _dpapi(ctypes.windll.crypt32.CryptUnprotectData, data)


class Vault:
    def __init__(self, dir_=None, *, encrypt=None, decrypt=None):
        self.dir = Path(dir_) if dir_ else config.home() / "vault"
        self._encrypt = encrypt or _default_encrypt
        self._decrypt = decrypt or _default_decrypt

    def _path(self, name):
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ControlError("USAGE", f"bad secret name {name!r}", "use a plain name, no path")
        return self.dir / f"{name}.dat"

    def set_secret(self, name, value):
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(name).write_bytes(self._encrypt(value.encode("utf-8")))

    def get_secret(self, name):
        path = self._path(name)
        if not path.exists():
            raise ControlError("SECRET_NOT_FOUND", f"no secret named {name!r} in the vault",
                                "set it with `control vault set <name>`, or check the name")
        return self._decrypt(path.read_bytes()).decode("utf-8")

    def has_secret(self, name):
        return self._path(name).exists()

    def delete_secret(self, name):
        path = self._path(name)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_secrets(self):
        if not self.dir.exists():
            return []
        return sorted(p.stem for p in self.dir.glob("*.dat"))


_default = None


def default():
    global _default
    home = config.home() / "vault"
    if _default is None or _default.dir != home:
        _default = Vault(home)
    return _default


def reset_default():
    global _default
    _default = None


def get_secret(name):
    return default().get_secret(name)


def main(argv):
    """`control vault set|list|delete` - the only place a secret's plaintext is ever typed or
    shown. Never wired as an MCP tool (see the module docstring)."""
    import getpass

    if not argv or argv[0] not in ("set", "list", "delete"):
        print("usage: control vault set <name> | control vault list | control vault delete <name>")
        return 1
    cmd = argv[0]
    if cmd == "list":
        names = default().list_secrets()
        print("\n".join(names) if names else "no secrets stored yet")
        return 0
    if len(argv) < 2:
        print(f"usage: control vault {cmd} <name>")
        return 1
    name = argv[1]
    if cmd == "delete":
        removed = default().delete_secret(name)
        print(f"deleted {name}" if removed else f"no secret named {name!r}")
        return 0 if removed else 1
    value = getpass.getpass(f"value for secret {name!r} (not shown): ")
    if not value:
        print("empty value, nothing stored")
        return 1
    try:
        default().set_secret(name, value)
    except ControlError as e:
        print(e.text())
        return 1
    print(f"stored {name} ({len(value)} characters)")
    return 0
