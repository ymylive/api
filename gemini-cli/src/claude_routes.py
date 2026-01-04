"""
Claude API Routes - Handles Claude-compatible endpoints.
This module provides Claude-compatible endpoints that transform requests/responses
and delegate to the Google API client.
"""
import json
import uuid
import asyncio
import logging
from fastapi import APIRouter, Request, Response, Depends
from fastapi.responses import StreamingResponse

from .auth import authenticate_user
from .models import OpenAIChatCompletionRequest
from .claude_transformers import (
    claude_request_to_gemini,
    gemini_response_to_claude,
    gemini_stream_chunk_to_claude
)
from .openai_transformers import (
    openai_request_to_gemini,
    gemini_response_to_openai,
    gemini_stream_chunk_to_openai
)
from .google_api_client import send_gemini_request, build_gemini_payload_from_openai

router = APIRouter()


@router.post("/v1/messages")
async def claude_messages(
    request: Request,
    username: str = Depends(authenticate_user)
):
    """
    Claude-compatible messages endpoint.
    Transforms Claude requests to Gemini format, sends to Google API,
    and transforms responses back to Claude format.

    Claude API format:
    POST /v1/messages
    {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": "Hello, Claude"}
        ]
    }
    """

    try:
        # Parse the incoming Claude request
        body = await request.body()
        claude_request = json.loads(body)

        logging.info(f"Claude messages request: model={claude_request.get('model')}, stream={claude_request.get('stream', False)}")

        # Transform Claude request to OpenAI format first
        openai_format = claude_request_to_gemini(claude_request)

        # Create an OpenAI request object for transformation
        openai_request_obj = OpenAIChatCompletionRequest(
            model=openai_format['model'],
            messages=openai_format['messages'],
            stream=openai_format.get('stream', False),
            max_tokens=openai_format.get('max_tokens'),
            temperature=openai_format.get('temperature'),
            top_p=openai_format.get('top_p'),
            stop=openai_format.get('stop')
        )

        # Transform to Gemini format
        gemini_request_data = openai_request_to_gemini(openai_request_obj)

        # Build the payload for Google API
        gemini_payload = build_gemini_payload_from_openai(gemini_request_data)

    except Exception as e:
        logging.error(f"Error processing Claude request: {str(e)}", exc_info=True)
        return Response(
            content=json.dumps({
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": f"Request processing failed: {str(e)}"
                }
            }),
            status_code=400,
            media_type="application/json"
        )

    is_streaming = claude_request.get('stream', False)

    if is_streaming:
        # Handle streaming response
        async def claude_stream_generator():
            try:
                response = send_gemini_request(gemini_payload, is_streaming=True)

                if isinstance(response, StreamingResponse):
                    message_id = "msg_" + str(uuid.uuid4()).replace('-', '')[:29]
                    logging.info(f"Starting Claude streaming response: {message_id}")

                    # Send message_start event
                    start_event = {
                        "type": "message_start",
                        "message": {
                            "id": message_id,
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": claude_request.get('model', 'claude-3-5-sonnet-20241022'),
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

                    async for chunk in response.body_iterator:
                        if isinstance(chunk, bytes):
                            chunk = chunk.decode('utf-8', "ignore")

                        if chunk.startswith('data: '):
                            try:
                                # Parse the Gemini streaming chunk
                                chunk_data = chunk[6:]  # Remove 'data: ' prefix
                                gemini_chunk = json.loads(chunk_data)

                                # Check if this is an error chunk
                                if "error" in gemini_chunk:
                                    logging.error(f"Error in streaming response: {gemini_chunk['error']}")
                                    error_event = {
                                        "type": "error",
                                        "error": {
                                            "type": "api_error",
                                            "message": gemini_chunk["error"].get("message", "Unknown error")
                                        }
                                    }
                                    yield f"event: error\ndata: {json.dumps(error_event)}\n\n"
                                    return

                                # Transform to Claude format
                                claude_chunk = gemini_stream_chunk_to_claude(
                                    gemini_chunk,
                                    message_id
                                )

                                if claude_chunk:
                                    # Send content_block_delta event
                                    yield f"event: content_block_delta\ndata: {json.dumps(claude_chunk)}\n\n"

                                await asyncio.sleep(0)

                            except (json.JSONDecodeError, KeyError, UnicodeDecodeError) as e:
                                logging.warning(f"Failed to parse streaming chunk: {str(e)}")
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

                    logging.info(f"Completed Claude streaming response: {message_id}")
                else:
                    # Error case
                    error_msg = "Streaming request failed"
                    status_code = 500

                    if hasattr(response, 'status_code'):
                        status_code = response.status_code
                        error_msg += f" (status: {status_code})"

                    logging.error(f"Streaming request failed: {error_msg}")
                    error_event = {
                        "type": "error",
                        "error": {
                            "type": "api_error",
                            "message": error_msg
                        }
                    }
                    yield f"event: error\ndata: {json.dumps(error_event)}\n\n"
            except Exception as e:
                logging.error(f"Streaming error: {str(e)}")
                error_event = {
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": f"Streaming failed: {str(e)}"
                    }
                }
                yield f"event: error\ndata: {json.dumps(error_event)}\n\n"

        return StreamingResponse(
            claude_stream_generator(),
            media_type="text/event-stream"
        )

    else:
        # Handle non-streaming response
        try:
            response = send_gemini_request(gemini_payload, is_streaming=False)

            if isinstance(response, Response) and response.status_code != 200:
                # Handle error responses from Google API
                logging.error(f"Gemini API error: status={response.status_code}")

                try:
                    error_body = response.body
                    if isinstance(error_body, bytes):
                        error_body = error_body.decode('utf-8', "ignore")

                    error_data = json.loads(error_body)
                    if "error" in error_data:
                        # Transform Google API error to Claude format
                        claude_error = {
                            "type": "error",
                            "error": {
                                "type": "api_error",
                                "message": error_data["error"].get("message", f"API error: {response.status_code}")
                            }
                        }
                        return Response(
                            content=json.dumps(claude_error),
                            status_code=response.status_code,
                            media_type="application/json"
                        )
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

                # Fallback error response
                return Response(
                    content=json.dumps({
                        "type": "error",
                        "error": {
                            "type": "api_error",
                            "message": f"API error: {response.status_code}"
                        }
                    }),
                    status_code=response.status_code,
                    media_type="application/json"
                )

            try:
                # Parse Gemini response and transform to Claude format
                # First convert Gemini -> OpenAI, then OpenAI -> Claude
                gemini_response = json.loads(response.body)

                logging.debug(f"Gemini response keys: {gemini_response.keys()}")

                # Convert Gemini to OpenAI format first
                openai_response = gemini_response_to_openai(gemini_response, openai_request_obj.model)

                logging.debug(f"OpenAI response keys: {openai_response.keys()}")

                # Then convert OpenAI to Claude format
                claude_response = gemini_response_to_claude(
                    openai_response,
                    claude_request.get('model', 'claude-3-5-sonnet-20241022')
                )

                logging.info(f"Successfully processed Claude non-streaming response")
                return Response(
                    content=json.dumps(claude_response),
                    status_code=200,
                    media_type="application/json"
                )

            except Exception as e:
                logging.error(f"Failed to parse/transform response: {str(e)}", exc_info=True)
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
        except Exception as e:
            logging.error(f"Non-streaming request failed: {str(e)}")
            return Response(
                content=json.dumps({
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": f"Request failed: {str(e)}"
                    }
                }),
                status_code=500,
                media_type="application/json"
            )
