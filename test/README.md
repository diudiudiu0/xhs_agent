# Test And Manual Script Guide

This directory contains both automated checks and manual scripts. Some files open a real browser or call external model APIs, so do not run every file blindly as a single test suite.

## Shared Bootstrap

- `_bootstrap.py`
  Adds the project root to `sys.path`, so direct execution from PyCharm, VSCode, and plain Python can import `src` consistently.

## Manual Browser Utilities

- `browser_session_control.py`
  Unified manual browser entry for Xiaohongshu login and inspection. It replaces the old separate creator-login, web-login, and keep-open scripts.

  Common commands:

  ```powershell
  D:\ANACONDA\envs\xhs_agent\python.exe test\browser_session_control.py --target both --mode login
  D:\ANACONDA\envs\xhs_agent\python.exe test\browser_session_control.py --target creator --mode login
  D:\ANACONDA\envs\xhs_agent\python.exe test\browser_session_control.py --target web --mode login
  D:\ANACONDA\envs\xhs_agent\python.exe test\browser_session_control.py --target creator --mode keep-open
  ```

- `xhs_terminal_agent.py`
  Terminal interaction entry for `ManagerAgent`. Use this when you want to talk to the account-management agent in natural language.

## Browser / Account Data Tests

- `test_collect_latest_published_note_metrics.py`
  Opens the Xiaohongshu main site, enters `我 -> 笔记`, opens the top note, collects title, publish date, comments, comment count, likes, collects, and shares, then writes deduplicated data to `data/xhs_published_note_metrics.json`.

- `test_create_draft.py`
  Opens the creator center and runs the note draft workflow: choose note type, upload media, fill title/content, and save draft.

- `test_generate_images_and_create_draft.py`
  Runs the image-prompt/image-generation flow first, then uses the generated images to create a Xiaohongshu draft.

## Image Pipeline Tests

- `test_image_prompt_generation.py`
  Uses the configured vision prompt task to generate image prompts, then can chain into image generation depending on the config.

- `test_image_generation.py`
  Runs the configured image generation task and saves generated images locally.

## Local Logic / Config Checks

These checks do not need a real browser login.

- `test_web_note_metrics_collector.py`
  Verifies local JSON deduplication, publish-date normalization, and comment cleanup for collected note metrics.

- `test_skill_catalog.py`
  Verifies all required skills are registered and manager-facing skill metadata is available.

- `test_page_tool_registry.py`
  Verifies page-explorer tool schemas and action validation.

- `test_prompt_config.py`
  Verifies prompt/config sections required by the project can be loaded.

- `test_manager_config.py`
  Verifies manager configuration loading and prompt rendering.

- `test_manager_agent.py`
  Uses fake planner/executor objects to verify manager planning, skill execution, memory updates, and confirmation flow.

- `test_page_context.py`
  Verifies page-context update and summarization logic.

## Recommended Quick Check

Run these after code changes that do not require browser/API access:

```powershell
D:\ANACONDA\envs\xhs_agent\python.exe -m compileall -q src skills test
D:\ANACONDA\envs\xhs_agent\python.exe test\test_web_note_metrics_collector.py
D:\ANACONDA\envs\xhs_agent\python.exe test\test_skill_catalog.py
D:\ANACONDA\envs\xhs_agent\python.exe test\test_page_tool_registry.py
D:\ANACONDA\envs\xhs_agent\python.exe test\test_prompt_config.py
D:\ANACONDA\envs\xhs_agent\python.exe test\test_manager_config.py
D:\ANACONDA\envs\xhs_agent\python.exe test\test_manager_agent.py
```
