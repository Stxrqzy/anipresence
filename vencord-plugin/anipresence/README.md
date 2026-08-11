AniPresence Vencord userplugin
===============================

A [Vencord](https://github.com/Vendicated/Vencord) userplugin that turns the
activity AniPresence posts into a `LOCAL_ACTIVITY_UPDATE`, so your Rich
Presence is visible to friends even though your Discord Application ID is
unverified.

How it works
------------

- AniPresence posts each activity change to a tiny local server this plugin
  opens on `127.0.0.1:6339`.
- The plugin forwards the payload to Discord as a `LOCAL_ACTIVITY_UPDATE`
  (Vencord's client mod accepts local activity regardless of server-side app
  verification), which makes the presence visible to other users.
- Pick `Vencord bridge` (or `Both`) under **Broadcast** in the AniPresence
  Controls. If the plugin isn't running the app simply falls back to the
  official Discord pipe.

Install
-------

1. Copy the `anipresence` folder into Vencord's `src\userplugins\`.
2. Rebuild Vencord (`pnpm build` / `pnpm build:standalone`) and restart.
3. Start AniPresence and set *Broadcast* to `Vencord bridge` or `Both`.

The server binds to `127.0.0.1` only, and the plugin is the sole listener on
the port, so nothing external can spoof your presence.