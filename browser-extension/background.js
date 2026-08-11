// AniPresence Bridge — background service worker.
//
// Coalesces the per-frame messages into the latest state per tab, then POSTs
// them to the desktop app's localhost bridge (127.0.0.1:<port>/report). The
// send is debounced so several tabs updating on the same poll tick cost one
// HTTP request; a failed send is simply retried on the next message, which
// keeps the app-disconnected case cheap.
//
// Frame merging: content.js runs in EVERY frame of a tab (all_frames). The
// top frame holds the anime page (URL + title the detector needs) but usually
// no <video>, while the player lives in a cross-origin <iframe> that DOES have
// the <video>. Left unmerged, the two reports stomp on each other and the app
// flips between "video present" and "nothing"; worse, the iframe's player URL
// never matches a site detector. So per tab we keep the top frame's page info
// and graft on the latest video state from whichever frame has one.

const DEFAULTS = { bridgePort: 8793, bridgeToken: "" };

let lastPerTab = new Map(); // tabKey -> { at, payload }
let sendTimer = null;
const TAB_TTL_MS = 45000;

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (!msg || msg.type !== "report") return false;

  const tabKey = sender.tab ? String(sender.tab.id) : String(msg.tab || "x0");
  const hasVideo = !!(msg.video && msg.video.hasVideo);
  const isTop = msg.isTop === true;

  let merged;
  const existing = lastPerTab.get(tabKey);
  if (existing) {
    // Keep the top frame's page URL/title; adopt any frame's video state.
    // visibility/focused describe the page, so they come from the top frame.
    merged = {
      ...existing.payload,
      url: isTop ? msg.url : existing.payload.url,
      title: isTop ? msg.title : existing.payload.title,
      visibility: isTop ? msg.visibility : existing.payload.visibility,
      focused: isTop ? msg.focused : existing.payload.focused,
      video: hasVideo ? msg.video : existing.payload.video,
    };
  } else {
    merged = { ...msg };
  }

  // The app keys its per-tab store by `tab`, so it must be on the forwarded
  // payload (the content script itself has no stable tab id).
  lastPerTab.set(tabKey, { at: Date.now(), payload: { ...merged, tab: tabKey } });

  scheduleSend();
  return false;
});

// A tab that closes must vanish immediately; otherwise the heartbeat below
// would keep resurrecting it (its last snapshot sits in lastPerTab).
chrome.tabs.onRemoved.addListener((tabId) => {
  lastPerTab.delete(String(tabId));
});

// Heartbeat: Chrome throttles setInterval in hidden/background tabs to about
// once a minute, which would let their 45s TTL expire and make the app flap
// between "connected" and "nothing" when you focus another window. A 30s alarm
// re-sends the last known state, so a tab you're still playing stays live.
chrome.alarms.create("anipresence-heartbeat", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "anipresence-heartbeat") flush();
});

function scheduleSend() {
  if (sendTimer) return;
  sendTimer = setTimeout(() => {
    sendTimer = null;
    flush();
  }, 600);
}

// The app writes settings.json into the extension folder on every startup, so
// it is the authoritative source and must be re-read each send (the worker may
// have been suspended since the file changed). Only when it's missing do we
// fall back to what the user saved in the Options page.
async function loadSettings() {
  try {
    const res = await fetch(chrome.runtime.getURL("settings.json"), { cache: "no-store" });
    if (res.ok) {
      const s = await res.json();
      return {
        bridgePort: Number.isInteger(s.bridgePort) ? s.bridgePort : undefined,
        bridgeToken: typeof s.bridgeToken === "string" ? s.bridgeToken : undefined,
      };
    }
  } catch (_) {
    // not in the bundle / packed-read only: fall through to storage.
  }
  return {};
}

async function getConfig() {
  const stored = await chrome.storage.local.get(DEFAULTS);
  const fromFile = await loadSettings();
  // The app-written file always wins; user-saved values are a fallback for
  // folders the app doesn't provision (e.g. a relocated copy).
  return { ...DEFAULTS, ...stored, ...fromFile };
}

async function flush() {
  const config = await getConfig();

  const now = Date.now();
  const live = [];
  for (const [tabKey, entry] of lastPerTab) {
    if (now - entry.at > TAB_TTL_MS) {
      // Tab stopped reporting (closed / navigated away); drop it.
      lastPerTab.delete(tabKey);
    } else {
      live.push(entry.payload);
    }
  }
  if (!live.length) return;

  const endpoint = `http://127.0.0.1:${config.bridgePort || 8793}/report`;
  fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-AniPresence-Token": config.bridgeToken || "",
    },
    body: JSON.stringify({ tabs: live, at: now }),
    keepalive: true,
  }).catch(() => {
    // Desktop app not running (or the port is closed). Nothing to do here;
    // the next content-script message will re-attempt.
  });
}
