import sys
from ctypes import WINFUNCTYPE, byref, create_unicode_buffer, windll
from ctypes.wintypes import BOOL, HWND, LPARAM


FILE_DIALOG_TITLE_KEYWORDS = (
    "打开",
    "选择",
    "文件",
    "上传",
    "Open",
    "Select",
    "Choose",
    "Upload",
)
FILE_DIALOG_CLASSES = {"#32770"}


def _empty_state(reason: str) -> dict:
    return {
        "observable": sys.platform.startswith("win"),
        "possible_native_dialog_open": False,
        "foreground": {},
        "matches": [],
        "hint": reason,
    }


def _window_text(hwnd) -> str:
    buffer = create_unicode_buffer(512)
    windll.user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _class_name(hwnd) -> str:
    buffer = create_unicode_buffer(256)
    windll.user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def _is_visible(hwnd) -> bool:
    return bool(windll.user32.IsWindowVisible(hwnd))


def _looks_like_file_dialog(window: dict) -> bool:
    title = window.get("title", "")
    class_name = window.get("class_name", "")
    if class_name not in FILE_DIALOG_CLASSES:
        return False
    return any(keyword.lower() in title.lower() for keyword in FILE_DIALOG_TITLE_KEYWORDS)


def _enumerate_windows() -> list[dict]:
    windows = []

    @WINFUNCTYPE(BOOL, HWND, LPARAM)
    def callback(hwnd, _lparam):
        if not _is_visible(hwnd):
            return True
        title = _window_text(hwnd).strip()
        class_name = _class_name(hwnd).strip()
        if title or class_name:
            windows.append({"hwnd": int(hwnd), "title": title, "class_name": class_name})
        return True

    windll.user32.EnumWindows(callback, 0)
    return windows


def get_native_dialog_state() -> dict:
    """Detect likely Windows native file chooser dialogs.

    Playwright cannot inspect OS file pickers, so this uses Win32 foreground
    window metadata as a best-effort signal.
    """
    if not sys.platform.startswith("win"):
        return _empty_state("非 Windows 平台，未启用原生系统弹窗检测。")

    try:
        foreground_hwnd = windll.user32.GetForegroundWindow()
        foreground = {
            "hwnd": int(foreground_hwnd),
            "title": _window_text(foreground_hwnd).strip(),
            "class_name": _class_name(foreground_hwnd).strip(),
        }
        windows = _enumerate_windows()
        matches = [window for window in windows if _looks_like_file_dialog(window)]
        foreground_match = _looks_like_file_dialog(foreground)
        if foreground_match and foreground not in matches:
            matches.insert(0, foreground)
        possible = foreground_match or bool(matches)
        return {
            "observable": True,
            "possible_native_dialog_open": possible,
            "foreground": foreground,
            "matches": matches[:5],
            "hint": "检测到疑似 Windows 原生文件选择弹窗。" if possible else "未检测到疑似 Windows 原生文件选择弹窗。",
        }
    except Exception as exc:
        return _empty_state(f"系统弹窗检测失败：{exc}")


def close_native_dialog_with_escape() -> bool:
    """Close likely native file dialog by sending Escape."""
    state = get_native_dialog_state()
    if not state.get("possible_native_dialog_open"):
        return False
    try:
        vk_escape = 0x1B
        key_event = 0x0001
        key_up = 0x0002
        windll.user32.keybd_event(vk_escape, 0, key_event, 0)
        windll.user32.keybd_event(vk_escape, 0, key_event | key_up, 0)
        return True
    except Exception:
        return False
