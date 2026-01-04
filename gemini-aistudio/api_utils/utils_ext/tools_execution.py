import asyncio
from typing import Any, Dict, List, Optional, Union

from api_utils.tools_registry import execute_tool_call, register_runtime_tools
from api_utils.utils_ext.string_utils import (
    extract_json_from_text,
    get_latest_user_text,
)
from models import Message


async def maybe_execute_tools(
    messages: List[Message],
    tools: Optional[List[Dict[str, Any]]],
    tool_choice: Optional[Union[str, Dict[str, Any]]],
) -> Optional[List[Dict[str, Any]]]:
    """
    基于 tools/tool_choice 的主动函数执行：
    - 若 tool_choice 指明函数名（字符串或 {type:'function', function:{name}}），则尝试执行该函数；
    - 若 tool_choice 为 'auto' 且仅提供一个工具，则执行该工具；
    - 参数来源：从最近一条用户消息的文本中尝试提取 JSON；若失败则使用空参数。
    - 返回 [{name, arguments, result}]；如无可执行则返回 None。
    """
    try:
        # Track runtime-declared tools和可选 MCP 端点
        mcp_ep: Optional[str] = None
        # support per-request MCP endpoint via request-level message or tool spec extension (if present later)
        # current: read from env only in registry when not provided
        register_runtime_tools(tools, mcp_ep)
        # 若已有工具结果消息（role='tool'），遵循对话式调用循环，由客户端驱动，服务器不主动再次执行
        for m in messages:
            if getattr(m, "role", None) == "tool":
                return None
        chosen_name: Optional[str] = None
        if isinstance(tool_choice, dict):
            fn_raw = tool_choice.get("function")
            if isinstance(fn_raw, dict):
                name_raw = fn_raw.get("name")
                if isinstance(name_raw, str):
                    chosen_name = name_raw
        elif isinstance(tool_choice, str):
            lc = tool_choice.lower()
            if lc in ("none", "no", "off"):
                return None
            if lc in ("auto", "required", "any"):
                if isinstance(tools, list) and len(tools) == 1:
                    first_tool = tools[0]
                    func_raw = first_tool.get("function", {})
                    if isinstance(func_raw, dict):
                        name_from_func = func_raw.get("name")
                        if isinstance(name_from_func, str):
                            chosen_name = name_from_func
                    if not chosen_name:
                        name_from_tool = first_tool.get("name")
                        if isinstance(name_from_tool, str):
                            chosen_name = name_from_tool
            else:
                chosen_name = tool_choice
        elif tool_choice is None:
            # 不主动执行
            return None

        if not chosen_name:
            return None

        user_text = get_latest_user_text(messages)
        args_json = extract_json_from_text(user_text) or "{}"
        result_str = await execute_tool_call(chosen_name, args_json)
        return [{"name": chosen_name, "arguments": args_json, "result": result_str}]
    except asyncio.CancelledError:
        raise
    except Exception:
        return None
