import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.manager_agent import ManagerAgent


HELP_TEXT = """
小红书 Manager Agent 终端入口：

基础命令：
  help / 帮助          查看帮助
  exit / quit / 退出   退出

使用方式：
  直接用自然语言告诉 agent 你的诉求，它会先规划，再调用合适的 skill 执行。

示例：
  根据 doc/pic_exam.png 生成 3 张脚轮商品图，并写一篇草稿
  查看我的主页评论
  根据喜仔的评论和所属帖子内容回复喜仔
  保存当前草稿并回到首页
  删除保存于 2026-05-31 19:38:43 的那篇草稿

说明：
  manager 负责理解目标和调度能力。
  图片、文案、草稿创建、网页探索、评论回复等具体执行由对应 skill / specialist agent 完成。
""".strip()


async def main():
    agent = ManagerAgent()
    print("小红书 Manager Agent 已启动。输入 help 查看说明，输入 exit 退出。")
    try:
        while True:
            user_text = input("\n你> ").strip()
            if not user_text:
                continue
            if user_text.lower() in {"exit", "quit"} or user_text in {"退出", "结束"}:
                break
            if user_text.lower() in {"help", "h"} or user_text in {"帮助", "菜单"}:
                print(HELP_TEXT)
                continue

            try:
                answer = await agent.handle_user_message(user_text)
                if answer:
                    print(f"\nManager> {answer}")
            except Exception as exc:
                print(f"\nManager 执行异常：{exc}")
    finally:
        await agent.close()
        print("Manager Agent 已退出。")


if __name__ == "__main__":
    asyncio.run(main())
