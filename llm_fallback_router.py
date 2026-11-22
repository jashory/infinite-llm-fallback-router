#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Infinite LLM Auto-Fallback Router (Production Multi-Threaded SSE Streaming Edition)
Author: HM Shihabuddin Jashory (Shihab) — Vision Flow (https://visionflowgroup.com)
License: MIT

Features:
1. Full Server-Sent Events (`stream: true` / `text/event-stream`) & Non-Streaming support
   via `ThreadingHTTPServer` so concurrent agent tool calls and subagents never block.
2. Automatic Zero-Downtime Failover across unlimited free & paid providers (OpenRouter,
   Groq, Gemini, Anthropic, OpenAI, 9Router, Ollama) on HTTP 429, 402, 500, 502, 503, 504, or timeout.
3. Built-in Anthropic `/v1/messages` <-> OpenAI `/v1/chat/completions` bridge so Claude Code,
   Cursor, Antigravity, and Codex can all use any provider pool seamlessly.
4. Live `/stats` and `/health` JSON telemetry + automatic cooldown recovery.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_PROVIDERS = [
    {
        "name": "primary-provider",
        "base_url": "https://api.openai.com/v1/chat/completions",
        "api_key_env": "OPENAI_API_KEY",
        "model_override": "",
        "cooldown_seconds": 60,
        "timeout": 30,
    }
]


def anthropic_to_openai_payload(anth_body: dict[str, Any]) -> dict[str, Any]:
    """Converts Anthropic `/v1/messages` request format to OpenAI `/v1/chat/completions`."""
    messages = []
    sys_prompt = anth_body.get("system")
    if isinstance(sys_prompt, str) and sys_prompt.strip():
        messages.append({"role": "system", "content": sys_prompt})
    elif isinstance(sys_prompt, list):
        txt = "\n".join(b.get("text", "") for b in sys_prompt if isinstance(b, dict))
        if txt.strip():
            messages.append({"role": "system", "content": txt})

    for m in anth_body.get("messages", []):
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
        messages.append({"role": role, "content": content})

    return {
        "model": anth_body.get("model", "gpt-4o"),
        "messages": messages,
        "max_tokens": anth_body.get("max_tokens", 4096),
        "stream": bool(anth_body.get("stream", False)),
    }


def openai_to_anthropic_response(oai_resp: dict[str, Any], model: str) -> dict[str, Any]:
    """Converts OpenAI chat completion response to Anthropic `/v1/messages` response."""
    choices = oai_resp.get("choices") or [{}]
    msg = choices[0].get("message") or {}
    text = msg.get("content") or ""
    usage = oai_resp.get("usage") or {}
    return {
        "id": oai_resp.get("id", "msg_router"),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
        "_routed_provider": oai_resp.get("_routed_provider", ""),
    }


class FallbackRouterEngine:
    def __init__(self, providers: list[dict[str, Any]]):
        self.providers = providers
        self.lock = threading.Lock()
        self.cooldown_until: dict[str, float] = {}
        self.stats: dict[str, dict[str, int]] = {
            p["name"]: {"success": 0, "failover": 0, "stream_chunks": 0} for p in providers
        }

    def open_upstream(self, payload: dict[str, Any]) -> tuple[Any, str, list[str]]:
        """Finds the first healthy upstream provider and returns the open HTTP response handle."""
        now = time.time()
        errors = []

        for prov in self.providers:
            name = prov["name"]
            with self.lock:
                cd_until = self.cooldown_until.get(name, 0)
            if cd_until > now:
                errors.append(f"{name}: cooldown ({int(cd_until - now)}s left)")
                continue

            url = prov["base_url"]
            api_key = prov.get("api_key") or os.environ.get(prov.get("api_key_env", ""), "")
            req_body = dict(payload)
            if prov.get("model_override"):
                req_body["model"] = prov["model_override"]

            data = json.dumps(req_body).encode("utf-8")
            req_headers = {"Content-Type": "application/json"}
            if api_key:
                req_headers["Authorization"] = f"Bearer {api_key}"

            req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
            try:
                resp = urllib.request.urlopen(req, timeout=float(prov.get("timeout", 30)))
                with self.lock:
                    self.stats[name]["success"] += 1
                return resp, name, errors
            except urllib.error.HTTPError as e:
                code = e.code
                with self.lock:
                    self.stats[name]["failover"] += 1
                    if code in (429, 402, 500, 502, 503, 504):
                        self.cooldown_until[name] = time.time() + float(prov.get("cooldown_seconds", 60))
                errors.append(f"{name}: HTTP {code}")
            except Exception as e:
                with self.lock:
                    self.stats[name]["failover"] += 1
                    self.cooldown_until[name] = time.time() + float(prov.get("cooldown_seconds", 30))
                errors.append(f"{name}: {e}")

        return None, "none", errors

    def forward_chat(self, payload: dict[str, Any], headers_in: dict[str, str]) -> tuple[int, dict[str, Any], str]:
        resp, used_provider, errors = self.open_upstream(payload)
        if resp is None:
            return 503, {"error": "All providers exhausted", "details": errors}, "none"
        try:
            raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                parsed["_routed_provider"] = used_provider
            return resp.status, parsed, used_provider
        finally:
            resp.close()


def create_handler(engine: FallbackRouterEngine):
    class RouterHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            if self.path in ("/health", "/stats", "/v1/models"):
                out = json.dumps({"status": "ok", "stats": engine.stats}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", errors="replace") if length > 0 else "{}"
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {}

            is_anthropic = self.path.startswith("/v1/messages")
            orig_model = payload.get("model", "gpt-4o")
            if is_anthropic:
                payload = anthropic_to_openai_payload(payload)

            is_stream = bool(payload.get("stream", False))
            if is_stream and not is_anthropic:
                resp, used_provider, errors = engine.open_upstream(payload)
                if resp is None:
                    err_body = json.dumps({"error": "All providers exhausted", "details": errors}).encode("utf-8")
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(err_body)
                    return

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Routed-Provider", used_provider)
                self.end_headers()
                try:
                    for line in resp:
                        self.wfile.write(line)
                        self.wfile.flush()
                        with engine.lock:
                            engine.stats[used_provider]["stream_chunks"] += 1
                finally:
                    resp.close()
                return

            status, resp_obj, used_provider = engine.forward_chat(payload, dict(self.headers))
            if status == 200 and is_anthropic:
                resp_obj = openai_to_anthropic_response(resp_obj, orig_model)
            body = json.dumps(resp_obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Routed-Provider", used_provider)
            self.end_headers()
            self.wfile.write(body)

    return RouterHandler


__version__ = "2.1.1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Infinite LLM Auto-Fallback Router (Streaming + Multi-Threaded)")
    parser.add_argument("--config", type=str, default="", help="Path to providers JSON")
    parser.add_argument("--port", type=int, default=20129, help="Listen port")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    providers = DEFAULT_PROVIDERS
    if args.config and os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            providers = json.load(f)

    engine = FallbackRouterEngine(providers)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), create_handler(engine))
    print(f"Infinite LLM Router v{__version__} listening on http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

