"""Discord Rich Presence via pypresence.

All socket operations are serialized with a lock so the UI thread and the
worker thread can both touch the client safely (e.g. the "Clear RPC" button
while a presence update is in flight).

Discord's local client validates the activity payload (Joi) and rejects it if
an asset value is not a valid uploaded-asset key or a fetchable image URL with
a file extension. We therefore send images optimistically and, if Discord
complains about ``assets``, retry with progressively fewer image fields so the
text/timestamps/buttons still display.
"""
import logging
import threading

from pypresence import Presence
from pypresence.exceptions import ServerError

logger = logging.getLogger("anipresence.discord")


def format_seconds(seconds):
    """Render seconds as MM:SS (or H:MM:SS past an hour)."""
    if not seconds or seconds <= 0:
        return "00:00"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class DiscordClient:
    """Thin wrapper hiding reconnect/close details behind a simple API."""

    def __init__(self):
        self._lock = threading.Lock()
        self._client = None

    @property
    def is_connected(self):
        with self._lock:
            return self._client is not None

    def connect(self, application_id):
        """Connect to Discord's local IPC pipe. Raises on failure."""
        with self._lock:
            self._close_locked()
            client = Presence(str(application_id))
            client.connect()          # raises if Discord is not running
            self._client = client

    def update(self, **kwargs):
        """Push a rich presence payload (details, state, assets, timestamps...).

        If Discord rejects the ``assets`` field (e.g. an image URL it cannot
        validate), retry without the small image and then without any images so
        the rest of the presence still shows.
        """
        with self._lock:
            if self._client is None:
                raise ConnectionError("Discord is not connected")
            last_error = None
            for payload in self._degraded_payloads(kwargs):
                try:
                    self._client.update(**payload)
                    return
                except ServerError as exc:
                    last_error = exc
                    if "assets" not in str(exc):
                        raise
                    logger.warning(
                        "Discord rejected activity assets (%s); retrying without images", exc
                    )
            raise last_error

    @staticmethod
    def _degraded_payloads(kwargs):
        """Full payload, then image-free variant, in send order."""
        variants = [dict(kwargs)]
        image_keys = ("small_image", "small_text", "large_image", "large_text")
        if any(k in kwargs for k in image_keys):
            variants.append({k: v for k, v in kwargs.items() if k not in image_keys})
        return variants

    def clear(self):
        """Clear the current presence, if any."""
        with self._lock:
            if self._client is None:
                return
            try:
                self._client.clear()
            except Exception as exc:  # noqa: BLE001 - already disconnected
                logger.debug("clear failed: %s", exc)

    def close(self):
        with self._lock:
            self._close_locked()

    def _close_locked(self):
        if self._client is not None:
            try:
                try:
                    self._client.clear()
                except Exception:
                    pass
                self._client.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("close failed: %s", exc)
        self._client = None
