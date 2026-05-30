"""
模型调用配置。

本文件只放“模型服务参数”，例如 API Key、base_url、模型名、超时、温度、图片尺寸等。
具体任务参数放在 cfg/task.yaml：
- 小红书发帖任务：note_tasks
- 图片生成/编辑任务：image_generation_tasks

常见文本模型示例（具体名称以服务商控制台为准）：
- DeepSeek: deepseek-v4-pro, deepseek-v4-flash
- OpenAI: gpt-4.1, gpt-4.1-mini, gpt-4o, gpt-4o-mini
- 通义千问 OpenAI 兼容接口: qwen-plus, qwen-max, qwen-turbo
- Moonshot/Kimi OpenAI 兼容接口: moonshot-v1-8k, moonshot-v1-32k, moonshot-v1-128k
- 智谱 OpenAI 兼容接口: glm-4-plus, glm-4-air

常见图片模型建议：
- 稳定优先：OpenAI gpt-image-1，适合商品图、封面图、参考图编辑。
- 成本优先：使用服务商提供的轻量/flash 图片模型，适合批量出草图后人工筛选。
- 第三方 OpenAI-compatible 图片接口：通常只需要改 api_key、base_url、image_model。
"""


MODEL_CONFIG = {
    "provider": "deepseek",
    "api_key": "your api key here",  # 为空时会尝试读取环境变量 DEEPSEEK_API_KEY。                  
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
    "provider": "openai",
    "api_key": "",  # 为空时会尝试读取环境变量 OPENAI_API_KEY。
    "base_url": "https://api.openai.com/v1",
    "image_model": "gpt-image-1",
    "size": "1024x1024",
    "quality": "medium",
    "output_format": "png",
    "timeout": 120,
}
