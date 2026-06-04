import asyncio
import json
import re
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cfg.model_config import MODEL_CONFIG
from src.note_draft_workflow import agent_create_note_draft
from src.browser_session import open_creator_home, open_xhs_home
from src.browser_tools import close_upload_dialog_if_present
from src.browser_state import observe_browser_state, summarize_browser_state
from src.image_generation_service import generate_or_edit_image_from_config
from src.image_prompt_agent import generate_image_prompts_from_image
from src.note_content_service import generate_note_text_from_image_prompts, get_note_task_inputs
from src.prompt_config import get_prompt_config, render_prompt_template
from src.task_config_loader import get_active_image_prompt_pipeline_config
from src.page_explorer_agent import XhsPageExplorer


def _text_client() -> OpenAI:
    return OpenAI(
        api_key=MODEL_CONFIG["api_key"],
        base_url=MODEL_CONFIG["base_url"],
        timeout=MODEL_CONFIG.get("timeout", 30),
    )


def _extract_json(raw_text: str):
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = min([pos for pos in [text.find("{"), text.find("[")] if pos >= 0], default=-1)
    if start < 0:
        return None
    end_obj = text.rfind("}")
    end_arr = text.rfind("]")
    end = max(end_obj, end_arr)
    if end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


@dataclass
class XhsAgentMemory:
    generated_prompts: list[str] = field(default_factory=list)
    generated_images: list[str] = field(default_factory=list)
    last_note_title: str = ""
    last_note_content: str = ""
    active_site: str = "creator"
    page_sessions: dict[str, dict[str, object]] = field(default_factory=dict)


class XhsAgentSkills:
    """小红书个人账号运营 Agent 的可复用技能集合。"""

    def __init__(self):
        self.memory = XhsAgentMemory()
        self.explorer = XhsPageExplorer()

    def _normalize_site(self, site: str | None) -> str:
        text = (site or "").strip().lower()
        site_aliases = {
            "creator": "creator",
            "creator_center": "creator",
            "creator_home": "creator",
            "publish": "creator",
            "studio": "creator",
            "创作中心": "creator",
            "后台": "creator",
            "发布中心": "creator",
            "web": "web",
            "main": "web",
            "main_site": "web",
            "home": "web",
            "xhs_web": "web",
            "xhs_home": "web",
            "xhs": "web",
            "www": "web",
            "xiaohongshu": "web",
            "小红书主页": "web",
            "小红书主站": "web",
            "主站": "web",
            "个人主页": "web",
            "评论区": "web",
            "首页": "web",
        }
        if text in {"current", "当前", ""}:
            return self.memory.active_site or "creator"
        return site_aliases.get(text, "creator")

    def _site_label(self, site: str) -> str:
        return "小红书创作中心" if site == "creator" else "小红书主站"

    def _session(self, site: str) -> dict[str, object]:
        return self.memory.page_sessions.setdefault(site, {})

    def _live_page(self, site: str):
        page = self._session(site).get("page")
        if page is not None and not page.is_closed():
            return page
        return None

    async def _open_site_page(self, site: str):
        site = self._normalize_site(site)
        page = self._live_page(site)
        if page is not None:
            self.memory.active_site = site
            return page

        if site == "web":
            page, browser, context, playwright = await open_xhs_home(headless=False, persistent=True)
        else:
            page, browser, context, playwright = await open_creator_home(headless=False, persistent=True)
            site = "creator"

        self.memory.page_sessions[site] = {
            "page": page,
            "browser": browser,
            "context": context,
            "playwright": playwright,
        }
        self.memory.active_site = site
        return page

    def _page_session_summary(self) -> list[dict]:
        summaries = []
        for site, session in self.memory.page_sessions.items():
            page = session.get("page")
            if page is None:
                continue
            summaries.append(
                {
                    "site": site,
                    "label": self._site_label(site),
                    "open": not page.is_closed(),
                    "url": "" if page.is_closed() else page.url,
                }
            )
        return summaries

    async def _choose_site_for_goal(self, user_goal: str, target_site: str | None = None) -> str:
        if target_site:
            return self._normalize_site(target_site)

        prompt = render_prompt_template(
            str(get_prompt_config("xhs_skill_runtime", "site_selector_prompt_template", default="")),
            active_site=self.memory.active_site,
            page_sessions_json=json.dumps(self._page_session_summary(), ensure_ascii=False, indent=2),
            user_goal=user_goal,
        )
        try:
            response = _text_client().chat.completions.create(
                model=MODEL_CONFIG.get("planner_model", MODEL_CONFIG.get("content_model")),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=MODEL_CONFIG.get("planner_temperature", 0.1),
            )
            parsed = _extract_json(response.choices[0].message.content or "")
            if isinstance(parsed, dict):
                return self._normalize_site(parsed.get("site"))
        except Exception as exc:
            print(f"页面选择模型暂不可用，继续使用当前页面：{exc}")
        return self._normalize_site("current")

    def _pipeline_config(self) -> dict:
        return get_active_image_prompt_pipeline_config()

    def show_memory(self) -> str:
        page_summaries = self._page_session_summary()
        page_text = "\n".join(
            f"  - {item['site']}({item['label']}): open={item['open']} url={item['url']}"
            for item in page_summaries
        ) or "  - 尚未打开页面"
        return (
            f"当前记忆：\n"
            f"- prompts: {len(self.memory.generated_prompts)} 条\n"
            f"- images: {len(self.memory.generated_images)} 张\n"
            f"- title: {self.memory.last_note_title or '未生成'}\n"
            f"- active_site: {self.memory.active_site}\n"
            f"- pages:\n{page_text}"
        )

    def generate_prompts(
        self,
        input_image: str | None = None,
        user_goal: str | None = None,
        count: int | None = None,
    ) -> list[str]:
        """根据参考图生成图片提示词。"""
        pipeline = self._pipeline_config()
        prompt_task_config = deepcopy(pipeline["prompt_task"])
        generation_task_config = deepcopy(pipeline["generation_task"])

        if input_image:
            prompt_task_config["input_image"] = input_image
            prompt_task_config["input_image_source"] = "url" if input_image.startswith(("http://", "https://")) else "local"
        if user_goal:
            prompt_task_config["user_goal"] = user_goal
        if count:
            generation_task_config["count"] = int(count)

        prompts = generate_image_prompts_from_image(prompt_task_config, generation_task_config)
        self.memory.generated_prompts = prompts
        print("已生成图片提示词：")
        for index, prompt in enumerate(prompts, start=1):
            print(f"\n[{index}] {prompt}")
        return prompts

    def revise_prompts(self, revision_instruction: str) -> list[str]:
        """根据用户反馈修改当前图片提示词。"""
        if not self.memory.generated_prompts:
            raise ValueError("当前还没有可修改的图片提示词，请先生成提示词。")

        prompt = render_prompt_template(
            str(get_prompt_config("xhs_skill_runtime", "revise_prompts_prompt_template", default="")),
            prompt_count=len(self.memory.generated_prompts),
            revision_instruction=revision_instruction,
            current_prompts_json=json.dumps(self.memory.generated_prompts, ensure_ascii=False, indent=2),
        )

        client = _text_client()
        response = client.chat.completions.create(
            model=MODEL_CONFIG.get("formatter_model") or MODEL_CONFIG.get("content_model"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=MODEL_CONFIG.get("formatter_max_tokens", 3200),
            temperature=MODEL_CONFIG.get("formatter_temperature", 0.1),
        )
        parsed = _extract_json(response.choices[0].message.content or "")
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise ValueError("提示词修改模型没有返回合法 JSON 字符串数组。")
        self.memory.generated_prompts = [item.strip() for item in parsed if item.strip()]
        print("已修改图片提示词：")
        for index, prompt_text in enumerate(self.memory.generated_prompts, start=1):
            print(f"\n[{index}] {prompt_text}")
        return self.memory.generated_prompts

    def generate_images(self, prompts: list[str] | None = None) -> list[str]:
        """根据当前提示词生成图片，并保存到配置目录。"""
        prompts = prompts or self.memory.generated_prompts
        if not prompts:
            raise ValueError("当前没有图片提示词，请先生成或输入提示词。")

        generation_task_config = deepcopy(self._pipeline_config()["generation_task"])
        saved_paths = []
        for index, prompt in enumerate(prompts, start=1):
            print(f"\n开始生成第 {index}/{len(prompts)} 张图片")
            config = deepcopy(generation_task_config)
            config["prompt"] = prompt
            config["count"] = 1
            saved_paths.extend(str(path) for path in generate_or_edit_image_from_config(config))

        self.memory.generated_images = saved_paths
        print("\n图片生成完成：")
        for path in saved_paths:
            print(path)
        return saved_paths

    def plan_note_text(self, title: str | None = None) -> dict:
        """根据当前图片提示词生成或重写发帖标题/正文。"""
        if not self.memory.generated_prompts:
            raise ValueError("当前还没有图片提示词，无法根据图片提示词写标题和正文。")
        task_input = get_note_task_inputs(validate=False)
        note_text = generate_note_text_from_image_prompts(
            self.memory.generated_prompts,
            title=title if title is not None else task_input["title"],
            seed_content=task_input["seed_content"],
            topic=task_input["topic"],
            target_chars=task_input["target_chars"],
        )
        self.memory.last_note_title = note_text["title"]
        self.memory.last_note_content = note_text["content"]
        print(f"标题：{note_text['title']}\n\n正文：\n{note_text['content']}")
        return note_text

    async def open_creator_page(self):
        """打开小红书创作中心，并把 page 保存在会话记忆里。"""
        return await self._open_site_page("creator")

    async def open_xhs_page(self):
        """打开小红书主站，并把 page 保存在会话记忆里。"""
        return await self._open_site_page("web")

    async def open_page(self, site: str | None = None):
        """打开或复用指定站点页面。site 可为 creator/web/current。"""
        return await self._open_site_page(self._normalize_site(site))

    async def switch_page(self, target_site: str):
        """给页面探索器使用的跨站点切换入口。"""
        site = self._normalize_site(target_site)
        page = await self._open_site_page(site)
        print(f"已切换到{self._site_label(site)}：{page.url}")
        return page

    async def get_page_state(self) -> dict:
        """获取当前已打开页面的状态。"""
        page = await self.open_page("current")
        state = await observe_browser_state(page)
        print(summarize_browser_state(state))
        return state

    async def _read_draft_count_from_page(self, page) -> tuple[int | None, list[str]]:
        data = await page.evaluate(
            """() => {
                const bodyText = document.body ? document.body.innerText : '';
                const candidates = Array.from(document.querySelectorAll('body *'))
                    .map(el => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim())
                    .filter(text => text && text.includes('草稿'))
                    .slice(0, 80);
                return {bodyText, candidates: Array.from(new Set(candidates))};
            }"""
        )
        candidates = data.get("candidates") or []
        search_text = "\n".join(candidates + [data.get("bodyText") or ""])
        patterns = [
            r"草稿箱\s*[（(]\s*(\d+)\s*[)）]",
            r"草稿\s*[（(]\s*(\d+)\s*[)）]",
            r"草稿箱\s*[:：]?\s*(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, search_text)
            if match:
                return int(match.group(1)), candidates[:12]
        return None, candidates[:12]

    async def inspect_draft_count(self) -> dict:
        """读取小红书创作中心可见的草稿箱数量。"""
        page = await self.open_creator_page()
        count, candidates = await self._read_draft_count_from_page(page)

        if count is None:
            for label in ("发布笔记", "上传图文"):
                try:
                    await page.get_by_text(label, exact=True).first.click(timeout=2500)
                    await page.wait_for_timeout(1500)
                    break
                except Exception:
                    continue
            count, candidates = await self._read_draft_count_from_page(page)

        result = {
            "draft_count": count,
            "url": page.url,
            "candidates": candidates,
        }
        if count is None:
            print("未能从当前页面识别草稿箱数量。页面草稿相关文本候选：")
            for item in candidates:
                print(f"- {item[:160]}")
        else:
            print(f"当前识别到草稿箱数量：{count} 篇")
        return result

    async def explore_page_task(
        self,
        user_goal: str,
        max_steps: int = 12,
        worklog_hints: list[dict] | None = None,
        target_site: str | None = None,
    ) -> dict:
        """对没有固定 skill 的页面任务进行自主探索，并沉淀成功路径。"""
        site = await self._choose_site_for_goal(user_goal, target_site=target_site)
        page = await self.open_page(site)
        print(f"页面探索起点：{self._site_label(self.memory.active_site)} -> {page.url}")
        result = await self.explorer.explore(
            page,
            user_goal=user_goal,
            max_steps=max_steps,
            worklog_hints=worklog_hints or [],
            switch_page=self.switch_page,
        )
        if result.get("success"):
            print("探索任务完成：")
        else:
            print("探索任务未完全完成：")
        print(result.get("answer", ""))
        return result

    def cleanup_page_task_trace(self):
        """删除单次网页探索产生的短期 trace 文件。"""
        self.explorer.trace.clear()

    async def handle_page_dialogs(self) -> bool:
        """尝试处理网页弹窗/上传收尾弹窗。"""
        page = await self.open_page("current")
        handled = await close_upload_dialog_if_present(page)
        print(f"弹窗处理结果：{handled}")
        return handled

    async def create_draft(self, image_files: list[str] | None = None) -> None:
        """使用当前图片和内容创建小红书图文草稿。"""
        task_input = get_note_task_inputs(validate=False)
        page = await self.open_creator_page()
        image_files = image_files or self.memory.generated_images

        title = self.memory.last_note_title or task_input["title"]
        content = self.memory.last_note_content or task_input["seed_content"]
        if self.memory.generated_prompts and not self.memory.last_note_content:
            note_text = self.plan_note_text(title=title)
            title = note_text["title"]
            content = note_text["content"]

        await agent_create_note_draft(
            page,
            post_type="image",
            title=title,
            content=content,
            image_folder=str(Path(image_files[0]).parent) if image_files else task_input["image_folder"],
            image_files=image_files,
            num_images=len(image_files) if image_files else task_input["num_images"],
            expand_content=False if self.memory.generated_prompts else task_input["expand_content"],
            content_topic=task_input["topic"],
            default_image_file=image_files[0] if image_files else task_input["default_image_file"],
            video_folder=task_input["video_folder"],
            default_video_file=task_input["default_video_file"],
            num_videos=task_input["num_videos"],
        )

    async def close(self):
        for session in list(self.memory.page_sessions.values()):
            browser = session.get("browser")
            playwright = session.get("playwright")
            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()
        self.memory.page_sessions.clear()
