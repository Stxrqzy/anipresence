// AniPresence Bridge — options page logic.

const DEFAULTS = { bridgePort: 8793, bridgeToken: "" };
const portField = document.getElementById("port");
const tokenField = document.getElementById("token");
const statusField = document.getElementById("status");
const saved = document.getElementById("saved");

async function loadDefaults() {
  // The desktop app pre-fills settings.json in the extension folder when it
  // starts, so typically nothing needs typing at all.
  try {
    const res = await fetch(chrome.runtime.getURL("settings.json"));
    if (res.ok) {
      const s = await res.json();
      return {
        bridgePort: Number.isInteger(s.bridgePort) ? s.bridgePort : DEFAULTS.bridgePort,
        bridgeToken: typeof s.bridgeToken === "string" ? s.bridgeToken : DEFAULTS.bridgeToken,
      };
    }
  } catch (_) {
    // settings.json missing (folder moved) -> use storage / defaults.
  }
  return DEFAULTS;
}

chrome.storage.local.get(DEFAULTS, (stored) => {
  loadDefaults().then((fromFile) => {
    // The app-written settings.json is authoritative; user-saved values only
    // take over when the file is absent (e.g. a relocated copy of the folder).
    const s = { ...stored, ...fromFile };
    portField.value = s.bridgePort;
    tokenField.value = s.bridgeToken || "";
  });
});

document.getElementById("save").addEventListener("click", () => {
  const port = parseInt(portField.value, 10);
  chrome.storage.local.set(
    {
      bridgePort: Number.isInteger(port) && port >= 1024 && port <= 65535 ? port : 8793,
      bridgeToken: (tokenField.value || "").trim(),
    },
    () => {
      saved.style.visibility = "visible";
      setTimeout(() => (saved.style.visibility = "hidden"), 1500);
    }
  );
  testConnection();
});

document.getElementById("test").addEventListener("click", testConnection);

function currentConfig() {
  const port = parseInt(portField.value, 10);
  return {
    port: Number.isInteger(port) && port >= 1024 && port <= 65535 ? port : 8793,
    token: (tokenField.value || "").trim(),
  };
}

async function testConnection() {
  const { port, token } = currentConfig();
  statusField.textContent = "Testing…";
  statusField.className = "";
  try {
    const res = await fetch(`http://127.0.0.1:${port}/ping`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-AniPresence-Token": token },
    });
    if (res.ok) {
      statusField.textContent = "Connected — AniPresence is accepting reports.";
      statusField.className = "ok";
    } else if (res.status === 403) {
      statusField.textContent = "Reached AniPresence, but the token is wrong.";
      statusField.className = "err";
    } else {
      statusField.textContent = `Unexpected response: ${res.status}.`;
      statusField.className = "err";
    }
  } catch (_) {
    statusField.textContent = "Could not reach AniPresence. Is it running?";
    statusField.className = "err";
  }
}