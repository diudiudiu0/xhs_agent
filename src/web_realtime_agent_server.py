from __future__ import annotations

import argparse
import asyncio
import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from skills.catalog import DEFAULT_SKILL_REGISTRY
from src.manager_agent import ManagerAgent
from src.memory_embedding import MemoryEmbeddingIndex, embedding_provider_status, vector_index_status
from src.memory_retriever import MemoryRetriever, build_memory_chunks, load_memory_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "web"


class ChatRequest(BaseModel):
    message: str


class MemorySearchRequest(BaseModel):
    query: str
    retrieval_method: str = "bm25_embedding"
    target_agent: str = "manager_agent"
    memory_types: list[str] | None = None
    site: str = ""
    limit: int = 5


class VectorBuildRequest(BaseModel):
    force: bool = False


@dataclass
class AgentJob:
    task_id: str
    message: str
    created_at: float = field(default_factory=time.time)


class EventBroker:
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.history: list[dict[str, Any]] = []
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self.lock:
            self.clients.add(websocket)
            history = list(self.history[-80:])
        for event in history:
            await websocket.send_json(event)

    async def disconnect(self, websocket: WebSocket):
        async with self.lock:
            self.clients.discard(websocket)

    async def publish(self, event_type: str, data: dict[str, Any] | None = None):
        event = {
            "type": event_type,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "data": data or {},
        }
        async with self.lock:
            self.history.append(event)
            self.history = self.history[-120:]
            clients = list(self.clients)
        for websocket in clients:
            try:
                await websocket.send_json(event)
            except Exception:
                await self.disconnect(websocket)


class RealtimeAgentRuntime:
    def __init__(self, broker: EventBroker):
        self.broker = broker
        self.main_loop: asyncio.AbstractEventLoop | None = None
        self.job_queue: queue.Queue[AgentJob] = queue.Queue()
        self.lock = threading.RLock()
        self.messages: list[dict[str, Any]] = []
        self.current_task: dict[str, Any] | None = None
        self.cancel_requested = False
        self.agent: ManagerAgent | None = None
        self.agent_loop: asyncio.AbstractEventLoop | None = None
        self.worker_thread = threading.Thread(target=self._worker_main, name="xhs-agent-web-worker", daemon=True)
        self.monitor_thread = threading.Thread(target=self._monitor_main, name="xhs-agent-web-monitor", daemon=True)
        self.worker_thread.start()
        self.monitor_thread.start()

    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        self.main_loop = loop

    def publish(self, event_type: str, data: dict[str, Any] | None = None):
        if self.main_loop and self.main_loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broker.publish(event_type, data), self.main_loop)

    def enqueue(self, message: str) -> dict[str, Any]:
        message = (message or "").strip()
        if not message:
            return {"ok": False, "error": "message is empty"}
        job = AgentJob(task_id=str(uuid.uuid4()), message=message)
        with self.lock:
            self.messages.append({"role": "user", "content": message, "task_id": job.task_id})
        self.job_queue.put(job)
        self.publish("task_queued", {"task_id": job.task_id, "message": message, "queue_size": self.job_queue.qsize()})
        return {"ok": True, "task_id": job.task_id, "queue_size": self.job_queue.qsize()}

    def request_cancel(self) -> dict[str, Any]:
        with self.lock:
            if not self.current_task:
                return {"ok": True, "message": "no running task"}
            self.cancel_requested = True
            task_id = self.current_task.get("task_id")
        self.publish("cancel_requested", {"task_id": task_id, "message": "已请求停止，当前阻塞调用结束后生效。"})
        return {"ok": True, "task_id": task_id, "message": "cancel requested"}

    def state(self) -> dict[str, Any]:
        agent_state = self.agent.state.to_dict(recent_steps=30) if self.agent else {}
        with self.lock:
            messages = list(self.messages[-80:])
            current_task = dict(self.current_task or {})
            cancel_requested = self.cancel_requested
            queue_size = self.job_queue.qsize()
        agent_state.update(
            {
                "messages": messages,
                "current_task": current_task,
                "cancel_requested": cancel_requested,
                "queue_size": queue_size,
            }
        )
        return agent_state

    def _worker_main(self):
        self.agent_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.agent_loop)
        self.agent = ManagerAgent()
        self.publish("runtime_ready", {"message": "ManagerAgent ready"})
        while True:
            job = self.job_queue.get()
            try:
                self.agent_loop.run_until_complete(self._run_job(job))
            finally:
                self.job_queue.task_done()

    async def _run_job(self, job: AgentJob):
        with self.lock:
            self.current_task = {"task_id": job.task_id, "message": job.message, "started_at": time.time()}
            self.cancel_requested = False
        self.publish("task_started", {"task_id": job.task_id, "message": job.message})

        if self.cancel_requested:
            self.publish("task_cancelled", {"task_id": job.task_id})
            return

        try:
            answer = await self.agent.handle_user_message(job.message)
            with self.lock:
                if self.cancel_requested:
                    self.messages.append({"role": "assistant", "content": "已收到停止请求；当前任务已结束。", "task_id": job.task_id})
                else:
                    self.messages.append({"role": "assistant", "content": answer, "task_id": job.task_id})
            self.publish("task_completed", {"task_id": job.task_id, "answer": answer, "state": self.state()})
        except Exception as exc:
            message = f"Manager 执行异常：{exc}"
            with self.lock:
                self.messages.append({"role": "error", "content": message, "task_id": job.task_id})
            self.publish("task_failed", {"task_id": job.task_id, "error": message, "state": self.state()})
        finally:
            with self.lock:
                self.current_task = None
                self.cancel_requested = False
            self.publish("state", self.state())

    def _monitor_main(self):
        last_payload = ""
        while True:
            time.sleep(0.8)
            with self.lock:
                running = bool(self.current_task)
            if not running:
                continue
            payload = json.dumps(self.state(), ensure_ascii=False, sort_keys=True, default=str)
            if payload != last_payload:
                last_payload = payload
                self.publish("state", self.state())

    def close(self):
        if self.agent and self.agent_loop:
            future = asyncio.run_coroutine_threadsafe(self.agent.close(), self.agent_loop)
            try:
                future.result(timeout=10)
            except Exception:
                pass


broker = EventBroker()
runtime = RealtimeAgentRuntime(broker)
app = FastAPI(title="XHS Agent Web Console")


@app.on_event("startup")
async def startup():
    runtime.bind_loop(asyncio.get_running_loop())
    await broker.publish("service_started", {"message": "XHS Agent realtime web service started"})


@app.on_event("shutdown")
async def shutdown():
    runtime.close()


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "xhs_agent_realtime_web"}


@app.get("/api/state")
async def state():
    return {"ok": True, "state": runtime.state()}


@app.get("/api/skills")
async def skills():
    return {"ok": True, "skills": DEFAULT_SKILL_REGISTRY.names()}


@app.post("/api/tasks")
async def create_task(request: ChatRequest):
    result = runtime.enqueue(request.message)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.post("/api/cancel")
async def cancel_task():
    return runtime.request_cancel()


@app.get("/api/memory/status")
async def memory_status():
    config = load_memory_config()
    return {
        "ok": True,
        "embedding_provider": embedding_provider_status(config),
        "vector_index": vector_index_status(config),
        "chunk_count": len(build_memory_chunks(config)),
    }


@app.post("/api/memory/search")
async def memory_search(request: MemorySearchRequest):
    retriever = MemoryRetriever()
    result = retriever.search_with_metadata(
        query=request.query,
        target_agent=request.target_agent,
        memory_types=request.memory_types,
        site=request.site,
        limit=request.limit,
        retrieval_method=request.retrieval_method,
    )
    return {"ok": True, "result": result}


@app.post("/api/vector/build")
async def vector_build(request: VectorBuildRequest):
    config = load_memory_config()
    chunks = build_memory_chunks(config)
    index = MemoryEmbeddingIndex(config)
    result = await asyncio.to_thread(index.sync, chunks, request.force)
    await broker.publish("vector_built", result)
    return {"ok": True, "result": result}


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    await broker.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await broker.disconnect(websocket)


app.mount("/", StaticFiles(directory=str(WEB_ROOT), html=True), name="web")


def main():
    parser = argparse.ArgumentParser(description="Run realtime XHS Agent web console.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
