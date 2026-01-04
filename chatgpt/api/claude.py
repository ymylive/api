"""
Claude API compatibility for chat2api
Provides Claude Messages API endpoint that converts to ChatGPT format
"""
import json
import uuid
from typing import Dict, Any

from fastapi import Request, HTTPException, Security
from fastapi.responses import StreamingResponse, Response
from fastapi.security import HTTPAuthorizationCredentials
from starlette.background import BackgroundTask

from app import app, security_scheme
from chatgpt.ChatService import ChatService
from utils.Logger import logger
from utils.configs import api_prefix
from utils.retry import async_retry


def claude_to_openai_request(claude_request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert Claude API request to OpenAI format

    Claude format:
    {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "messages": [...],
        "system": "...",
        "stream": false
    }

    OpenAI format:
    {
        "model": "gpt-4o",
        "messages": [...],
        "stream": false
    }
    """
    # Map Claude model to ChatGPT model
    claude_model = claude_request.get('model', 'claude-3-5-sonnet-20241022')
    chatgpt_model = _map_claude_to_chatgpt_model(claude_model)

    # Build messages
    messages = []

    # Add system message if present
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

    # Convert Claude messages
    for msg in claude_request.get('messages', []):
        role = msg.get('role')
        content = msg.get('content')

        if isinstance(content, list):
            # Handle content blocks
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

    # Build OpenAI request
    openai_request = {
        "model": chatgpt_model,
        "messages": messages,
        "stream": claude_request.get('stream', False)
    }

    # Map parameters
    if 'max_tokens' in claude_request:
        openai_request['max_tokens'] = claude_request['max_tokens']

    if 'temperature' in claude_request:
        openai_request['temperature'] = claude_request['temperature']

    if 'top_p' in claude_request:
        openai_request['top_p'] = claude_request['top_p']

    if 'stop_sequences' in claude_request:
        openai_request['stop'] = claude_request['stop_sequences']

    return openai_request


def openai_to_claude_response(openai_response: Dict[str, Any], model: str) -> Dict[str, Any]:
    """
    Convert OpenAI response to Claude format

    OpenAI format:
    {
        "id": "chatcmpl-...",
        "choices": [{
            "message": {"role": "assistant", "content": "..."},
            "finish_reason": "stop"
        }],
        "usage": {...}
    }

    Claude format:
    {
        "id": "msg_...",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "..."}],
        "model": "claude-3-5-sonnet-20241022",
        "stop_reason": "end_turn",
        "usage": {...}
    }
    """
    try:
        choices = openai_response.get('choices', [])
        if not choices:
            raise ValueError("No choices in response")

        first_choice = choices[0]
        message = first_choice.get('message', {})
        content_text = message.get('content', '')

        # Map finish reason
        finish_reason = first_choice.get('finish_reason', 'stop')
        stop_reason = 'end_turn' if finish_reason == 'stop' else 'max_tokens'

        # Extract usage
        usage = openai_response.get('usage', {})
        input_tokens = usage.get('prompt_tokens', 0)
        output_tokens = usage.get('completion_tokens', 0)

        # Build Claude response
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
        logger.error(f"Error converting response to Claude format: {str(e)}")
        raise


async def openai_stream_to_claude_stream(openai_stream, model: str, message_id: str):
    """
    Convert OpenAI streaming response to Claude format
    """
    # Send message_start event
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

    # Send content_block_start event
    content_start_event = {
        "type": "content_block_start",
        "index": 0,
        "content_block": {
            "type": "text",
            "text": ""
        }
    }
    yield f"event: content_block_start\ndata: {json.dumps(content_start_event)}\n\n"

    # Process stream
    async for chunk in openai_stream:
        if isinstance(chunk, bytes):
            chunk = chunk.decode('utf-8')

        if chunk.startswith('data: '):
            try:
                chunk_data = chunk[6:].strip()
                if chunk_data == '[DONE]':
                    continue

                data = json.loads(chunk_data)

                # Extract content
                if 'choices' in data and len(data['choices']) > 0:
                    delta = data['choices'][0].get('delta', {})
                    content = delta.get('content', '')

                    if content:
                        # Send content_block_delta event
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

    # Send content_block_stop event
    content_stop_event = {
        "type": "content_block_stop",
        "index": 0
    }
    yield f"event: content_block_stop\ndata: {json.dumps(content_stop_event)}\n\n"

    # Send message_delta event
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

    # Send message_stop event
    stop_event = {
        "type": "message_stop"
    }
    yield f"event: message_stop\ndata: {json.dumps(stop_event)}\n\n"


def _map_claude_to_chatgpt_model(claude_model: str) -> str:
    """
    Map Claude model names to ChatGPT model names
    """
    model_mapping = {
        # Claude 3.5 Sonnet -> GPT-4o
        'claude-3-5-sonnet-20241022': 'gpt-4o',
        'claude-3-5-sonnet-20240620': 'gpt-4o',
        'claude-3-5-sonnet': 'gpt-4o',

        # Claude 3.5 Haiku -> GPT-4o-mini
        'claude-3-5-haiku-20241022': 'gpt-4o-mini',
        'claude-3-5-haiku': 'gpt-4o-mini',

        # Claude 3 Opus -> GPT-4o
        'claude-3-opus-20240229': 'gpt-4o',
        'claude-3-opus': 'gpt-4o',

        # Claude 3 Sonnet -> GPT-4o
        'claude-3-sonnet-20240229': 'gpt-4o',
        'claude-3-sonnet': 'gpt-4o',

        # Claude 3 Haiku -> GPT-4o-mini
        'claude-3-haiku-20240307': 'gpt-4o-mini',
        'claude-3-haiku': 'gpt-4o-mini',

        # Claude 2 -> GPT-4o-mini
        'claude-2.1': 'gpt-4o-mini',
        'claude-2.0': 'gpt-4o-mini',
        'claude-2': 'gpt-4o-mini',

        # Claude Instant -> GPT-4o-mini
        'claude-instant-1.2': 'gpt-4o-mini',
        'claude-instant-1': 'gpt-4o-mini',
        'claude-instant': 'gpt-4o-mini',
    }

    return model_mapping.get(claude_model, 'gpt-4o')


async def process_claude(request_data, req_token, original_model):
    """Process Claude request by converting to OpenAI format"""
    # Convert to OpenAI format
    openai_request = claude_to_openai_request(request_data)

    # Process with ChatService
    chat_service = ChatService(req_token)
    try:
        await chat_service.set_dynamic_data(openai_request)
        await chat_service.get_chat_requirements()
        await chat_service.prepare_send_conversation()
        res = await chat_service.send_conversation()
        return chat_service, res, original_model
    except HTTPException as e:
        await chat_service.close_client()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        await chat_service.close_client()
        logger.error(f"Server error, {str(e)}")
        raise HTTPException(status_code=500, detail="Server error")


@app.post(f"/{api_prefix}/v1/messages" if api_prefix else "/v1/messages")
async def claude_messages(request: Request, credentials: HTTPAuthorizationCredentials = Security(security_scheme)):
    """
    Claude Messages API endpoint

    Accepts Claude format requests and converts them to ChatGPT format
    """
    req_token = credentials.credentials

    try:
        request_data = await request.json()
    except Exception:
        return Response(
            content=json.dumps({
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Invalid JSON body"
                }
            }),
            status_code=400,
            media_type="application/json"
        )

    original_model = request_data.get('model', 'claude-3-5-sonnet-20241022')
    is_streaming = request_data.get('stream', False)

    logger.info(f"Claude API request: model={original_model}, stream={is_streaming}")

    try:
        chat_service, res, model = await async_retry(process_claude, request_data, req_token, original_model)

        if is_streaming:
            # Handle streaming response
            message_id = "msg_" + str(uuid.uuid4()).replace('-', '')[:29]

            async def claude_stream_wrapper():
                try:
                    async for chunk in openai_stream_to_claude_stream(res, model, message_id):
                        yield chunk
                finally:
                    await chat_service.close_client()

            return StreamingResponse(
                claude_stream_wrapper(),
                media_type="text/event-stream"
            )
        else:
            # Handle non-streaming response
            try:
                claude_response = openai_to_claude_response(res, model)
                background = BackgroundTask(chat_service.close_client)
                return Response(
                    content=json.dumps(claude_response),
                    media_type="application/json",
                    background=background
                )
            except Exception as e:
                await chat_service.close_client()
                logger.error(f"Error converting response: {str(e)}")
                return Response(
                    content=json.dumps({
                        "type": "error",
                        "error": {
                            "type": "api_error",
                            "message": f"Failed to process response: {str(e)}"
                        }
                    }),
                    status_code=500,
                    media_type="application/json"
                )

    except HTTPException as e:
        return Response(
            content=json.dumps({
                "type": "error",
                "error": {
                    "type": "api_error" if e.status_code == 500 else "invalid_request_error",
                    "message": str(e.detail)
                }
            }),
            status_code=e.status_code,
            media_type="application/json"
        )
    except Exception as e:
        logger.error(f"Server error: {str(e)}")
        return Response(
            content=json.dumps({
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": "Internal server error"
                }
            }),
            status_code=500,
            media_type="application/json"
        )
