"""
Claude API 兼容路由 - 处理 Claude 格式的请求
"""
import asyncio
import json
import logging
import random
import time
import uuid
from asyncio import Future, Queue
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from config import RESPONSE_COMPLETION_TIMEOUT, get_environment_variable
from logging_utils import set_request_id, set_source

from ..dependencies import (
    get_logger,
    get_request_queue,
    get_server_state,
    get_worker_task,
)
from ..error_utils import service_unavailable

router = APIRouter()


@router.post("/v1/messages")
async def claude_messages(
    http_request: Request,
    logger: logging.Logger = Depends(get_logger),
    request_queue: "Queue[Any]" = Depends(get_request_queue),
    server_state: dict = Depends(get_server_state),
    worker_task: Any = Depends(get_worker_task),
):
    """
    Claude API 兼容的消息端点

    接受 Claude 格式的请求并转换为内部格式处理

    Claude 请求格式:
    {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": "Hello"}
        ],
        "system": "You are a helpful assistant",
        "stream": false
    }
    """
    req_id = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=7))

    # 设置日志上下文
    set_request_id(req_id)
    set_source("CLAUDE_API")

    try:
        # 解析 Claude 请求
        body = await http_request.body()
        claude_request = json.loads(body)

        is_streaming = claude_request.get('stream', False)
        logger.info(f"收到 Claude /v1/messages 请求 (Stream={is_streaming})")

        # 转换为内部格式
        from models import ChatCompletionRequest

        # 构建消息列表
        messages = []

        # 添加系统消息
        if 'system' in claude_request:
            system_content = claude_request['system']
            if isinstance(system_content, str):
                messages.append({
                    "role": "system",
                    "content": system_content
                })
            elif isinstance(system_content, list):
                system_text = ""
                for block in system_content:
                    if block.get('type') == 'text':
                        system_text += block.get('text', '')
                if system_text:
                    messages.append({
                        "role": "system",
                        "content": system_text
                    })

        # 转换 Claude 消息
        for msg in claude_request.get('messages', []):
            role = msg.get('role')
            content = msg.get('content')

            if isinstance(content, list):
                text_content = ""
                for block in content:
                    if block.get('type') == 'text':
                        text_content += block.get('text', '')
                messages.append({
                    "role": role,
                    "content": text_content
                })
            else:
                messages.append({
                    "role": role,
                    "content": content
                })

        # 映射 Claude 模型到 Gemini 模型
        claude_model = claude_request.get('model', 'claude-3-5-sonnet-20241022')
        gemini_model = _map_claude_model_to_gemini(claude_model)

        # 创建内部请求对象
        internal_request = ChatCompletionRequest(
            model=gemini_model,
            messages=messages,
            stream=is_streaming,
            max_tokens=claude_request.get('max_tokens'),
            temperature=claude_request.get('temperature'),
            top_p=claude_request.get('top_p'),
            stop=claude_request.get('stop_sequences')
        )

    except json.JSONDecodeError as e:
        logger.error(f"无效的 JSON 请求: {str(e)}")
        return _claude_error_response(
            "invalid_request_error",
            "Invalid JSON in request body",
            400
        )
    except Exception as e:
        logger.error(f"处理 Claude 请求时出错: {str(e)}")
        return _claude_error_response(
            "invalid_request_error",
            f"Request processing failed: {str(e)}",
            400
        )

    # 检查服务状态
    launch_mode = get_environment_variable("LAUNCH_MODE", "unknown")
    browser_page_critical = launch_mode != "direct_debug_no_browser"

    is_service_unavailable = (
        server_state["is_initializing"]
        or not server_state["is_playwright_ready"]
        or (
            browser_page_critical
            and (
                not server_state["is_page_ready"]
                or not server_state["is_browser_connected"]
            )
        )
        or not worker_task
        or worker_task.done()
    )

    if is_service_unavailable:
        return _claude_error_response(
            "overloaded_error",
            "Service is currently unavailable",
            503
        )

    # 将请求加入队列
    result_future = Future()
    queue_item = {
        "req_id": req_id,
        "request_data": internal_request,
        "http_request": http_request,
        "result_future": result_future,
        "enqueue_time": time.time(),
        "cancelled": False,
        "api_format": "claude",  # 标记为 Claude 格式
        "original_model": claude_model,  # 保存原始模型名
    }
    await request_queue.put(queue_item)

    try:
        timeout_seconds = RESPONSE_COMPLETION_TIMEOUT / 1000 + 120
        response = await asyncio.wait_for(result_future, timeout=timeout_seconds)

        # 如果是流式响应，需要转换为 Claude 格式
        if is_streaming and isinstance(response, StreamingResponse):
            return StreamingResponse(
                _transform_stream_to_claude(response, claude_model, req_id),
                media_type="text/event-stream"
            )

        # 非流式响应转换
        if hasattr(response, 'body'):
            try:
                response_data = json.loads(response.body)
                claude_response = _transform_response_to_claude(response_data, claude_model)
                return StreamingResponse(
                    iter([json.dumps(claude_response)]),
                    media_type="application/json"
                )
            except:
                pass

        return response

    except asyncio.TimeoutError:
        return _claude_error_response(
            "overloaded_error",
            f"Request processing timeout",
            504
        )
    except asyncio.CancelledError:
        logger.info(f"请求被客户端取消: {req_id}")
        raise
    except HTTPException as http_exc:
        if http_exc.status_code == 499:
            logger.info(f"客户端断开连接: {http_exc.detail}")
        else:
            logger.warning(f"HTTP异常: {http_exc.detail}")
        raise http_exc
    except Exception as e:
        logger.exception("等待Worker响应时出错")
        return _claude_error_response(
            "api_error",
            f"Internal server error: {str(e)}",
            500
        )


async def _transform_stream_to_claude(response: StreamingResponse, model: str, req_id: str):
    """将内部流式响应转换为 Claude 格式"""
    message_id = "msg_" + str(uuid.uuid4()).replace('-', '')[:29]

    # 发送 message_start 事件
    start_event = {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0
            }
        }
    }
    yield f"event: message_start\ndata: {json.dumps(start_event)}\n\n"

    # 发送 content_block_start 事件
    content_start_event = {
        "type": "content_block_start",
        "index": 0,
        "content_block": {
            "type": "text",
            "text": ""
        }
    }
    yield f"event: content_block_start\ndata: {json.dumps(content_start_event)}\n\n"

    # 处理流式数据
    try:
        async for chunk in response.body_iterator:
            if isinstance(chunk, bytes):
                chunk = chunk.decode('utf-8', "ignore")

            if chunk.startswith('data: '):
                try:
                    chunk_data = chunk[6:]
                    if chunk_data.strip() == '[DONE]':
                        continue

                    data = json.loads(chunk_data)

                    # 提取内容
                    if 'choices' in data and len(data['choices']) > 0:
                        delta = data['choices'][0].get('delta', {})
                        content = delta.get('content', '')

                        if content:
                            # 发送 content_block_delta 事件
                            delta_event = {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {
                                    "type": "text_delta",
                                    "text": content
                                }
                            }
                            yield f"event: content_block_delta\ndata: {json.dumps(delta_event)}\n\n"

                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception as e:
        logging.error(f"流式转换错误: {str(e)}")

    # 发送 content_block_stop 事件
    content_stop_event = {
        "type": "content_block_stop",
        "index": 0
    }
    yield f"event: content_block_stop\ndata: {json.dumps(content_stop_event)}\n\n"

    # 发送 message_delta 事件
    delta_event = {
        "type": "message_delta",
        "delta": {
            "stop_reason": "end_turn",
            "stop_sequence": None
        },
        "usage": {
            "output_tokens": 0
        }
    }
    yield f"event: message_delta\ndata: {json.dumps(delta_event)}\n\n"

    # 发送 message_stop 事件
    stop_event = {
        "type": "message_stop"
    }
    yield f"event: message_stop\ndata: {json.dumps(stop_event)}\n\n"


def _transform_response_to_claude(response_data: dict, model: str) -> dict:
    """将内部响应转换为 Claude 格式"""
    try:
        choices = response_data.get('choices', [])
        if not choices:
            raise ValueError("No choices in response")

        first_choice = choices[0]
        message = first_choice.get('message', {})
        content_text = message.get('content', '')

        # 映射 finish_reason
        finish_reason = first_choice.get('finish_reason', 'stop')
        stop_reason = 'end_turn' if finish_reason == 'stop' else 'max_tokens'

        # 提取使用情况
        usage = response_data.get('usage', {})
        input_tokens = usage.get('prompt_tokens', 0)
        output_tokens = usage.get('completion_tokens', 0)

        # 构建 Claude 响应
        claude_response = {
            "id": "msg_" + str(uuid.uuid4()).replace('-', '')[:29],
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": content_text
                }
            ],
            "model": model,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens
            }
        }

        return claude_response

    except Exception as e:
        logging.error(f"响应转换错误: {str(e)}")
        raise


def _claude_error_response(error_type: str, message: str, status_code: int):
    """创建 Claude 格式的错误响应"""
    from fastapi.responses import JSONResponse

    return JSONResponse(
        content={
            "type": "error",
            "error": {
                "type": error_type,
                "message": message
            }
        },
        status_code=status_code
    )


def _map_claude_model_to_gemini(claude_model: str) -> str:
    """将 Claude 模型名映射到 Gemini 模型名"""
    model_mapping = {
        # Claude 3.5 Sonnet -> Gemini 2.5 Pro
        'claude-3-5-sonnet-20241022': 'gemini-2.5-pro',
        'claude-3-5-sonnet-20240620': 'gemini-2.5-pro',
        'claude-3-5-sonnet': 'gemini-2.5-pro',

        # Claude 3.5 Haiku -> Gemini 2.5 Flash
        'claude-3-5-haiku-20241022': 'gemini-2.5-flash',
        'claude-3-5-haiku': 'gemini-2.5-flash',

        # Claude 3 Opus -> Gemini 2.5 Pro
        'claude-3-opus-20240229': 'gemini-2.5-pro',
        'claude-3-opus': 'gemini-2.5-pro',

        # Claude 3 Sonnet -> Gemini 2.0 Flash
        'claude-3-sonnet-20240229': 'gemini-2.0-flash',
        'claude-3-sonnet': 'gemini-2.0-flash',

        # Claude 3 Haiku -> Gemini 2.0 Flash Lite
        'claude-3-haiku-20240307': 'gemini-2.0-flash-lite',
        'claude-3-haiku': 'gemini-2.0-flash-lite',

        # Claude 2 -> Gemini Flash
        'claude-2.1': 'gemini-flash-latest',
        'claude-2.0': 'gemini-flash-latest',
        'claude-2': 'gemini-flash-latest',

        # Claude Instant -> Gemini Flash Lite
        'claude-instant-1.2': 'gemini-flash-lite-latest',
        'claude-instant-1': 'gemini-flash-lite-latest',
        'claude-instant': 'gemini-flash-lite-latest',
    }

    return model_mapping.get(claude_model, 'gemini-2.5-pro')
