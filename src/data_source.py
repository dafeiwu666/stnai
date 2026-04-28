import json
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any

from httpx import AsyncClient, Timeout

from astrbot import logger

from .config import Config
from .models import ForceCleanQueueResp, QueryKeyResp, Req, Resp

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    " AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)


def create_client_from_config(config: "Config"):
    return AsyncClient(
        base_url=config.request.base_url,
        headers={
            "User-Agent": USER_AGENT,
            "Origin": f"{config.request.base_url}",
            "Referer": f"{config.request.base_url}/",
        },
        timeout=Timeout(
            config.request.connect_timeout, read=config.request.read_timeout
        ),
    )


class GenerateError(Exception):
    def __init__(self, resp: Resp | None = None):
        super().__init__(resp)
        self.resp = resp

    def __str__(self) -> str:
        if self.resp:
            return f"status={self.resp.status}, data={self.resp.data}"
        return "No response received"



def _sanitize_for_log(obj: Any) -> Any:
    """递归处理对象，隐藏敏感信息但保留完整内容供排查。"""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k == "base64" and isinstance(v, str) and v:
                # 提取 MIME 类型和长度信息
                if v.startswith("data:"):
                    mime_end = v.find(";")
                    mime_type = v[5:mime_end] if mime_end > 5 else "unknown"
                    result[k] = f"<{mime_type}, {len(v)} chars>"
                else:
                    result[k] = f"<{len(v)} chars>"
            elif k == "token" and isinstance(v, str) and v:
                # 隐藏 token
                result[k] = f"{v[:8]}...{v[-4:]}" if len(v) > 12 else "***"
            else:
                result[k] = _sanitize_for_log(v)
        return result
    elif isinstance(obj, list):
        return [_sanitize_for_log(item) for item in obj]
    else:
        return obj


async def generate(cli: AsyncClient, req: Req) -> AsyncGenerator[Resp, Any]:
    data = req.model_dump(by_alias=True)
    
    # INFO 级别日志：打印完整请求结构（隐藏敏感和过长内容）
    sanitized_data = _sanitize_for_log(data)
    logger.info(f"[nai] 发送请求: {json.dumps(sanitized_data, ensure_ascii=False, indent=2)}")
    
    async with cli.stream("POST", "generate", json=data) as stream:
        stream.raise_for_status()
        async for data in stream.aiter_lines():
            if data:
                yield Resp.model_validate_json(data)



async def generate_wait(
    cli: AsyncClient,
    req: Req,
    progress_callback: Callable[[Resp], Any] | None = None,
) -> str:
    last_resp: Resp | None = None
    async for resp in generate(cli, req):
        last_resp = resp
        if progress_callback is not None:
            progress_callback(resp)
        if resp.url:
            return resp.url
    raise GenerateError(last_resp)


async def generate_fetch_image(
    cli: AsyncClient,
    req: Req,
    progress_callback: Callable[[Resp], Any] | None = None,
) -> bytes:
    url = await generate_wait(cli, req, progress_callback)
    resp = await cli.get(url)
    resp.raise_for_status()
    return resp.content


async def wrapped_generate(req: Req, config: Config, token: str = ""):
    """生成图片
    
    Args:
        req: 请求对象
        config: 配置
        token: 使用的 Token（如果为空则使用 req 中已有的 token）
    """
    if token:
        req.token = token
    start_time = time.time_ns()
    logger.debug(f"[nai] {start_time} -> start")
    async with create_client_from_config(config) as cli:
        prog_cb = lambda resp: logger.debug(  # noqa: E731
            f"[nai] {start_time} -> {resp.status}: {resp.url or resp.data}"
        )
        image = await generate_fetch_image(cli, req, prog_cb)
    consumed_time_s = (time.time_ns() - start_time) / 1e9
    logger.debug(f"[nai] {start_time} -> end ({consumed_time_s} s)")
    return image


async def query_key(cli: AsyncClient, key: str) -> QueryKeyResp:
    resp = await cli.post("api/api/getUser", json={"toUserId": key})
    resp.raise_for_status()
    return QueryKeyResp.model_validate_json(resp.content)


async def force_clean_queue(cli: AsyncClient, key: str) -> ForceCleanQueueResp:
    resp = await cli.post("api/api/soshelp", json={"key": key})
    resp.raise_for_status()
    return ForceCleanQueueResp.model_validate_json(resp.content)
