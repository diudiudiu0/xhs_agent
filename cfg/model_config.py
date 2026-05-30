"""
模型调用配置。

本文件只放“模型服务参数”，例如 API Key、base_url、模型名、超时、温度、图片尺寸等。
具体任务参数放在 cfg/task.yaml：
- 小红书发帖任务：note_tasks
- 图片生成/编辑任务：image_generation_tasks

图片生成 provider 可选：
- openai：使用 OpenAI Images API。
- doubao：使用火山方舟 Seedream 图片生成 API。
"""


MODEL_CONFIG = {
    "provider": "deepseek",
    "api_key": "your api key here",  # 为空时可自行改造为读取 DEEPSEEK_API_KEY。
    "base_url": "https://api.deepseek.com",
    "planner_model": "deepseek-v4-flash",
    "content_model": "deepseek-v4-flash",
    "timeout": 30,
    "planner_max_tokens": 800,
    "content_max_tokens": 1800,
    "planner_temperature": 0.1,
    "content_temperature": 0.7,
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
    "api_key": "your api key here",
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
