#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Infinite LLM Auto-Fallback Router & Stdio MCP Server (v2.2.0)
Author: HM Shihabuddin Jashory (Shihab) — Vision Flow Group (https://visionflowgroup.com)
License: MIT

Features:
1. Full Server-Sent Events (`stream: true` / `text/event-stream`) & Non-Streaming support
   via `ThreadingHTTPServer` so concurrent agent tool calls and subagents never block.
2. Automatic Zero-Downtime Failover across unlimited free & paid providers (OpenRouter,
   Groq, Gemini, Anthropic, OpenAI, 9Router, Ollama) on HTTP 429, 402, 500, 502, 503, 504, or timeout.
3. Built-in Anthropic `/v1/messages` <-> OpenAI `/v1/chat/completions` bridge so Claude Code,
   Cursor, Antigravity, and Codex can all use any provider pool seamlessly.
4. Stdio JSON-RPC 2.0 Model Context Protocol (MCP) server (`--mcp`) exposing real tools:
   `list_provider_pool`, `convert_anthropic_to_openai`, `route_chat_completion`, `check_router_health`.
5. Deterministic loopback self-test (`--self-test`) verifying live HTTP 429 -> 200 failover,
   circuit-breaker cooldowns, SSE streaming, and Anthropic <-> OpenAI translation.
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

__version__ = "2.2.0"

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

    def snapshot_pool(self) -> list[dict[str, Any]]:
        now = time.time()
        rows: list[dict[str, Any]] = []
        with self.lock:
            for p in self.providers:
                name = p["name"]
                cd = max(0.0, self.cooldown_until.get(name, 0.0) - now)
                key_env = p.get("api_key_env", "")
                has_key = bool(p.get("api_key") or (key_env and os.environ.get(key_env)))
                rows.append(
                    {
                        "name": name,
                        "base_url": p["base_url"],
                        "model_override": p.get("model_override", ""),
                        "api_key_env": key_env,
                        "credentials_configured": has_key,
                        "cooldown_remaining_seconds": round(cd, 2),
                        "status": "cooldown" if cd > 0 else "ready",
                        "stats": dict(self.stats.get(name, {})),
                    }
                )
        return rows

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
                out = json.dumps({"status": "ok", "version": __version__, "stats": engine.stats}).encode("utf-8")
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


def run_self_test() -> int:
    """Runs deterministic loopback tests for HTTP 429->200 failover, cooldowns, SSE streaming, and Anthropic bridge."""

    class Mock429Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 0:
                self.rfile.read(length)
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"rate_limit_exceeded"}')

    class MockOkHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", errors="replace") if length > 0 else "{}"
            req = json.loads(raw)
            if req.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"ok-stream"}}]}\n\n')
                self.wfile.write(b"data: [DONE]\n\n")
                return
            resp = {
                "id": "chatcmpl-selftest",
                "choices": [{"message": {"role": "assistant", "content": "failover-success"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode("utf-8"))

    m429 = ThreadingHTTPServer(("127.0.0.1", 0), Mock429Handler)
    mok = ThreadingHTTPServer(("127.0.0.1", 0), MockOkHandler)
    p429 = m429.server_address[1]
    pok = mok.server_address[1]

    providers = [
        {"name": "mock-429-primary", "base_url": f"http://127.0.0.1:{p429}/v1/chat/completions", "cooldown_seconds": 30, "timeout": 5},
        {"name": "mock-200-backup", "base_url": f"http://127.0.0.1:{pok}/v1/chat/completions", "cooldown_seconds": 30, "timeout": 5},
    ]
    engine = FallbackRouterEngine(providers)
    router = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(engine))
    prouter = router.server_address[1]

    threads = [
        threading.Thread(target=m429.serve_forever, daemon=True),
        threading.Thread(target=mok.serve_forever, daemon=True),
        threading.Thread(target=router.serve_forever, daemon=True),
    ]
    for t in threads:
        t.start()

    try:
        # 1. Anthropic /v1/messages failover test
        anth_req = urllib.request.Request(
            f"http://127.0.0.1:{prouter}/v1/messages",
            data=json.dumps({
                "model": "claude-sonnet-4-5",
                "system": "You are concise.",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 32,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(anth_req, timeout=10) as r:
            body = json.loads(r.read().decode("utf-8"))
            assert r.status == 200
            assert body["type"] == "message"
            assert body["content"][0]["text"] == "failover-success"
            assert body["_routed_provider"] == "mock-200-backup"

        # 2. SSE streaming test (primary should now be in cooldown!)
        sse_req = urllib.request.Request(
            f"http://127.0.0.1:{prouter}/v1/chat/completions",
            data=json.dumps({"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "stream": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(sse_req, timeout=10) as r:
            sse_text = r.read().decode("utf-8")
            assert r.status == 200
            assert "ok-stream" in sse_text
            assert r.headers.get("X-Routed-Provider") == "mock-200-backup"

        # 3. Health telemetry check
        with urllib.request.urlopen(f"http://127.0.0.1:{prouter}/health", timeout=10) as r:
            health = json.loads(r.read().decode("utf-8"))
            assert health["status"] == "ok"
            assert health["stats"]["mock-429-primary"]["failover"] == 1
            assert health["stats"]["mock-200-backup"]["success"] == 2

        print(json.dumps({"self_test": "passed", "version": __version__, "stats": health["stats"]}, indent=2))
        return 0
    finally:
        router.shutdown()
        router.server_close()
        m429.shutdown()
        m429.server_close()
        mok.shutdown()
        mok.server_close()


def run_mcp_server(providers: list[dict[str, Any]] | None = None) -> int:
    """Stdio JSON-RPC 2.0 Model Context Protocol (MCP) server for multi-provider LLM fallback routing."""
    engine = FallbackRouterEngine(providers or DEFAULT_PROVIDERS)
    tools_spec = [
        {
            "name": "list_provider_pool",
            "description": "Inspect configured LLM failover providers, credential readiness, active circuit-breaker cooldowns, and routing stats.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "convert_anthropic_to_openai",
            "description": "Translate an Anthropic /v1/messages request payload into an OpenAI-compatible /v1/chat/completions payload.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "Target model identifier."},
                    "system": {"type": "string", "description": "Optional system prompt."},
                    "messages": {
                        "type": "array",
                        "description": "List of Anthropic message objects ({role, content}).",
                        "items": {"type": "object"},
                    },
                    "max_tokens": {"type": "integer", "default": 4096},
                    "stream": {"type": "boolean", "default": False},
                },
                "required": ["messages"],
                "additionalProperties": False,
            },
        },
        {
            "name": "route_chat_completion",
            "description": "Route a chat completion request across the configured provider pool with automatic HTTP 429/402/5xx failover.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "default": "gpt-4o"},
                    "messages": {
                        "type": "array",
                        "description": "OpenAI-style chat messages ({role, content}).",
                        "items": {"type": "object"},
                    },
                    "max_tokens": {"type": "integer", "default": 1024},
                },
                "required": ["messages"],
                "additionalProperties": False,
            },
        },
        {
            "name": "check_router_health",
            "description": "Return circuit-breaker health diagnostics, provider pool readiness, and failover counters.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    ]

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method")
        msg_id = msg.get("id")
        if msg_id is None:
            continue

        if method == "initialize":
            req_proto = str((msg.get("params") or {}).get("protocolVersion") or "2024-11-05")
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": req_proto,
                    "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                    "serverInfo": {"name": "infinite-llm-fallback-router", "version": __version__},
                },
            }
        elif method == "ping":
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        elif method == "resources/list":
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": []}}
        elif method == "prompts/list":
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": []}}
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools_spec}}
        elif method == "tools/call":
            params = msg.get("params") or {}
            tname = params.get("name")
            targs = params.get("arguments") or {}
            if tname == "list_provider_pool":
                payload = json.dumps({"version": __version__, "providers": engine.snapshot_pool()}, indent=2)
                resp = {"jsonrpc": "2.0", "id": msg_id, "result": {"content": [{"type": "text", "text": payload}]}}
            elif tname == "convert_anthropic_to_openai":
                converted = anthropic_to_openai_payload(targs)
                resp = {"jsonrpc": "2.0", "id": msg_id, "result": {"content": [{"type": "text", "text": json.dumps(converted, indent=2)}]}}
            elif tname == "route_chat_completion":
                req_payload = {
                    "model": targs.get("model", "gpt-4o"),
                    "messages": targs.get("messages", []),
                    "max_tokens": targs.get("max_tokens", 1024),
                    "stream": False,
                }
                status, body, used = engine.forward_chat(req_payload, {})
                out_obj = {"http_status": status, "routed_provider": used, "response": body}
                resp = {"jsonrpc": "2.0", "id": msg_id, "result": {"content": [{"type": "text", "text": json.dumps(out_obj, indent=2)}]}}
            elif tname == "check_router_health":
                pool = engine.snapshot_pool()
                ready_cnt = sum(1 for p in pool if p["status"] == "ready")
                health_obj = {
                    "status": "ok",
                    "version": __version__,
                    "total_providers": len(pool),
                    "ready_providers": ready_cnt,
                    "providers": pool,
                }
                resp = {"jsonrpc": "2.0", "id": msg_id, "result": {"content": [{"type": "text", "text": json.dumps(health_obj, indent=2)}]}}
            else:
                resp = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Unknown tool: {tname}"}}
        else:
            resp = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Infinite LLM Auto-Fallback Router & Stdio MCP Server")
    parser.add_argument("--config", type=str, default="", help="Path to providers JSON")
    parser.add_argument("--port", type=int, default=20129, help="Listen port for HTTP/SSE proxy mode")
    parser.add_argument("--mcp", action="store_true", help="Run as a stdio JSON-RPC 2.0 MCP server")
    parser.add_argument("--self-test", action="store_true", help="Run deterministic loopback failover & bridge self-test")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    providers = DEFAULT_PROVIDERS
    if args.config and os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            providers = json.load(f)

    if args.mcp:
        return run_mcp_server(providers)

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
