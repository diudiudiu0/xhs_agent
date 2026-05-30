# xhs_agent

一个基于 Playwright + DeepSeek 的小红书创作中心自动化 Agent，用于登录复用、进入创作中心、创建图文/视频笔记草稿、填写标题与正文，并按配置自动上传图片或视频素材。项目也提供独立的图片生成 Agent，用于提前生成本地图片素材。

## 当前能力

- 复用 `auth.json` 登录状态进入小红书创作中心。
- 自动提取页面上的按钮、输入框、富文本编辑区等可交互元素。
- 使用阶段状态、最近动作历史、页面上下文和 DeepSeek 共同规划下一步，避免重复点击同一个入口。
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

任务参数写在 `cfg/task.yaml` 中。之后要切换运营任务，可以新增一个任务配置，并把 `active_note_task` 或 `active_image_generation_task` 改成对应任务名。

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
python src/login_init.py
```

浏览器打开后，请手动完成扫码或账号登录。登录成功后程序会把登录状态保存到项目根目录的 `auth.json`。

## 创建草稿测试

运行测试：

```bash
python test/test_create_draft.py
```

发布类型、标题、主题、正文要点、图片/视频素材目录都在 `cfg/task.yaml` 的 `note_tasks` 中配置。

支持的图片格式：`.jpg`、`.jpeg`、`.png`、`.webp`、`.bmp`、`.gif`。
支持的视频格式：`.mp4`、`.mov`、`.avi`、`.mkv`、`.webm`。

## 生成本地图片素材

图片生成任务参数统一写在 `cfg/task.yaml` 的 `image_generation_tasks` 中；模型服务参数仍然写在 `cfg/model_config.py` 的 `IMAGE_MODEL_CONFIG` 中。

- `IMAGE_MODEL_CONFIG`：图片模型、API Key、base_url、默认尺寸、默认质量。
- `image_generation_tasks`：本次测试的 prompt、参考图路径、输出目录、生成数量。

纯提示词生成图片时，把 `image_generation_tasks.<任务名>.input_image` 留空。

基于本地图片修改时，把 `image_generation_tasks.<任务名>.input_image` 填成本地图片路径，例如 `doc/pic_exam.png`。

如果参考图来自本机，设置 `input_image_source: local`；如果参考图来自网络图片地址，设置 `input_image_source: url`。

图片尺寸和比例在 `cfg/task.yaml` 的图片任务里设置：

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

## 主要文件

- `src/login_init.py`：手动登录并保存 `auth.json`。
- `src/core_function/browser_actions.py`：启动浏览器并进入创作中心。
- `src/core_function/element_extractor.py`：提取当前页面可交互元素。
- `src/core_function/browser_skills.py`：点击、填写、等待、上传图片/视频等浏览器动作。
- `src/core_function/llm_planner.py`：调用模型根据页面元素规划下一步动作，并扩写正文。
- `src/core_function/agent_note_publisher.py`：创建小红书草稿的主流程。
- `src/core_function/image_generation_agent.py`：图片生成/编辑方法实现，不负责具体测试入口。
- `test/test_image_generation.py`：读取 `cfg/task.yaml` 的图片生成任务配置并执行一次生成/编辑测试。
- `cfg/model_config.py`：文本模型和图片模型的服务商、API Key、base_url、模型名和生成参数配置。
- `cfg/task.yaml`：发帖任务和图片生成任务的任务参数、素材路径、规划规则、正文扩写提示词和兜底文案配置。
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
python src/login_init.py
```

然后再次运行测试。
