import asyncio
import json
import re
import sys
from pathlib import Path

from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cfg.model_config import MODEL_CONFIG
from src.xhs_action_registry import VALID_ACTION_NAMES, render_action_catalog
from src.xhs_workflow import XhsWorkflow
from src.xhs_agent_skills import XhsAgentSkills


HELP_TEXT = """
小红书个人账号 Agent 终端命令：

基础：
  help / 帮助                         查看帮助
  memory / 记忆                       查看当前会话记忆
  工作台 / 任务状态                    查看任务记录、完成进度和下一步建议
  exit / quit / 退出                  退出

图片提示词：
  生成提示词                          使用 cfg/image_task.yaml 默认参考图生成提示词
  生成提示词 图片=doc/pic_exam.png 数量=3 目标=做脚轮商业海报
  修改提示词 增加品牌露出，第二张改成参数信息图

图片：
  生成图片                            使用当前提示词生成图片

发帖：
  写文案                              根据当前图片提示词写标题和正文
  创建草稿                            使用当前生成图片和文案创建小红书草稿

页面：
  打开页面                            打开小红书创作中心
  页面状态                            获取当前页面状态
  探索页面任务                        统一处理查看、编辑、删除、草稿箱等网页操作
  处理弹窗                            尝试处理网页弹窗/上传收尾弹窗

也可以直接说自然语言，例如：
  根据 doc/pic_exam.png 这张图片，帮我生成 3 条脚轮商品海报提示词
  第二张提示词加上 M6 参数信息，第一张不要纯白背景
  满意了，生成图片
  用这些图片创建草稿
""".strip()


def _text_client() -> OpenAI:
    return OpenAI(
        api_key=MODEL_CONFIG["api_key"],
        base_url=MODEL_CONFIG["base_url"],
        timeout=MODEL_CONFIG.get("timeout", 30),
    )


def _extract_json(raw_text: str) -> dict | None:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return None


def _exact_terminal_intent(user_text: str) -> dict | None:
    text = user_text.strip()
    if text.lower() in {"help", "h"} or text in {"帮助", "菜单"}:
        return {"action": "help", "args": {}}
    if text.lower() in {"exit", "quit"} or text in {"退出", "结束"}:
        return {"action": "exit", "args": {}}
    if text.lower() in {"workflow", "worklog", "tasks"} or text in {"工作台", "任务状态", "任务进度", "进度"}:
        return {"action": "workflow_status", "args": {}}
    if text.lower() in {"memory", "status"} or text in {"记忆", "当前状态"}:
        return {"action": "memory", "args": {}}
    return None


def _extract_prompt_args(text: str) -> dict:
    image_match = re.search(r"(?:图片|图|image)\s*[=:：]\s*([^\s]+)", text)
    if not image_match:
        image_match = re.search(
            r"([A-Za-z]:[\\/][^\s，,。；;]+?\.(?:png|jpg|jpeg|webp)|[^\s，,。；;]+?\.(?:png|jpg|jpeg|webp))",
            text,
            flags=re.IGNORECASE,
        )
    count_match = re.search(r"(?:数量|count)\s*[=:：]\s*(\d+)", text)
    if not count_match:
        count_match = re.search(r"(\d+)\s*(?:条|张|个)", text)
    return {
        "input_image": image_match.group(1) if image_match else None,
        "count": int(count_match.group(1)) if count_match else None,
        "user_goal": text,
    }


def _normalize_intent(intent: dict, user_text: str) -> dict:
    if not isinstance(intent, dict):
        return {"action": "chat", "args": {"message": user_text}}
    action = intent.get("action")
    if action not in VALID_ACTION_NAMES and action not in {"help", "exit", "memory", "workflow_status"}:
        action = "chat"
    args = intent.get("args") if isinstance(intent.get("args"), dict) else {}

    if action == "generate_prompts":
        parsed_args = _extract_prompt_args(user_text)
        parsed_args.update({key: value for key, value in args.items() if value is not None})
        args = parsed_args
    elif action == "revise_prompts":
        args.setdefault("revision_instruction", user_text)
    elif action == "explore_page_task":
        args.setdefault("user_goal", user_text)
    elif action == "chat":
        args.setdefault("message", user_text)

    return {"action": action, "args": args}


def classify_intent(user_text: str) -> dict:
    exact_intent = _exact_terminal_intent(user_text)
    if exact_intent:
        return exact_intent

    prompt = f"""
你是小红书个人账号终端 Agent 的动作选择器。

你的任务：根据用户输入，从“可用 action 目录”中选择最合适的 action。
你不是关键词匹配器，必须读懂用户意图和 action 描述。

重要原则：
1. 如果用户是在操作“当前网页/当前草稿/已有帖子/草稿箱”，选择 explore_page_task。
2. 如果用户说“保存草稿回到首页 / 暂存离开 / 保存当前草稿”，这是对当前网页的操作，必须选择 explore_page_task，不是 create_draft。
3. create_draft 只用于“从零新建一篇草稿并上传素材填写文案”的完整流程。
4. 删除、移除、清空、撤回等高风险网页任务也选择 explore_page_task；是否需要用户确认由页面探索模型在具体 action 中返回 requires_user_confirmation 决定。
5. 生成提示词、生成图片、写文案是离线内容生产任务，不是网页操作。
6. 如果无法确定，但请求看起来依赖当前页面状态，优先选择 explore_page_task。
7. 只输出一个 JSON 对象，不要 Markdown，不要解释。

可用 action 目录：
{render_action_catalog()}

输出格式：
{{
  "action": "action_name",
  "args": {{}}
}}

用户输入：
{user_text}
""".strip()
    try:
        client = _text_client()
        response = client.chat.completions.create(
            model=MODEL_CONFIG.get("planner_model", MODEL_CONFIG.get("content_model")),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=MODEL_CONFIG.get("planner_max_tokens", 800),
            temperature=MODEL_CONFIG.get("planner_temperature", 0.1),
        )
        parsed = _extract_json(response.choices[0].message.content or "")
        if isinstance(parsed, dict) and parsed.get("action"):
            return _normalize_intent(parsed, user_text)
    except Exception as exc:
        print(f"意图识别模型暂不可用，不执行自动路由：{exc}")
    return {"action": "chat", "args": {"message": user_text}}


async def dispatch(skills: XhsAgentSkills, workflow: XhsWorkflow, intent: dict) -> tuple[bool, str]:
    action = intent.get("action")
    args = intent.get("args") or {}

    if action == "help":
        print(HELP_TEXT)
        return True, "已显示帮助"
    elif action == "exit":
        return False, "用户退出"
    elif action == "workflow_status":
        snapshot = workflow.snapshot()
        print(snapshot)
        print(workflow.next_suggestion())
        return True, "已显示工作台状态"
    elif action == "memory":
        memory_text = skills.show_memory()
        print(memory_text)
        return True, "已显示会话记忆"
    elif action == "generate_prompts":
        workflow.step("读取图片任务配置")
        skills.generate_prompts(
            input_image=args.get("input_image"),
            user_goal=args.get("user_goal"),
            count=args.get("count"),
        )
        workflow.step("调用视觉模型生成提示词")
        workflow.step("保存提示词到会话记忆")
        return True, f"已生成 {len(skills.memory.generated_prompts)} 条图片提示词"
    elif action == "revise_prompts":
        revision = args.get("revision_instruction") or args.get("message")
        if not revision:
            revision = input("请输入修改要求：").strip()
        workflow.step("读取当前提示词")
        skills.revise_prompts(revision)
        workflow.step("按用户要求重写提示词")
        workflow.step("保存修改后的提示词")
        return True, f"已修改 {len(skills.memory.generated_prompts)} 条图片提示词"
    elif action == "generate_images":
        workflow.step("读取当前提示词")
        skills.generate_images()
        workflow.step("调用图片模型生成图片")
        workflow.step("保存图片路径到会话记忆")
        return True, f"已生成 {len(skills.memory.generated_images)} 张图片"
    elif action == "plan_note_text":
        workflow.step("读取当前图片提示词")
        skills.plan_note_text(title=args.get("title"))
        workflow.step("生成标题和正文")
        workflow.step("保存发帖文案")
        return True, "已生成发帖标题和正文"
    elif action == "create_draft":
        await skills.create_draft()
        workflow.step("打开创作中心")
        workflow.step("上传素材")
        workflow.step("填写标题正文")
        workflow.step("暂存离开")
        return True, "草稿创建流程已执行"
    elif action == "open_page":
        await skills.open_creator_page()
        print("已打开创作中心")
        workflow.step("打开创作中心")
        workflow.step("保存页面句柄")
        return True, "已打开创作中心"
    elif action == "page_state":
        state = await skills.get_page_state()
        workflow.step("打开或复用页面")
        workflow.step("采集页面状态")
        workflow.step("输出状态摘要")
        return True, f"已输出页面状态：{state.get('loading_phase')}"
    elif action == "explore_page_task":
        user_goal = args.get("user_goal") or args.get("message") or ""
        worklog_hints = workflow.search_experiences(user_goal)
        if worklog_hints:
            print(f"已从 xhs_agent_worklog.json 检索到 {len(worklog_hints)} 条相关成功经验：")
            for index, hint in enumerate(worklog_hints, start=1):
                request = hint.get("user_request", "")
                score = hint.get("match_score")
                reuse_level = hint.get("reuse_level")
                ratio = hint.get("match_ratio")
                request_ratio = hint.get("request_match_ratio")
                request_overlap = "、".join(hint.get("request_overlap_terms", [])[:8])
                overlap = "、".join(hint.get("overlap_terms", [])[:8])
                print(
                    f"  {index}. 之前请求：{request}；匹配分={score}，"
                    f"复用级别={reuse_level}，"
                    f"请求相似度={request_ratio}，整体相似度={ratio}，"
                    f"请求重合={request_overlap}，整体重合={overlap}"
                )
        else:
            print("未从 xhs_agent_worklog.json 检索到相关成功经验，进入自主探索")
        workflow.step("理解页面任务")
        workflow.step("观察页面和可交互元素", status="in_progress")
        try:
            result = await skills.explore_page_task(user_goal=user_goal, worklog_hints=worklog_hints)
            if not result.get("success"):
                raise RuntimeError(result.get("answer") or "页面探索任务未完成")
            workflow.step("观察页面和可交互元素")
            workflow.step("小步探索并观察反馈")
            workflow.step("提取结果并记录成功路径")
            if result.get("next_suggestion"):
                workflow.set_next_suggestion(result.get("next_suggestion", ""))
            if result.get("remember", True):
                workflow.remember_experience(
                    user_request=args.get("user_goal") or user_goal,
                    result=result.get("answer", "探索任务已执行"),
                    raw_steps=result.get("steps") or [],
                )
            return True, result.get("answer", "探索任务已执行")
        finally:
            skills.cleanup_page_task_trace()
    elif action == "handle_dialogs":
        handled = await skills.handle_page_dialogs()
        workflow.step("打开或复用页面")
        workflow.step("检测并处理网页弹窗")
        workflow.step("记录处理结果")
        return True, f"弹窗处理结果：{handled}"
    else:
        print("我还没有把这句话映射到具体技能。可以输入 help 查看可用能力。")
        return True, "未匹配到具体技能"


async def main():
    skills = XhsAgentSkills()
    workflow = XhsWorkflow()
    print("小红书个人账号 Agent 已启动。输入 help 查看能力，输入 exit 退出。")
    try:
        while True:
            user_text = input("\n你> ").strip()
            if not user_text:
                continue
            intent = classify_intent(user_text)
            print(f"Agent 路由：{intent}")
            workflow.start(user_text, intent)
            try:
                keep_running, result = await dispatch(skills, workflow, intent)
                workflow.complete(result)
                print(workflow.snapshot())
                print(workflow.next_suggestion())
            except Exception as exc:
                workflow.fail(str(exc))
                print(f"任务执行失败：{exc}")
                print(workflow.snapshot())
                print(workflow.next_suggestion())
                keep_running = True
            if not keep_running:
                break
    finally:
        await skills.close()
        print("Agent 已退出。")


if __name__ == "__main__":
    asyncio.run(main())
