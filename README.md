# xhs_agent

一个基于 Playwright + DeepSeek 的小红书创作中心自动化 Agent，用于登录复用、进入创作中心、创建图文/视频笔记草稿、填写标题与正文，并按配置自动上传图片或视频素材。项目也提供独立的图片生成 Agent，用于提前生成本地图片素材。

## 当前能力

- 复用项目内 `.browser_profile/xhs_creator` 持久浏览器目录进入小红书创作中心，同时保留 `auth.json` 兼容旧流程。
- 自动提取页面上的按钮、输入框、富文本编辑区等可交互元素。
- 使用阶段状态、最近动作历史、页面上下文和 DeepSeek 共同规划下一步，避免重复点击同一个入口。
- 采集浏览器运行状态，包括页面是否关闭、DOM 是否加载中、网络是否忙、页面是否响应、网页弹窗/浮层、文件上传 input 线索，以及动作后页面是否发生可见响应。
- 支持按 `post_type` 创建图文或视频草稿；图文从本地图片文件夹取素材，视频从本地视频文件夹取素材。
- 素材上传步骤由 Playwright 直接写入 file input，不依赖模型识别系统弹窗。
- 支持在发布前用 DeepSeek 将简短要点扩写成约 1000 字的小红书商品推荐正文。
- 支持用独立图片生成 Agent 根据提示词生成图片，或基于本地图片进行修改。
- 异常时自动关闭浏览器，避免 Playwright 子进程残留。

## 环境准备

建议使用 Python 3.11。

```bash
pip install playwright openai
playwright install chromium
```

文本规划和正文扩写配置写在 `cfg/model_config.py` 的 `MODEL_CONFIG` 中。若要切换 DeepSeek、OpenAI 或其他 OpenAI-compatible 接口，修改 `api_key`、`base_url`、`planner_model` 和 `content_model`。

图片生成/编辑配置写在 `cfg/model_config.py` 的 `IMAGE_MODEL_CONFIG` 中。当前支持：

- `provider="openai"`：使用 OpenAI Images API，例如 `gpt-image-1`。
- `provider="doubao"`：使用火山方舟 Seedream 图片生成 API，例如 `doubao-seedream-5-0-260128`。
- 稳定优先：OpenAI `gpt-image-1` 或火山方舟 Seedream 新版本，适合商品图、海报图、参考图编辑。
- 成本优先：使用服务商提供的轻量/flash 图片模型，适合批量生成草图后人工筛选。
- 国内或第三方兼容接口：优先选择明确支持 image generation / image edit 的模型，并修改 `base_url`、`api_key`、`image_model`。

## 任务配置

发帖任务参数写在 `cfg/task.yaml` 中。图片生成、看图写 prompt、批量出图参数写在 `cfg/image_task.yaml` 中。之后要切换任务，可以新增一个任务配置，并把对应的 `active_*_task` 改成任务名。

必填项：

- `input.post_type`：只能是 `image` 或 `video`。
- `input.topic`：发帖主题。
- `input.title`：笔记标题。
- `input.seed_content`：正文原始要点。

素材规则：

- `post_type=image` 时优先使用 `note_tasks.<任务名>.input.image_folder`，不可用时使用 `doc/pic_exam.png`。
- `post_type=video` 时优先使用 `note_tasks.<任务名>.input.video_folder`，不可用时使用 `doc/vid_exam.mp4`。
- 如果对应文件夹和默认素材都不可用，程序会在打开浏览器前停止并提示。

## 首次登录

首次使用前先运行登录脚本：

```bash
python test/login_init.py
```

浏览器打开后，请手动完成扫码或账号登录。登录成功后程序会把登录状态保存到项目根目录的 `auth.json`，同时写入 `.browser_profile/xhs_creator` 持久浏览器目录。

小红书创作中心提示“草稿存储于当前使用的浏览器本地”。因此本项目默认使用 Playwright persistent context 复用同一个浏览器数据目录，而不是每次只用 `auth.json` 创建全新的临时 context。这样更适合保存和继续观察本地草稿。

## 创建草稿测试

运行测试：

```bash
python test/test_create_draft.py
```

发布类型、标题、主题、正文要点、图片/视频素材目录都在 `cfg/task.yaml` 的 `note_tasks` 中配置。

支持的图片格式：`.jpg`、`.jpeg`、`.png`、`.webp`、`.bmp`、`.gif`。
支持的视频格式：`.mp4`、`.mov`、`.avi`、`.mkv`、`.webm`。

## 终端交互 Agent

如果希望通过终端与小红书个人账号 Agent 对话，运行：

```bash
python test/xhs_terminal_agent.py
```

终端 Agent 会维护当前会话记忆，已封装的技能包括：

- 根据本地图片或 URL 生成图片提示词。
- 根据用户反馈继续修改当前提示词。
- 使用当前提示词生成图片。
- 根据图片提示词生成标题和正文。
- 打开创作中心、获取页面状态、处理网页弹窗。
- 使用当前生成的图片和文案创建小红书图文草稿。

示例输入：

```text
根据 doc/pic_exam.png 这张图片，帮我生成 3 条脚轮商品海报提示词
第二张提示词加上 M6 参数信息，第一张不要纯白背景
满意了，生成图片
写文案
用这些图片创建草稿
页面状态
```

## 生成本地图片素材

图片生成任务参数统一写在 `cfg/image_task.yaml` 的 `image_generation_tasks` 中；模型服务参数仍然写在 `cfg/model_config.py` 的 `IMAGE_MODEL_CONFIG` 中。

- `IMAGE_MODEL_CONFIG`：图片模型、API Key、base_url、默认尺寸、默认质量。
- `image_generation_tasks`：本次测试的 prompt、参考图路径、输出目录、生成数量。

纯提示词生成图片时，把 `image_generation_tasks.<任务名>.input_image` 留空。

基于本地图片修改时，把 `image_generation_tasks.<任务名>.input_image` 填成本地图片路径，例如 `doc/pic_exam.png`。

如果参考图来自本机，设置 `input_image_source: local`；如果参考图来自网络图片地址，设置 `input_image_source: url`。

图片尺寸和比例在 `cfg/image_task.yaml` 的图片任务里设置：

```yaml
size: 2K
aspect_ratio: 1:1
watermark: false
```

常用比例可以写 `1:1`、`3:4`、`4:3`、`9:16`、`16:9`。豆包 Seedream 也支持你示例里的 `size: 2K`。如果某个模型不支持独立的 `aspect_ratio` 参数，可以把比例要求直接写进 `prompt`，例如“竖版 3:4 小红书封面”。

运行测试：

```bash
python test/test_image_generation.py
```

输出文件会自动按 `1.png`、`2.png`、`3.png` 顺序保存到指定目录。生成好的目录可以直接填入 `cfg/task.yaml` 的 `note_tasks.<任务名>.input.image_folder`，用于后续图文笔记发布。

如果希望把图片生成和创建小红书图文草稿串起来，运行：

```bash
python test/test_generate_images_and_create_draft.py
```

这个流程会先执行 `cfg/image_task.yaml` 中的图片生成流水线，再把本次生成出的图片路径按数字文件名顺序传给发帖 Agent，随后进入创作中心上传图片、填写标题、扩写/填写正文并暂存离开。这里不会从输出目录随机抽图。

组合流程中的标题和正文会根据图片生成提示词重新规划：

- 如果 `cfg/task.yaml` 的 `note_tasks.<任务名>.input.title` 为空，程序会根据图片提示词生成标题和正文。
- 如果标题非空，程序会保留标题，并结合标题和图片提示词生成正文。
- 正文模板要求不使用疑问句，不出现中文或英文问号。

## 看图生成图片提示词

如果想先让视觉大模型读取现有图片，再自动写出图片生成提示词，然后把这个提示词交给图片生成模型出图，运行：

```bash
python test/test_image_prompt_generation.py
```

这个流程由三个配置区域组合完成：

- `image_prompt_tasks`：看图生成图片提示词，要求 `input_image` 非空。
- `image_generation_tasks`：拿最终 prompt 出图，负责尺寸、比例、输出目录等。
- `image_prompt_pipeline_tasks`：声明使用哪一个 prompt 任务和哪一个出图任务。

`input_image_source: local` 时读取本机图片，`input_image_source: url` 时读取网络图片地址。视觉模型配置在 `cfg/model_config.py` 的 `VISION_PROMPT_MODEL_CONFIG` 中，默认按 Moonshot/Kimi 的 OpenAI-compatible 接口配置。

如果希望智能式批量生成图片，把 `image_generation_tasks.<任务名>.count` 改成目标数量，例如 `3`。在 `test/test_image_prompt_generation.py` 这条组合流程里，程序不会简单地用同一个 prompt 生成 3 张相似图，而是会把数量传给视觉模型，让它先根据 `image_prompt_tasks.<任务名>.batch_prompt_plan` 写出 3 条不同用途的 prompt，例如封面图、产品主体介绍图、使用场景图，再由 DeepSeek 格式化为稳定 JSON，最后逐条 prompt 单独生成图片。视觉模型提示词、重试提示词和格式化提示词都在 `cfg/image_task.yaml` 中维护。

## 主要文件

- `test/login_init.py`：手动登录，保存 `auth.json`，并初始化 `.browser_profile/xhs_creator` 持久浏览器目录。
- `src/browser_actions.py`：启动浏览器并进入创作中心。
- `src/element_extractor.py`：提取当前页面可交互元素。
- `src/browser_skills.py`：点击、填写、等待、上传图片/视频等浏览器动作。
- `src/browser_state_observer.py`：采集页面加载、响应、弹窗、文件 input 和动作后页面变化状态。
- `src/llm_planner.py`：调用模型根据页面元素规划下一步动作，并扩写正文。
- `src/agent_note_publisher.py`：创建小红书草稿的主流程。
- `src/xhs_agent_skills.py`：终端 Agent 的技能封装层，统一调用提示词生成、提示词修改、图片生成、页面状态和创建草稿。
- `test/xhs_terminal_agent.py`：小红书个人账号终端交互 Agent 入口。
- `src/image_generation_agent.py`：图片生成/编辑方法实现，不负责具体测试入口。
- `src/image_prompt_agent.py`：根据现有图片生成图片生成提示词，并调用图片生成流程出图。
- `test/test_image_generation.py`：读取 `cfg/image_task.yaml` 的图片生成任务配置并执行一次生成/编辑测试。
- `test/test_image_prompt_generation.py`：读取 `image_prompt_pipeline_tasks`，先执行看图提示词任务，再执行图片生成任务。
- `test/test_generate_images_and_create_draft.py`：先生成图片素材，再按顺序上传这些图片并创建图文草稿。
- `cfg/model_config.py`：文本模型和图片模型的服务商、API Key、base_url、模型名和生成参数配置。
- `cfg/task.yaml`：小红书发帖任务的任务参数、素材路径、规划规则、正文扩写提示词和兜底文案配置。
- `cfg/image_task.yaml`：图片生成、看图写 prompt、批量 prompt 策划、视觉提示词模板和格式化提示词模板配置。
- `cfg/terminal_actions.yaml`：终端 Agent 的 action 目录，配置每个核心能力的说明、适用场景、禁用场景和示例，用于让模型判断用户请求应该调用哪个能力。
- `test/test_create_draft.py`：创建草稿的端到端测试入口。

## 常见问题

### 一直提示“元素提取为空”

请确认页面没有被手动关闭，并观察是否停留在登录页或风控验证页。当前版本会提取更多 React 页面中常见的可点击节点，并给节点写入临时定位器，通常比单纯使用 `text=...` 更稳定。

### 报错 `Target page, context or browser has been closed`

这通常表示浏览器窗口被关闭、页面崩溃，或脚本异常后没有正确清理。测试入口已经加入 `try/finally`，异常时会自动关闭浏览器并停止 Playwright。

### 没有上传素材

请检查 `cfg/task.yaml` 里的 `post_type` 和素材路径。图文笔记需要图片文件夹或 `doc/pic_exam.png`；视频笔记需要视频文件夹或 `doc/vid_exam.mp4`。Agent 会根据 `post_type` 选择图文或视频入口。

### 图片生成 API Key 为空

请在 `cfg/model_config.py` 的 `IMAGE_MODEL_CONFIG["api_key"]` 中填写图片生成服务的 API Key。

如果 `provider="openai"`，也可以设置环境变量 `OPENAI_API_KEY`。

如果 `provider="doubao"`，也可以设置环境变量 `ARK_API_KEY` 或 `VOLCENGINE_API_KEY`。

### 登录状态失效

重新运行：

```bash
python test/login_init.py
```

然后再次运行测试。

### 草稿看起来没有保存

小红书图文草稿依赖当前浏览器本地数据。请确认先运行过 `python test/login_init.py`，并且后续测试使用默认的持久浏览器目录 `.browser_profile/xhs_creator`。不要手动删除 `.browser_profile`，也不要在浏览器中清除站点数据，否则本地草稿可能丢失。
