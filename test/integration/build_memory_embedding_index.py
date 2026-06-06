import json
import sys
import argparse
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

import _bootstrap  # noqa: F401

from src.memory_embedding import MemoryEmbeddingIndex, embedding_provider_status, vector_index_status
from src.memory_retriever import build_memory_chunks, load_memory_config


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def _print_local_model_hint():
    print("\n本地模型依赖提示：")
    print("1. 安装 sentence-transformers：")
    print(r"   D:\ANACONDA\envs\xhs_agent\python.exe -m pip install -U sentence-transformers")
    print("2. 首次运行会从 Hugging Face 下载 cfg/memory.yaml 中的 local_model.model_name。")
    print("3. 如果 Hugging Face 下载慢，可以手动下载模型到 embedding/ 后再运行。")


def _parse_args():
    parser = argparse.ArgumentParser(description="Build local FAISS vector stores for long-term memory.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--force", action="store_true", help="rebuild all vectors even if metadata is current")
    group.add_argument("--no-force", action="store_true", help="build only missing or changed vectors")
    return parser.parse_args()


def _decide_force(args) -> bool:
    if args.force:
        return True
    if args.no_force:
        return False
    if not sys.stdin.isatty():
        return False
    return _ask("是否强制重建全部向量？y/n", "n").lower() in {"y", "yes"}


def main():
    args = _parse_args()
    print("长期记忆本地向量库构建")
    config = load_memory_config()
    retrieval = config.get("retrieval") if isinstance(config.get("retrieval"), dict) else {}
    embedding_config = retrieval.get("embedding") if isinstance(retrieval.get("embedding"), dict) else {}
    local_config = embedding_config.get("local_model") if isinstance(embedding_config.get("local_model"), dict) else {}

    print("\n当前 embedding 配置：")
    print(f"- provider: {embedding_config.get('provider')}")
    print(f"- model_name: {local_config.get('model_name')}")
    print(f"- cache_dir: {local_config.get('cache_dir')}")
    print(f"- device: {local_config.get('device')}")
    print(f"- query_instruction: {local_config.get('query_instruction')}")
    index_config = embedding_config.get("index") if isinstance(embedding_config.get("index"), dict) else {}
    print(f"- index_backend: {index_config.get('backend')}")
    if index_config.get("path"):
        print(f"- legacy_json_index_path: {index_config.get('path')}")
    stores = index_config.get("stores") if isinstance(index_config.get("stores"), dict) else {}
    for name, store in stores.items():
        print(f"- store.{name}.faiss_path: {store.get('faiss_path')}")
        print(f"- store.{name}.metadata_path: {store.get('metadata_path')}")

    status = embedding_provider_status(config)
    print("\nProvider 状态：")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    index_status = vector_index_status(config)
    print("\n向量库后端状态：")
    print(json.dumps(index_status, ensure_ascii=False, indent=2))

    chunks = build_memory_chunks(config)
    print(f"\n当前 memory chunks 数量：{len(chunks)}")
    if not chunks:
        print("没有可编码的长期记忆。请先让 Agent 成功完成一些任务并写入 agent_memory。")
        return

    if not status.get("available") or not index_status.get("available"):
        _print_local_model_hint()
        print("\nFAISS 依赖提示：")
        print(r"   D:\ANACONDA\envs\xhs_agent\python.exe -m pip install -U faiss-cpu")
        return

    force = _decide_force(args)
    try:
        index = MemoryEmbeddingIndex(config)
        result = index.sync(chunks, force=force)
    except Exception as exc:
        print(f"\n构建失败：{exc}")
        _print_local_model_hint()
        return

    print("\n构建完成：")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("\n之后运行检索时，未变化的 chunk 不会重复编码。")


if __name__ == "__main__":
    main()
