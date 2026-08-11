"""Chrome DevTools Protocol (CDP) client.

Chrome exposes a JSON endpoint on a debugging port (default 9222). From there we
discover every page *and iframe* target, then evaluate JavaScript inside each
target over a WebSocket connection. Evaluating real JS is what lets us read the
actual `<video>` element state (currentTime / duration / paused) instead of
guessing from the page title.

Iframes appear as their own CDP targets with a ``parentId`` pointing at the
containing page - that is how we read video state for sandboxed/cross-origin
players (GoGoAnime, AnimePahe, AniWave and friends) whose player lives on a
different domain than the anime page.
"""
import json
import logging
import os
import shutil
import socket
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from websocket import WebSocketTimeoutException, create_connection

logger = logging.getLogger("anipresence.cdp")

try:
    from websocket import WebSocketConnectionClosedException  # pragma: no cover
except ImportError:  # older websocket-client keeps it in the same module
    WebSocketConnectionClosedException = None  # type: ignore[assignment]

_WINDOWS_CHROME_CANDIDATES = [
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", r"%LOCALAPPDATA%")) / "Google" / "Chrome" / "Application" / "chrome.exe",
]

_WINDOWS_BRAVE_CANDIDATES = [
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
    Path(os.environ.get("LOCALAPPDATA", r"%LOCALAPPDATA%")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
]

_WINDOWS_EDGE_CANDIDATES = [
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
]

_WINDOWS_OPERA_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", r"%LOCALAPPDATA%")) / "Programs" / "Opera" / "opera.exe",
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Opera" / "opera.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Opera" / "opera.exe",
]

# name -> (windows candidates, ["process exe names"], ["shutil.which names"])
_BROWSER_TABLE = {
    "chrome": (_WINDOWS_CHROME_CANDIDATES, ("chrome.exe",), ("google-chrome", "chromium", "chrome")),
    "brave": (_WINDOWS_BRAVE_CANDIDATES, ("brave.exe",), ("brave", "brave-browser")),
    "edge": (_WINDOWS_EDGE_CANDIDATES, ("msedge.exe",), ("microsoft-edge", "microsoft-edge-stable", "msedge")),
    "opera": (_WINDOWS_OPERA_CANDIDATES, ("opera.exe",), ("opera",)),
}


class ChromeError(Exception):
    """Raised when Chrome's debugging endpoint cannot be reached or launched."""


@dataclass
class Target:
    """One CDP target (a page or an iframe) we can attach to."""
    id: str
    url: str
    title: str
    type: str            # 'page' | 'iframe'
    parent_id: str = ""
    ws_url: str = ""
    debugger_url: str = ""


def _port_open(port, host="127.0.0.1"):
    """Cheap TCP probe to see if something listens on the debug port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


class ChromeClient:
    def __init__(self, port=9222, auto_launch=False, chrome_path="", browser="chrome"):
        self.port = int(port)
        self.auto_launch = auto_launch
        self.chrome_path = chrome_path
        self.browser = browser if browser in _BROWSER_TABLE else "chrome"
        self._conns = {}   # target_id -> (websocket, ws_url)
        self._counter = 0

    # ------------------------------------------------------------------ HTTP
    def _http(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise ChromeError(f"cannot reach Chrome debugging endpoint on port {self.port}: {exc}") from exc

    def is_available(self):
        try:
            self._http("/json/version")
            return True
        except ChromeError:
            return False

    def discover(self):
        """Return the current list of attachable page/iframe targets."""
        try:
            raw = self._http("/json")
        except ChromeError:
            self._conns.clear()
            raise
        targets = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            typ = item.get("type", "")
            url = item.get("url", "")
            if typ not in ("page", "iframe"):
                continue
            if not url.startswith("http"):
                continue  # chrome:// and devtools pages are not inspectable
            targets.append(
                Target(
                    id=item.get("id", ""),
                    url=url,
                    title=item.get("title", ""),
                    type=typ,
                    parent_id=item.get("parentId", ""),
                    ws_url=item.get("webSocketDebuggerUrl", ""),
                    debugger_url=item.get("devtoolsFrontendUrl", ""),
                )
            )
        # Prune cached sockets for targets that no longer exist.
        alive = {t.id for t in targets}
        self._conns = {k: v for k, v in self._conns.items() if k in alive}
        return targets

    # -------------------------------------------------------------- WebSocket
    def _get_connection(self, target):
        conn = self._conns.get(target.id)
        if conn and conn[1] == target.ws_url:
            return conn[0]
        try:
            ws = create_connection(
                target.ws_url,
                timeout=5,
                enable_multithread=True,
                suppress_origin=True,
                http_proxy_host=None,
                http_proxy_port=None,
            )
        except Exception as exc:  # noqa: BLE001 - surface anything to caller
            logger.debug("ws connect failed for %s: %s", target.url, exc)
            return None
        self._conns[target.id] = (ws, target.ws_url)
        return ws

    def _drop(self, target_id):
        entry = self._conns.pop(target_id, None)
        if entry:
            try:
                entry[0].close()
            except Exception:
                pass

    def evaluate(self, target, expression, timeout=5.0):
        """Run JS in the target's page context.

        Returns ``(ok, value)``. ``value`` is the JSON-serializable result of
        the expression (via ``returnByValue=True``) or None on failure.
        """
        ws = self._get_connection(target)
        if ws is None:
            return False, None
        self._counter += 1
        req_id = self._counter
        request = {
            "id": req_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True},
        }
        try:
            ws.settimeout(timeout)
            ws.send(json.dumps(request))
            while True:
                msg = json.loads(ws.recv())
                if not isinstance(msg, dict):
                    continue
                if msg.get("id") != req_id:
                    continue  # ignore unsolicited events/notifications
                result = msg.get("result", {})
                if "exceptionDetails" in result:
                    return False, None
                return True, result.get("result", {}).get("value")
        except (WebSocketTimeoutException, OSError) as exc:
            logger.debug("evaluate failed on %s: %s", target.url, exc)
            self._drop(target.id)
            return False, None
        except Exception as exc:  # noqa: BLE001 - connection teardown races
            if WebSocketConnectionClosedException is not None and isinstance(
                exc, WebSocketConnectionClosedException
            ):
                self._drop(target.id)
            logger.debug("evaluate error on %s: %s", target.url, exc)
            return False, None

    # --------------------------------------------------------------- launching
    def _browser_call(self, method, params):
        """Send a CDP command to the browser-level target and return the result."""
        version = self._http("/json/version")
        ws_url = version.get("webSocketDebuggerUrl") if isinstance(version, dict) else None
        if not ws_url:
            raise ChromeError("browser debugging websocket not available")
        ws = create_connection(
            ws_url,
            timeout=5,
            enable_multithread=True,
            suppress_origin=True,
            http_proxy_host=None,
            http_proxy_port=None,
        )
        try:
            self._counter += 1
            req_id = self._counter
            ws.send(json.dumps({"id": req_id, "method": method, "params": params}))
            while True:
                msg = json.loads(ws.recv())
                if not isinstance(msg, dict):
                    continue
                if msg.get("id") != req_id:
                    continue
                if "error" in msg:
                    raise ChromeError(f"{method} failed: {msg['error']}")
                return msg.get("result", {})
        finally:
            ws.close()

    def _open_new_window(self, url):
        """Open a URL in a fresh window of the already-running browser via CDP."""
        try:
            self._browser_call("Target.createTarget", {
                "url": url,
                "newWindow": True,
                "background": False,
            })
            return True
        except ChromeError:
            # Older Chromium: newWindow unsupported -> fall back to a new tab.
            return False

    def _open_tab(self, url):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/json/new?{urllib.parse.quote(url, safe='')}",
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
        return True

    def launch_pwa(self, url):
        """Open ``url`` in a standalone (app-style) window of the selected browser.

        If the browser is already running with the debug port open, the URL is
        opened in a new window of that instance via CDP. Otherwise the browser
        is started with ``--app=<url>``, which gives a true tab-less PWA window.
        Either way the window is a normal page target, so detection picks it up.
        """
        exe = self.find_chrome()
        if not exe:
            raise ChromeError(
                f"{self.browser.capitalize()} executable not found. Install it or set "
                "'chrome_path' in config."
            )

        label = self.browser.capitalize()
        try:
            if self.is_available():
                if self._open_new_window(url):
                    return f"Opened {label} window for {url} in the running browser."
                self._open_tab(url)
                return f"Opened {url} in the running browser."
        except ChromeError:
            pass  # fall through to a fresh app-mode launch

        flags = [
            exe,
            f"--app={url}",
            f"--remote-debugging-port={self.port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
        ]
        if self._chrome_running(self.browser):
            # The running instance (default profile) ignores --app; use a
            # dedicated profile so the flag takes effect in a fresh window.
            profile = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AniPresence" / "chrome-profile"
            profile.mkdir(parents=True, exist_ok=True)
            flags += [f"--user-data-dir={profile}"]

        logger.info("launching %s pwa: %s", self.browser, " ".join(flags))
        subprocess.Popen(flags, close_fds=os.name != "nt")
        return f"Launched {label} app window for {url}."

    def find_chrome(self):
        if self.chrome_path and Path(self.chrome_path).exists():
            return str(Path(self.chrome_path))
        candidates, _exe_names, which_names = _BROWSER_TABLE.get(self.browser, _BROWSER_TABLE["chrome"])
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        found = shutil.which(which_names[0])
        for name in which_names[1:]:
            found = found or shutil.which(name)
        if found:
            return found
        return None

    @staticmethod
    def _chrome_running(browser):
        _candidates, exe_names, _which = _BROWSER_TABLE.get(browser, _BROWSER_TABLE["chrome"])
        if os.name != "nt":
            return False
        try:
            for exe_name in exe_names:
                out = subprocess.run(
                    ["tasklist", "/NH", "/FI", f"IMAGENAME eq {exe_name}"],
                    capture_output=True, text=True, timeout=10,
                )
                if exe_name in out.stdout.lower():
                    return True
            return False
        except (subprocess.SubprocessError, OSError):
            return False

    def launch(self):
        """Spawn the selected browser with remote debugging enabled.

        If the browser is already running (without the debug flag) we must use a
        *dedicated* profile directory - the flag is ignored when the browser is
        already bound to the default profile. If it isn't running at all we can
        launch it on the user's normal profile.
        """
        exe = self.find_chrome()
        if not exe:
            raise ChromeError(
                f"{self.browser.capitalize()} executable not found. Install it or set "
                "'chrome_path' in config."
            )

        label = self.browser.capitalize()
        if self._chrome_running(self.browser):
            profile = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AniPresence" / "chrome-profile"
            profile.mkdir(parents=True, exist_ok=True)
            flags = [
                exe,
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
            ]
            message = (
                f"{label} was already running, so a separate AniPresence {label} "
                f"window was opened (dedicated profile). Use that window to watch anime "
                f"(debug port {self.port})."
            )
        else:
            flags = [
                exe,
                f"--remote-debugging-port={self.port}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
            ]
            message = f"Launched {label} with remote debugging on port {self.port}."

        logger.info("launching %s: %s", self.browser, " ".join(flags))
        subprocess.Popen(flags, close_fds=os.name != "nt")
        return message
