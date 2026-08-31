# -*- coding: utf-8 -*-
"""Classify a fatal reply exception into a UI-facing :class:`ErrorInfo`.

Provider-agnostic: classification keys off HTTP status codes and exception
class names, walking the ``__cause__`` / ``__context__`` chain (AgentScope
often wraps a provider error one layer deep). No provider SDK is imported;
matching is by class name so httpx / openai / anthropic / aiohttp are all
covered whether or not they are installed."""
from typing import Iterator

from ...exception import DeveloperOrientedException
from ...types import ErrorType, ErrorInfo


_STATUS_MAP: dict[int, ErrorType] = {
    401: ErrorType.AUTHENTICATION,
    403: ErrorType.PERMISSION,
    429: ErrorType.RATE_LIMIT,
    400: ErrorType.INVALID_REQUEST,
    404: ErrorType.INVALID_REQUEST,
    422: ErrorType.INVALID_REQUEST,
}

# Connection/timeout failures identified by MRO class name, so we catch
# httpx.TransportError subclasses and provider connection wrappers without
# importing (and thus requiring) those packages.
_NETWORK_EXC_NAMES: frozenset[str] = frozenset(
    {
        "TransportError",  # httpx base for Connect/Read/Write/Pool errors
        "APIConnectionError",  # openai / anthropic
        "APITimeoutError",  # openai / anthropic
        "ClientConnectionError",  # aiohttp
        "ClientConnectorError",  # aiohttp
    },
)

_GENERIC_MESSAGE: dict[ErrorType, str] = {
    ErrorType.AUTHENTICATION: (
        "抱歉，我连不上对话模型——身份认证没有通过。"
        "请管理员检查模型 API Key / 凭据是否配置正确、是否过期。"
    ),
    ErrorType.PERMISSION: (
        "抱歉，当前凭据没有权限调用这个模型或接口。"
        "请管理员检查模型网关权限与路由配置。"
    ),
    ErrorType.RATE_LIMIT: (
        "抱歉，模型调用太频繁或额度用尽了。"
        "请稍等一会儿再发一条消息给我；若持续出现，请管理员检查配额。"
    ),
    ErrorType.INVALID_REQUEST: (
        "抱歉，发给模型的请求被拒绝了（参数或内容不符合要求）。"
        "你可以换种说法再试；若一直失败，请管理员检查模型与提示配置。"
    ),
    ErrorType.UPSTREAM: (
        "抱歉，上游模型服务返回了错误。"
        "请稍后再试；若持续失败，请管理员查看模型服务商状态与运行时日志。"
    ),
    ErrorType.CONNECTION: (
        "抱歉，我暂时连不上模型服务（网络异常或超时）。"
        "请确认网络畅通后重试；管理员可检查模型 Endpoint 是否可达。"
    ),
    ErrorType.INTERNAL: (
        "抱歉，我内部出了点故障，这次没能完成回复。"
        "请稍后再试；若一直如此，请管理员查看 agentscope-runtime 日志里的报错详情。"
    ),
    ErrorType.SETUP: (
        "抱歉，我还没准备好开始工作（会话初始化失败）。"
        "请确认数字员工已发布到运行时，或重启 agentscope-runtime 后再试；"
        "管理员可检查模型路由、技能投影与 Redis 是否正常。"
    ),
    ErrorType.UNKNOWN: (
        "抱歉，这次回复失败了，我还没定位到具体原因。"
        "请再试一次；若重复出现，请管理员查看运行时错误日志。"
    ),
}


def _causes(e: BaseException) -> Iterator[BaseException]:
    """Yield ``e`` then everything it wraps, guarding against cycles.

    Walks the ``__cause__`` / ``__context__`` chain and, for an
    ``ExceptionGroup``, its members: async transports run inside task
    groups, so a provider's 401 arrives as a leaf of a group whose own
    message is ``"unhandled errors in a TaskGroup"``. Without descending
    into it every such failure classifies as ``UNKNOWN``.
    """
    seen: set[int] = set()

    def walk(exc: BaseException | None) -> Iterator[BaseException]:
        while exc is not None and id(exc) not in seen:
            seen.add(id(exc))
            yield exc
            if isinstance(exc, BaseExceptionGroup):
                for sub in exc.exceptions:
                    yield from walk(sub)
            exc = exc.__cause__ or exc.__context__

    yield from walk(e)


def _extract_status(e: BaseException) -> int | None:
    """Pull an HTTP status code from anywhere in the exception chain.

    Different clients expose it differently: openai/anthropic promote
    ``status_code`` onto the exception, aiohttp uses ``status``, and raw
    httpx keeps it on ``response.status_code``.
    """
    for exc in _causes(e):
        for src in (
            getattr(exc, "status_code", None),
            getattr(exc, "status", None),
            getattr(getattr(exc, "response", None), "status_code", None),
        ):
            if isinstance(src, int):
                return src
    return None


def _is_network_error(e: BaseException) -> bool:
    """Detect a connection/timeout failure anywhere in the chain, by
    builtin type or by provider class name."""
    for exc in _causes(e):
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return True
        if any(c.__name__ in _NETWORK_EXC_NAMES for c in type(exc).__mro__):
            return True
    return False


def _classify_type(e: Exception) -> ErrorType:
    """Map an exception to an :class:`ErrorType` without importing any
    provider SDK."""
    status = _extract_status(e)
    if status is not None:
        if status in _STATUS_MAP:
            return _STATUS_MAP[status]
        if status >= 500:
            return ErrorType.UPSTREAM
        return ErrorType.INVALID_REQUEST

    if _is_network_error(e):
        return ErrorType.CONNECTION
    if isinstance(e, DeveloperOrientedException):
        return ErrorType.INTERNAL
    return ErrorType.UNKNOWN


def _safe_exc_detail(e: BaseException, limit: int = 240) -> str:
    """Short, UI-safe exception summary (no stack; truncated)."""
    text = f"{type(e).__name__}: {e}".replace("\n", " ").strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _classify_setup_error(e: Exception) -> ErrorInfo:
    """Classify a failure that happened before the agent replied.

    Same rules as :func:`_classify_error`, except that what it cannot
    place falls to ``SETUP`` rather than ``UNKNOWN``: at this point the
    one thing known for certain is that preparing the run failed, and
    saying so beats saying nothing.

    Args:
        e (`Exception`):
            The exception raised while setting the run up.

    Returns:
        `ErrorInfo`:
            The structured, UI-facing error description.
    """
    info = _classify_error(e)
    if info.type is ErrorType.UNKNOWN:
        detail = _safe_exc_detail(e)
        return ErrorInfo(
            type=ErrorType.SETUP,
            message=(
                f"{_GENERIC_MESSAGE[ErrorType.SETUP]}\n\n"
                f"（内部原因：{detail}）"
            ),
        )
    # Known types still benefit from a short hint when setup failed early.
    detail = _safe_exc_detail(e)
    return ErrorInfo(
        type=info.type,
        message=f"{info.message}\n\n（内部原因：{detail}）",
    )


def _classify_error(e: Exception) -> ErrorInfo:
    """Classify a fatal reply exception into a structured
    :class:`ErrorInfo` for the frontend.

    The ``message`` is a generic per-type string (not the raw exception
    text) so no provider-internal details or credentials leak to the UI;
    the frontend localizes off the stable ``type`` key.

    Args:
        e (`Exception`):
            The exception that terminated the reply.

    Returns:
        `ErrorInfo`:
            The structured, UI-facing error description.
    """
    error_type = _classify_type(e)
    return ErrorInfo(
        type=error_type,
        message=_GENERIC_MESSAGE[error_type],
    )
