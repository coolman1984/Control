"""Read-only AD SSO diagnostic: clicks "AD SSO Login" exactly once, then
only OBSERVES via a real CDP Network capture on the popup. Never calls
gmes_login.direct_login, never fills or submits any password field, never
clicks Login/Confirm on anything. Safe to run against a live account with a
login-attempt lockout counter.

Captures ONLY safe metadata per request/response on the popup: URL, method,
status, redirect chain, and a small allowlist of response headers
(www-authenticate, location, content-type). Cookies, Authorization header
values, and response bodies are never read or printed.

Usage: python gmes_sso_diagnose.py [--seconds N]

HISTORY.md Phase 56: the popup-blocking fix (56.1) is live-tested through
this tool, which exists specifically so no further SSO investigation ever
has to spend a real login attempt just to see what the popup does.

Rebuilt against the flat legacy engine (HISTORY.md Phase 57, Capability
Rescue Map) after the standalone src/gmes package - where this tool
originally lived - was removed. Logic is unchanged; only the imports moved
to gmes_common/gmes_login/cdp_common."""
import argparse
import json
import sys
import time
import urllib.request

import websocket

import cdp_common
import gmes_login
from gmes_common import click_by_id, connect_gmes, gmes_tab, is_logged_in, js_find_by_id

SAFE_RESPONSE_HEADERS = ("www-authenticate", "location", "content-type", "x-ms-forms-auth")


def browser_ws_url(port=None):
    port = port or cdp_common.CDP_PORT
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://{cdp_common.CDP_HOST}:{port}/json/version", timeout=5) as response:
        info = json.loads(response.read().decode())
    return cdp_common.ipv4(info["webSocketDebuggerUrl"])


def safe_headers(headers):
    if not headers:
        return {}
    lowered = {k.lower(): v for k, v in headers.items()}
    return {k: lowered[k] for k in SAFE_RESPONSE_HEADERS if k in lowered}


def network_probe(seconds, baseline_target_ids):
    """Attach to whatever new page target the click opens, BEFORE it makes
    its first request (Target.setAutoAttach with waitForDebuggerOnStart),
    turn on Network capture for it, then let it proceed and record the
    safe metadata of every request/response until the deadline."""
    bws = websocket.create_connection(browser_ws_url(), timeout=10)
    bws.settimeout(1.0)
    next_id = [1000]

    def call(method, params=None, session_id=None):
        msg_id = next_id[0]
        next_id[0] += 1
        payload = {"id": msg_id, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        bws.send(json.dumps(payload))
        return msg_id

    call("Target.setDiscoverTargets", {"discover": True})
    call("Target.setAutoAttach",
         {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True})

    popup_session = None
    popup_target = None
    events = []          # (t, kind, dict) - only safe fields ever stored
    deadline = time.monotonic() + seconds
    started = time.monotonic()

    while time.monotonic() < deadline:
        try:
            raw = bws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        except Exception as error:
            events.append((time.monotonic() - started, "socket-error", {"error": str(error)}))
            break
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        method = msg.get("method")
        if method == "Target.attachedToTarget":
            info = msg["params"]["targetInfo"]
            sid = msg["params"]["sessionId"]
            if info.get("type") == "page" and info.get("targetId") not in baseline_target_ids:
                popup_session = sid
                popup_target = info["targetId"]
                events.append((time.monotonic() - started, "popup-attached",
                                {"url": info.get("url"), "targetId": popup_target}))
                call("Network.enable", {}, session_id=sid)
                call("Runtime.runIfWaitingForDebugger", {}, session_id=sid)
            elif info.get("type") == "page":
                # Some other/pre-existing target auto-attached (e.g. the
                # main G-MES tab itself) - just let it run, don't instrument it.
                call("Runtime.runIfWaitingForDebugger", {},
                     session_id=msg["params"]["sessionId"])
        elif method == "Network.requestWillBeSent" and msg.get("sessionId") == popup_session:
            p = msg["params"]
            row = {"url": p["request"]["url"], "method": p["request"]["method"]}
            redirect = p.get("redirectResponse")
            if redirect:
                row["redirected_from_status"] = redirect.get("status")
                row["redirected_from_headers"] = safe_headers(redirect.get("headers"))
            events.append((time.monotonic() - started, "request", row))
        elif method == "Network.responseReceived" and msg.get("sessionId") == popup_session:
            r = msg["params"]["response"]
            events.append((time.monotonic() - started, "response", {
                "url": r.get("url"),
                "status": r.get("status"),
                "mimeType": r.get("mimeType"),
                "headers": safe_headers(r.get("headers")),
            }))
        elif method == "Target.targetDestroyed":
            if msg["params"].get("targetId") == popup_target:
                events.append((time.monotonic() - started, "popup-closed", {}))

    try:
        call("Target.setAutoAttach", {"autoAttach": False})
    except Exception:
        pass
    try:
        bws.close()
    except Exception:
        pass
    return events, popup_target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=30,
                         help="how long to capture network activity after the click")
    args = parser.parse_args()

    if not cdp_common.cdp_is_up():
        print("The automation browser is not running.")
        return 1

    tab = gmes_tab()
    if tab is None:
        print("Could not find the G-MES page tab.")
        return 1
    ws = connect_gmes()

    signed_in, who = is_logged_in(ws)
    if signed_in:
        print(f"Already signed in as {who!r} - nothing to diagnose.")
        return 0

    if not cdp_common.evaluate(ws, js_find_by_id(gmes_login.BTN_SSO)).get("found"):
        print("The AD SSO Login button is not on screen right now. Nothing clicked.")
        return 1

    main_ua = cdp_common.evaluate(ws, "JSON.stringify({ua: navigator.userAgent})").get("ua")
    print(f"Automation Chrome User-Agent: {main_ua}\n")

    baseline_ids = {t["id"] for t in cdp_common.get_tabs()}

    print("Clicking AD SSO Login (once)...")
    clicked = click_by_id(ws, gmes_login.BTN_SSO)
    if not clicked:
        print("The click did not land.")
        return 1
    click_time = time.monotonic()
    print(f"Clicked. Capturing network activity on the resulting popup for "
          f"up to {args.seconds}s. Cookies/Authorization values/bodies are "
          "never read.\n")

    events, popup_target = network_probe(args.seconds, baseline_ids)

    print("=" * 70)
    if not events:
        print("No new page target and no network activity was ever observed "
              "on one. The click did not lead anywhere.")
    else:
        for t, kind, row in events:
            print(f"t={t:6.2f}s  [{kind}] {row}")

    negotiate_seen = any(
        "negotiate" in str(row.get("headers", {}).get("www-authenticate", "")).lower()
        or "negotiate" in str(row.get("redirected_from_headers", {}).get("www-authenticate", "")).lower()
        for _, _, row in events
    )
    ntlm_seen = any(
        "ntlm" in str(row.get("headers", {}).get("www-authenticate", "")).lower()
        or "ntlm" in str(row.get("redirected_from_headers", {}).get("www-authenticate", "")).lower()
        for _, _, row in events
    )
    first_response = next((row for _, kind, row in events if kind == "response"), None)

    print("\n" + "-" * 70)
    print(f"WWW-Authenticate: Negotiate ever seen: {negotiate_seen}")
    print(f"WWW-Authenticate: NTLM ever seen:      {ntlm_seen}")
    if first_response:
        print(f"First response from the popup: status={first_response['status']} "
              f"mimeType={first_response['mimeType']!r} url={first_response['url']!r}")
        if first_response["status"] == 200 and "html" in str(first_response["mimeType"]):
            print("  -> ADFS answered the FIRST request with a 200 HTML page directly.\n"
                  "     No 401/Negotiate challenge round-trip happened at all - this\n"
                  "     points at ADFS's own server-side WIA policy (commonly\n"
                  "     WIASupportedUserAgents not matching this browser's UA string),\n"
                  "     not at anything the client/automation controls.")
    print("-" * 70)

    # Fresh reconnect - never trust a socket that lived through the redirect.
    try:
        fresh_tab = gmes_tab()
        if fresh_tab is not None:
            fresh_ws = connect_gmes()
            try:
                signed_in, who = is_logged_in(fresh_ws)
                err = gmes_login.login_error(fresh_ws)
            finally:
                fresh_ws.close()
            print(f"\nFinal state on the G-MES tab: signed_in={signed_in} "
                  f"error_field={err!r}")
    except Exception as error:
        print(f"\n(could not re-check final state: {error})")

    print("\nNo credentials were read, typed, or submitted by this tool.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
