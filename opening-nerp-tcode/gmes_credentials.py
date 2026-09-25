"""
Store the GMES login safely for unattended runs.

    python gmes_credentials.py set      # ask once and save
    python gmes_credentials.py show     # show the user id only, never the password
    python gmes_credentials.py test     # confirm it can be read back
    python gmes_credentials.py clear    # delete it

How it is protected
-------------------
The file is encrypted with Windows DPAPI (CryptProtectData) using YOUR
Windows account key, plus an extra application-specific salt. In practice:

  * Only this Windows user, on this machine, can decrypt it.
  * Copying the file to another PC or another account makes it useless.
  * It is not a password "hidden" in a script - the plaintext never touches
    the disk, the script, or any log line.

That is the same mechanism Chrome itself uses to store saved passwords, so
it is as safe as the browser you already trust with them. It is not
protection against someone who is already logged in as you on this PC.
"""
import ctypes
import json
import os
import sys
from ctypes import wintypes
from getpass import getpass


STORE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "GMES_Automation")
STORE_PATH = os.path.join(STORE_DIR, "credentials.dat")

# Extra entropy: a value stored in the code, so a blob lifted out of this
# file cannot be decrypted by another program running as the same user
# without also knowing this salt.
_ENTROPY = b"gmes-automation-v1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _from_blob(blob):
    return ctypes.string_at(blob.pbData, blob.cbData)


def _crypt(func, data):
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    blob_in, _keep_in = _to_blob(data)
    blob_entropy, _keep_entropy = _to_blob(_ENTROPY)
    blob_out = _Blob()

    ok = func(ctypes.byref(blob_in), None, ctypes.byref(blob_entropy),
              None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError(ctypes.GetLastError(), "Windows DPAPI call failed")
    try:
        return _from_blob(blob_out)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def encrypt(data):
    return _crypt(ctypes.windll.crypt32.CryptProtectData, data)


def decrypt(data):
    return _crypt(ctypes.windll.crypt32.CryptUnprotectData, data)


def save(user, password):
    os.makedirs(STORE_DIR, exist_ok=True)
    payload = json.dumps({"user": user, "password": password}).encode("utf-8")
    with open(STORE_PATH, "wb") as fh:
        fh.write(encrypt(payload))
    return STORE_PATH


# Why the last load() returned (None, None) although a file exists - '' when it
# was simply absent or fine. The caller says it; this module never prints a
# credential or any part of one.
LAST_PROBLEM = ""


def load():
    """Returns (user, password), or (None, None) if nothing usable is stored.

    Never raises. HISTORY.md Phase 84.14: a store that decrypted but held damaged
    content (not JSON, invalid UTF-8, a list) raised JSONDecodeError /
    UnicodeDecodeError / AttributeError out of a sign-in - a traceback at 02:00 -
    and a store the account CANNOT decrypt (its Windows password was reset, it
    was copied from another PC) looked exactly like "nothing stored". Both now
    return (None, None) with `LAST_PROBLEM` saying which, so the message can tell
    a person what to do. The file itself is never modified or deleted here
    (CLAUDE.md 2.1a): fixing it is `gmes_credentials.py set`, done by them."""
    global LAST_PROBLEM
    LAST_PROBLEM = ""
    if not os.path.isfile(STORE_PATH):
        return None, None
    try:
        with open(STORE_PATH, "rb") as fh:
            blob = fh.read()
    except OSError as e:
        LAST_PROBLEM = f"the saved credentials could not be read ({e.strerror or e})"
        return None, None
    try:
        payload = decrypt(blob)
    except OSError:
        # Wrong Windows account, or the file was copied from another machine.
        LAST_PROBLEM = ("the saved credentials exist but this Windows account cannot "
                        "decrypt them - its password was reset, or the file came from "
                        "another PC or user")
        return None, None
    try:
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        data = None
    if not isinstance(data, dict):
        LAST_PROBLEM = "the saved credentials are damaged"
        return None, None
    user, password = data.get("user"), data.get("password")
    if not isinstance(user, str) or not isinstance(password, str) or not user or not password:
        LAST_PROBLEM = "the saved credentials are incomplete"
        return None, None
    return user, password


def clear():
    if os.path.isfile(STORE_PATH):
        os.remove(STORE_PATH)
        return True
    return False


def ask_in_window(default_user=""):
    """Ask for the credentials in a small window.

    Needed because this is often launched from a runner with no keyboard
    attached to it - input() then fails immediately with EOFError, and the
    user has no way to type anything. A window works regardless of how the
    script was started. Returns (user, password) or (None, None) if
    cancelled."""
    import tkinter as tk
    from tkinter import messagebox

    result = {}

    root = tk.Tk()
    root.title("GMES automation - save login")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    tk.Label(root, text="These are encrypted with your Windows account key\n"
                        "and can only be read back by you, on this PC.",
             justify="left", fg="#444").grid(row=0, column=0, columnspan=2,
                                             padx=12, pady=(12, 8), sticky="w")

    tk.Label(root, text="Knox / GMES user ID:").grid(row=1, column=0, padx=12, pady=4, sticky="e")
    user_entry = tk.Entry(root, width=28)
    user_entry.insert(0, default_user)
    user_entry.grid(row=1, column=1, padx=12, pady=4)

    tk.Label(root, text="Password:").grid(row=2, column=0, padx=12, pady=4, sticky="e")
    pw_entry = tk.Entry(root, width=28, show="*")
    pw_entry.grid(row=2, column=1, padx=12, pady=4)

    tk.Label(root, text="Password again:").grid(row=3, column=0, padx=12, pady=4, sticky="e")
    pw2_entry = tk.Entry(root, width=28, show="*")
    pw2_entry.grid(row=3, column=1, padx=12, pady=4)

    def submit():
        user, pw1, pw2 = user_entry.get().strip(), pw_entry.get(), pw2_entry.get()
        if not user:
            messagebox.showwarning("Missing", "Please enter your user ID.", parent=root)
            return
        if not pw1:
            messagebox.showwarning("Missing", "Please enter your password.", parent=root)
            return
        if pw1 != pw2:
            messagebox.showwarning("Mismatch", "The two passwords do not match.", parent=root)
            pw_entry.delete(0, tk.END)
            pw2_entry.delete(0, tk.END)
            pw_entry.focus_set()
            return
        result["user"], result["password"] = user, pw1
        root.destroy()

    buttons = tk.Frame(root)
    buttons.grid(row=4, column=0, columnspan=2, pady=(8, 12))
    tk.Button(buttons, text="Save", width=12, command=submit).pack(side="left", padx=6)
    tk.Button(buttons, text="Cancel", width=12, command=root.destroy).pack(side="left", padx=6)

    root.bind("<Return>", lambda _e: submit())
    (user_entry if not default_user else pw_entry).focus_set()
    root.update_idletasks()
    # Centre it, so it does not open behind the terminal window.
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 3
    root.geometry(f"+{x}+{y}")
    root.mainloop()

    return result.get("user"), result.get("password")


def ask_credentials():
    """Prompt in the terminal when there is one, otherwise in a window."""
    try:
        if not sys.stdin or not sys.stdin.isatty():
            raise EOFError
        user = input("GMES / Knox user ID: ").strip()
        if not user:
            return None, None
        pw1 = getpass("Password: ")
        pw2 = getpass("Password again: ")
        if pw1 != pw2:
            print("The two passwords do not match - nothing was saved.")
            return None, None
        return user, pw1
    except (EOFError, OSError):
        print("(no keyboard attached to this window - opening a dialog instead)")
        existing_user, _ = load()
        return ask_in_window(existing_user or "")


def main(command):
    if command == "set":
        print("These are stored encrypted with your Windows account key.")
        print("The password is not shown as you type and is never written in plain text.\n")
        user, password = ask_credentials()
        if not user or not password:
            print("Cancelled - nothing was saved.")
            return 2
        path = save(user, password)
        print(f"\nSaved for user {user!r}.")
        print(f"Location: {path}")
        print("Only your Windows account on this PC can read it back.")
        return 0

    if command == "show":
        user, password = load()
        if not user:
            print("Nothing is stored yet. Run:  python gmes_credentials.py set")
            return 1
        print(f"Stored user   : {user}")
        print(f"Password      : {'*' * 8} (stored, {len(password)} characters)")
        print(f"File          : {STORE_PATH}")
        return 0

    if command == "test":
        user, password = load()
        if not user or not password:
            print("FAILED: nothing readable is stored.")
            return 1
        print(f"OK: credentials for {user!r} decrypt correctly.")
        return 0

    if command == "clear":
        print("Deleted." if clear() else "Nothing was stored.")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
