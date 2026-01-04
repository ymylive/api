# --- browser_utils/operations.py ---
# 浏览器页面操作相关功能模块
# Refactored into browser_utils/operations_modules/

from browser_utils.operations_modules.errors import (
    detect_and_extract_page_error,
    save_error_snapshot,
)
from browser_utils.operations_modules.interactions import (
    _get_final_response_content,
    _wait_for_response_completion,
    get_raw_text_content,
    get_response_via_copy_button,
    get_response_via_edit_button,
)
from browser_utils.operations_modules.parsers import (
    _get_injected_models,
    _handle_model_list_response,
    _parse_userscript_models,
)

__all__ = [
    # Error handling
    "detect_and_extract_page_error",
    "save_error_snapshot",
    # Interactions
    "_get_final_response_content",
    "_wait_for_response_completion",
    "get_raw_text_content",
    "get_response_via_copy_button",
    "get_response_via_edit_button",
    # Parsers
    "_get_injected_models",
    "_handle_model_list_response",
    "_parse_userscript_models",
]
