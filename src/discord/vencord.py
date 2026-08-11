"""Vencord plugin bridge.

Posts the current watch activity to a tiny local server that the AniPresence
Vencord userplugin (``vencord-plugin/anipresence``) opens inside Discord. The
plugin turns each payload into a ``LOCAL_ACTIVITY_UPDATE`` so friends see the
presence even though a personal Application ID is never verified.

The bridge is best-effort: if the plugin isn't running the POST fails quickly
and the app keeps working through the normal Discord pipe.
"""
import logging

import requests

logger = logging.getLogger("anipresence.vencord")

# The AniPresence Vencord plugin listens on 127.0.0.1:6339 and accepts a single
# JSON POST per activity change.
DEFAULT_PLUGIN_URL = "http://127.0.0.1:6339/activity"


class VencordBridge:
    """Sends Rich Presence payloads to the AniPresence Vencord plugin."""

    def __init__(self, url=DEFAULT_PLUGIN_URL, timeout=1.5):
        self._url = url
        self._timeout = timeout

    def send(self, activity, signature=None):
        """POST an activity payload to the plugin.

        Returns True when the plugin acknowledges it (HTTP 2xx); False on any
        network failure so the controller can treat the bridge as offline.
        """
        try:
            resp = requests.post(
                self._url,
                json={"activity": activity, "sig": signature},
                timeout=self._timeout,
            )
            return resp.ok
        except requests.RequestException as exc:
            logger.debug("vencord bridge send failed: %s", exc)
            return False

    def clear(self):
        """Ask the plugin to clear the current activity."""
        try:
            requests.post(self._url, json={"clear": True}, timeout=self._timeout)
        except requests.RequestException as exc:  # noqa: BLE001 - best-effort
            logger.debug("vencord bridge clear failed: %s", exc)