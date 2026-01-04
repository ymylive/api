import asyncio
from asyncio import Event, Task
from logging import Logger
from typing import Any, Callable, Coroutine, Dict, Tuple

from fastapi import HTTPException, Request

from logging_utils import set_request_id
from models import ClientDisconnectedError


async def check_client_connection(req_id: str, http_request: Request) -> bool:
    """
    Checks if the client is still connected.
    Returns True if connected, False if disconnected.
    """
    try:
        if hasattr(http_request, "_receive"):
            try:
                # Use a very short timeout to check for disconnect message
                # _receive is a private Starlette/FastAPI method that returns a coroutine
                receive_obj = http_request  # type: ignore[misc]
                receive_coro: Coroutine[Any, Any, Dict[str, Any]] = (
                    receive_obj._receive()
                )  # type: ignore[misc]
                receive_task: Task[Dict[str, Any]] = asyncio.create_task(receive_coro)
                done, pending = await asyncio.wait([receive_task], timeout=0.01)

                if done:
                    message = receive_task.result()
                    if message.get("type") == "http.disconnect":
                        return False
                else:
                    # Cancel the task if it didn't complete immediately
                    receive_task.cancel()
                    try:
                        await receive_task
                    except asyncio.CancelledError:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception:
                # If checking fails, assume disconnected to be safe, or log and continue?
                # Usually if _receive fails it might mean connection issues.
                return False

        # Fallback to is_disconnected() if available (Starlette/FastAPI)
        if await http_request.is_disconnected():
            return False

        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        return False


async def setup_disconnect_monitoring(
    req_id: str, http_request: Request, result_future: asyncio.Future[Any]
) -> Tuple[Event, Task[None], Callable[[str], bool]]:
    """
    Sets up a background task to monitor client disconnection.
    Returns:
        - client_disconnected_event: Event set when disconnect is detected
        - disconnect_check_task: The background task
        - check_client_disconnected: Helper function to raise error if disconnected
    """
    import logging

    logger = logging.getLogger("AIStudioProxyServer")
    client_disconnected_event = Event()
    set_request_id(req_id)

    async def check_disconnect_periodically() -> None:
        while not client_disconnected_event.is_set():
            try:
                is_connected = await check_client_connection(req_id, http_request)
                if not is_connected:
                    logger.info(
                        "Active disconnect check detected client disconnection."
                    )
                    client_disconnected_event.set()
                    if not result_future.done():
                        result_future.set_exception(
                            HTTPException(
                                status_code=499,
                                detail=f"[{req_id}] Client closed request",
                            )
                        )
                    break

                await asyncio.sleep(0.3)
            except asyncio.CancelledError:
                # Task cancelled, exit gracefully
                break
            except Exception as e:
                logger.error(f"(Disco Check Task) Error: {e}")
                client_disconnected_event.set()
                if not result_future.done():
                    result_future.set_exception(
                        HTTPException(
                            status_code=500,
                            detail=f"[{req_id}] Internal disconnect checker error: {e}",
                        )
                    )
                break

    disconnect_check_task = asyncio.create_task(check_disconnect_periodically())

    def check_client_disconnected(stage: str = "") -> bool:
        if client_disconnected_event.is_set():
            logger.info(f"Client disconnected detected at stage: '{stage}'")
            raise ClientDisconnectedError(
                f"[{req_id}] Client disconnected at stage: {stage}"
            )
        return False

    return client_disconnected_event, disconnect_check_task, check_client_disconnected


async def enhanced_disconnect_monitor(
    req_id: str, http_request: Request, completion_event: Event, logger: Logger
) -> bool:
    """
    Enhanced disconnect monitor for streaming responses.
    Returns True if client disconnected early.
    """
    set_request_id(req_id)
    client_disconnected_early = False
    while not completion_event.is_set():
        try:
            is_connected = await check_client_connection(req_id, http_request)
            if not is_connected:
                logger.info(
                    "(Monitor) Client disconnected during streaming, triggering completion event."
                )
                client_disconnected_early = True
                if not completion_event.is_set():
                    completion_event.set()
                break
            await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"(Monitor) Enhanced disconnect checker error: {e}")
            break
    return client_disconnected_early


async def non_streaming_disconnect_monitor(
    req_id: str,
    http_request: Request,
    result_future: asyncio.Future[Any],
    logger: Logger,
) -> bool:
    """
    Disconnect monitor for non-streaming responses.
    Returns True if client disconnected early.
    """
    set_request_id(req_id)
    client_disconnected_early = False
    while not result_future.done():
        try:
            is_connected = await check_client_connection(req_id, http_request)
            if not is_connected:
                logger.info(
                    "(Monitor) Client disconnected during non-streaming processing."
                )
                client_disconnected_early = True
                if not result_future.done():
                    result_future.set_exception(
                        HTTPException(
                            status_code=499,
                            detail=f"[{req_id}] Client disconnected during processing",
                        )
                    )
                break
            await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"(Monitor) Non-streaming disconnect checker error: {e}")
            break
    return client_disconnected_early
