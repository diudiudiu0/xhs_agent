import json
import sys
from datetime import datetime
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import _bootstrap  # noqa: F401

from src.web_note_metrics_collector import (
    _clean_comment_items,
    _normalize_published_at_text,
    _pick_published_at,
    load_account_data_config,
    save_note_metrics_if_new,
)


def main():
    config = load_account_data_config()
    output_file = Path("data/test_xhs_published_note_metrics.json")
    if output_file.exists():
        output_file.unlink()

    note = {
        "title": "测试笔记标题",
        "published_at": "2026-06-05 10:00",
        "comment_count": 1,
        "comments": [{"author": "测试用户", "content": "测试评论"}],
        "collect_count": 2,
        "like_count": 3,
        "share_count": 4,
        "source_url": "https://www.xiaohongshu.com/explore/test",
        "collected_at": "2026-06-05T10:01:00+08:00",
    }

    first = save_note_metrics_if_new(note, output_file=output_file)
    second = save_note_metrics_if_new(dict(note), output_file=output_file)

    if not first["added"] or first["duplicate"]:
        raise AssertionError(first)
    if second["added"] or not second["duplicate"]:
        raise AssertionError(second)

    data = json.loads(output_file.read_text(encoding="utf-8"))
    if len(data.get("notes", [])) != 1:
        raise AssertionError(data)

    legacy_output_file = Path("data/test_xhs_legacy_time_metrics.json")
    if legacy_output_file.exists():
        legacy_output_file.unlink()
    legacy_note = dict(note)
    legacy_note["published_at"] = "昨天 10:00 湖北"
    legacy_note["collected_at"] = "2026-06-06T12:00:00+08:00"
    legacy_first = save_note_metrics_if_new(legacy_note, output_file=legacy_output_file)
    legacy_second = save_note_metrics_if_new(note, output_file=legacy_output_file)
    if not legacy_first["added"] or legacy_second["added"] or not legacy_second["duplicate"]:
        raise AssertionError((legacy_first, legacy_second))

    polluted_comments = [
        {"raw": "喜仔 好样的 昨天 15:33湖北 赞 1", "isReply": False},
        {"raw": "小红薯5D587855 作者 谢谢喜仔！这篇脚轮指南能帮到你太好了，有问题随时交流~ 昨天 16:39湖北 赞 回复", "isReply": True},
        {"raw": "共 2 条评论 喜仔 好样的 昨天 15:33湖北 赞 1 小红薯5D587855 作者 谢谢喜仔！这篇脚轮指南能帮到你太好了，有问题随时交流~ 昨天 16:39湖北 赞 回复 - THE END -", "isReply": False},
        {"raw": "喜仔 好样的 昨天 15:33湖北 赞 1 小红薯5D587855 作者 谢谢喜仔！这篇脚轮指南能帮到你太好了，有问题随时交流~ 昨天 16:39湖北 赞 回复", "isReply": False},
    ]
    comments = _clean_comment_items(polluted_comments, config)
    if comments != [
        {
            "author": "喜仔",
            "content": "好样的",
        }
    ]:
        raise AssertionError(comments)

    normalized_time = _normalize_published_at_text(
        "昨天 15:16 湖北",
        now=datetime(2026, 6, 5, 12, 0),
    )
    if normalized_time != "2026-06-04":
        raise AssertionError(normalized_time)

    if _normalize_published_at_text("6-4", now=datetime(2026, 6, 5, 12, 0)) != "2026-06-04":
        raise AssertionError("month-day date normalization failed")
    if _normalize_published_at_text("2026-06-04 15:16", now=datetime(2026, 6, 5, 12, 0)) != "2026-06-04":
        raise AssertionError("full date-time normalization failed")

    published_at = _pick_published_at(
        {
            "publishTimeCandidates": ["1/3", "发布于 昨天 16:39 湖北"],
            "bodyText": "1/3\n脚轮正文\n发布于 昨天 16:39 湖北",
        },
        config["extraction"]["published_at_patterns"],
        now=datetime(2026, 6, 5, 12, 0),
    )
    if published_at != "2026-06-04":
        raise AssertionError(published_at)

    output_file.unlink()
    legacy_output_file.unlink()
    print("web note metrics storage check passed")


if __name__ == "__main__":
    main()
