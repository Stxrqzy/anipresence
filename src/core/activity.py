"""Bounded, local activity journal for the searchable v3 activity page."""
import json
import time
from pathlib import Path


class ActivityJournal:
    LIMIT = 2000

    def __init__(self, path):
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = [e for e in data if isinstance(e, dict) and isinstance(e.get("time"), (int, float))][-self.LIMIT:]
        except (OSError, ValueError, TypeError):
            self.entries = []

    def add(self, kind, message, **context):
        entry = dict(context, time=time.time(), type=kind, message=str(message))
        self.entries.append(entry)
        del self.entries[:-self.LIMIT]
        self.save()
        return entry

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(self.entries, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            pass

    def clear(self):
        self.entries.clear()
        self.save()


def classify_message(message):
    text = message.lower()
    if any(word in text for word in ("sync", "marked", "progress")):
        return "Progress"
    if any(word in text for word in ("account", "switched", "login")):
        return "Account"
    if any(word in text for word in ("discord", "chrome", "browser", "connection", "vencord", "extension")):
        return "Connection"
    if any(word in text for word in ("setting", "theme", "config", "update")):
        return "Setting"
    return "System"
