"""
Claude API Transformers - Convert between Claude and Gemini formats.
"""
import uuid
import logging
from typing import Dict, Any, List


def claude_request_to_gemini(claude_request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transform a Claude API request to Gemini format.

    Claude format:
    {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": "Hello"}
        ],
        "system": "You are a helpful assistant",
        "temperature": 1.0,
        "stream": false
    }

    Gemini format (via OpenAI compatibility):
    {
        "model": "gemini-2.5-pro",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Hello"}
        ],
        "max_tokens": 1024,
        "temperature": 1.0,
        "stream": false
    }
    """
    try:
        # Map Claude model to Gemini model
        claude_model = claude_request.get('model', 'claude-3-5-sonnet-20241022')
        gemini_model = _map_claude_model_to_gemini(claude_model)

        # Convert messages
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
                # Handle system message blocks
                system_text = ""
                for block in system_content:
                    if block.get('type') == 'text':
                        system_text += block.get('text', '')
                if system_text:
                    messages.append({
                        "role": "system",
                        "content": system_text
                    })

        # Convert Claude messages to OpenAI format
        for msg in claude_request.get('messages', []):
            role = msg.get('role')
            content = msg.get('content')

            # Handle content blocks
            if isinstance(content, list):
                # Claude uses content blocks
                text_content = ""
                for block in content:
                    if block.get('type') == 'text':
                        text_content += block.get('text', '')
                    elif block.get('type') == 'image':
                        # Handle image blocks if needed
                        logging.warning("Image blocks in Claude messages are not yet fully supported")

                # Only add message if there's actual content
                if text_content.strip():
                    messages.append({
                        "role": role,
                        "content": text_content
                    })
            else:
                # Simple string content
                if content and str(content).strip():
                    messages.append({
                        "role": role,
                        "content": str(content)
                    })

        # Ensure we have at least one message
        if not messages:
            raise ValueError("At least one message is required")

        # Build Gemini request
        gemini_request = {
            "model": gemini_model,
            "messages": messages,
            "stream": claude_request.get('stream', False)
        }

        # Map parameters
        if 'max_tokens' in claude_request:
            gemini_request['max_tokens'] = claude_request['max_tokens']

        if 'temperature' in claude_request:
            gemini_request['temperature'] = claude_request['temperature']

        if 'top_p' in claude_request:
            gemini_request['top_p'] = claude_request['top_p']

        if 'top_k' in claude_request:
            # Gemini supports top_k, OpenAI doesn't, but we can pass it through
            gemini_request['top_k'] = claude_request['top_k']

        if 'stop_sequences' in claude_request:
            gemini_request['stop'] = claude_request['stop_sequences']

        logging.debug(f"Transformed Claude request to Gemini format: {gemini_model}")
        return gemini_request

    except Exception as e:
        logging.error(f"Error transforming Claude request: {str(e)}")
        raise


def gemini_response_to_claude(gemini_response: Dict[str, Any], model: str) -> Dict[str, Any]:
    """
    Transform a Gemini response to Claude format.

    Gemini response (OpenAI format):
    {
        "id": "chatcmpl-...",
        "object": "chat.completion",
        "created": 1234567890,
        "model": "gemini-2.5-pro",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Hello! How can I help you?"
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30
        }
    }

    Claude response:
    {
        "id": "msg_...",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": "Hello! How can I help you?"
            }
        ],
        "model": "claude-3-5-sonnet-20241022",
        "stop_reason": "end_turn",
        "stop_sequence": null,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20
        }
    }
    """
    try:
        # Extract content from Gemini response
        choices = gemini_response.get('choices', [])
        if not choices:
            raise ValueError("No choices in Gemini response")

        first_choice = choices[0]
        message = first_choice.get('message', {})
        content_text = message.get('content', '')

        # Map finish reason
        finish_reason = first_choice.get('finish_reason', 'stop')
        stop_reason = _map_finish_reason_to_claude(finish_reason)

        # Extract usage
        usage = gemini_response.get('usage', {})
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

        logging.debug(f"Transformed Gemini response to Claude format")
        return claude_response

    except Exception as e:
        logging.error(f"Error transforming Gemini response: {str(e)}")
        raise


def gemini_stream_chunk_to_claude(gemini_chunk: Dict[str, Any], message_id: str) -> Dict[str, Any]:
    """
    Transform a Gemini streaming chunk to Claude format.

    Gemini chunk (OpenAI format):
    {
        "id": "chatcmpl-...",
        "object": "chat.completion.chunk",
        "created": 1234567890,
        "model": "gemini-2.5-pro",
        "choices": [{
            "index": 0,
            "delta": {
                "content": "Hello"
            },
            "finish_reason": null
        }]
    }

    Claude chunk (content_block_delta event):
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {
            "type": "text_delta",
            "text": "Hello"
        }
    }
    """
    try:
        choices = gemini_chunk.get('choices', [])
        if not choices:
            return None

        first_choice = choices[0]
        delta = first_choice.get('delta', {})
        content = delta.get('content', '')

        if not content:
            return None

        # Build Claude chunk
        claude_chunk = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "text_delta",
                "text": content
            }
        }

        return claude_chunk

    except Exception as e:
        logging.error(f"Error transforming Gemini chunk: {str(e)}")
        return None


def _map_claude_model_to_gemini(claude_model: str) -> str:
    """
    Map Claude model names to Gemini model names.
    """
    # Map Claude models to appropriate Gemini models
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

    # Return mapped model or default to gemini-2.5-pro
    return model_mapping.get(claude_model, 'gemini-2.5-pro')


def _map_finish_reason_to_claude(finish_reason: str) -> str:
    """
    Map OpenAI finish reasons to Claude stop reasons.
    """
    reason_mapping = {
        'stop': 'end_turn',
        'length': 'max_tokens',
        'content_filter': 'stop_sequence',
        'function_call': 'end_turn',
        'tool_calls': 'end_turn',
    }

    return reason_mapping.get(finish_reason, 'end_turn')
