/**
 * Native (main-process) half of the AniPresence Vencord plugin.
 *
 * Opens a tiny HTTP server on 127.0.0.1 that AniPresence POSTs activity changes
 * to. Because the native half runs outside the renderer it cannot dispatch
 * Discord events itself; instead each posted payload is parked in a "latest"
 * slot that the renderer (index.tsx) drains and turns into a
 * LOCAL_ACTIVITY_UPDATE.
 */
import { createServer, IncomingMessage, ServerResponse } from "node:http";

let server: ReturnType<typeof createServer> | null = null;
let latestActivity: unknown = null;

function readBody(req: IncomingMessage): Promise<string> {
    return new Promise((resolve) => {
        let body = "";
        req.on("data", (chunk) => (body += chunk.toString()));
        req.on("end", () => resolve(body));
    });
}

export function openServer(port: number) {
    if (server) return;
    server = createServer(async (req: IncomingMessage, res: ServerResponse) => {
        if (req.method !== "POST") {
            res.statusCode = 405;
            res.end();
            return;
        }
        try {
            const raw = await readBody(req);
            latestActivity = raw ? JSON.parse(raw) : null;
            res.statusCode = 200;
            res.setHeader("Content-Type", "application/json");
            res.end(JSON.stringify({ ok: true }));
        } catch {
            res.statusCode = 400;
            res.end(JSON.stringify({ ok: false }));
        }
    });
    // Only ever bind to the loopback interface.
    server.listen(port, "127.0.0.1");
}

export function closeServer() {
    if (!server) return;
    server.close();
    server = null;
    latestActivity = null;
}

/**
 * Atomically take the last posted activity (or { clear: true }). Returns null
 * when nothing new has arrived since the last call.
 */
export function consumeLatest(): unknown {
    const next = latestActivity;
    latestActivity = null;
    return next;
}