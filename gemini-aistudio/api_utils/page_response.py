import asyncio
import logging
from typing import Callable

from playwright.async_api import Error as PlaywrightAsyncError
from playwright.async_api import Page as AsyncPage
from playwright.async_api import expect as expect_async

from config import RESPONSE_CONTAINER_SELECTOR, RESPONSE_TEXT_SELECTOR
from logging_utils import set_request_id


async def locate_response_elements(
    page: AsyncPage,
    req_id: str,
    logger: logging.Logger,
    check_client_disconnected: Callable[[str], bool],
) -> None:
    """定位响应容器与文本元素，包含超时与错误处理。"""
    set_request_id(req_id)
    logger.info("定位响应元素...")
    response_container = page.locator(RESPONSE_CONTAINER_SELECTOR).last
    response_element = response_container.locator(RESPONSE_TEXT_SELECTOR)

    try:
        await expect_async(response_container).to_be_attached(timeout=20000)
        check_client_disconnected("After Response Container Attached: ")
        await expect_async(response_element).to_be_attached(timeout=90000)
        logger.info("响应元素已定位。")
    except (PlaywrightAsyncError, asyncio.TimeoutError) as locate_err:
        from .error_utils import upstream_error

        raise upstream_error(req_id, f"定位AI Studio响应元素失败: {locate_err}")
    except asyncio.CancelledError:
        raise
    except Exception as locate_exc:
        from .error_utils import server_error

        raise server_error(req_id, f"定位响应元素时意外错误: {locate_exc}")
