// AniPresence Bridge — content script.
//
// Runs in every frame and reports the raw player state to the background
// worker, which batches the tabs into one POST per cadence. This works for a
// normally-launched browser window (any profile), which is the whole point:
// the desktop app never needs a CDP debug port when the extension is around.

(() => {
  const REPORT_INTERVAL = 3000;
  const MAX_URL = 2000;

  function runtime() {
    if (typeof browser !== "undefined" && browser.runtime) return browser.runtime;
    return chrome.runtime;
  }

  function readVideo(v) {
    if (!v) return { hasVideo: false };
    return {
      hasVideo: true,
      currentTime: Number(v.currentTime) || 0,
      duration:
        Number.isFinite(v.duration) && v.duration > 0 ? v.duration : 0,
      paused: !!v.paused,
      ended: !!v.ended,
      playbackRate: v.playbackRate || 1,
    };
  }

  let observed = null;
  function snapshot() {
    // Keep event listeners bound to the current player element.
    const v = document.querySelector("video");
    if (v !== observed) {
      observed = v;
      if (v) {
        ["play", "pause", "seeked", "ended"].forEach((evt) =>
          v.addEventListener(evt, sendNow)
        );
      }
    }
    return {
      type: "report",
      isTop: window === window.top,
      url: location.href.slice(0, MAX_URL),
      title: document.title || "",
      visibility: document.visibilityState || "hidden",
      focused: document.hasFocus ? document.hasFocus() : false,
      video: readVideo(v),
    };
  }

  function sendNow() {
    const rt = runtime();
    const msg = snapshot();
    try {
      if (typeof browser !== "undefined") {
        rt.sendMessage(msg).catch(() => {});
      } else {
        rt.sendMessage(msg, () => void chrome.runtime.lastError);
      }
    } catch (_) {
      // Background worker busy/suspended — the interval retries shortly.
    }
  }

  setInterval(sendNow, REPORT_INTERVAL);
  sendNow();
  document.addEventListener("visibilitychange", sendNow);
})();