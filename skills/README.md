# XHS Skills

这个目录是未来“账号管理大脑”调用能力的标准技能层。

它不替代 `src` 中已经验证过的底层实现，而是把那些实现包装成统一协议：

- `SkillSpec`：给管理大脑看的技能说明，包括输入、输出、风险等级、副作用和示例。具体自然语言内容来自 `cfg/skills.yaml`。
- `SkillResult`：每个技能统一返回的结构，包含 success、message、data、artifacts、observations、error、next_suggestions。
- `SkillContext`：跨技能共享会话状态，例如浏览器页面、当前生成的图片、当前文案。
- `SkillRegistry`：技能注册表，未来新增账号数据分析、评论回复、选题规划时继续注册即可。

维护原则：

- Python 文件只放执行逻辑、参数整理和结果封装。
- 技能描述、示例、输入输出说明、风险等级、副作用、结果提示模板统一放在 `cfg/skills.yaml`。
- 新增技能时，先在 `cfg/skills.yaml` 添加同名配置，再在本目录创建对应适配器类并注册到 `catalog.py`。

当前已封装的技能：

- `generate_image_prompts`
- `revise_image_prompts`
- `generate_images`
- `plan_note_text`
- `create_note_draft`
- `collect_note_metrics`
- `collect_latest_published_note_metrics`
- `analyze_account_performance`
- `plan_content_topics`
- `reply_comments`
- `schedule_content_calendar`
- `review_risky_action`
- `open_creator_page`
- `get_page_state`
- `observe_page_report`
- `explore_page_task`
- `handle_dialogs`
- `search_long_term_memory`
- `show_session_memory`
- `close_session`

`search_long_term_memory` 支持通过 `retrieval_method` 选择 `bm25`、`embedding`、`bm25_embedding`、`bm25_embedding_rerank`。实际可用状态、回退信息和命中结果会一起返回给调用方。也可以通过 `save_embedding_index` 或 `rebuild_embedding_index` 生成 `agent_memory/vector_store/` 下的本地 FAISS 向量库。
