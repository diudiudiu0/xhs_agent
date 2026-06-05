# Test Directory Guide

`test/` is split into two groups:

- `unit/`: local, fast checks. They do not open a browser and do not call external model APIs.
- `integration/`: browser/API/manual flows. They may open Playwright, require login state, upload files, or call model/image APIs.

`_bootstrap.py` stays at the test root and is imported by scripts in both groups so direct execution works in PyCharm, VSCode, and plain Python.

## Unit Tests

- `unit/test_web_note_metrics_collector.py`
  Verifies JSON deduplication, publish-date normalization, and comment cleanup for collected note metrics.

- `unit/test_account_management_service.py`
  Verifies local account analysis, content topic planning, content calendar scheduling, and risky-action review.

- `unit/test_account_management_skills.py`
  Verifies account-management skills can be called through `SkillRegistry` and return unified `SkillResult` objects.

- `unit/test_skill_catalog.py`
  Verifies required skills are registered and manager-facing metadata is present.

- `unit/test_page_tool_registry.py`
  Verifies page-explorer tool schemas and action validation.

- `unit/test_prompt_config.py`
  Verifies prompt/config sections required by the project can be loaded.

- `unit/test_manager_config.py`
  Verifies manager configuration loading and prompt rendering.

- `unit/test_manager_agent.py`
  Uses fake planner/executor objects to verify manager planning, skill execution, memory updates, and confirmation flow.

- `unit/test_page_context.py`
  Verifies page-context update and summarization logic.

## Integration And Manual Scripts

- `integration/browser_session_control.py`
  Unified manual browser entry for Xiaohongshu login and inspection.

  Common commands:

  ```powershell
  D:\ANACONDA\envs\xhs_agent\python.exe test\integration\browser_session_control.py --target both --mode login
  D:\ANACONDA\envs\xhs_agent\python.exe test\integration\browser_session_control.py --target creator --mode login
  D:\ANACONDA\envs\xhs_agent\python.exe test\integration\browser_session_control.py --target web --mode login
  D:\ANACONDA\envs\xhs_agent\python.exe test\integration\browser_session_control.py --target creator --mode keep-open
  ```

- `integration/xhs_terminal_agent.py`
  Terminal interaction entry for `ManagerAgent`.

- `integration/test_collect_latest_published_note_metrics.py`
  Opens the Xiaohongshu main site, enters `我 -> 笔记`, opens the top note, collects note metrics, and writes deduplicated data to `data/xhs_published_note_metrics.json`.

- `integration/test_create_draft.py`
  Opens the creator center and runs the note draft workflow.

- `integration/test_generate_images_and_create_draft.py`
  Runs the image-prompt/image-generation flow first, then uses generated images to create a Xiaohongshu draft.

- `integration/test_image_prompt_generation.py`
  Uses the configured vision prompt task to generate image prompts and can chain into image generation.

- `integration/test_image_generation.py`
  Runs the configured image generation task and saves generated images locally.

## Recommended Unit Check

Run these after code changes that do not require browser/API access:

```powershell
D:\ANACONDA\envs\xhs_agent\python.exe -m compileall -q src skills test
D:\ANACONDA\envs\xhs_agent\python.exe test\unit\test_web_note_metrics_collector.py
D:\ANACONDA\envs\xhs_agent\python.exe test\unit\test_account_management_service.py
D:\ANACONDA\envs\xhs_agent\python.exe test\unit\test_account_management_skills.py
D:\ANACONDA\envs\xhs_agent\python.exe test\unit\test_skill_catalog.py
D:\ANACONDA\envs\xhs_agent\python.exe test\unit\test_page_tool_registry.py
D:\ANACONDA\envs\xhs_agent\python.exe test\unit\test_prompt_config.py
D:\ANACONDA\envs\xhs_agent\python.exe test\unit\test_manager_config.py
D:\ANACONDA\envs\xhs_agent\python.exe test\unit\test_manager_agent.py
D:\ANACONDA\envs\xhs_agent\python.exe test\unit\test_page_context.py
```
