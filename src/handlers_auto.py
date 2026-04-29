"""Auto-draw command handlers and hook logic."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from astrbot import logger
from astrbot.api.message_components import Image, Node, Nodes
from astrbot.api.provider import LLMResponse

from .data_source import wrapped_generate
from .llm import llm_generate_advanced_req
from .llm_utils import format_readable_error


async def handle_auto_draw_off(plugin, event) -> AsyncIterator:
    plugin.auto_draw_info[event.unified_msg_origin] = None
    yield event.plain_result("❌ 自动画图已关闭")


async def handle_auto_draw_on(plugin, event) -> AsyncIterator:
    umo = event.unified_msg_origin
    user_id = plugin._get_user_id(event)

    if plugin.user_manager.is_blacklisted(user_id):
        yield event.plain_result("你已被加入黑名单，无法开启自动画图")
        return

    raw_input = event.message_str.removeprefix("nai自动画图开").strip()
    preset_names, _ = plugin._parse_presets_from_params(raw_input)
    preset_names = plugin._apply_default_preset_to_names(preset_names)

    for preset_name in preset_names:
        preset = plugin.preset_manager.get_preset(preset_name)
        if preset is None:
            yield event.plain_result(f"预设 {preset_name} 不存在，使用 nai预设列表 查看可用预设")
            return

    plugin.auto_draw_info[umo] = {
        "enabled": True,
        "presets": preset_names,
        "opener_user_id": user_id,
    }

    if preset_names:
        preset_str = ", ".join(f"#{name}" for name in preset_names)
        yield event.plain_result(
            f"✅ 自动画图已开启\n"
            f"使用预设：{preset_str}\n"
            f"主 AI 的回复将与预设内容结合后生成图片\n"
            f"⚠️ 后续触发的画图将消耗你的额度"
        )
    else:
        yield event.plain_result(
            "✅ 自动画图已开启\n"
            "主 AI 的回复将被自动分析生成图片\n"
            "⚠️ 后续触发的画图将消耗你的额度"
        )


async def handle_auto_draw(plugin, event) -> AsyncIterator:
    umo = event.unified_msg_origin
    user_id = plugin._get_user_id(event)
    raw_input = event.message_str.removeprefix("nai自动画图").strip()

    if raw_input:
        if plugin.user_manager.is_blacklisted(user_id):
            yield event.plain_result("你已被加入黑名单，无法开启自动画图")
            return

        preset_names, _ = plugin._parse_presets_from_params(raw_input)
        preset_names = plugin._apply_default_preset_to_names(preset_names)
        if not preset_names:
            yield event.plain_result("请使用键值对格式设置预设，例如：\nnai自动画图\ns1=猫娘")
            return

        for preset_name in preset_names:
            preset = plugin.preset_manager.get_preset(preset_name)
            if preset is None:
                yield event.plain_result(f"预设 {preset_name} 不存在，使用 nai预设列表 查看可用预设")
                return

        plugin.auto_draw_info[umo] = {
            "enabled": True,
            "presets": preset_names,
            "opener_user_id": user_id,
        }

        preset_str = ", ".join(f"#{name}" for name in preset_names)
        yield event.plain_result(
            f"✅ 自动画图已开启\n"
            f"使用预设：{preset_str}\n"
            f"⚠️ 后续触发的画图将消耗你的额度"
        )
        return

    current = plugin.auto_draw_info.get(umo)
    if current is None:
        yield event.plain_result(
            "当前会话自动画图状态：❌ 关闭\n\n"
            "使用 nai自动画图开 来开启自动画图"
        )
        return

    presets = current.get("presets", [])
    opener_id = current.get("opener_user_id", "")
    opener_quota = plugin.user_manager.get_quota(opener_id)
    is_whitelisted = plugin.user_manager.is_whitelisted(opener_id)

    status_parts = ["当前会话自动画图状态：✅ 开启"]
    if presets:
        preset_str = ", ".join(f"#{name}" for name in presets)
        status_parts.append(f"使用预设：{preset_str}")
    else:
        status_parts.append("未使用预设")
    status_parts.append(f"开启者：{opener_id}")
    if is_whitelisted:
        status_parts.append("额度：无限（白名单）")
    else:
        status_parts.append(f"剩余额度：{opener_quota} 次")
    status_parts.append("\n使用 nai自动画图关 来关闭")

    yield event.plain_result("\n".join(status_parts))


async def handle_llm_response_auto_draw(plugin, event, resp: LLMResponse):
    umo = event.unified_msg_origin
    auto_info = plugin.auto_draw_info.get(umo)
    if auto_info is None:
        return

    presets = auto_info.get("presets", [])
    opener_user_id = auto_info.get("opener_user_id", "")

    if not plugin.config.request.tokens:
        return

    ai_response = resp.completion_text if hasattr(resp, "completion_text") else str(resp)
    if not ai_response or len(ai_response.strip()) < 10:
        return

    if plugin.user_manager.is_blacklisted(opener_user_id):
        logger.debug(f"[nai] Auto draw: opener {opener_user_id} is blacklisted, skipping")
        return

    is_whitelisted = plugin.user_manager.is_whitelisted(opener_user_id)
    quota_enabled = plugin.config.quota.enable_quota

    if quota_enabled and not is_whitelisted:
        can_use, reason = plugin.user_manager.can_use(opener_user_id)
        if not can_use:
            await event.send(
                event.plain_result(
                    "⚠️ 自动画图已暂停：开启者额度不足\n"
                    f"开启者 {opener_user_id} 的额度已用完，请签到获取额度后重新开启"
                )
            )
            plugin.auto_draw_info[umo] = None
            return

    preset_contents: list[str] = []
    for preset_name in presets:
        preset = plugin.preset_manager.get_preset(preset_name)
        if preset:
            preset_contents.append(preset.content)

    logger.debug(
        f"[nai] Auto draw: generating from response ({len(ai_response)} chars), "
        f"presets={presets}, opener={opener_user_id}"
    )

    asyncio.create_task(
        _auto_draw_generate(
            plugin,
            event,
            ai_response,
            preset_contents,
            opener_user_id,
            is_whitelisted,
        )
    )


async def _auto_draw_generate(
    plugin,
    event,
    ai_response: str,
    preset_contents: list[str],
    opener_user_id: str,
    is_whitelisted: bool,
):
    quota_enabled = plugin.config.quota.enable_quota
    umo = event.unified_msg_origin

    res = await plugin._queue.reserve(
        opener_user_id,
        is_whitelisted=is_whitelisted,
        max_queue_size=plugin.config.request.max_queue_size,
        max_concurrent=plugin.config.request.max_concurrent,
        consume_quota=(lambda: plugin.user_manager.consume_quota(opener_user_id))
        if quota_enabled and not is_whitelisted
        else None,
    )

    close_auto = False
    if not res.ok:
        if res.reason == "inflight":
            await event.send(event.plain_result("🎨 自动画图跳过：你的上一张还没画完呢~"))
        elif res.reason == "queue_full":
            await event.send(
                event.plain_result(
                    f"⚠️ 自动画图跳过：队列已满（{plugin.config.request.max_queue_size}）"
                )
            )
        elif res.reason == "quota":
            close_auto = True
            await event.send(
                event.plain_result(
                    "⚠️ 自动画图已暂停：开启者额度不足\n"
                    f"开启者 {opener_user_id} 的额度已用完，请签到获取额度后重新开启"
                )
            )
        if close_auto:
            plugin.auto_draw_info[umo] = None
        return

    if close_auto:
        plugin.auto_draw_info[umo] = None

    reserved_user = res.reserved_user
    queue_total = res.queue_total

    token = plugin._get_next_token()
    queue_status = f"（当前队列：{queue_total}）" if queue_total > 1 else ""

    try:
        ai_response_with_prefix = f"参考：{ai_response}"
        full_parts = list(reversed(preset_contents)) + [ai_response_with_prefix]
        vision_images = [x for x in event.message_obj.message if isinstance(x, Image)]
        full_instructions = "\n\n".join(full_parts)

        await event.send(event.plain_result(f"🎨 自动画图中...{queue_status}"))

        sem = plugin._ensure_semaphore()
        async with sem:
            await plugin._queue.mark_wait_finished(
                max_concurrent=plugin.config.request.max_concurrent
            )

            req = await llm_generate_advanced_req(
                instructions=f"画一张图\n{full_instructions}",
                config=plugin.config,
                ctx=plugin.context,
                event=event,
                vision_images=vision_images,
                skip_default_prompts=bool(preset_contents),
            )

            async def _do_generate():
                nonlocal token
                token = plugin._get_next_token()
                return await wrapped_generate(req, plugin.config, token=token)

            image = await plugin._run_with_retry(_do_generate)

        user_id = event.get_sender_id()
        user_name = event.get_sender_name()
        nodes = Nodes([
            Node(
                uin=user_id,
                name=user_name,
                content=[Image.fromBytes(image)],
            )
        ])
        await event.send(event.chain_result([nodes]))

    except asyncio.CancelledError:
        await plugin._queue.mark_wait_finished(
            max_concurrent=plugin.config.request.max_concurrent
        )
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Auto draw generation failed: {e}")
        await event.send(event.plain_result(f"🎨 自动画图失败：{format_readable_error(e)}"))
    finally:
        await plugin._queue.release(
            user_id=opener_user_id,
            reserved_user=reserved_user,
            max_concurrent=plugin.config.request.max_concurrent,
        )
