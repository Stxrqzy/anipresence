import definePlugin from "@utils/types";
import { findByProps } from "@webpack";
import { definePluginSettings } from "@api/Settings";
import { OptionType } from "@utils/types";

import { consumeLatest } from "./native";

const PLUGIN_NAME = "AniPresence";

export const settings = definePluginSettings({
    port: {
        type: OptionType.NUMBER,
        description: "Local port the AniPresence bridge posts activity to",
        default: 6339,
    },
});

const dispatcher = findByProps("dispatch", "subscribe");

export default definePlugin({
    name: PLUGIN_NAME,
    description:
        "Mirror the anime watch activity from the AniPresence desktop app into your " +
        "own Rich Presence, visible to friends even with an unverified Application ID.",
    authors: [{ name: "AniPresence", id: BigInt(0) }],
    settings,
    dependencies: [],

    start() {
        const native = VencordNative.pluginHelpers[PLUGIN_NAME] as {
            openServer?: (port: number) => void;
            consumeLatest?: () => unknown;
        };
        try {
            native?.openServer?.(settings.store.port);
        } catch (err) {
            console.error("[AniPresence] local server failed to start", err);
        }

        this._drain = () => this._drainLatest(native);
        this._timer = setInterval(this._drain, 750);
    },

    _drainLatest(native: { consumeLatest?: () => unknown }) {
        if (!dispatcher) return;
        let payload: unknown = null;
        try {
            payload = (native?.consumeLatest ?? consumeLatest)();
        } catch (err) {
            console.error("[AniPresence] drain failed", err);
            return;
        }
        if (payload === null || payload === undefined) return;

        if (payload && (payload as any).clear) {
            dispatcher.dispatch({ type: "LOCAL_ACTIVITY_UPDATE", activity: null });
            return;
        }
        dispatcher.dispatch({
            type: "LOCAL_ACTIVITY_UPDATE",
            activity: payload,
        });
    },

    stop() {
        clearInterval(this._timer);
        this._timer = undefined;
        const native = VencordNative.pluginHelpers[PLUGIN_NAME] as {
            closeServer?: () => void;
        };
        try {
            native?.closeServer?.();
        } catch (err) {
            console.error("[AniPresence] plugin stop failed", err);
        }
    },
});