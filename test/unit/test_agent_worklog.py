import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent_worklog import WorkExperience, XhsWorkflow


def main():
    with TemporaryDirectory() as tmp_dir:
        worklog_path = Path(tmp_dir) / "xhs_agent_worklog.json"
        worklog_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "user_request": "old transient task",
                            "action": "explore_page_task",
                            "status": "completed",
                            "steps": [],
                            "result": "old result",
                        }
                    ],
                    "memorized_requests": ["request b", "request a", "missing request"],
                    "experiences": [
                        {
                            "user_request": "request a",
                            "result": "result a",
                            "summary": "summary a",
                            "steps": [],
                        },
                        {
                            "user_request": "request b",
                            "result": "result b",
                            "summary": "summary b",
                            "steps": [],
                        },
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        workflow = XhsWorkflow(worklog_path=worklog_path)
        workflow.experiences.append(
            WorkExperience(
                user_request="request c",
                result="result c",
                summary="summary c",
                steps=[{"step": 1, "action": "generate_images", "result": "ok"}],
            )
        )
        workflow.save()

        data = json.loads(worklog_path.read_text(encoding="utf-8"))
        if set(data) != {"memorized_requests", "experiences"}:
            raise AssertionError(data.keys())

        experience_requests = [item["user_request"] for item in data["experiences"]]
        if data["memorized_requests"] != experience_requests:
            raise AssertionError((data["memorized_requests"], experience_requests))
        if experience_requests != ["request b", "request a", "request c"]:
            raise AssertionError(experience_requests)
        if "tasks" in data:
            raise AssertionError("tasks should not be persisted")

    print("agent worklog memory schema check passed")


if __name__ == "__main__":
    main()
