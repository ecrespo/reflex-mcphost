// reflex-mcphost MCP App relay (TD DD-012, API §7, delta D4; MCP Apps 2026-01-26).
//
// The only code on the Host UI's origin that touches MCP App messages. It handles them as
// data: it renders the sandbox proxy iframe (a distinct origin), forwards every message the
// proxy window posts (event.source === that window AND event.origin === the proxy origin)
// to the backend as a JSON string (dropped above 1 MiB), and posts each new backend outbox
// entry ({seq, message}) to the proxy window with the proxy origin as target, then
// acknowledges its seq. Message content is never written to the DOM and never evaluated.
import React, { useEffect, useRef } from "react";

const MAX_MESSAGE_BYTES = 1048576; // 1 MiB (API §7 "Limits")

function utf8Length(text) {
  return new TextEncoder().encode(text).length;
}

function serialize(data) {
  if (typeof data === "string") return data;
  try {
    const text = JSON.stringify(data);
    return typeof text === "string" ? text : null;
  } catch (error) {
    return null;
  }
}

export function McpAppRelay({
  viewId,
  proxySrc,
  proxyOrigin,
  outbox,
  height,
  border,
  onMessage,
  onAck,
}) {
  const frame = useRef(null);
  const posted = useRef({ viewId: null, seq: 0 });
  const handlers = useRef({ onMessage, onAck });
  handlers.current = { onMessage, onAck };

  // Proxy -> backend: identity AND origin checks, size limit, data only.
  useEffect(() => {
    function receive(event) {
      const node = frame.current;
      if (node === null || node.contentWindow === null) return;
      if (event.source !== node.contentWindow) return;
      if (event.origin !== proxyOrigin) return;
      const text = serialize(event.data);
      if (text === null || text.length > MAX_MESSAGE_BYTES) return;
      if (utf8Length(text) > MAX_MESSAGE_BYTES) return;
      if (handlers.current.onMessage) handlers.current.onMessage(viewId, text);
    }
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [viewId, proxyOrigin]);

  // Backend -> proxy: post each entry once, in seq order, then acknowledge the last seq.
  useEffect(() => {
    const node = frame.current;
    if (node === null || node.contentWindow === null || !Array.isArray(outbox)) return;
    if (posted.current.viewId !== viewId) posted.current = { viewId: viewId, seq: 0 };
    let last = posted.current.seq;
    for (const entry of outbox) {
      if (entry === null || typeof entry !== "object") continue;
      const seq = Number(entry.seq);
      if (!Number.isInteger(seq) || seq <= last || typeof entry.message !== "string") continue;
      node.contentWindow.postMessage(entry.message, proxyOrigin);
      last = seq;
    }
    if (last !== posted.current.seq) {
      posted.current.seq = last;
      if (handlers.current.onAck) handlers.current.onAck(viewId, last);
    }
  }, [outbox, viewId, proxyOrigin]);

  return React.createElement("iframe", {
    ref: frame,
    src: proxySrc,
    sandbox: "allow-scripts allow-same-origin",
    allow: "",
    referrerPolicy: "no-referrer",
    title: "MCP App",
    "data-testid": "mcp-app-proxy",
    style: {
      display: "block",
      width: "100%",
      height: String(Number(height) || 320) + "px",
      border: border ? "1px solid var(--gray-a6)" : "0",
      borderRadius: "var(--radius-2)",
    },
  });
}
