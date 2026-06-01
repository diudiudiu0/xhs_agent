# src/core_function/agent_note_publisher.py
from pathlib import Path

from src.core_function.browser_skills import (
    click_save_and_leave,
    click_by_index,
    fill_content_direct,
    fill_by_index,
    fill_title_direct,
    go_back,
    page_has_text_value,
    wait_seconds,
    upload_media_directly,
)
from src.core_function.llm_planner import build_task_description, expand_note_content, get_next_action, get_note_task_inputs
from src.core_function.element_extractor import extract_interactive_elements
from src.core_function.browser_state_observer import (
    observe_browser_state,
    summarize_browser_state,
    wait_for_browser_feedback,
)


STOP_PUBLISH_KEYWORDS = ["发布", "提交", "确认发布", "立即发布"]
SAVE_AND_LEAVE_KEYWORDS = ["暂存离开", "保存草稿", "存草稿"]
PUBLISH_ENTRY_KEYWORDS = ["发布笔记", "开始发布", "创建笔记"]
IMAGE_ENTRY_KEYWORDS = ["发布图文笔记", "图文笔记", "上传图文", "图片笔记"]
VIDEO_ENTRY_KEYWORDS = ["上传视频", "视频笔记", "发布视频"]
UPLOAD_KEYWORDS = ["上传图片", "选择图片", "上传文件", "添加图片", "点击上传"]
VIDEO_KEYWORDS = ["上传视频", "视频大小", "视频格式", "mp4", "mov", "avi", "20GB", "4小时"]


def _image_folder_ready(image_folder: str | None) -> bool:
    if not image_folder:
        return False
    folder = Path(image_folder).expanduser()
    return folder.exists() and folder.is_dir()


def _text_of(element: dict) -> str:
    return (element.get("text") or element.get("desc") or "").strip()


def _find_element(elements, keywords, types=None, excludes=None):
    excludes = excludes or []
    matches = []
    for element in elements:
        text = _text_of(element)
        desc = element.get("desc", "")
        element_type = element.get("type", "")
        if types and element_type not in types:
            continue
        if any(word in text or word in desc for word in excludes):
            continue
        if any(word in text or word in desc for word in keywords):
            exact_bonus = 100 if text in keywords else 0
            score = exact_bonus - len(text)
            matches.append((score, element["index"]))
    if not matches:
        return None
    return sorted(matches, reverse=True)[0][1]


def _element_by_index(elements, index):
    if index is None or index < 0 or index >= len(elements):
        return None
    return elements[index]


def _is_dangerous_publish(element):
    text = _text_of(element)
    if "发布笔记" in text or "发布图文笔记" in text:
        return False
    return any(word == text or text.endswith(word) for word in STOP_PUBLISH_KEYWORDS)


def _same_page_signature(elements):
    important = [element.get("desc", "") for element in elements[:15]]
    return "|".join(important)


def _action_signature(action, elements):
    element = _element_by_index(elements, action.get("element_index"))
    desc = element.get("desc", "") if element else ""
    return (action.get("action"), desc, action.get("value", ""))


def _recent_repeat_count(history, action, elements):
    signature = _action_signature(action, elements)
    count = 0
    for item in reversed(history):
        if (item.get("action"), item.get("desc", ""), item.get("value", "")) == signature:
            count += 1
        else:
            break
    return count


def _choose_builtin_action(elements, title, content, media_uploaded, filled_title, filled_content, draft_saved, state):
    """根据明确页面状态做保守兜底；不再让规则盲目重复点击同一入口。"""
    post_type = state.get("post_type", "image")
    title_idx = _find_element(
        elements,
        ["标题", "请输入标题", "填写标题"],
        types={"input", "textarea", "textbox"},
        excludes=["搜索"],
    )
    content_idx = _find_element(
        elements,
        ["正文", "内容", "描述", "分享", "空白正文编辑区"],
        types={"contenteditable", "textarea", "textbox"},
        excludes=["标题", "搜索"],
    )

    image_entry_idx = _find_element(elements, IMAGE_ENTRY_KEYWORDS, excludes=VIDEO_KEYWORDS)
    video_entry_idx = _find_element(elements, VIDEO_ENTRY_KEYWORDS)
    upload_idx = _find_element(
        elements,
        UPLOAD_KEYWORDS if post_type == "image" else ["上传视频", "选择视频", "上传文件", "点击上传"],
        excludes=VIDEO_KEYWORDS if post_type == "image" else ["上传图文", "上传图片", "图片大小"],
    )
    publish_entry_idx = _find_element(elements, PUBLISH_ENTRY_KEYWORDS, excludes=["管理", "数据"])
    save_leave_idx = _find_element(elements, SAVE_AND_LEAVE_KEYWORDS)

    if draft_saved:
        return {"action": "done"}

    if media_uploaded and filled_title and filled_content:
        if save_leave_idx is not None:
            return {"action": "save_and_leave", "element_index": save_leave_idx}
        return {"action": "save_and_leave", "element_index": None}

    if (
        not media_uploaded
        and upload_idx is not None
        and (state["media_entry_clicks"] > 0 or state["publish_entry_clicks"] > 0)
        and state["upload_attempts"] < 3
    ):
        return {"action": "upload_media", "element_index": upload_idx}

    if not media_uploaded and state["media_entry_clicks"] > 0 and state["upload_attempts"] < 3:
        return {"action": "upload_media", "element_index": None}

    if title_idx is None and content_idx is None and post_type == "image" and image_entry_idx is not None:
        if state["media_entry_clicks"] == 0:
            return {"action": "click", "element_index": image_entry_idx}
        if state["upload_attempts"] < 3:
            return {"action": "upload_media", "element_index": None}
        return None

    if title_idx is None and content_idx is None and post_type == "video" and video_entry_idx is not None:
        if state["media_entry_clicks"] == 0:
            return {"action": "click", "element_index": video_entry_idx}
        if state["upload_attempts"] < 3:
            return {"action": "upload_media", "element_index": None}
        return None

    # 还没有进入编辑器时，只允许点一次“发布笔记”入口；重复点击交给 LLM 或等待处理。
    if title_idx is None and content_idx is None and publish_entry_idx is not None:
        if state["publish_entry_clicks"] == 0:
            return {"action": "click", "element_index": publish_entry_idx}
        return None

    if media_uploaded and not filled_title:
        return {"action": "fill_title", "element_index": title_idx, "value": title}

    if not filled_title:
        if title_idx is not None:
            return {"action": "fill", "element_index": title_idx, "value": title}

    if media_uploaded and not filled_content:
        return {"action": "fill_content", "element_index": content_idx, "value": content}

    if not filled_content:
        if content_idx is not None:
            return {"action": "fill", "element_index": content_idx, "value": content}

        idx = _find_element(elements, ["空白正文编辑区"], types={"contenteditable"})
        if idx is not None:
            return {"action": "fill", "element_index": idx, "value": content}

    return None


async def agent_create_note_draft(
    page,
    title: str | None = None,
    content: str | None = None,
    image_folder: str = None,
    video_folder: str = None,
    image_files: list[str] | None = None,
    video_files: list[str] | None = None,
    num_images: int | None = None,
    num_videos: int | None = None,
    expand_content: bool | None = None,
    content_topic: str | None = None,
    post_type: str | None = None,
    default_image_file: str | None = None,
    default_video_file: str | None = None,
):
    task_input = get_note_task_inputs(validate=False)
    post_type = post_type or task_input["post_type"]
    title = title or task_input["title"]
    content = content or task_input["seed_content"]
    content_topic = content_topic or task_input["topic"]
    num_images = num_images if num_images is not None else task_input["num_images"]
    num_videos = num_videos if num_videos is not None else task_input["num_videos"]
    expand_content = task_input["expand_content"] if expand_content is None else expand_content
    image_folder = image_folder if image_folder is not None else task_input["image_folder"]
    video_folder = video_folder if video_folder is not None else task_input["video_folder"]
    default_image_file = default_image_file or task_input["default_image_file"]
    default_video_file = default_video_file or task_input["default_video_file"]

    if expand_content:
        content = expand_note_content(
            title,
            content,
            topic=content_topic,
            target_chars=task_input["target_chars"],
        )

    media_uploaded = False
    filled_title = False
    filled_content = False
    draft_saved = False
    history = []
    state = {
        "phase": "creator_home",
        "post_type": post_type,
        "publish_entry_clicks": 0,
        "image_entry_clicks": 0,
        "media_entry_clicks": 0,
        "upload_attempts": 0,
        "save_attempts": 0,
        "filled_title": False,
        "filled_content": False,
        "draft_saved": False,
        "media_uploaded": media_uploaded,
        "last_page_signature": "",
    }

    task = build_task_description(title, content)

    max_steps = 25
    for step in range(max_steps):
        print(f"--- Agent 步骤 {step+1} ---")
        if page.is_closed():
            print("页面已关闭，Agent 停止执行")
            break

        browser_state = await observe_browser_state(page)
        state["browser_state"] = browser_state
        print(f"浏览器状态快照：{summarize_browser_state(browser_state)}")
        if browser_state.get("page_closed"):
            print("浏览器状态：页面已关闭，Agent 停止执行")
            break
        if not browser_state.get("page_responsive"):
            print("浏览器状态：页面暂未响应，等待后重新观察")
            await wait_seconds(page, 2)
            continue
        if browser_state.get("loading_phase") in {"dom_loading", "page_loading"}:
            print(f"浏览器状态：{browser_state.get('loading_phase')}，等待页面加载")
            await wait_seconds(page, 2)
            continue
        if browser_state.get("dialogs", {}).get("visible"):
            print(f"浏览器状态：检测到网页弹窗/浮层 {browser_state.get('dialogs', {}).get('items', [])[:1]}")

        # 获取当前页面元素缓存
        elements_cache = await extract_interactive_elements(page)
        if not elements_cache:
            print("页面元素为空，等待2秒后重试...")
            await wait_seconds(page, 2)
            continue  # 跳过本轮，让 Agent 再次尝试

        state["media_uploaded"] = media_uploaded
        state["filled_title"] = filled_title
        state["filled_content"] = filled_content
        state["draft_saved"] = draft_saved
        state["current_url"] = page.url
        state["current_page_signature"] = _same_page_signature(elements_cache)

        action = _choose_builtin_action(
            elements_cache,
            title,
            content,
            media_uploaded,
            filled_title,
            filled_content,
            draft_saved,
            state,
        )
        if action:
            print(f"状态兜底动作: {action}")
        else:
            action = await get_next_action(page, task, history, elements_cache, state)

        if action.get("action") in {"click", "fill"}:
            idx = action.get("element_index")
            if idx is None or idx < 0 or idx >= len(elements_cache):
                print(f"动作索引无效，改为等待: {action}")
                action = {"action": "wait"}
            elif action["action"] == "click" and media_uploaded and (filled_content or state["phase"] == "content_filled"):
                print("草稿内容已写入，阻止继续点击入口类按钮，结束任务")
                action = {"action": "done"}
            elif action["action"] == "click" and _is_dangerous_publish(elements_cache[idx]):
                print(f"阻止可能正式发布的点击：{elements_cache[idx]['desc']}")
                action = {"action": "done"}
            elif (
                action["action"] == "click"
                and state.get("last_action_observation", {}).get("status") == "no_visible_response"
                and _recent_repeat_count(history, action, elements_cache) >= 1
            ):
                print("上一次相同点击没有页面反馈，本轮改为等待重新观察，避免无效连点")
                action = {"action": "wait"}
            elif _recent_repeat_count(history, action, elements_cache) >= 2:
                print("检测到同一动作连续重复且页面无进展，改为等待并重新观察页面")
                action = {"action": "wait"}

        before_action_state = browser_state
        should_wait_for_feedback = action.get("action") in {
            "click",
            "fill",
            "fill_title",
            "fill_content",
            "upload_images",
            "upload_media",
            "save_and_leave",
            "back",
        }
        if should_wait_for_feedback:
            print(f"动作前状态：{summarize_browser_state(before_action_state)}")

        if action["action"] == "done":
            print("Agent 任务完成")
            break

        elif action["action"] == "fill_title":
            print("开始执行标题填写工具步骤")
            success = await fill_title_direct(page, title)
            filled_title = success and await page_has_text_value(page, title)
            state["filled_title"] = filled_title
            if filled_title:
                print("标题已确认写入")
            else:
                print("标题填写未确认成功，等待后重新观察页面")
            await wait_seconds(page, 1.5)

        elif action["action"] == "fill_content":
            print("开始执行正文填写工具步骤")
            success = await fill_content_direct(page, content)
            filled_content = success and await page_has_text_value(page, content)
            state["filled_content"] = filled_content
            if filled_content:
                print("正文已确认写入")
            else:
                print("正文填写未确认成功，等待后重新观察页面")
            await wait_seconds(page, 1.5)

        elif action["action"] == "save_and_leave":
            print("开始执行暂存离开")
            state["save_attempts"] += 1
            success = await click_save_and_leave(page)
            draft_saved = success
            state["draft_saved"] = success
            if success:
                print("已暂存离开，Agent 任务完成")
                break
            if state["save_attempts"] >= 1:
                print("暂存离开未确认，已保留当前页面并停止，避免重复执行同一失败动作")
                break
            await wait_seconds(page, 2)

        elif action["action"] in {"upload_images", "upload_media"}:
            if media_uploaded:
                print("素材已经上传过，跳过重复上传")
                await wait_seconds(page, 1)
                continue
            print("开始执行素材上传工具步骤")
            state["upload_attempts"] += 1
            success = await upload_media_directly(
                page,
                post_type,
                image_folder=image_folder,
                video_folder=video_folder,
                image_files=image_files,
                video_files=video_files,
                default_image_file=default_image_file,
                default_video_file=default_video_file,
                num_images=num_images,
                num_videos=num_videos,
            )
            if success:
                media_uploaded = True
                state["media_uploaded"] = True
                state["phase"] = "media_uploaded"
                print("素材上传工具步骤完成，进入下一步页面观察")
            else:
                print("素材上传工具步骤未成功，等待后重新观察页面")
                if state["upload_attempts"] >= 3:
                    print("素材上传连续失败 3 次，停止本次任务，避免重复点击上传入口")
                    break
            await wait_seconds(page, 2)

        elif action["action"] == "click":
            idx = action.get("element_index")
            if idx is None:
                print("click 缺少 element_index，跳过")
                continue
            # 获取被点击元素的描述
            desc = elements_cache[idx]['desc'] if idx < len(elements_cache) else ""
            print(f"点击索引 {idx}：{desc}")
            if any(word in desc for word in PUBLISH_ENTRY_KEYWORDS):
                state["publish_entry_clicks"] += 1
            if any(word in desc for word in IMAGE_ENTRY_KEYWORDS):
                state["image_entry_clicks"] += 1
                state["media_entry_clicks"] += 1
            if any(word in desc for word in VIDEO_ENTRY_KEYWORDS):
                state["media_entry_clicks"] += 1

            try:
                await click_by_index(page, idx, elements_cache)
            except Exception as e:
                print(f"点击失败: {e}")
            await wait_seconds(page, 2)

        elif action["action"] == "fill":
            idx = action.get("element_index")
            value = action.get("value", "")
            if idx is None or not value:
                print("fill 参数缺失")
                continue
            try:
                if value == title:
                    success = await fill_title_direct(page, title)
                    if not success:
                        await fill_by_index(page, idx, value, elements_cache)
                elif value == content:
                    success = await fill_content_direct(page, content)
                    if not success:
                        await fill_by_index(page, idx, value, elements_cache)
                else:
                    await fill_by_index(page, idx, value, elements_cache)
                if value == title:
                    filled_title = await page_has_text_value(page, title)
                    if filled_title:
                        print("标题已确认写入")
                    state["phase"] = "title_filled"
                if value == content:
                    filled_content = await page_has_text_value(page, content)
                    if filled_content:
                        print("正文已确认写入")
                    state["phase"] = "content_filled"
            except Exception as e:
                print(f"填充失败: {e}")
            await wait_seconds(page, 1.5)

        elif action["action"] == "back":
            await go_back(page)
            await wait_seconds(page, 1.5)

        elif action["action"] == "wait":
            print("Agent 请求等待")
            await wait_seconds(page, 3)

        else:
            print(f"未知动作: {action}")
            await wait_seconds(page, 1)

        if should_wait_for_feedback:
            after_action_state, action_observation = await wait_for_browser_feedback(
                page,
                before_action_state,
                timeout=3.0,
                interval=0.5,
            )
        else:
            after_action_state = await observe_browser_state(page)
            action_observation = {
                "status": "not_observed_for_action",
                "loading_phase": after_action_state.get("loading_phase"),
                "dialog_visible": after_action_state.get("dialogs", {}).get("visible", False),
                "file_input_count": len(after_action_state.get("file_inputs") or []),
            }
        state["last_action_observation"] = action_observation
        state["browser_state"] = after_action_state
        print(f"动作后状态：{summarize_browser_state(after_action_state)}")
        print(f"页面反馈判断：{action_observation}")
        if (
            action.get("action") == "click"
            and action_observation.get("status") == "no_visible_response"
        ):
            print("页面反馈：本次点击未观察到 URL、DOM、弹窗或加载状态变化，下一轮会避免盲目重复点击")

        # 记录历史（包含 element_index 以便追踪）
        history.append({
            "step": step + 1,
            "action": action.get("action"),
            "element_index": action.get("element_index"),
            "desc": elements_cache[action["element_index"]]["desc"]
            if action.get("element_index") is not None
            and 0 <= action.get("element_index") < len(elements_cache)
            else "",
            "value": action.get("value", ""),
            "reason": action.get("reason", ""),
            "expected_result": action.get("expected_result", ""),
            "result": f"url={page.url} browser={action_observation.get('status')}",
        })
        state["last_page_signature"] = state.get("current_page_signature", "")

    print("Agent 草稿创建流程结束")
    return page
