// AniPresence Bridge — content script.
//
// Runs in every frame and reports the raw player state to the background
// worker, which batches the tabs into one POST per cadence. This works for a
// normally-launched browser window (any profile), which is the whole point:
// the desktop app never needs a CDP debug port when the extension is around.

(() => {
  const REPORT_INTERVAL = 3000;
  const MAX_URL = 2000;
  // Captured in this script, so old tabs are detectable after an extension update.
  const READER_VERSION = "1.7.0";

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

  // Keep this reader in sync with PAGE_INFO_JS in src/core/controller.py.
  function readAniPmEpisode() {
    if (!/(^|\.)ani\.pm$/i.test(location.hostname)) return null;
    const ordinal = document.querySelector('.ytw-title__n');
    const heading = document.querySelector('h1.ytw-title');
    const current = document.querySelector(
      '[data-episode-number].is-current, [data-episode-number][aria-current="true"], ' +
      '[data-episode-number]:has([aria-current="true"])'
    );
    // The heading remains mounted even when the playlist is filtered/virtualized.
    const text = ordinal ? ordinal.textContent.trim().replace(/\.$/, '') : '';
    const match = heading && heading.textContent.match(/^Episode\s+(\d{1,4})\b/i);
    const raw = text || (match && match[1]) ||
      (current && current.getAttribute('data-episode-number'));
    if (!/^\d{1,4}$/.test(raw || '')) return null;
    const episode = Number(raw);
    return episode > 0 ? episode : null;
  }

  function readAniPmEpisodeTitle() {
    if (!/(^|\.)ani\.pm$/i.test(location.hostname)) return '';
    const heading = document.querySelector('h1.ytw-title');
    const ordinal = document.querySelector('.ytw-title__n');
    // ani.pm includes an ordinal only for a named episode. Plain "Episode N"
    // headings and movie/series titles must not become episode names.
    if (!heading || !ordinal) return '';
    const text = heading.textContent.trim();
    const prefix = ordinal.textContent.trim();
    if (!prefix || !text.startsWith(prefix)) return '';
    return text.slice(prefix.length).replace(/\s+/g, ' ').trim().slice(0, 128);
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
      readerVersion: READER_VERSION,
      isTop: window === window.top,
      url: location.href.slice(0, MAX_URL),
      title: document.title || "",
      episode: readAniPmEpisode(),
      episodeTitle: readAniPmEpisodeTitle(),
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
