#!/usr/bin/env node
// PTY over WebSocket + ghostty-web assets. Protocol matches @ghostty-web/demo:
// text frames, JSON {type:"resize",cols,rows}. Listen on 0.0.0.0:5000 for CANFAR.

import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import pty from "@lydell/node-pty";
import { WebSocketServer } from "ws";

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 5000);
const HOST = process.env.HOST || "0.0.0.0";
const TITLE = process.env.ASTROAI_TAB_TITLE || "AstroAI ghostty-web";
const CWD = process.env.PWD || process.cwd();

function stickTitleScript(title) {
  return `<script data-astroai-tab="1">(function(){var t=${JSON.stringify(title)};function s(){if(document.title!==t)document.title=t}s();addEventListener('load',s);var el=document.querySelector('title');if(el)new MutationObserver(s).observe(el,{childList:true,characterData:true,subtree:true});})();</script>`;
}

const ghosttyMain = require.resolve("ghostty-web");
const pkgRoot = ghosttyMain.replace(/[/\\]dist[/\\].*$/, "");
const distPath = path.join(pkgRoot, "dist");
const wasmPath = path.join(pkgRoot, "ghostty-vt.wasm");
const clientPath = path.join(__dirname, "client.mjs");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript",
  ".mjs": "application/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".wasm": "application/wasm",
};

const HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>${TITLE.replace(/[<>]/g, "")}</title>
${stickTitleScript(TITLE)}
<script>if(!location.pathname.endsWith("/"))location.replace(location.pathname+"/"+location.search+location.hash);</script>
<style>
html,body{margin:0;height:100%;background:#1e1e2e;overflow:hidden}
#terminal{position:absolute;inset:0}
#terminal canvas{display:block}
</style>
</head>
<body>
<div id="terminal"></div>
<script type="module" src="./client.mjs"></script>
</body>
</html>`;

function underRoot(root, rel) {
  const resolved = path.resolve(root, rel);
  const base = path.resolve(root);
  if (resolved !== base && !resolved.startsWith(base + path.sep)) return null;
  return resolved;
}

function sendFile(filePath, res) {
  const type = MIME[path.extname(filePath)] || "application/octet-stream";
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404);
      res.end("Not Found");
      return;
    }
    res.writeHead(200, { "Content-Type": type });
    res.end(data);
  });
}

const sessions = new Map();

function spawnTmux(cols, rows) {
  return pty.spawn("tmux", ["new-session", "-A", "-s", "astroai", "bash", "-l"], {
    name: "xterm-256color",
    cols,
    rows,
    cwd: CWD,
    env: { ...process.env, TERM: "xterm-256color", COLORTERM: "truecolor" },
  });
}

const httpServer = http.createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const pathname = url.pathname;
  if (pathname === "/" || pathname === "/index.html") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(HTML);
    return;
  }
  if (pathname === "/client.mjs") {
    sendFile(clientPath, res);
    return;
  }
  if (pathname.startsWith("/dist/")) {
    const filePath = underRoot(distPath, pathname.slice("/dist/".length));
    if (!filePath) {
      res.writeHead(403);
      res.end();
      return;
    }
    sendFile(filePath, res);
    return;
  }
  if (pathname === "/ghostty-vt.wasm") {
    sendFile(wasmPath, res);
    return;
  }
  res.writeHead(404);
  res.end("Not Found");
});

const wss = new WebSocketServer({ noServer: true });
httpServer.on("upgrade", (req, socket, head) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  if (url.pathname !== "/ws" && !url.pathname.endsWith("/ws")) {
    socket.destroy();
    return;
  }
  wss.handleUpgrade(req, socket, head, (ws) => wss.emit("connection", ws, req));
});

wss.on("connection", (ws, req) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const cols = Number.parseInt(url.searchParams.get("cols") || "80", 10);
  const rows = Number.parseInt(url.searchParams.get("rows") || "24", 10);
  const ptyProcess = spawnTmux(cols, rows);
  sessions.set(ws, ptyProcess);
  ptyProcess.onData((data) => {
    if (ws.readyState === ws.OPEN) ws.send(data);
  });
  ptyProcess.onExit(() => {
    if (ws.readyState === ws.OPEN) ws.close();
  });
  ws.on("message", (data) => {
    const message = data.toString("utf8");
    if (message.startsWith("{")) {
      try {
        const msg = JSON.parse(message);
        if (msg.type === "resize") {
          ptyProcess.resize(msg.cols, msg.rows);
          return;
        }
      } catch {
        // input that happens to start with '{'
      }
    }
    ptyProcess.write(message);
  });
  const drop = () => {
    const p = sessions.get(ws);
    if (p) {
      p.kill();
      sessions.delete(ws);
    }
  };
  ws.on("close", drop);
  ws.on("error", drop);
});

process.on("SIGINT", () => process.exit(0));
process.on("SIGTERM", () => process.exit(0));

httpServer.listen(PORT, HOST, () => {
  console.log(`ghostty-web listening on http://${HOST}:${PORT}/ cwd=${CWD}`);
});
