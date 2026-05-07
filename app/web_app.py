#!/usr/bin/env python3
"""Small local web UI for the multi-agent orchestrator."""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orchestrator


APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent if APP_DIR.name == "app" else APP_DIR


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Muti-Agent Workbench</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #1d2430;
      --muted: #687386;
      --line: #d9dee7;
      --accent: #0f766e;
      --accent-ink: #ffffff;
      --soft: #edf7f5;
      --danger: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }
    .app {
      min-height: 100vh;
      display: grid;
      grid-template-columns: 280px 1fr;
    }
    aside {
      background: var(--panel);
      border-right: 1px solid var(--line);
      padding: 18px;
    }
    main {
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-width: 0;
    }
    header {
      padding: 16px 22px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    h1 {
      font-size: 18px;
      margin: 0;
      font-weight: 680;
    }
    label {
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin: 16px 0 6px;
    }
    select, input, textarea, button {
      font: inherit;
    }
    select, input {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      padding: 8px 10px;
    }
    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 14px;
      color: var(--ink);
      font-size: 13px;
    }
    .check input {
      width: auto;
      min-height: auto;
      margin: 0;
    }
    .hint {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      margin-top: 12px;
    }
    .chat {
      padding: 22px;
      overflow: auto;
    }
    .message {
      max-width: 980px;
      margin: 0 0 14px;
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      white-space: pre-wrap;
      line-height: 1.5;
    }
    .message.user {
      background: var(--soft);
      border-color: #b9ded8;
    }
    .meta {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 8px;
    }
    .composer {
      background: var(--panel);
      border-top: 1px solid var(--line);
      padding: 14px 22px 18px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
    }
    textarea {
      min-height: 72px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      color: var(--ink);
    }
    button {
      border: 0;
      border-radius: 8px;
      background: var(--accent);
      color: var(--accent-ink);
      padding: 0 18px;
      min-width: 96px;
      cursor: pointer;
      font-weight: 620;
    }
    button:disabled {
      opacity: .55;
      cursor: wait;
    }
    .error {
      color: var(--danger);
    }
    @media (max-width: 820px) {
      .app { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .composer { grid-template-columns: 1fr; }
      button { min-height: 42px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <h1>Muti-Agent</h1>
      <label for="agent">Agent</label>
      <select id="agent"></select>
      <label for="skill">Skill</label>
      <select id="skill">
        <option value="">Auto</option>
      </select>
      <label for="provider">Provider</label>
      <select id="provider">
        <option value="quality-review">Quality Review</option>
        <option value="echo">Echo</option>
        <option value="codex-cli">Codex CLI</option>
      </select>
      <label for="context">Context files or folders</label>
      <input id="context" placeholder="path1.md, /path/to/latex-folder" />
      <label for="deepseek-key">DeepSeek API key</label>
      <input id="deepseek-key" type="password" autocomplete="off" placeholder="optional; not saved" />
      <label class="check">
        <input id="use-gemini" type="checkbox" checked />
        <span>Use Gemini readability review</span>
      </label>
      <div class="hint">
        Separate multiple paths with commas. Folders are scanned for tex, bib, cls, sty, md, txt, json, yaml, and csv/tsv files.
        Use <strong>Quality Review</strong> for parallel DeepSeek grammar, Codex format, and Gemini readability review. Use <strong>Echo</strong> to preview prompts safely.
        Use <strong>Codex CLI</strong> for small direct model runs.
      </div>
    </aside>
    <main>
      <header>
        <h1>Agent Chat</h1>
        <div class="hint" id="status">Ready</div>
      </header>
      <section class="chat" id="chat"></section>
      <form class="composer" id="form">
        <textarea id="task" placeholder="@reviewer 审查这个 LaTeX 项目"></textarea>
        <button id="send" type="submit">Send</button>
      </form>
    </main>
  </div>
  <script>
    const state = { agents: [], skills: [] };
    const el = (id) => document.getElementById(id);

    function addMessage(kind, text, meta = "") {
      const node = document.createElement("div");
      node.className = `message ${kind}`;
      if (meta) {
        const m = document.createElement("div");
        m.className = "meta";
        m.textContent = meta;
        node.appendChild(m);
      }
      const body = document.createElement("div");
      body.textContent = text;
      node.appendChild(body);
      el("chat").appendChild(node);
      el("chat").scrollTop = el("chat").scrollHeight;
    }

    async function loadMeta() {
      const res = await fetch("/api/meta");
      const data = await res.json();
      state.agents = data.agents;
      state.skills = data.skills;
      el("agent").innerHTML = state.agents.map(a => `<option value="${a}">${a}</option>`).join("");
      el("agent").value = "reviewer";
      el("skill").innerHTML = `<option value="">Auto</option>` + state.skills.map(s => `<option value="${s}">${s}</option>`).join("");
    }

    el("form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const task = el("task").value.trim();
      if (!task) return;
      const context = el("context").value.split(",").map(s => s.trim()).filter(Boolean);
      const skillValue = el("skill").value;
      addMessage("user", task, "You");
      el("task").value = "";
      el("send").disabled = true;
      const providerLabel = el("provider").selectedOptions[0]?.textContent || el("provider").value;
      el("status").textContent = providerLabel === "Quality Review" ? "Indexing and reviewing..." : "Running...";
      try {
        const res = await fetch("/api/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            task,
            agent: el("agent").value,
            provider: el("provider").value,
            context,
            skill: skillValue ? [skillValue] : [],
            auto_skill: !skillValue,
            deepseek_api_key: el("deepseek-key").value.trim(),
            use_gemini: el("use-gemini").checked,
          }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Request failed");
        const metaParts = [
          data.agent,
          `provider: ${data.provider}`,
          `skills: ${(data.skills || []).join(", ") || "none"}`,
          data.report_path ? `report: ${data.report_path}` : data.run_dir,
        ];
        addMessage("assistant", data.output, metaParts.join(" | "));
        el("status").textContent = "Ready";
      } catch (error) {
        addMessage("assistant error", String(error.message || error), "Error");
        el("status").textContent = "Error";
      } finally {
        el("send").disabled = false;
      }
    });

    loadMeta().catch(error => {
      addMessage("assistant error", String(error.message || error), "Startup error");
    });
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/meta":
            self._send_json(200, {"agents": orchestrator.list_agents(), "skills": orchestrator.list_skills()})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/ask":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            result = orchestrator.run_task(
                task=payload.get("task", ""),
                agent=payload.get("agent") or None,
                context=payload.get("context") or [],
                skill=payload.get("skill") or [],
                provider=payload.get("provider") or "echo",
                auto_skill=bool(payload.get("auto_skill", True)),
                deepseek_api_key=payload.get("deepseek_api_key") or None,
                use_gemini=bool(payload.get("use_gemini", True)),
            )
            result.pop("prompt", None)
            if result.get("provider") == "echo":
                summary = result.get("context_summary") or {}
                files = summary.get("files") or []
                preview_limit = 5
                preview = "\n".join(f"- {item['path']} ({item['bytes']} bytes)" for item in files[:preview_limit])
                extra = ""
                if len(files) > preview_limit:
                    extra = f"\n- ... {len(files) - preview_limit} more files"
                result["output"] = (
                    "Echo preview complete.\n\n"
                    f"Agent: {result.get('agent')}\n"
                    f"Skills: {', '.join(result.get('skills') or []) or 'none'}\n"
                    f"Context files: {summary.get('count', 0)}\n"
                    f"Context bytes: {summary.get('total_bytes', 0)}\n"
                    f"Run saved: {result.get('run_dir')}\n\n"
                    "Loaded files:\n"
                    f"{preview or '- none'}"
                    f"{extra}\n\n"
                    "Full prompt is saved as prompt.md inside the run directory."
                )
            self._send_json(200, result)
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})

    def log_message(self, fmt: str, *args) -> None:
        print(f"[web] {self.address_string()} - {fmt % args}")


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Muti-Agent web UI: http://127.0.0.1:8765")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
