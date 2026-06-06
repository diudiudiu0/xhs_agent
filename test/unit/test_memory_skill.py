import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from skills.base import SkillContext
from skills.catalog import DEFAULT_SKILL_REGISTRY


async def main():
    result = await DEFAULT_SKILL_REGISTRY.run(
        "search_long_term_memory",
        args={
            "query": "查看草稿箱详情",
            "target_agent": "manager_agent",
            "memory_types": ["manager_experience", "page_path"],
            "limit": 3,
            "retrieval_method": "bm25_embedding",
        },
        context=SkillContext(),
    )
    if not result.success:
        raise AssertionError(result)
    required_keys = {
        "hits",
        "count",
        "requested_method",
        "effective_method",
        "available_methods",
        "warnings",
    }
    if not required_keys.issubset(result.data):
        raise AssertionError(result.data)
    if result.data["count"] != len(result.data["hits"]):
        raise AssertionError(result.data)
    if result.data["requested_method"] != "bm25_embedding":
        raise AssertionError(result.data)
    print("memory skill check passed")


if __name__ == "__main__":
    asyncio.run(main())
