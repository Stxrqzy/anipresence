// Exercise the shipped extension and CDP readers with ani.pm player DOM values.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const cdp = fs.readFileSync(0, 'utf8');
const content = fs.readFileSync('browser-extension/content.js', 'utf8');
const background = fs.readFileSync('browser-extension/background.js', 'utf8');

for (const [host, ordinal, heading, selected, expected] of [
  ['ani.pm', '5.', '5. A named episode', '4', 5],
  ['ani.pm', null, 'Episode 6', null, 6],
  ['www.ani.pm', null, null, '7', 7],
  ['ani.pm', null, null, null, null],
  ['ani.pm', null, null, 'e1.token', null],
  ['other.example', '5.', 'Episode 5', '5', null],
]) {
  let report, interval;
  const document = {
    title: 'Watch NARUTO · ani.pm',
    querySelector(selector) {
      if (selector === 'video') return null;
      if (selector === '.ytw-title__n') return ordinal && {textContent: ordinal};
      if (selector === 'h1.ytw-title') return heading && {textContent: heading};
      if (selector.includes('[data-episode-number]')) {
        return selected && {getAttribute: () => selected};
      }
      throw new Error(`Unexpected selector: ${selector}`);
    },
    addEventListener() {},
  };
  const window = {}; window.top = window;
  const ctx = {
    document, window, location: {hostname: host, href: `https://${host}/anime/naruto?ep=e1.token`},
    chrome: {runtime: {sendMessage(msg) { report = msg; }}},
    setInterval(fn) { interval = fn; },
  };
  assert.equal(vm.runInNewContext(cdp, ctx).episode, expected);
  vm.runInNewContext(content, ctx);
  assert.equal(report.episode, expected);
  // A SPA page can change its episode without reloading the content script.
  document.querySelector = () => null;
  interval();
  assert.equal(report.episode, null);
}

// The iframe's video must not replace the top-level page's episode metadata.
let listener;
const ctx = {
  chrome: {
    runtime: {onMessage: {addListener(fn) { listener = fn; }}},
    tabs: {onRemoved: {addListener() {}}},
    alarms: {create() {}, onAlarm: {addListener() {}}},
  },
  setTimeout() { return 1; },
};
vm.createContext(ctx);
vm.runInContext(background, ctx);
const sender = {tab: {id: 1}};
listener({type: 'report', isTop: true, episode: 5, url: 'https://ani.pm/anime/naruto?ep=e1.token'}, sender);
listener({type: 'report', isTop: false, episode: 99, video: {hasVideo: true}}, sender);
assert.equal(vm.runInContext('lastPerTab.get("1").payload.episode', ctx), 5);
listener({type: 'report', isTop: true, episode: 6}, sender);
assert.equal(vm.runInContext('lastPerTab.get("1").payload.episode', ctx), 6);
listener({type: 'report', isTop: true}, sender);
assert.equal(vm.runInContext('lastPerTab.get("1").payload.episode', ctx), null);
