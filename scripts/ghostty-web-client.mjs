// Browser client for CANFAR contributed ingress: all URLs must be relative to
// location.pathname (e.g. /session/contrib/<id>/), not domain-root absolute.

import { init, Terminal, FitAddon } from "./dist/ghostty-web.js";

function sessionBase() {
  const p = location.pathname;
  return p.endsWith("/") ? p : `${p}/`;
}

function showError(err) {
  const el = document.getElementById("terminal");
  if (!el) return;
  el.innerHTML = "";
  const box = document.createElement("pre");
  box.style.cssText =
    "margin:1rem;padding:1rem;background:#313244;color:#f38ba8;" +
    "border:1px solid #585b70;border-radius:8px;white-space:pre-wrap;font:14px/1.4 Menlo,monospace";
  box.textContent = `ghostty-web failed to start:\n${err?.stack || err?.message || err}`;
  el.appendChild(box);
}

/** Decode OSC 52 payload (`Ps;Pt` where Pt is base64). Returns null on query/?/bad. */
function decodeOsc52Payload(data) {
  const semi = data.indexOf(";");
  if (semi < 0) return null;
  const b64 = data.slice(semi + 1);
  if (!b64 || b64.charAt(0) === "?") return null;
  try {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder().decode(bytes);
  } catch {
    return null;
  }
}

function writeClipboardText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text);
  }
  return new Promise((resolve, reject) => {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;left:-9999px";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    try {
      if (document.execCommand("copy")) resolve();
      else reject(new Error("execCommand copy failed"));
    } catch (e) {
      reject(e);
    } finally {
      ta.remove();
    }
  });
}

/**
 * ghostty-web does not expose xterm's registerOscHandler. Strip OSC 52 write
 * sequences from the PTY stream, copy to the OS clipboard, and pass the rest
 * through. Handles BEL and ST terminators; carries a small hold buffer for
 * split frames.
 */
function makeOsc52Filter(onCopy) {
  let hold = "";
  const maxHold = 256 * 1024;
  // ESC ] 52 ; <clipboard> ; <b64> BEL | ST
  const osc52 = /\x1b\]52;([^\x07\x1b]*?)(?:\x07|\x1b\\)/g;

  return (chunk) => {
    let data = typeof chunk === "string" ? chunk : String(chunk);
    if (hold) {
      data = hold + data;
      hold = "";
    }
    // Incomplete OSC at end: keep from last ESC ] that has no terminator yet.
    const lastEsc = data.lastIndexOf("\x1b]");
    if (lastEsc >= 0) {
      const tail = data.slice(lastEsc);
      if (!/(?:\x07|\x1b\\)/.test(tail) && tail.length < maxHold) {
        hold = tail;
        data = data.slice(0, lastEsc);
      }
    }
    if (hold.length > maxHold) hold = "";

    let out = "";
    let last = 0;
    osc52.lastIndex = 0;
    let m;
    while ((m = osc52.exec(data)) !== null) {
      out += data.slice(last, m.index);
      last = m.index + m[0].length;
      const text = decodeOsc52Payload(m[1]);
      if (text != null) onCopy(text);
    }
    out += data.slice(last);
    return out;
  };
}

try {
  await init();
  const term = new Terminal({
    cols: 80,
    rows: 24,
    fontFamily: "Menlo, monospace",
    fontSize: 15,
    theme: {
      background: "#1e1e2e",
      foreground: "#cdd6f4",
      cursor: "#f5e0dc",
      selectionBackground: "#585b70",
    },
  });
  const fitAddon = new FitAddon();
  term.loadAddon(fitAddon);
  await term.open(document.getElementById("terminal"));
  fitAddon.fit();
  fitAddon.observeResize();

  // ghostty-web's link click handler is async and only preventDefault()s after
  // awaiting link lookup — too late for Ctrl/Cmd+click on a <canvas>, so the
  // browser treats it as "Open image". Prevent that synchronously, then open
  // via <a> (more reliable than window.open inside the CANFAR iframe).
  const canvas = term.element && term.element.querySelector("canvas");
  const openExternal = (url) => {
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    document.body.appendChild(a);
    a.click();
    a.remove();
  };
  if (canvas) {
    canvas.addEventListener(
      "click",
      (e) => {
        if (e.ctrlKey || e.metaKey) e.preventDefault();
      },
      true,
    );
  }
  // Minified build keeps linkDetector as a plain field; wrap activate so
  // Ctrl/Cmd+click always opens (and plain-text / OSC 8 share one path).
  const ld = term.linkDetector;
  if (ld && typeof ld.getLinkAt === "function") {
    const origGet = ld.getLinkAt.bind(ld);
    ld.getLinkAt = async (col, row) => {
      const link = await origGet(col, row);
      if (!link || !link.text) return link;
      return {
        text: link.text,
        range: link.range,
        hover: link.hover,
        activate: (ev) => {
          if (!(ev.ctrlKey || ev.metaKey)) return;
          ev.preventDefault();
          openExternal(link.text);
        },
      };
    };
  }

  const filterOsc52 = makeOsc52Filter((text) => {
    writeClipboardText(text).catch(() => {});
  });

  // Selection drag → OS clipboard (parity with former ttyd terminal UX).
  if (typeof term.onSelectionChange === "function") {
    term.onSelectionChange(() => {
      const sel = typeof term.getSelection === "function" ? term.getSelection() : "";
      if (sel) writeClipboardText(sel).catch(() => {});
    });
  }

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl =
    `${proto}//${location.host}${sessionBase()}ws?cols=${term.cols}&rows=${term.rows}`;
  let ws;
  function connect() {
    ws = new WebSocket(wsUrl);
    ws.onmessage = (ev) => term.write(filterOsc52(ev.data));
    ws.onclose = () => setTimeout(connect, 2000);
    ws.onerror = () => {
      term.write("\r\n\x1b[31mWebSocket error — retrying…\x1b[0m\r\n");
    };
  }
  connect();

  term.onData((data) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(data);
  });
  term.onResize(({ cols, rows }) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "resize", cols, rows }));
    }
  });
  window.addEventListener("resize", () => fitAddon.fit());

  // ghostty-web 0.4.0 ignores SGR mouse tracking (tmux `mouse on`): its wheel
  // handler only scrolls its own viewport or sends arrow keys on alt-screens,
  // so scrolling over tmux does nothing. Forward wheel events as mouse
  // sequences ourselves whenever the running application requested tracking.
  const sgrMouse = () => {
    try {
      return typeof term.getMode === "function" ? term.getMode(1006, false) : true;
    } catch {
      return true;
    }
  };
  term.attachCustomWheelEventHandler((e) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return true;
    if (e.deltaY === 0 && e.deltaX !== 0) return false; // horizontal: native scroll
    if (typeof term.hasMouseTracking === "function" && !term.hasMouseTracking()) {
      return false; // no app wants mouse events — native viewport scroll
    }
    const canvas = term.element && term.element.querySelector("canvas");
    if (!canvas) return false;
    const rect = canvas.getBoundingClientRect();
    const cellW = rect.width / term.cols;
    const cellH = rect.height / term.rows;
    if (!(cellW > 0 && cellH > 0)) return false;
    const col = Math.min(term.cols, Math.max(1, Math.floor((e.clientX - rect.left) / cellW) + 1));
    const row = Math.min(term.rows, Math.max(1, Math.floor((e.clientY - rect.top) / cellH) + 1));
    let seq;
    if (sgrMouse()) {
      // SGR (mode 1006): button 64 = wheel up, 65 = wheel down.
      seq = `\x1b[<${e.deltaY < 0 ? 64 : 65};${col};${row}M`;
    } else {
      // X10 legacy: wheel up/down are buttons 64/65, encoded +32 like coords.
      const btn = String.fromCharCode((e.deltaY < 0 ? 64 : 65) + 32);
      seq = `\x1b[M${btn}${String.fromCharCode(col + 32)}${String.fromCharCode(row + 32)}`;
    }
    ws.send(seq);
    return true; // consumed — suppress the local viewport scroll
  });
} catch (err) {
  showError(err);
}
