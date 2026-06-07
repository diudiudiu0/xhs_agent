import sys
import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REQUIRED_PACKAGES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
}


def _missing_packages() -> list[str]:
    return [name for module_name, name in REQUIRED_PACKAGES.items() if importlib.util.find_spec(module_name) is None]


missing = _missing_packages()
if missing:
    print("Web 实时控制台需要安装依赖：")
    print(r"D:\ANACONDA\envs\xhs_agent\python.exe -m pip install -U fastapi ""uvicorn[standard]""")
    print(f"当前缺少：{', '.join(missing)}")
    raise SystemExit(1)

from src.web_realtime_agent_server import main


if __name__ == "__main__":
    main()
