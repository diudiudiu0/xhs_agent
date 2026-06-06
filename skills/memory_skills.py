from __future__ import annotations

from typing import Any

from skills.base import BaseSkill, SkillContext, SkillResult
from skills.config import build_skill_spec, skill_message
from src.memory_embedding import save_memory_embedding_index
from src.memory_retriever import MemoryRetriever, build_memory_chunks, load_memory_config, save_memory_chunks


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


class SearchLongTermMemorySkill(BaseSkill):
    spec = build_skill_spec("search_long_term_memory")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        query = str(args.get("query") or args.get("user_goal") or "").strip()
        if not query:
            return SkillResult.fail(self.name, skill_message(self.name, "missing_query"), risk_level=self.spec.risk_level)

        memory_types = args.get("memory_types")
        if isinstance(memory_types, str):
            memory_types = [memory_types]
        elif not isinstance(memory_types, list):
            memory_types = None

        limit = args.get("limit") or args.get("top_k")
        try:
            limit = int(limit) if limit is not None else None
        except (TypeError, ValueError):
            limit = None

        retrieval_method = str(args.get("retrieval_method") or args.get("method") or "").strip() or None
        allow_fallback = _as_bool(args.get("allow_fallback"), True)

        retriever = MemoryRetriever()
        search_result = retriever.search_with_metadata(
            query=query,
            target_agent=str(args.get("target_agent") or ""),
            memory_types=[str(item) for item in memory_types] if memory_types else None,
            site=str(args.get("site") or ""),
            limit=limit,
            retrieval_method=retrieval_method,
            allow_fallback=allow_fallback,
        )
        hits = search_result["hits"]

        artifacts = []
        if args.get("save_chunks"):
            artifacts.append(str(save_memory_chunks()))
        if args.get("save_embedding_index") or args.get("rebuild_embedding_index"):
            config = load_memory_config()
            chunks = build_memory_chunks(config)
            artifacts.append(
                str(
                    save_memory_embedding_index(
                        config=config,
                        chunks=chunks,
                        force=_as_bool(args.get("rebuild_embedding_index"), False),
                    )
                )
            )

        observations = [
            "method="
            f"{search_result.get('effective_method') or 'unavailable'} "
            f"requested={search_result.get('requested_method')}"
        ]
        observations.extend(str(item) for item in search_result.get("warnings") or [])
        observations.extend(
            f"{index}. [{hit.get('memory_type')}] score={hit.get('match_score')} {hit.get('user_request')}"
            for index, hit in enumerate(hits, start=1)
        )
        message = skill_message(self.name, "success", count=len(hits))
        return SkillResult.ok(
            self.name,
            message=message,
            data=search_result,
            artifacts=artifacts,
            observations=observations,
            memory_updates={
                "last_memory_search": {
                    "query": query,
                    "requested_method": search_result.get("requested_method"),
                    "effective_method": search_result.get("effective_method"),
                    "count": len(hits),
                    "hits": hits[:5],
                }
            },
        )


MEMORY_SKILLS = [
    SearchLongTermMemorySkill(),
]
