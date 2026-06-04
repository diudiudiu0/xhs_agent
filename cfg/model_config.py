"""
模型调用配置。

本文件只放“模型服务参数”，例如 API Key、base_url、模型名、超时、温度、图片尺寸等。
具体任务参数放在 cfg/task.yaml：
- 小红书发帖任务：note_tasks
图片生成/编辑/看图写 prompt 任务放在 cfg/image_task.yaml。

图片生成 provider 可选：
- openai：使用 OpenAI Images API。
- doubao：使用火山方舟 Seedream 图片生成 API。
"""


MODEL_CONFIG = {
    "provider": "deepseek",
    "api_key": "your_api_key",  # 为空时可自行改造为读取 DEEPSEEK_API_KEY。
    "base_url": "https://api.deepseek.com",
    "planner_model": "deepseek-v4-flash",
    "content_model": "deepseek-v4-flash",
    "timeout": 30,
    "planner_max_tokens": 1600,
    "content_max_tokens": 1800,
    "planner_temperature": 0.1,
    "content_temperature": 0.7,

    # 用于把视觉模型的自由文本结果整理成严格 JSON。
    # 这里建议低温度，保证批量图片 prompt 的结构稳定。
    "formatter_model": "deepseek-v4-flash",
    "formatter_max_tokens": 3200,
    "formatter_temperature": 0.1,
}


# Small-model config for page_context maintenance.
# Responsibility:
# - Compress page snapshots, action results, and observations into short structured page_context.
# - This is a helper model, not the main account-management brain.
# Recommended model: deepseek-v4-flash.
PAGE_CONTEXT_MODEL_CONFIG = {
    # 页面短期上下文维护模型：负责把动作日志和页面变化压缩成结构化 page_context。
    # 后续“大脑”可以改用 deepseek-v4-pro，但这里建议继续用 flash，低成本、低温、稳定输出 JSON。
    "provider": "deepseek",
    "api_key": MODEL_CONFIG["api_key"],
    "base_url": MODEL_CONFIG["base_url"],
    "model": "deepseek-v4-flash",
    "timeout": 30,
    "max_tokens": 1800,
    "temperature": 0.1,
}


# Small-model config for long-term memory review.
# Responsibility:
# - Runs in a background thread after a task succeeds.
# - Decides whether the successful user request is semantically new enough to be written into agent_memory.
# - Prevents duplicate successful paths from repeatedly polluting xhs_agent_worklog.json.
# - It does NOT execute user tasks and does NOT control the browser.
# Recommended model: deepseek-v4-flash because the task is small, structured, and should be cheap/stable.
MEMORY_REVIEW_MODEL_CONFIG = {
    "provider": "deepseek",
    "api_key": MODEL_CONFIG["api_key"],
    "base_url": MODEL_CONFIG["base_url"],
    "model": "deepseek-v4-flash",
    "timeout": 30,
    "max_tokens": 800,
    "temperature": 0.1,
}


# Main manager brain config.
# Responsibility:
# - Understand the user's account-management goal.
# - Choose which skill/specialist agent to call.
# - Decide whether to continue, ask the user, or produce the final answer.
# - It should not directly click pages or call image APIs.
# Recommended model: deepseek-v4-pro for stronger planning and multi-step reasoning.
MANAGER_MODEL_CONFIG = {
    "provider": "deepseek",
    "api_key": MODEL_CONFIG["api_key"],
    "base_url": MODEL_CONFIG["base_url"],
    "model": "deepseek-v4-pro",
    "timeout": 45,
    "max_tokens": 2200,
    "temperature": 0.2,
}


IMAGE_MODEL_CONFIG = {
    # "openai" 或 "doubao"
    "provider": "doubao",

    # OpenAI:
    # - api_key 为空时读取 OPENAI_API_KEY。
    # - base_url 通常为 https://api.openai.com/v1。
    #
    # Doubao/火山方舟:
    # - api_key 为空时优先读取 ARK_API_KEY，其次读取 VOLCENGINE_API_KEY。
    # - base_url 通常为 https://ark.cn-beijing.volces.com/api/v3。
    "api_key": "your_api_key",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",

    # OpenAI 示例：gpt-image-1
    # Doubao 示例：doubao-seedream-5-0-260128
    "image_model": "doubao-seedream-5-0-260128",

    # 默认尺寸。具体任务可在 cfg/task.yaml 的 image_generation_tasks 中覆盖。
    # OpenAI 常用：1024x1024、1024x1536、1536x1024。
    # 豆包 Seedream 可用：2K 等，具体以火山方舟模型文档为准。
    "size": "2K",
    "quality": "medium",
    "output_format": "png",
    "timeout": 120,

    # Doubao 专用可选参数。
    "response_format": "b64_json",
}


VISION_PROMPT_MODEL_CONFIG = {
    # 用于“看图写图片生成提示词”的多模态大模型。
    # Moonshot/Kimi 示例：
    # - api_key 为空时读取环境变量 MOONSHOT_API_KEY。
    # - base_url: https://api.moonshot.cn/v1
    # - model: kimi-k2.6
    "provider": "moonshot",
    "api_key": "your_api_key",
    "env_key": "MOONSHOT_API_KEY",
    "base_url": "https://api.moonshot.cn/v1",
    "model": "kimi-k2.6",
    # 批量看图写 prompt 会比普通文本慢，建议 180-300。
    "timeout": 180,
    "retry_attempts": 2,
    # kimi-k2.6 当前只允许 temperature=1。
    "temperature": 1,
    "max_tokens": 3000,
    "system_prompt": "你是一名专业的电商视觉提示词工程师，擅长根据参考图写出可用于图片生成模型的高质量中文提示词。",
}
