from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from skills.catalog import DEFAULT_SKILL_REGISTRY
from src.manager_agent import ManagerAgent
from src.memory_embedding import MemoryEmbeddingIndex, vector_index_status, embedding_provider_status
from src.memory_retriever import MemoryRetriever, build_memory_chunks, load_memory_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "web"


class AgentRuntime:
    def __init__(self):
        self.agent = ManagerAgent()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, name="xhs-web-agent-loop", daemon=True)
        self.lock = threading.Lock()
        self.messages: list[dict[str, Any]] = []
        self.thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run_coro(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result()

    def chat(self, message: str) -> dict[str, Any]:
        message = (message or "").strip()
        if not message:
            return {"ok": False, "error": "message is empty"}
        with self.lock:
            self.messages.append({"role": "user", "content": message})
        try:
            answer = self.run_coro(self.agent.handle_user_message(message))
        except Exception as exc:
            answer = f"Manager 执行异常：{exc}"
            with self.lock:
                self.messages.append({"role": "error", "content": answer})
            return {"ok": False, "answer": answer, "state": self.state()}

        with self.lock:
            self.messages.append({"role": "assistant", "content": answer})
        return {"ok": True, "answer": answer, "state": self.state()}

    def state(self) -> dict[str, Any]:
        state = self.agent.state.to_dict(recent_steps=20)
        with self.lock:
            messages = list(self.messages[-50:])
        state["messages"] = messages
        return state

    def close(self):
        try:
            self.run_coro(self.agent.close())
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)


RUNTIME: AgentRuntime | None = None


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200):
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _safe_static_path(path: str) -> Path:
    request_path = path.strip("/") or "index.html"
    candidate = (WEB_ROOT / request_path).resolve()
    if not str(candidate).startswith(str(WEB_ROOT.resolve())):
        return WEB_ROOT / "index.html"
    if candidate.is_dir():
        return candidate / "index.html"
    return candidate


class XhsAgentRequestHandler(BaseHTTPRequestHandler):
    server_version = "XhsAgentWeb/0.1"

    def log_message(self, format: str, *args):  # noqa: A002
        # Keep routine HTTP polling quiet. Re-enable this line when debugging
        # request routing:
        # print(f"[web] {self.address_string()} - {format % args}")
        return

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            _json_response(self, {"ok": True, "service": "xhs_agent_web"})
            return
        if parsed.path == "/api/state":
            _json_response(self, {"ok": True, "state": RUNTIME.state() if RUNTIME else {}})
            return
        if parsed.path == "/api/skills":
            _json_response(self, {"ok": True, "skills": DEFAULT_SKILL_REGISTRY.names()})
            return
        if parsed.path == "/api/memory/status":
            config = load_memory_config()
            _json_response(
                self,
                {
                    "ok": True,
                    "embedding_provider": embedding_provider_status(config),
                    "vector_index": vector_index_status(config),
                    "chunk_count": len(build_memory_chunks(config)),
                },
            )
            return

        static_path = _safe_static_path(parsed.path)
        if not static_path.exists():
            self.send_error(404)
            return
        content = static_path.read_bytes()
        content_type = mimetypes.guess_type(str(static_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        data = _read_json(self)

        if parsed.path == "/api/chat":
            result = RUNTIME.chat(str(data.get("message") or "")) if RUNTIME else {"ok": False, "error": "runtime missing"}
            _json_response(self, result, 200 if result.get("ok") else 500)
            return

        if parsed.path == "/api/memory/search":
            query = str(data.get("query") or "").strip()
            if not query:
                _json_response(self, {"ok": False, "error": "query is empty"}, 400)
                return
            memory_types = data.get("memory_types")
            if isinstance(memory_types, str):
                memory_types = [memory_types]
            if not isinstance(memory_types, list):
                memory_types = None
            retriever = MemoryRetriever()
            result = retriever.search_with_metadata(
                query=query,
                target_agent=str(data.get("target_agent") or ""),
                memory_types=[str(item) for item in memory_types] if memory_types else None,
                site=str(data.get("site") or ""),
                limit=int(data.get("limit") or 5),
                retrieval_method=str(data.get("retrieval_method") or "bm25_embedding"),
            )
            _json_response(self, {"ok": True, "result": result})
            return

        if parsed.path == "/api/vector/build":
            config = load_memory_config()
            chunks = build_memory_chunks(config)
            index = MemoryEmbeddingIndex(config)
            result = index.sync(chunks, force=bool(data.get("force")))
            _json_response(self, {"ok": True, "result": result})
            return

        if parsed.path == "/api/close":
            if RUNTIME:
                RUNTIME.close()
            _json_response(self, {"ok": True, "message": "closed"})
            return

        self.send_error(404)


def main():
    parser = argparse.ArgumentParser(description="Run XHS Agent web console.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    global RUNTIME
    RUNTIME = AgentRuntime()
    server = ThreadingHTTPServer((args.host, args.port), XhsAgentRequestHandler)
    print(f"XHS Agent Web 控制台已启动：http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("正在关闭 XHS Agent Web 控制台...")
    finally:
        server.server_close()
        if RUNTIME:
            RUNTIME.close()


if __name__ == "__main__":
    main()
