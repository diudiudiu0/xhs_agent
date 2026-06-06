from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_VERSION = 1


def _resolve_project_path(path_value: str | Path | None, default_value: str) -> Path:
    raw_path = Path(str(path_value or default_value))
    if raw_path.is_absolute():
        return raw_path
    return PROJECT_ROOT / raw_path


def text_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def normalize_vector(vector: Iterable[float]) -> list[float]:
    values = [float(item) for item in vector]
    norm = math.sqrt(sum(value * value for value in values))
    if not norm:
        return values
    return [value / norm for value in values]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    if size <= 0:
        return 0.0
    return sum(left[index] * right[index] for index in range(size))


def _tokenize(text: str, token_config: dict[str, Any] | None = None) -> list[str]:
    text = str(text or "").lower()
    token_config = token_config or {}
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


class BaseEmbeddingProvider:
    provider_name = "base"

    def signature(self) -> str:
        raise NotImplementedError

    def encode_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def encode_query(self, query: str) -> list[float]:
        vectors = self.encode_texts([query])
        return vectors[0] if vectors else []


class LocalHashEmbeddingProvider(BaseEmbeddingProvider):
    provider_name = "local_hash"

    def __init__(self, embedding_config: dict[str, Any], token_config: dict[str, Any]):
        self.dimensions = int(embedding_config.get("dimensions") or 384)
        self.token_config = token_config

    def signature(self) -> str:
        return f"local_hash:{self.dimensions}:v1"

    def encode_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._encode_one(text) for text in texts]

    def _encode_one(self, text: str) -> list[float]:
        if self.dimensions <= 0:
            return []

        vector = [0.0] * self.dimensions
        for token, weight in Counter(_tokenize(text, self.token_config)).items():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign * math.log1p(weight)
        return normalize_vector(vector)


class ApiEmbeddingProvider(BaseEmbeddingProvider):
    provider_name = "api"

    def __init__(self, embedding_config: dict[str, Any]):
        from cfg import model_config as model_config_module

        config_name = str(embedding_config.get("model_config_name") or "EMBEDDING_MODEL_CONFIG")
        model_config = getattr(model_config_module, config_name, None)
        if not isinstance(model_config, dict):
            raise ValueError(f"{config_name} is missing in cfg/model_config.py")
        self.model_config = model_config
        self.model = str(model_config.get("model") or "").strip()
        api_key = str(model_config.get("api_key") or "").strip()
        env_key = str(model_config.get("env_key") or "").strip()
        self.api_key = api_key or (os.environ.get(env_key) if env_key else "")
        self.base_url = str(model_config.get("base_url") or "").strip()
        self.timeout = model_config.get("timeout", 30)
        if not self.api_key:
            raise ValueError("embedding API key is empty")
        if not self.model:
            raise ValueError("embedding model is empty")

    def signature(self) -> str:
        return f"api:{self.base_url}:{self.model}:v1"

    def encode_texts(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url or None, timeout=self.timeout)
        response = client.embeddings.create(model=self.model, input=texts)
        return [normalize_vector(item.embedding) for item in response.data]


class LocalModelEmbeddingProvider(BaseEmbeddingProvider):
    provider_name = "local_model"

    def __init__(self, embedding_config: dict[str, Any]):
        local_config = embedding_config.get("local_model") if isinstance(embedding_config.get("local_model"), dict) else {}
        self.model_name = str(local_config.get("model_name") or "BAAI/bge-small-zh-v1.5")
        self.cache_dir = str(local_config.get("cache_dir") or "")
        self.device = str(local_config.get("device") or "").strip() or None
        self.trust_remote_code = bool(local_config.get("trust_remote_code", False))
        self.query_instruction = str(local_config.get("query_instruction") or "")
        self.normalize_embeddings = bool(local_config.get("normalize_embeddings", True))
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            raise ValueError(f"sentence-transformers is not installed: {exc}") from exc
        resolved_model_name = self._resolve_model_name(self.model_name)
        resolved_cache_dir = str(_resolve_project_path(self.cache_dir, self.cache_dir)) if self.cache_dir else None
        try:
            self.model = SentenceTransformer(
                resolved_model_name,
                cache_folder=resolved_cache_dir,
                device=self.device,
                trust_remote_code=self.trust_remote_code,
            )
        except TypeError:
            self.model = SentenceTransformer(
                resolved_model_name,
                cache_folder=resolved_cache_dir,
                device=self.device,
            )

    def _resolve_model_name(self, model_name: str) -> str:
        path = Path(model_name)
        if path.is_absolute() and path.exists():
            return str(path)
        project_path = PROJECT_ROOT / path
        if project_path.exists():
            return str(project_path)
        return model_name

    def signature(self) -> str:
        return f"local_model:{self.model_name}:query_instruction={self.query_instruction}:v1"

    def encode_texts(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(texts, normalize_embeddings=self.normalize_embeddings)
        return [[float(value) for value in vector] for vector in vectors]

    def encode_query(self, query: str) -> list[float]:
        text = f"{self.query_instruction}{query}" if self.query_instruction else query
        return super().encode_query(text)


def embedding_provider_status(config: dict[str, Any]) -> dict[str, Any]:
    retrieval = config.get("retrieval") if isinstance(config.get("retrieval"), dict) else {}
    embedding_config = retrieval.get("embedding") if isinstance(retrieval.get("embedding"), dict) else {}
    if not embedding_config.get("enabled", True):
        return {"available": False, "provider": "", "reason": "embedding is disabled"}

    provider = str(embedding_config.get("provider") or "local_hash").strip().lower()
    if provider == "local_hash":
        return {"available": True, "provider": provider, "reason": "built-in local hash embedding"}
    if provider == "api":
        try:
            ApiEmbeddingProvider(embedding_config)
        except Exception as exc:
            return {"available": False, "provider": provider, "reason": str(exc)}
        return {"available": True, "provider": provider, "reason": "OpenAI-compatible embedding API"}
    if provider == "local_model":
        try:
            import sentence_transformers  # noqa: F401
        except Exception as exc:
            return {"available": False, "provider": provider, "reason": f"sentence-transformers unavailable: {exc}"}
        return {"available": True, "provider": provider, "reason": "local sentence-transformers model"}
    return {"available": False, "provider": provider, "reason": f"unsupported embedding provider: {provider}"}


def vector_index_status(config: dict[str, Any]) -> dict[str, Any]:
    retrieval = config.get("retrieval") if isinstance(config.get("retrieval"), dict) else {}
    embedding_config = retrieval.get("embedding") if isinstance(retrieval.get("embedding"), dict) else {}
    index_config = embedding_config.get("index") if isinstance(embedding_config.get("index"), dict) else {}
    backend = str(index_config.get("backend") or "json").strip().lower()
    if backend == "json":
        return {"available": True, "backend": backend, "reason": "built-in json vector index"}
    if backend == "faiss":
        try:
            import faiss  # noqa: F401
        except Exception as exc:
            return {"available": False, "backend": backend, "reason": f"faiss unavailable: {exc}"}
        try:
            import numpy  # noqa: F401
        except Exception as exc:
            return {"available": False, "backend": backend, "reason": f"numpy unavailable: {exc}"}
        return {"available": True, "backend": backend, "reason": "local FAISS vector index"}
    return {"available": False, "backend": backend, "reason": f"unsupported vector index backend: {backend}"}


def build_embedding_provider(config: dict[str, Any]) -> BaseEmbeddingProvider:
    retrieval = config.get("retrieval") if isinstance(config.get("retrieval"), dict) else {}
    embedding_config = retrieval.get("embedding") if isinstance(retrieval.get("embedding"), dict) else {}
    provider = str(embedding_config.get("provider") or "local_hash").strip().lower()
    if provider == "local_hash":
        return LocalHashEmbeddingProvider(embedding_config, config.get("tokenization") or {})
    if provider == "api":
        return ApiEmbeddingProvider(embedding_config)
    if provider == "local_model":
        return LocalModelEmbeddingProvider(embedding_config)
    raise ValueError(f"unsupported embedding provider: {provider}")


class MemoryEmbeddingIndex:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        retrieval = config.get("retrieval") if isinstance(config.get("retrieval"), dict) else {}
        embedding_config = retrieval.get("embedding") if isinstance(retrieval.get("embedding"), dict) else {}
        index_config = embedding_config.get("index") if isinstance(embedding_config.get("index"), dict) else {}
        self.embedding_config = embedding_config
        self.index_config = index_config
        self.backend = str(index_config.get("backend") or "json").strip().lower()
        self.path = _resolve_project_path(index_config.get("path"), "data/memory_embedding_index.json")
        self.batch_size = int(embedding_config.get("batch_size") or 32)
        self.remove_stale = bool(index_config.get("remove_stale", True))
        self.provider = build_embedding_provider(config)
        self.provider_signature = self.provider.signature()
        self.data = self._load() if self.backend == "json" else self._empty_data()

    def _empty_data(self) -> dict[str, Any]:
        return {
            "version": INDEX_VERSION,
            "backend": self.backend,
            "provider_signature": self.provider_signature,
            "items": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_data()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return self._empty_data()
        if not isinstance(data, dict) or data.get("provider_signature") != self.provider_signature:
            return self._empty_data()
        if not isinstance(data.get("items"), dict):
            data["items"] = {}
        return data

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.path

    def sync(self, chunks: list[Any], force: bool = False) -> dict[str, Any]:
        if self.backend == "faiss":
            return self._sync_faiss(chunks, force=force)
        return self._sync_json(chunks, force=force)

    def _sync_json(self, chunks: list[Any], force: bool = False) -> dict[str, Any]:
        items = self.data.setdefault("items", {})
        expected_ids = {str(chunk.memory_id) for chunk in chunks}
        changed = False
        if self.remove_stale:
            for memory_id in list(items):
                if memory_id not in expected_ids:
                    del items[memory_id]
                    changed = True

        pending = []
        for chunk in chunks:
            memory_id = str(chunk.memory_id)
            digest = text_hash(chunk.text_for_retrieval)
            existing = items.get(memory_id) if isinstance(items.get(memory_id), dict) else None
            if force or not existing or existing.get("text_hash") != digest:
                pending.append((chunk, digest))

        for start in range(0, len(pending), self.batch_size):
            batch = pending[start : start + self.batch_size]
            vectors = self.provider.encode_texts([chunk.text_for_retrieval for chunk, _digest in batch])
            for (chunk, digest), vector in zip(batch, vectors):
                items[str(chunk.memory_id)] = {
                    "memory_id": chunk.memory_id,
                    "source_file": chunk.source_file,
                    "source_index": chunk.source_index,
                    "memory_type": chunk.memory_type,
                    "target_agents": chunk.target_agents,
                    "site": chunk.site,
                    "task_type": chunk.task_type,
                    "text_hash": digest,
                    "vector": vector,
                }
                changed = True

        if changed:
            self.save()
        return {
            "path": str(self.path),
            "provider_signature": self.provider_signature,
            "total": len(items),
            "encoded": len(pending),
            "changed": changed,
        }

    def _store_config_for(self, memory_type: str) -> dict[str, Any]:
        stores = self.index_config.get("stores") if isinstance(self.index_config.get("stores"), dict) else {}
        store_config = stores.get(memory_type) if isinstance(stores.get(memory_type), dict) else {}
        if store_config:
            return store_config
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", memory_type or "unknown")
        return {
            "faiss_path": f"agent_memory/vector_store/{safe_name}.faiss",
            "metadata_path": f"agent_memory/vector_store/{safe_name}_metadata.json",
        }

    def _faiss_paths_for(self, memory_type: str) -> tuple[Path, Path]:
        store_config = self._store_config_for(memory_type)
        faiss_path = _resolve_project_path(store_config.get("faiss_path"), f"agent_memory/vector_store/{memory_type}.faiss")
        metadata_path = _resolve_project_path(
            store_config.get("metadata_path"),
            f"agent_memory/vector_store/{memory_type}_metadata.json",
        )
        return faiss_path, metadata_path

    def _load_faiss_metadata(self, memory_type: str) -> dict[str, Any]:
        _faiss_path, metadata_path = self._faiss_paths_for(memory_type)
        if not metadata_path.exists():
            return {}
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _faiss_metadata_is_current(self, memory_type: str, chunks: list[Any]) -> bool:
        metadata = self._load_faiss_metadata(memory_type)
        if metadata.get("provider_signature") != self.provider_signature:
            return False
        if metadata.get("version") != INDEX_VERSION:
            return False
        items = metadata.get("items")
        if not isinstance(items, list):
            return False
        expected = {str(chunk.memory_id): text_hash(chunk.text_for_retrieval) for chunk in chunks}
        actual = {
            str(item.get("memory_id")): str(item.get("text_hash"))
            for item in items
            if isinstance(item, dict) and item.get("memory_id")
        }
        return expected == actual

    def _chunk_metadata(self, chunk: Any, digest: str) -> dict[str, Any]:
        return {
            "memory_id": chunk.memory_id,
            "source_file": chunk.source_file,
            "source_index": chunk.source_index,
            "memory_type": chunk.memory_type,
            "target_agents": chunk.target_agents,
            "site": chunk.site,
            "task_type": chunk.task_type,
            "text_hash": digest,
        }

    def _sync_faiss(self, chunks: list[Any], force: bool = False) -> dict[str, Any]:
        import faiss
        import numpy as np

        grouped: dict[str, list[Any]] = {}
        for chunk in chunks:
            grouped.setdefault(str(chunk.memory_type or "unknown"), []).append(chunk)

        results = {
            "backend": "faiss",
            "provider_signature": self.provider_signature,
            "stores": {},
            "total": 0,
            "encoded": 0,
            "changed": False,
        }
        configured_types = set(grouped)
        stores = self.index_config.get("stores") if isinstance(self.index_config.get("stores"), dict) else {}
        if self.remove_stale:
            configured_types.update(str(item) for item in stores)

        for memory_type in sorted(configured_types):
            type_chunks = grouped.get(memory_type, [])
            faiss_path, metadata_path = self._faiss_paths_for(memory_type)
            should_rebuild = force or not faiss_path.exists() or not self._faiss_metadata_is_current(memory_type, type_chunks)

            if not type_chunks:
                if self.remove_stale and (faiss_path.exists() or metadata_path.exists()):
                    if faiss_path.exists():
                        faiss_path.unlink()
                    if metadata_path.exists():
                        metadata_path.unlink()
                    results["changed"] = True
                results["stores"][memory_type] = {
                    "faiss_path": str(faiss_path),
                    "metadata_path": str(metadata_path),
                    "total": 0,
                    "encoded": 0,
                    "changed": should_rebuild,
                }
                continue

            encoded_count = 0
            if should_rebuild:
                vectors: list[list[float]] = []
                for start in range(0, len(type_chunks), self.batch_size):
                    batch = type_chunks[start : start + self.batch_size]
                    vectors.extend(self.provider.encode_texts([chunk.text_for_retrieval for chunk in batch]))
                if not vectors:
                    continue

                matrix = np.asarray(vectors, dtype="float32")
                if matrix.ndim != 2 or matrix.shape[0] != len(type_chunks):
                    raise ValueError(f"invalid embedding matrix for {memory_type}: {matrix.shape}")
                index = faiss.IndexFlatIP(int(matrix.shape[1]))
                index.add(matrix)

                faiss_path.parent.mkdir(parents=True, exist_ok=True)
                metadata_path.parent.mkdir(parents=True, exist_ok=True)
                faiss.write_index(index, str(faiss_path))
                metadata = {
                    "version": INDEX_VERSION,
                    "backend": "faiss",
                    "provider_signature": self.provider_signature,
                    "memory_type": memory_type,
                    "faiss_path": str(faiss_path),
                    "items": [
                        self._chunk_metadata(chunk, text_hash(chunk.text_for_retrieval))
                        for chunk in type_chunks
                    ],
                }
                metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
                encoded_count = len(type_chunks)
                results["changed"] = True

            results["stores"][memory_type] = {
                "faiss_path": str(faiss_path),
                "metadata_path": str(metadata_path),
                "total": len(type_chunks),
                "encoded": encoded_count,
                "changed": should_rebuild,
            }
            results["total"] += len(type_chunks)
            results["encoded"] += encoded_count
        return results

    def encode_query(self, query: str) -> list[float]:
        return self.provider.encode_query(query)

    def score(
        self,
        query: str,
        chunks: list[Any],
        force_sync: bool = False,
        all_chunks: list[Any] | None = None,
    ) -> list[tuple[float, Any]]:
        if self.backend == "faiss":
            return self._score_faiss(query, chunks, all_chunks=all_chunks, force_sync=force_sync)
        return self._score_json(query, chunks, all_chunks=all_chunks, force_sync=force_sync)

    def _score_json(
        self,
        query: str,
        chunks: list[Any],
        all_chunks: list[Any] | None = None,
        force_sync: bool = False,
    ) -> list[tuple[float, Any]]:
        self.sync(all_chunks or chunks, force=force_sync)
        items = self.data.get("items") if isinstance(self.data.get("items"), dict) else {}
        query_vector = self.encode_query(query)
        scored = []
        for chunk in chunks:
            item = items.get(str(chunk.memory_id)) if isinstance(items.get(str(chunk.memory_id)), dict) else None
            vector = item.get("vector") if item else None
            if not isinstance(vector, list):
                continue
            score = cosine_similarity(query_vector, [float(value) for value in vector])
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored

    def _score_faiss(
        self,
        query: str,
        chunks: list[Any],
        all_chunks: list[Any] | None = None,
        force_sync: bool = False,
    ) -> list[tuple[float, Any]]:
        import faiss
        import numpy as np

        self.sync(all_chunks or chunks, force=force_sync)
        selected_by_id = {str(chunk.memory_id): chunk for chunk in chunks}
        selected_types = sorted({str(chunk.memory_type or "unknown") for chunk in chunks})
        query_vector = np.asarray([self.encode_query(query)], dtype="float32")
        scored: list[tuple[float, Any]] = []

        for memory_type in selected_types:
            faiss_path, metadata_path = self._faiss_paths_for(memory_type)
            if not faiss_path.exists() or not metadata_path.exists():
                continue
            metadata = self._load_faiss_metadata(memory_type)
            items = metadata.get("items") if isinstance(metadata.get("items"), list) else []
            if not items:
                continue
            index = faiss.read_index(str(faiss_path))
            top_k = min(max(len(items), 1), index.ntotal)
            scores, ids = index.search(query_vector, top_k)
            for score, item_id in zip(scores[0].tolist(), ids[0].tolist()):
                if item_id < 0 or item_id >= len(items):
                    continue
                item = items[item_id]
                memory_id = str(item.get("memory_id") or "")
                chunk = selected_by_id.get(memory_id)
                if chunk is None:
                    continue
                if score > 0:
                    scored.append((float(score), chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored


def save_memory_embedding_index(config: dict[str, Any], chunks: list[Any], force: bool = False) -> Path:
    index = MemoryEmbeddingIndex(config)
    result = index.sync(chunks, force=force)
    if index.backend == "faiss":
        stores = result.get("stores") if isinstance(result.get("stores"), dict) else {}
        for store in stores.values():
            if isinstance(store, dict) and store.get("faiss_path"):
                return Path(str(store["faiss_path"])).parent
        return PROJECT_ROOT / "agent_memory" / "vector_store"
    return index.path
