"""Source file detection from the first bytes (before Excel is started)."""
from __future__ import annotations


def sniff_file(path, wrapper_prefixes=()):
    """'zip' | 'drm' | 'encrypted' | 'other' from the first bytes, so obvious failures are reported without Excel.

    `wrapper_prefixes` comes from project config (`[environment] wrapper_prefixes`): byte prefixes of
    rights-management wrappers that only Excel itself can open. The platform knows no vendor names.
    """
    prefixes = tuple(wrapper_prefixes)
    with open(path, "rb") as f:
        head = f.read(16)
        if head.startswith(b"PK"):
            return "zip"
        if prefixes and head.startswith(prefixes):
            return "drm"
        if head.startswith(bytes.fromhex("D0CF11E0A1B11AE1")) and not path.lower().endswith(".xls"):
            # an OOXML/xlsb file that is an OLE container = Office password encryption ('EncryptedPackage' stream)
            f.seek(0)
            if "EncryptedPackage".encode("utf-16-le") in f.read(64 * 1024 * 1024):
                return "encrypted"
    return "other"
