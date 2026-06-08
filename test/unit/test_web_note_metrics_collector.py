import json
import sys
from datetime import datetime
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

import _bootstrap  # noqa: F401

from src.web_note_metrics_collector import (
    _clean_comment_items,
    _normalize_published_at_text,
    _pick_published_at,
    load_account_data_config,
    save_note_metrics_if_new,
    save_note_metrics_snapshot,
)


TODAY_CN = "\u4eca\u5929"
YESTERDAY_CN = "\u6628\u5929"
HUBEI_CN = "\u6e56\u5317"
PUBLISHED_CN = "\u53d1\u5e03\u4e8e"


def main():
    config = load_account_data_config()
    output_file = Path("data/test_xhs_published_note_metrics.json")
    if output_file.exists():
        output_file.unlink()

    note = {
        "title": "test note title",
        "content": "test note body",
        "published_at": "2026-06-05",
        "comment_count": 1,
        "comments": [{"author": "test_user", "content": "test comment"}],
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
    legacy_note["published_at"] = f"{YESTERDAY_CN} 10:00 {HUBEI_CN}"
    legacy_note["collected_at"] = "2026-06-06T12:00:00+08:00"
    legacy_first = save_note_metrics_if_new(legacy_note, output_file=legacy_output_file)
    legacy_second = save_note_metrics_if_new(note, output_file=legacy_output_file)
    if not legacy_first["added"] or legacy_second["added"] or not legacy_second["duplicate"]:
        raise AssertionError((legacy_first, legacy_second))

    snapshot_output_file = Path("data/test_xhs_snapshot_metrics.json")
    if snapshot_output_file.exists():
        snapshot_output_file.unlink()
    second_note = dict(note)
    second_note["title"] = "second test note"
    first_snapshot = save_note_metrics_snapshot([note, second_note], output_file=snapshot_output_file)
    if first_snapshot["note_count"] != 2 or not first_snapshot.get("overwritten"):
        raise AssertionError(first_snapshot)
    updated_note = dict(note)
    updated_note["like_count"] = 99
    updated_note["content"] = "updated note body"
    second_snapshot = save_note_metrics_snapshot([updated_note], output_file=snapshot_output_file)
    snapshot_data = json.loads(snapshot_output_file.read_text(encoding="utf-8"))
    snapshot_notes = snapshot_data.get("notes", [])
    if second_snapshot["note_count"] != 1 or len(snapshot_notes) != 1:
        raise AssertionError((second_snapshot, snapshot_data))
    if snapshot_notes[0].get("like_count") != 99 or snapshot_notes[0].get("content") != "updated note body":
        raise AssertionError(snapshot_notes)

    polluted_comments = [
        {"raw": "user_a nice", "isReply": False},
        {"raw": "author_account thanks", "isReply": True},
        {"raw": "2 comments user_a nice - THE END -", "isReply": False},
        {"raw": "user_a nice", "isReply": False},
    ]
    comments = _clean_comment_items(polluted_comments, config)
    if comments != [{"author": "user_a", "content": "nice"}]:
        raise AssertionError(comments)

    normalized_time = _normalize_published_at_text(
        f"{YESTERDAY_CN} 15:16 {HUBEI_CN}",
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
            "publishTimeCandidates": ["1/3", f"{PUBLISHED_CN} {YESTERDAY_CN} 16:39 {HUBEI_CN}"],
            "bodyText": f"1/3\nnote body\n{PUBLISHED_CN} {YESTERDAY_CN} 16:39 {HUBEI_CN}",
        },
        config["extraction"]["published_at_patterns"],
        now=datetime(2026, 6, 5, 12, 0),
    )
    if published_at != "2026-06-04":
        raise AssertionError(published_at)

    output_file.unlink()
    legacy_output_file.unlink()
    snapshot_output_file.unlink()
    print("web note metrics storage check passed")


if __name__ == "__main__":
    main()
