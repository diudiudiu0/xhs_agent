import json
import sys
from pathlib import Path
from textwrap import shorten


TEST_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

import _bootstrap  # noqa: F401

from src.memory_embedding import MemoryEmbeddingIndex
from src.memory_retriever import MemoryRetriever, build_memory_chunks, load_memory_config


METHODS = [
    "bm25",
    "embedding",
    "bm25_embedding",
    "bm25_embedding_rerank",
]

TARGET_AGENTS = [
    "",
    "manager_agent",
    "page_explorer_agent",
]

MEMORY_TYPE_PRESETS = {
    "0": None,
    "1": ["manager_experience"],
    "2": ["page_path"],
    "3": ["manager_experience", "page_path"],
}

SITES = ["", "creator", "web", "unknown"]


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def _choose_from_list(title: str, options: list[str], default_index: int = 0) -> str:
    print(f"\n{title}")
    for index, option in enumerate(options, start=1):
        label = option or "不过滤"
        marker = "  <- 默认" if index - 1 == default_index else ""
        print(f"  {index}. {label}{marker}")
    raw = _ask("请输入编号", str(default_index + 1))
    try:
        selected = int(raw) - 1
    except ValueError:
        selected = default_index
    if selected < 0 or selected >= len(options):
        selected = default_index
    return options[selected]


def _choose_memory_types() -> list[str] | None:
    print("\n记忆类型")
    print("  0. 不过滤")
    print("  1. manager_experience")
    print("  2. page_path")
    print("  3. manager_experience + page_path  <- 默认")
    raw = _ask("请输入编号", "3")
    return MEMORY_TYPE_PRESETS.get(raw, MEMORY_TYPE_PRESETS["3"])


def _ask_limit(default: int = 5) -> int:
    raw = _ask("返回条数", str(default))
    try:
        limit = int(raw)
    except ValueError:
        return default
    return max(1, min(limit, 20))


def _sync_embedding_index(config: dict, force: bool = False):
    chunks = build_memory_chunks(config)
    index = MemoryEmbeddingIndex(config)
    result = index.sync(chunks, force=force)
    print("\n向量库状态：")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _print_available_methods(result: dict):
    print("\n检索策略状态：")
    print(f"- requested_method: {result.get('requested_method')}")
    print(f"- effective_method: {result.get('effective_method')}")
    warnings = result.get("warnings") or []
    if warnings:
        print("- warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    available_methods = result.get("available_methods") or {}
    for method, status in available_methods.items():
        print(f"- {method}: available={status.get('available')} reason={status.get('reason')}")


def _step_preview(steps: list[dict], limit: int = 3) -> str:
    parts = []
    for step in steps[:limit]:
        action = step.get("action") or ""
        element_text = step.get("element_text") or ""
        observation = step.get("observation") or step.get("result") or ""
        parts.append(shorten(" | ".join(str(item) for item in (action, element_text, observation) if item), width=160))
    return "\n      ".join(parts)


def _print_hits(result: dict):
    hits = result.get("hits") or []
    print(f"\n命中数量：{len(hits)}")
    if not hits:
        print("没有命中。可以尝试换成 bm25_embedding、放宽 target_agent，或先确认 agent_memory 中已有成功记忆。")
        return

    for index, hit in enumerate(hits, start=1):
        print("\n" + "=" * 80)
        print(f"[{index}] {hit.get('user_request')}")
        print(
            "type={memory_type} site={site} task={task_type} score={score} reuse={reuse}".format(
                memory_type=hit.get("memory_type"),
                site=hit.get("site"),
                task_type=hit.get("task_type"),
                score=hit.get("match_score"),
                reuse=hit.get("reuse_level"),
            )
        )
        print(f"source={hit.get('source_file')}#{hit.get('source_index')}")
        result_text = shorten(str(hit.get("result") or ""), width=300)
        summary = shorten(str(hit.get("summary") or ""), width=500)
        if result_text:
            print(f"result: {result_text}")
        if summary:
            print(f"summary: {summary}")
        steps = hit.get("steps") if isinstance(hit.get("steps"), list) else []
        if steps:
            print("steps:")
            print(f"      {_step_preview(steps)}")


def main():
    print("长期记忆检索交互测试")
    print("输入 q / quit / exit 可退出。")
    print("提示：embedding 和 bm25_embedding 会自动同步 agent_memory/vector_store/ 下的 FAISS 向量库，未变化的 chunk 不会重复编码。")

    config = load_memory_config()
    retriever = MemoryRetriever(config=config)

    if _ask("是否先同步向量库？y/n", "n").lower() in {"y", "yes"}:
        force = _ask("是否强制重建全部向量？y/n", "n").lower() in {"y", "yes"}
        _sync_embedding_index(config, force=force)

    while True:
        method = _choose_from_list("检索算法", METHODS, default_index=2)
        query = _ask("\n请输入检索内容")
        if query.lower() in {"q", "quit", "exit"}:
            print("已退出。")
            return
        if not query:
            print("检索内容不能为空。")
            continue

        target_agent = _choose_from_list("目标 agent", TARGET_AGENTS, default_index=1)
        memory_types = _choose_memory_types()
        site = _choose_from_list("站点过滤", SITES, default_index=0)
        limit = _ask_limit(5)
        allow_fallback = _ask("所选策略不可用时是否回退？y/n", "y").lower() not in {"n", "no"}

        result = retriever.search_with_metadata(
            query=query,
            target_agent=target_agent,
            memory_types=memory_types,
            site=site,
            limit=limit,
            retrieval_method=method,
            allow_fallback=allow_fallback,
        )
        _print_available_methods(result)
        _print_hits(result)

        if _ask("\n是否继续检索？y/n", "y").lower() in {"n", "no"}:
            print("已退出。")
            return


if __name__ == "__main__":
    main()
