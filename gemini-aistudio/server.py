import os
from typing import (
    Any,
)

# 新增: 导入 load_dotenv
from dotenv import load_dotenv

# 新增: 在所有其他导入之前加载 .env 文件
load_dotenv()


# --- 导入集中状态模块 ---
from api_utils.server_state import state

# --- 向后兼容：通过 __getattr__ 将属性访问转发到 state 对象 ---
# 这允许现有代码继续使用 `import server; server.page_instance`
# 同时保持状态的集中管理

# 定义需要转发到 state 的属性名称
_STATE_ATTRS = {
    # Stream Queue
    "STREAM_QUEUE",
    "STREAM_PROCESS",
    # Playwright/Browser State
    "playwright_manager",
    "browser_instance",
    "page_instance",
    "is_playwright_ready",
    "is_browser_connected",
    "is_page_ready",
    "is_initializing",
    # Proxy Configuration
    "PLAYWRIGHT_PROXY_SETTINGS",
    # Model State
    "global_model_list_raw_json",
    "parsed_model_list",
    "model_list_fetch_event",
    "current_ai_studio_model_id",
    "model_switching_lock",
    "excluded_model_ids",
    # Request Processing State
    "request_queue",
    "processing_lock",
    "worker_task",
    # Parameter Cache
    "page_params_cache",
    "params_cache_lock",
    # Debug Logging State
    "console_logs",
    "network_log",
    # Logging
    "logger",
    "log_ws_manager",
    # Control Flags
    "should_exit",
}


def __getattr__(name: str) -> Any:
    """Forward attribute access to the state object for backward compatibility."""
    if name in _STATE_ATTRS:
        return getattr(state, name)
    raise AttributeError(f"module 'server' has no attribute '{name}'")


def __setattr__(name: str, value: Any) -> None:
    """Forward attribute assignment to the state object for backward compatibility."""
    if name in _STATE_ATTRS:
        setattr(state, name, value)
    else:
        # For non-state attributes, use the module's __dict__
        globals()[name] = value


def clear_debug_logs() -> None:
    """Clear console and network logs (called after each request)."""
    state.clear_debug_logs()


# --- 配置模块导入 ---

# --- models模块导入 ---

# --- logging_utils模块导入 ---

# --- browser_utils模块导入 ---

# --- api_utils模块导入 ---
from api_utils import (
    create_app,
)

# --- FastAPI App 定义 ---
app = create_app()

# --- Main Guard ---
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 2048))
    uvicorn.run(
        "server:app", host="0.0.0.0", port=port, log_level="info", access_log=False
    )
