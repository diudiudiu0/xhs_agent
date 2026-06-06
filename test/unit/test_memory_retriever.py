import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.memory_retriever import MemoryRetriever, build_memory_chunks, save_memory_chunks


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _test_config(root: Path) -> dict:
    return {
        "files": {
            "worklog": str(root / "xhs_agent_worklog.json"),
            "exploration": str(root / "xhs_exploration_memory.json"),
            "chunks_output": str(root / "memory_chunks.json"),
        },
        "retrieval": {
            "default_method": "bm25",
            "fallback_method": "bm25",
            "allow_fallback": True,
            "default_top_k": 4,
            "min_score": 0.01,
            "bm25_k1": 1.5,
            "bm25_b": 0.75,
            "embedding": {
                "enabled": True,
                "provider": "local_hash",
                "dimensions": 128,
                "batch_size": 8,
                "index": {
                    "path": str(root / "memory_embedding_index.json"),
                    "auto_sync": True,
                    "remove_stale": True,
                },
            },
            "hybrid": {
                "bm25_weight": 0.62,
                "embedding_weight": 0.38,
            },
            "rerank": {
                "enabled": False,
                "candidate_k": 6,
                "prompt_template": "{query}\n{candidates_json}",
            },
        },
        "chunking": {
            "max_result_chars": 900,
            "max_summary_chars": 1800,
            "max_step_chars": 320,
            "max_steps": 12,
            "target_agents": {
                "manager_experience": ["manager_agent"],
                "page_path": ["page_explorer_agent", "manager_agent"],
            },
        },
        "tokenization": {
            "domain_terms": ["草稿", "草稿箱", "图文", "笔记", "删除", "评论", "回复"],
            "stop_chars": "的了和是我你他她它在有把给为与及中上下",
        },
    }


def main():
    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        config = _test_config(root)

        _write_json(
            Path(config["files"]["worklog"]),
            {
                "memorized_requests": ["查看草稿箱详情"],
                "experiences": [
                    {
                        "user_request": "查看草稿箱详情",
                        "result": "草稿箱有2篇图文笔记",
                        "summary": "调用 explore_page_task 打开草稿箱并返回数量和标题。",
                        "steps": [
                            {
                                "step": 1,
                                "action": "explore_page_task",
                                "result": "打开草稿箱",
                                "observation": "看到了图文笔记列表",
                            }
                        ],
                        "created_at": "2026-06-06T10:00:00",
                    }
                ],
            },
        )
        _write_json(
            Path(config["files"]["exploration"]),
            {
                "task_requests": ["打开草稿箱页面"],
                "records": [
                    {
                        "task": "打开草稿箱页面",
                        "result": "成功进入草稿箱列表",
                        "path": [
                            {
                                "action": "{\"action\":\"click\"}",
                                "element_text": "草稿箱(2)",
                                "result": "页面展开草稿箱",
                                "observation": "出现图文笔记(2)",
                                "page_url": "https://creator.xiaohongshu.com/publish/publish",
                            }
                        ],
                        "created_at": "2026-06-06T10:01:00",
                    }
                ],
            },
        )

        chunks = build_memory_chunks(config)
        if len(chunks) != 2:
            raise AssertionError(chunks)

        retriever = MemoryRetriever(config=config)
        bm25_result = retriever.search_with_metadata(
            "查看草稿箱详情",
            target_agent="manager_agent",
            limit=4,
            retrieval_method="bm25",
        )
        if bm25_result["effective_method"] != "bm25" or len(bm25_result["hits"]) < 2:
            raise AssertionError(bm25_result)

        embedding_result = retriever.search_with_metadata(
            "进入草稿列表",
            target_agent="page_explorer_agent",
            memory_types=["page_path"],
            limit=4,
            retrieval_method="embedding",
        )
        if embedding_result["effective_method"] != "embedding" or not embedding_result["hits"]:
            raise AssertionError(embedding_result)
        index_path = Path(config["retrieval"]["embedding"]["index"]["path"])
        if not index_path.exists():
            raise AssertionError(f"embedding index was not created: {index_path}")
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
        if len(index_data.get("items", {})) != 2:
            raise AssertionError(index_data)

        hybrid_result = retriever.search_with_metadata(
            "打开草稿箱页面",
            target_agent="page_explorer_agent",
            memory_types=["page_path"],
            retrieval_method="bm25_embedding",
        )
        if hybrid_result["effective_method"] != "bm25_embedding" or not hybrid_result["hits"]:
            raise AssertionError(hybrid_result)

        rerank_result = retriever.search_with_metadata(
            "查看草稿箱详情",
            target_agent="manager_agent",
            retrieval_method="bm25_embedding_rerank",
        )
        if rerank_result["effective_method"] != "bm25_embedding" or not rerank_result["warnings"]:
            raise AssertionError(rerank_result)

        saved_path = save_memory_chunks(config=config)
        saved = json.loads(saved_path.read_text(encoding="utf-8"))
        if len(saved.get("chunks", [])) != 2:
            raise AssertionError(saved)

    print("memory retriever check passed")


if __name__ == "__main__":
    main()
