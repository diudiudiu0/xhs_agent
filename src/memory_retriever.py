from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.task_config_loader import _safe_load_yaml_file
from src.memory_embedding import MemoryEmbeddingIndex, embedding_provider_status, vector_index_status


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEMORY_CONFIG_PATH = PROJECT_ROOT / "cfg" / "memory.yaml"

SUPPORTED_RETRIEVAL_METHODS = {
    "bm25",
    "embedding",
    "bm25_embedding",
    "bm25_embedding_rerank",
}


@dataclass
class MemoryChunk:
    memory_id: str
    source_file: str
    source_index: int
    memory_type: str
    target_agents: list[str]
    user_request: str
    result: str = ""
    summary: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    site: str = "unknown"
    task_type: str = "unknown"
    created_at: str = ""
    text_for_retrieval: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_memory_config() -> dict[str, Any]:
    if not MEMORY_CONFIG_PATH.exists():
        return {}
    data = _safe_load_yaml_file(MEMORY_CONFIG_PATH)
    config = data.get("memory") or {}
    return config if isinstance(config, dict) else {}


def _resolve_project_path(path_value: str | Path | None, default_value: str) -> Path:
    raw_path = Path(str(path_value or default_value))
    if raw_path.is_absolute():
        return raw_path
    return PROJECT_ROOT / raw_path


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _compact_text(value: Any, limit: int = 1200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _step_text(step: dict[str, Any], max_chars: int) -> str:
    return _compact_text(
        " ".join(
            str(step.get(key) or "")
            for key in ("action", "element_text", "result", "observation", "reason", "page_url")
        ),
        max_chars,
    )


def _format_steps(raw_steps: list[Any], max_steps: int, max_step_chars: int) -> list[dict[str, Any]]:
    out = []
    for index, raw_step in enumerate(raw_steps[:max_steps], start=1):
        if not isinstance(raw_step, dict):
            continue
        out.append(
            {
                "step": raw_step.get("step") or index,
                "action": _compact_text(raw_step.get("action"), max_step_chars),
                "element_text": _compact_text(raw_step.get("element_text"), max_step_chars),
                "result": _compact_text(raw_step.get("result"), max_step_chars),
                "observation": _compact_text(raw_step.get("observation"), max_step_chars),
                "page_url": _compact_text(raw_step.get("page_url"), 300),
            }
        )
    return out


def _infer_site(text: str) -> str:
    lowered = text.lower()
    if "creator.xiaohongshu.com" in lowered or "创作中心" in text or "草稿" in text:
        return "creator"
    if "www.xiaohongshu.com" in lowered or "评论" in text or "回复" in text:
        return "web"
    return "unknown"


def _infer_task_type(text: str, memory_type: str) -> str:
    if any(term in text for term in ("删除", "移除", "清空")):
        return "delete"
    if any(term in text for term in ("评论", "回复")):
        return "comment"
    if any(term in text for term in ("草稿", "暂存", "保存")):
        return "draft"
    if any(term in text for term in ("图片", "提示词", "生成图")):
        return "image"
    if any(term in text for term in ("数据", "指标", "分析")):
        return "analytics"
    return memory_type


def _target_agents_for(memory_type: str, config: dict[str, Any]) -> list[str]:
    target_agents = ((config.get("chunking") or {}).get("target_agents") or {}).get(memory_type) or []
    return [str(item) for item in target_agents if str(item).strip()]


def _build_text_for_retrieval(chunk: MemoryChunk) -> str:
    step_text = " ".join(_step_text(step, 240) for step in chunk.steps)
    return _compact_text(
        " ".join(
            [
                chunk.user_request,
                chunk.result,
                chunk.summary,
                chunk.site,
                chunk.task_type,
                step_text,
            ]
        ),
        6000,
    )


def build_worklog_chunks(config: dict[str, Any] | None = None) -> list[MemoryChunk]:
    config = config or load_memory_config()
    files = config.get("files") or {}
    chunking = config.get("chunking") or {}
    path = _resolve_project_path(files.get("worklog"), "agent_memory/xhs_agent_worklog.json")
    data = _read_json(path)
    experiences = data.get("experiences") if isinstance(data.get("experiences"), list) else []
    chunks: list[MemoryChunk] = []
    for index, item in enumerate(experiences):
        if not isinstance(item, dict):
            continue
        user_request = _compact_text(item.get("user_request"), 500)
        if not user_request:
            continue
        steps = _format_steps(
            item.get("steps") if isinstance(item.get("steps"), list) else [],
            int(chunking.get("max_steps") or 12),
            int(chunking.get("max_step_chars") or 320),
        )
        result = _compact_text(item.get("result"), int(chunking.get("max_result_chars") or 900))
        summary = _compact_text(item.get("summary"), int(chunking.get("max_summary_chars") or 1800))
        combined = " ".join([user_request, result, summary, " ".join(_step_text(step, 240) for step in steps)])
        chunk = MemoryChunk(
            memory_id=f"worklog_{index + 1:04d}",
            source_file=_display_path(path),
            source_index=index,
            memory_type="manager_experience",
            target_agents=_target_agents_for("manager_experience", config) or ["manager_agent"],
            user_request=user_request,
            result=result,
            summary=summary,
            steps=steps,
            site=_infer_site(combined),
            task_type=_infer_task_type(combined, "manager_experience"),
            created_at=_compact_text(item.get("created_at"), 60),
        )
        chunk.text_for_retrieval = _build_text_for_retrieval(chunk)
        chunks.append(chunk)
    return chunks


def build_exploration_chunks(config: dict[str, Any] | None = None) -> list[MemoryChunk]:
    config = config or load_memory_config()
    files = config.get("files") or {}
    chunking = config.get("chunking") or {}
    path = _resolve_project_path(files.get("exploration"), "agent_memory/xhs_exploration_memory.json")
    data = _read_json(path)
    records = data.get("records") if isinstance(data.get("records"), list) else []
    chunks: list[MemoryChunk] = []
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            continue
        user_request = _compact_text(item.get("task"), 500)
        if not user_request:
            continue
        steps = _format_steps(
            item.get("path") if isinstance(item.get("path"), list) else [],
            int(chunking.get("max_steps") or 12),
            int(chunking.get("max_step_chars") or 320),
        )
        result = _compact_text(item.get("result"), int(chunking.get("max_result_chars") or 900))
        summary = "\n".join(
            f"{step.get('step')}. {step.get('observation') or step.get('result')}"
            for step in steps
            if step.get("observation") or step.get("result")
        )
        summary = _compact_text(summary, int(chunking.get("max_summary_chars") or 1800))
        combined = " ".join([user_request, result, summary, " ".join(_step_text(step, 240) for step in steps)])
        chunk = MemoryChunk(
            memory_id=f"explore_{index + 1:04d}",
            source_file=_display_path(path),
            source_index=index,
            memory_type="page_path",
            target_agents=_target_agents_for("page_path", config) or ["page_explorer_agent", "manager_agent"],
            user_request=user_request,
            result=result,
            summary=summary,
            steps=steps,
            site=_infer_site(combined),
            task_type=_infer_task_type(combined, "page_path"),
            created_at=_compact_text(item.get("created_at"), 60),
        )
        chunk.text_for_retrieval = _build_text_for_retrieval(chunk)
        chunks.append(chunk)
    return chunks


def build_memory_chunks(config: dict[str, Any] | None = None) -> list[MemoryChunk]:
    config = config or load_memory_config()
    return build_worklog_chunks(config) + build_exploration_chunks(config)


def save_memory_chunks(output_file: str | Path | None = None, config: dict[str, Any] | None = None) -> Path:
    config = config or load_memory_config()
    files = config.get("files") or {}
    path = _resolve_project_path(output_file or files.get("chunks_output"), "agent_memory/memory_chunks.json")
    chunks = [chunk.to_dict() for chunk in build_memory_chunks(config)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"chunks": chunks}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class MemoryRetriever:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or load_memory_config()
        self._embedding_index: MemoryEmbeddingIndex | None = None
        self._chunks_cache: list[MemoryChunk] | None = None

    def _tokenize(self, text: str) -> list[str]:
        text = str(text or "").lower()
        token_config = self.config.get("tokenization") or {}
        domain_terms = [str(item).lower() for item in token_config.get("domain_terms") or []]
        stop_chars = set(str(token_config.get("stop_chars") or ""))

        tokens = re.findall(r"[a-z0-9]+", text)
        tokens.extend(term for term in domain_terms if term and term in text)

        for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
            cleaned = "".join(char for char in chunk if char not in stop_chars)
            if len(cleaned) == 1:
                tokens.append(cleaned)
            else:
                tokens.extend(cleaned[index : index + 2] for index in range(len(cleaned) - 1))
        return [token for token in tokens if token]

    def _filtered_chunks(
        self,
        target_agent: str = "",
        memory_types: list[str] | None = None,
        site: str = "",
    ) -> list[MemoryChunk]:
        memory_type_set = {str(item) for item in memory_types or [] if str(item).strip()}
        chunks = []
        for chunk in self._all_chunks():
            if target_agent and target_agent not in chunk.target_agents:
                continue
            if memory_type_set and chunk.memory_type not in memory_type_set:
                continue
            if site and chunk.site not in {site, "unknown"}:
                continue
            chunks.append(chunk)
        return chunks

    def _all_chunks(self) -> list[MemoryChunk]:
        if self._chunks_cache is None:
            self._chunks_cache = build_memory_chunks(self.config)
        return self._chunks_cache

    def _bm25_scores(self, query: str, chunks: list[MemoryChunk]) -> list[tuple[float, MemoryChunk]]:
        query_tokens = self._tokenize(query)
        if not query_tokens or not chunks:
            return []

        doc_tokens = [self._tokenize(chunk.text_for_retrieval) for chunk in chunks]
        doc_counters = [Counter(tokens) for tokens in doc_tokens]
        doc_lengths = [len(tokens) or 1 for tokens in doc_tokens]
        avg_doc_length = sum(doc_lengths) / max(1, len(doc_lengths))

        df = Counter()
        for counter in doc_counters:
            for token in set(counter):
                df[token] += 1

        retrieval = self.config.get("retrieval") or {}
        k1 = float(retrieval.get("bm25_k1") or 1.5)
        b = float(retrieval.get("bm25_b") or 0.75)
        total_docs = len(chunks)
        query_counter = Counter(query_tokens)
        scored = []
        normalized_query = re.sub(r"\s+", "", query.lower())

        for chunk, counter, doc_length in zip(chunks, doc_counters, doc_lengths):
            score = 0.0
            for token, query_weight in query_counter.items():
                tf = counter.get(token, 0)
                if not tf:
                    continue
                idf = math.log(1 + (total_docs - df[token] + 0.5) / (df[token] + 0.5))
                denominator = tf + k1 * (1 - b + b * doc_length / avg_doc_length)
                score += query_weight * idf * (tf * (k1 + 1)) / denominator

            request_text = chunk.user_request.lower()
            full_text = chunk.text_for_retrieval.lower()
            if normalized_query and normalized_query in re.sub(r"\s+", "", request_text):
                score += 5.0
            elif normalized_query and normalized_query in re.sub(r"\s+", "", full_text):
                score += 1.5

            request_overlap = set(query_tokens) & set(self._tokenize(chunk.user_request))
            score += len(request_overlap) * 0.35

            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored

    def _get_embedding_index(self) -> MemoryEmbeddingIndex:
        if self._embedding_index is None:
            self._embedding_index = MemoryEmbeddingIndex(self.config)
        return self._embedding_index

    def _embedding_scores(self, query: str, chunks: list[MemoryChunk]) -> list[tuple[float, MemoryChunk]]:
        if not chunks:
            return []
        return self._get_embedding_index().score(query, chunks, all_chunks=self._all_chunks())

    def _normalize_score_map(self, scored: list[tuple[float, MemoryChunk]]) -> dict[str, float]:
        if not scored:
            return {}
        max_score = max(score for score, _chunk in scored) or 1.0
        return {chunk.memory_id: score / max_score for score, chunk in scored}

    def _fused_scores(self, query: str, chunks: list[MemoryChunk]) -> list[tuple[float, MemoryChunk]]:
        retrieval = self.config.get("retrieval") or {}
        hybrid_config = retrieval.get("hybrid") if isinstance(retrieval.get("hybrid"), dict) else {}
        bm25_weight = float(hybrid_config.get("bm25_weight") or 0.62)
        embedding_weight = float(hybrid_config.get("embedding_weight") or 0.38)

        bm25_scores = self._bm25_scores(query, chunks)
        embedding_scores = self._embedding_scores(query, chunks)
        bm25_map = self._normalize_score_map(bm25_scores)
        embedding_map = self._normalize_score_map(embedding_scores)
        chunk_by_id = {chunk.memory_id: chunk for chunk in chunks}

        scored = []
        for memory_id in set(bm25_map) | set(embedding_map):
            score = bm25_weight * bm25_map.get(memory_id, 0.0) + embedding_weight * embedding_map.get(memory_id, 0.0)
            if score > 0:
                scored.append((score, chunk_by_id[memory_id]))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored

    def _retrieval_settings(self) -> dict[str, Any]:
        retrieval = self.config.get("retrieval") or {}
        return retrieval if isinstance(retrieval, dict) else {}

    def _normalize_method(self, method: str | None) -> str:
        retrieval = self._retrieval_settings()
        selected = str(method or retrieval.get("default_method") or "bm25").strip().lower()
        aliases = {
            "hybrid": "bm25_embedding",
            "bm25+embedding": "bm25_embedding",
            "bm25_embedding_hybrid": "bm25_embedding",
            "rerank": "bm25_embedding_rerank",
            "bm25+embedding+rerank": "bm25_embedding_rerank",
            "pure_bm25": "bm25",
            "pure_embedding": "embedding",
        }
        selected = aliases.get(selected, selected)
        return selected if selected in SUPPORTED_RETRIEVAL_METHODS else "bm25"

    def available_methods(self) -> dict[str, dict[str, Any]]:
        retrieval = self._retrieval_settings()
        rerank_config = retrieval.get("rerank") if isinstance(retrieval.get("rerank"), dict) else {}
        embedding_status = embedding_provider_status(self.config)
        index_status = vector_index_status(self.config)
        embedding_available = bool(embedding_status.get("available")) and bool(index_status.get("available"))
        rerank_available = embedding_available and bool(rerank_config.get("enabled", False))
        return {
            "bm25": {"available": True, "reason": "lexical scorer is built in"},
            "embedding": {
                "available": embedding_available,
                "reason": (
                    f"{embedding_status.get('reason') or embedding_status.get('provider') or ''}; "
                    f"{index_status.get('reason') or index_status.get('backend') or ''}"
                ),
            },
            "bm25_embedding": {
                "available": embedding_available,
                "reason": "requires embedding scorer",
            },
            "bm25_embedding_rerank": {
                "available": rerank_available,
                "reason": "requires embedding scorer and enabled rerank model",
            },
        }

    def _resolve_method(self, requested_method: str, allow_fallback: bool) -> tuple[str, list[str]]:
        method = self._normalize_method(requested_method)
        status = self.available_methods().get(method) or {}
        if status.get("available"):
            return method, []
        if method == "bm25_embedding_rerank" and self.available_methods().get("bm25_embedding", {}).get("available"):
            return method, []

        warning = f"retrieval method '{method}' is not available: {status.get('reason') or 'unknown reason'}"
        if not allow_fallback:
            return method, [warning]

        fallback = self._normalize_method(self._retrieval_settings().get("fallback_method") or "bm25")
        fallback_status = self.available_methods().get(fallback) or {}
        if fallback_status.get("available"):
            return fallback, [f"{warning}; fallback to '{fallback}'."]
        return "bm25", [f"{warning}; fallback to 'bm25'."]

    def _rerank_scores(
        self,
        query: str,
        scored: list[tuple[float, MemoryChunk]],
    ) -> tuple[list[tuple[float, MemoryChunk]], list[str]]:
        retrieval = self._retrieval_settings()
        rerank_config = retrieval.get("rerank") if isinstance(retrieval.get("rerank"), dict) else {}
        if not rerank_config.get("enabled"):
            return scored, ["small-model rerank is disabled; used bm25_embedding order."]

        try:
            from openai import OpenAI
            from cfg import model_config as model_config_module
        except Exception as exc:
            return scored, [f"small-model rerank dependencies unavailable: {exc}; used bm25_embedding order."]

        model_config_name = str(rerank_config.get("model_config_name") or "MEMORY_REVIEW_MODEL_CONFIG")
        model_config = getattr(model_config_module, model_config_name, None)
        if not isinstance(model_config, dict):
            return scored, [f"{model_config_name} is missing; used bm25_embedding order."]

        candidate_k = int(rerank_config.get("candidate_k") or 12)
        candidates = []
        for score, chunk in scored[:candidate_k]:
            candidates.append(
                {
                    "memory_id": chunk.memory_id,
                    "score": round(score, 4),
                    "user_request": chunk.user_request,
                    "result": chunk.result,
                    "summary": chunk.summary,
                }
            )
        prompt_template = str(rerank_config.get("prompt_template") or "")
        prompt = prompt_template.format(
            query=query,
            candidates_json=json.dumps(candidates, ensure_ascii=False, indent=2),
        )

        try:
            client = OpenAI(
                api_key=model_config.get("api_key"),
                base_url=model_config.get("base_url"),
                timeout=model_config.get("timeout", 30),
            )
            response = client.chat.completions.create(
                model=model_config.get("model"),
                messages=[{"role": "user", "content": prompt}],
                temperature=model_config.get("temperature", 0.1),
                max_tokens=model_config.get("max_tokens", 800),
            )
            content = response.choices[0].message.content or ""
            match = re.search(r"\[[\s\S]*\]", content)
            parsed = json.loads(match.group(0) if match else content)
            order = [
                str(item.get("memory_id"))
                for item in parsed
                if isinstance(item, dict) and item.get("memory_id")
            ]
        except Exception as exc:
            return scored, [f"small-model rerank failed: {exc}; used bm25_embedding order."]

        if not order:
            return scored, ["small-model rerank returned no usable memory_id; used bm25_embedding order."]

        rank_map = {memory_id: index for index, memory_id in enumerate(order)}
        original_rank = {chunk.memory_id: index for index, (_score, chunk) in enumerate(scored)}
        reranked = sorted(
            scored,
            key=lambda item: (
                rank_map.get(item[1].memory_id, len(rank_map) + original_rank.get(item[1].memory_id, 9999)),
                -item[0],
            ),
        )
        return reranked, []

    def _results_from_scores(
        self,
        query: str,
        scored: list[tuple[float, MemoryChunk]],
        limit: int,
        min_score: float,
        method: str,
    ) -> list[dict[str, Any]]:
        results = []
        for score, chunk in scored:
            if score < min_score:
                continue
            reuse_level = "context_reference_only"
            if chunk.user_request and set(self._tokenize(query)) & set(self._tokenize(chunk.user_request)):
                reuse_level = "same_goal_candidate" if score >= 4 and method == "bm25" else "related_memory"
            results.append(
                {
                    "memory_id": chunk.memory_id,
                    "memory_type": chunk.memory_type,
                    "target_agents": chunk.target_agents,
                    "source_file": chunk.source_file,
                    "source_index": chunk.source_index,
                    "match_score": round(score, 4),
                    "retrieval_method": method,
                    "reuse_level": reuse_level,
                    "site": chunk.site,
                    "task_type": chunk.task_type,
                    "user_request": chunk.user_request,
                    "result": chunk.result,
                    "summary": chunk.summary,
                    "steps": chunk.steps,
                    "created_at": chunk.created_at,
                }
            )
            if len(results) >= limit:
                break
        return results

    def search_with_metadata(
        self,
        query: str,
        target_agent: str = "",
        memory_types: list[str] | None = None,
        site: str = "",
        limit: int | None = None,
        retrieval_method: str | None = None,
        allow_fallback: bool | None = None,
    ) -> dict[str, Any]:
        retrieval = self._retrieval_settings()
        limit = int(limit or retrieval.get("default_top_k") or 4)
        min_score = float(retrieval.get("min_score") or 0.0)
        fallback_allowed = bool(retrieval.get("allow_fallback", True) if allow_fallback is None else allow_fallback)
        requested_method = self._normalize_method(retrieval_method)
        effective_method, warnings = self._resolve_method(requested_method, fallback_allowed)
        available_methods = self.available_methods()

        if not available_methods.get(requested_method, {}).get("available") and not fallback_allowed:
            return {
                "query": query,
                "requested_method": requested_method,
                "effective_method": "",
                "available_methods": available_methods,
                "warnings": warnings,
                "hits": [],
                "count": 0,
            }

        chunks = self._filtered_chunks(target_agent=target_agent, memory_types=memory_types, site=site)
        if effective_method == "embedding":
            scored = self._embedding_scores(query, chunks)
        elif effective_method == "bm25_embedding":
            scored = self._fused_scores(query, chunks)
        elif effective_method == "bm25_embedding_rerank":
            scored = self._fused_scores(query, chunks)
            scored, rerank_warnings = self._rerank_scores(query, scored)
            warnings.extend(rerank_warnings)
            if rerank_warnings:
                effective_method = "bm25_embedding"
        else:
            scored = self._bm25_scores(query, chunks)

        hits = self._results_from_scores(query, scored, limit, min_score, effective_method)
        return {
            "query": query,
            "requested_method": requested_method,
            "effective_method": effective_method,
            "available_methods": available_methods,
            "warnings": warnings,
            "hits": hits,
            "count": len(hits),
        }

    def search(
        self,
        query: str,
        target_agent: str = "",
        memory_types: list[str] | None = None,
        site: str = "",
        limit: int | None = None,
        retrieval_method: str | None = None,
        allow_fallback: bool | None = None,
    ) -> list[dict[str, Any]]:
        result = self.search_with_metadata(
            query=query,
            target_agent=target_agent,
            memory_types=memory_types,
            site=site,
            limit=limit,
            retrieval_method=retrieval_method,
            allow_fallback=allow_fallback,
        )
        return result["hits"]
