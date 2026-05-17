import asyncio
import uuid
import os
from asyncio import Semaphore
from pathlib import Path
from typing import Annotated

from cookit.pyd import model_with_model_config
from pydantic import BaseModel, ConfigDict, Field
from pydantic.dataclasses import dataclass
from typing_extensions import override

from astrbot import logger
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, MessageChain, filter as event_filter
from astrbot.api.provider import LLMResponse
from astrbot.api.message_components import Image, Node, Nodes, Plain, Reply
from astrbot.api.star import Context, Star, StarTools # 引入 StarTools
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .src.config import Config
from .src.data_source import GenerateError, wrapped_generate
from .src.llm import (
    ConfigNeededTool,
    ReturnToLLMError,
    format_readable_error,
    llm_generate_advanced_req,
    llm_generate_image,
)
from .src.models import Req
from .src.character_keep_store import CharacterKeepStore, extract_nai_tag
from .src.params import (
    parse_req,
    req_model_assembler,
    resolve_image,
)
from .src.user_manager import UserManager
from .src.preset_manager import PresetManager
from .src.queue_manager import get_shared_queue
from .src.handlers_nai import handle_cmd_nai, handle_nai_draw
from .src.handlers_auto import (
    handle_auto_draw,
    handle_auto_draw_off,
    handle_auto_draw_on,
    handle_llm_response_auto_draw,
)
from .src.handlers_cs import (
    handle_ccs,
    handle_cs,
    handle_dcs,
    handle_scs,
)
# 确保引入了 AutoDrawStoreManager
try:
    from .src.auto_draw_store import AutoDrawStoreManager
except ImportError:
    # 兼容处理：如果 src 下没有这个类，则使用空实现或根据第一个文件补全
    class AutoDrawStoreManager:
        def __init__(self, data_dir: Path): self.path = data_dir / "auto_draw_info.json"
        async def ato_runtime(self): return {} 
        async def asave_from_runtime(self, info): pass

COMMAND = "nai"
PLUGIN_NAME = "astrbot_plugin_ppnai" # 定义共享的插件名

# region help
# 帮助文档保持在自己的插件目录下
USAGE_MD_PATH = Path(__file__).parent / "docs" / "USAGE.md"

def load_usage_md() -> str:
    try:
        if USAGE_MD_PATH.exists():
            return USAGE_MD_PATH.read_text(encoding="utf-8")
        else:
            return "# 砂糖画图\n\n帮助文档暂不可用。"
    except Exception as e:
        return f"# 砂糖画图\n\n加载失败: {e}"
# endregion

WAITING_REPLIES = ["少女绘画中……", "在画了在画了", "请稍等..."]

# ... (STNaiGenerateImageArgs 和 STNaiGenerateImageTool 的定义保持不变) ...

class Plugin(Star):
    """使用指令 nai 查看详细帮助"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = Config.model_validate(config)
        
        # --- 路径处理逻辑 ---
        # 1. 共享持久化数据目录 (user_data, presets, cs, auto_draw)
        shared_data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        
        # 2. 本地资源/缓存目录 (prompts, cache)
        local_dir = Path(__file__).parent
        local_cache_dir = local_dir / "data" / "cache"
        local_cache_dir.mkdir(parents=True, exist_ok=True)
        
        # 初始化管理器（使用共享目录）
        self.user_manager = UserManager(shared_data_dir)
        self.preset_manager = PresetManager(shared_data_dir)

        # 角色保持（数据在共享目录，提示词模板在本地目录）
        cs_dir = shared_data_dir / "cs"
        cssaying_path = local_dir / "src" / "prompts" / "cssaying.txt"
        self.cs_store = CharacterKeepStore(cs_dir, cssaying_path)
        
        # 自动画图存储（使用共享目录）
        self._auto_draw_store = AutoDrawStoreManager(shared_data_dir)
        self.auto_draw_info: dict[str, dict | None] = {}
        
        # Token 轮询与队列
        self._token_index = 0
        self._queue = get_shared_queue()
        self.context.add_llm_tools(STNaiGenerateImageTool(config_init=self.config))

    @override
    async def initialize(self):
        # 初始化并发
        self._queue.ensure(self.config.request.max_concurrent)
        
        # 异步加载持久化数据
        try:
            # 加载自动画图状态
            self.auto_draw_info = await self._auto_draw_store.ato_runtime()
            # 预加载用户和预设（如果 UserManager/PresetManager 有 reload 异步方法）
            if hasattr(self.user_manager, 'reload'):
                await asyncio.to_thread(self.user_manager.reload)
            if hasattr(self.preset_manager, 'reload'):
                await asyncio.to_thread(self.preset_manager.reload)
        except Exception as e:
            logger.error(f"[nai] 数据预加载失败: {e}")

        logger.info(f"[nai] 已挂载共享数据目录: {StarTools.get_data_dir(PLUGIN_NAME)}")

    @override
    async def terminate(self):
        # 插件关闭前保存自动画图状态到共享目录
        try:
            await self._auto_draw_store.asave_from_runtime(self.auto_draw_info)
        except Exception as e:
            logger.error(f"[nai] 持久化数据保存失败: {e}")

    def generate_help(self, umo: str) -> str:
        return load_usage_md()
    
    async def _render_markdown_to_images(self, markdown_content: str) -> list[str]:
        """渲染图片，缓存留在自己插件的 data/cache 下"""
        try:
            import pillowmd
            style_path = Path("data/styles/夏日冲浪") # 此路径通常相对于运行根目录
            style = pillowmd.LoadMarkdownStyles(str(style_path)) if style_path.exists() else pillowmd.MdStyle()
            
            render_result = await style.AioRender(text=markdown_content, useImageUrl=True, autoPage=True)
            images = render_result.images if hasattr(render_result, 'images') else ([render_result] if not isinstance(render_result, list) else render_result)
            
            # 使用本地缓存目录
            cache_dir = Path(__file__).parent / "data" / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            saved_paths = []
            session_id = uuid.uuid4().hex[:8]
            for i, img in enumerate(images):
                image_path = cache_dir / f"help_{session_id}_{i}.png"
                img.save(str(image_path), format="PNG")
                saved_paths.append(str(image_path))
            return saved_paths
        except Exception as e:
            logger.warning(f"渲染失败: {e}")
            return []
    
    def _get_user_id(self, event: AstrMessageEvent) -> str:
        """从事件中获取用户ID"""
        return event.get_sender_id()
    
    def _check_permission(self, event: AstrMessageEvent) -> bool:
        """检查是否是管理员"""
        # 这里简单判断，可以根据 AstrBot 的实际权限系统调整
        is_admin = getattr(event, "is_admin", None)
        if callable(is_admin):
            try:
                return bool(is_admin())
            except Exception:
                return False
        if isinstance(is_admin, bool):
            return is_admin
        return False
    
    def _get_next_token(self) -> str:
        """轮询获取下一个可用的 Token"""
        tokens = self.config.request.tokens
        if not tokens:
            return ""
        token = tokens[self._token_index % len(tokens)]
        self._token_index = (self._token_index + 1) % len(tokens)
        return token

    def _apply_default_preset_to_names(self, preset_names: list[str]) -> list[str]:
        """若用户未显式指定 sN= 预设，则套用 defaults.default_preset 配置的默认预设。

        默认预设未配置或预设不存在时，保持原样并记录 warning（静默降级）。
        """
        if preset_names:
            return preset_names
        default_preset_name = (self.config.defaults.default_preset or "").strip()
        if not default_preset_name:
            return preset_names
        if self.preset_manager.get_preset(default_preset_name) is None:
            logger.warning(
                f"[nai] defaults.default_preset 配置的预设 #{default_preset_name} 不存在，已跳过"
            )
            return preset_names
        return [default_preset_name]

    async def _run_with_retry(self, func):
        """内部重试包装器（不外显）。

        func: 一个无参 async callable
        """
        retries = max(0, int(getattr(self.config.request, "retry_times", 0) or 0))
        wait_s = float(getattr(self.config.request, "retry_wait", 0.0) or 0.0)

        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return await func()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_exc = e
                if attempt >= retries:
                    raise
                if wait_s > 0:
                    await asyncio.sleep(wait_s)
        assert last_exc is not None
        raise last_exc
    
    def _get_queue_status(self) -> str:
        """获取当前队列状态字符串"""
        queue_total = self._queue.queue_status()
        if queue_total > 1:
            return f"（当前队列：{queue_total}）"
        return ""

    def _ensure_semaphore(self) -> Semaphore:
        """确保并发信号量已初始化（兼容极端情况下 initialize 尚未执行）"""
        sem, _ = self._queue.ensure(self.config.request.max_concurrent)
        return sem

    def _get_reply_text(self, event: AstrMessageEvent) -> str:
        """获取引用消息的文本内容"""
        try:
            # 检查消息链中是否有Reply组件
            for component in event.message_obj.message:
                if isinstance(component, Reply):
                    # Reply组件包含被引用消息的信息
                    # 尝试获取Reply组件的文本属性
                    if hasattr(component, 'text') and component.text:
                        return component.text
                    
                    # 如果Reply有content属性
                    if hasattr(component, 'content') and component.content:
                        return str(component.content)
                    
                    # 如果有message属性（某些实现）
                    if hasattr(component, 'message'):
                        msg = component.message
                        if isinstance(msg, str):
                            return msg
                        elif hasattr(msg, 'get_plain_text'):
                            return msg.get_plain_text()
                    
                    # 尝试从event的原始消息中获取
                    if hasattr(event.message_obj, 'reply') and event.message_obj.reply:
                        reply_msg = event.message_obj.reply
                        if hasattr(reply_msg, 'message') and isinstance(reply_msg.message, str):
                            return reply_msg.message
                        elif hasattr(reply_msg, 'text') and isinstance(reply_msg.text, str):
                            return reply_msg.text
                    
                    return ""
            
            return ""
        except Exception as e:
            logger.debug(f"获取引用消息失败: {e}")
            return ""

    async def _parse_args(
        self,
        event: AstrMessageEvent,
        is_whitelisted: bool = False,
    ) -> tuple[Req, int] | None:
        """解析命令参数，支持多预设
        
        预设格式：s1=xxx, s2=xxx, ...
        优先级：直接参数 > s1 > s2 > ...
        tag 和 negative 是累加，其他参数是覆盖
        """
        raw_params = event.message_str.removeprefix(COMMAND).strip()
        if not raw_params:
            return None
        
        # 解析所有参数行
        lines = raw_params.split('\n')
        direct_params: list[tuple[str, str]] = []  # 直接参数
        preset_params_list: list[list[tuple[str, str]]] = []  # 按预设编号排序的预设参数
        preset_numbers: list[int] = []  # 预设编号列表
        cs_entries: dict[int, str] = {}
        
        import re
        preset_pattern = re.compile(r'^s(\d+)$')
        cs_pattern = re.compile(r'^cs(\d+)$')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                
                if key == "cs":
                    cs_num = 1
                else:
                    cs_match = cs_pattern.match(key)
                    cs_num = int(cs_match.group(1)) if cs_match else 0

                if cs_num:
                    existing = cs_entries.get(cs_num)
                    if existing and existing != value:
                        raise ValueError(f"cs{cs_num} 重复且不一致")
                    cs_entries[cs_num] = value
                    continue

                # 检查是否是预设参数
                if key == "s":
                    preset_num = 1
                else:
                    match = preset_pattern.match(key)
                    preset_num = int(match.group(1)) if match else 0

                if preset_num:
                    preset = self.preset_manager.get_preset(value)
                    if preset is None:
                        raise ValueError(f"预设 {value} 不存在，使用 nai预设列表 查看可用预设")
                    
                    # 解析预设内容
                    preset_lines = preset.content.split('\n')
                    preset_params: list[tuple[str, str]] = []
                    for pl in preset_lines:
                        pl = pl.strip()
                        if not pl:
                            continue
                        if '=' in pl:
                            pk, pv = pl.split('=', 1)
                            if pk.strip() == "cs":
                                continue
                            preset_params.append((pk.strip(), pv.strip()))
                        else:
                            # 没有 = 号的行视为 tag
                            preset_params.append(('tag', pl))
                    
                    preset_numbers.append(preset_num)
                    preset_params_list.append(preset_params)
                else:
                    direct_params.append((key, value))
            else:
                # 强制键值对格式，不接受无等号的行
                raise ValueError(f"参数格式错误：'{line}'，请使用键值对格式，例如：tag=xxx")

        # 用户未显式指定 sN=，且 defaults.default_preset 配置了存在的预设时，自动套用
        if not preset_numbers:
            default_preset_name = (self.config.defaults.default_preset or "").strip()
            if default_preset_name:
                preset = self.preset_manager.get_preset(default_preset_name)
                if preset is None:
                    logger.warning(
                        f"[nai] defaults.default_preset 配置的预设 #{default_preset_name} 不存在，已跳过"
                    )
                else:
                    preset_params: list[tuple[str, str]] = []
                    for pl in preset.content.split('\n'):
                        pl = pl.strip()
                        if not pl:
                            continue
                        if '=' in pl:
                            pk, pv = pl.split('=', 1)
                            if pk.strip() == "cs":
                                continue
                            preset_params.append((pk.strip(), pv.strip()))
                        else:
                            preset_params.append(('tag', pl))
                    preset_numbers.append(1)
                    preset_params_list.append(preset_params)

        # 按预设编号排序（1, 2, 3, ...）
        sorted_presets = sorted(zip(preset_numbers, preset_params_list), key=lambda x: x[0])
        
        # 合并参数
        # - tag 和 negative 是累加的（按优先级顺序）
        # - prepend_* 是累加的（高优先级在前）
        # - append_* 是累加的（高优先级在后）
        # - 其他参数是覆盖的
        merged: dict[str, str] = {}
        tag_parts: list[str] = []
        negative_parts: list[str] = []
        prepend_tag_parts: list[str] = []
        append_tag_parts: list[str] = []
        prepend_negative_parts: list[str] = []
        append_negative_parts: list[str] = []
        
        # 从最低优先级到最高：sN, ..., s2, s1, 直接参数
        all_params_groups = [p for _, p in reversed(sorted_presets)] + [direct_params]
        
        for params in all_params_groups:
            for key, value in params:
                if key == 'tag':
                    tag_parts.append(value)
                elif key in ('negative', '反向提示词'):
                    negative_parts.append(value)
                elif key in ('prepend_tag', '前置正向', '前置正向提示词'):
                    # 高优先级在前，所以后遍历的插入到列表开头
                    prepend_tag_parts.insert(0, value)
                elif key in ('append_tag', '后置正向', '后置正向提示词'):
                    # 高优先级在后，所以后遍历的追加到列表末尾
                    append_tag_parts.append(value)
                elif key in ('prepend_negative', '前置负面', '前置负面提示词'):
                    # 高优先级在前
                    prepend_negative_parts.insert(0, value)
                elif key in ('append_negative', '后置负面', '后置负面提示词'):
                    # 高优先级在后
                    append_negative_parts.append(value)
                else:
                    # 其他参数直接覆盖
                    merged[key] = value
        
        # 解析批量数量（不参与绘图参数传递）
        raw_count = merged.pop("n", "")
        if raw_count:
            if not raw_count.isdigit() or int(raw_count) < 1:
                raise ValueError("参数 n 必须是大于等于 1 的整数")
            batch_count = int(raw_count)
        else:
            batch_count = 1

        max_n = int(getattr(self.config.request, "max_n", 0) or 0)
        if max_n > 0 and batch_count > max_n:
            raise ValueError(f"参数 n 不能超过 {max_n}")

        if cs_entries:
            user_id = self._get_user_id(event)
            for cs_num, cs_name in sorted(cs_entries.items(), key=lambda x: x[0]):
                if not self.cs_store.exists(user_id, cs_name):
                    raise ValueError(f"角色保持 {cs_name} 不存在，请先使用 /cs 创建")
                cs_content = self.cs_store.read(user_id, cs_name)
                cs_tag = extract_nai_tag(cs_content)
                if not cs_tag:
                    raise ValueError("未找到 NovelAI tag style 外貌提示词内容")
                tag_parts.append(cs_tag)

        # 构建最终参数字符串
        final_params: list[str] = []
        
        # 合并 tag（按优先级顺序）
        if tag_parts:
            final_params.append(f'tag={", ".join(tag_parts)}')
        
        # 合并 prepend/append 提示词
        if prepend_tag_parts:
            final_params.append(f'prepend_tag={", ".join(prepend_tag_parts)}')
        if append_tag_parts:
            final_params.append(f'append_tag={", ".join(append_tag_parts)}')
        if prepend_negative_parts:
            final_params.append(f'prepend_negative={", ".join(prepend_negative_parts)}')
        if append_negative_parts:
            final_params.append(f'append_negative={", ".join(append_negative_parts)}')
        
        # 添加其他参数
        for key, value in merged.items():
            final_params.append(f'{key}={value}')
        
        # 合并 negative
        if negative_parts:
            final_params.append(f'negative={", ".join(negative_parts)}')
        
        final_raw = '\n'.join(final_params)
        
        req = await parse_req(final_raw, event.message_obj.message, self.config, is_whitelisted)
        return req, batch_count

    # ========== 签到命令 ==========
    
    @event_filter.command("nai签到")
    async def cmd_checkin(self, event: AstrMessageEvent):
        """每日签到获取画图额度"""
        user_id = self._get_user_id(event)
        success, gained, message = self.user_manager.checkin(user_id, self.config)
        yield event.plain_result(message)
    
    @event_filter.command("查询额度")
    async def cmd_query_quota(self, event: AstrMessageEvent):
        """查询自己的画图额度"""
        user_id = self._get_user_id(event)
        
        if self.user_manager.is_blacklisted(user_id):
            yield event.plain_result("你已被加入黑名单")
            return
        
        if self.user_manager.is_whitelisted(user_id):
            yield event.plain_result("你在白名单中，可无限使用画图功能")
            return
        
        if not self.config.quota.enable_quota:
            yield event.plain_result("当前未启用额度系统，可无限使用画图功能")
            return
        
        quota = self.user_manager.get_quota(user_id)
        yield event.plain_result(f"你当前剩余 {quota} 次画图额度")

    # ========== 管理员命令 ==========
    
    @event_filter.command("nai黑名单添加")
    async def cmd_add_blacklist(self, event: AstrMessageEvent):
        """将用户添加到黑名单（管理员）"""
        if not self._check_permission(event):
            yield event.plain_result("权限不足，仅管理员可使用此命令")
            return
        
        args = event.message_str.removeprefix("nai黑名单添加").strip()
        if not args:
            yield event.plain_result("请指定用户ID，例如：nai黑名单添加 123456")
            return
        
        user_id = args.split()[0]
        if self.user_manager.add_to_blacklist(user_id):
            yield event.plain_result(f"已将用户 {user_id} 添加到黑名单")
        else:
            yield event.plain_result(f"用户 {user_id} 已在黑名单中")
    
    @event_filter.command("nai黑名单移除")
    async def cmd_remove_blacklist(self, event: AstrMessageEvent):
        """将用户从黑名单移除（管理员）"""
        if not self._check_permission(event):
            yield event.plain_result("权限不足，仅管理员可使用此命令")
            return
        
        args = event.message_str.removeprefix("nai黑名单移除").strip()
        if not args:
            yield event.plain_result("请指定用户ID，例如：nai黑名单移除 123456")
            return
        
        user_id = args.split()[0]
        if self.user_manager.remove_from_blacklist(user_id):
            yield event.plain_result(f"已将用户 {user_id} 从黑名单移除")
        else:
            yield event.plain_result(f"用户 {user_id} 不在黑名单中")
    
    @event_filter.command("nai黑名单列表")
    async def cmd_list_blacklist(self, event: AstrMessageEvent):
        """查看黑名单列表（管理员）"""
        if not self._check_permission(event):
            yield event.plain_result("权限不足，仅管理员可使用此命令")
            return
        
        blacklist = self.user_manager.get_blacklist()
        if not blacklist:
            yield event.plain_result("黑名单为空")
        else:
            yield event.plain_result(f"黑名单用户：\n" + "\n".join(blacklist))
    
    @event_filter.command("nai白名单添加")
    async def cmd_add_whitelist(self, event: AstrMessageEvent):
        """将用户添加到白名单（管理员）"""
        if not self._check_permission(event):
            yield event.plain_result("权限不足，仅管理员可使用此命令")
            return
        
        args = event.message_str.removeprefix("nai白名单添加").strip()
        if not args:
            yield event.plain_result("请指定用户ID，例如：nai白名单添加 123456")
            return
        
        user_id = args.split()[0]
        if self.user_manager.add_to_whitelist(user_id):
            yield event.plain_result(f"已将用户 {user_id} 添加到白名单")
        else:
            yield event.plain_result(f"用户 {user_id} 已在白名单中")
    
    @event_filter.command("nai白名单移除")
    async def cmd_remove_whitelist(self, event: AstrMessageEvent):
        """将用户从白名单移除（管理员）"""
        if not self._check_permission(event):
            yield event.plain_result("权限不足，仅管理员可使用此命令")
            return
        
        args = event.message_str.removeprefix("nai白名单移除").strip()
        if not args:
            yield event.plain_result("请指定用户ID，例如：nai白名单移除 123456")
            return
        
        user_id = args.split()[0]
        if self.user_manager.remove_from_whitelist(user_id):
            yield event.plain_result(f"已将用户 {user_id} 从白名单移除")
        else:
            yield event.plain_result(f"用户 {user_id} 不在白名单中")
    
    @event_filter.command("nai白名单列表")
    async def cmd_list_whitelist(self, event: AstrMessageEvent):
        """查看白名单列表（管理员）"""
        if not self._check_permission(event):
            yield event.plain_result("权限不足，仅管理员可使用此命令")
            return
        
        whitelist = self.user_manager.get_whitelist()
        if not whitelist:
            yield event.plain_result("白名单为空")
        else:
            yield event.plain_result(f"白名单用户：\n" + "\n".join(whitelist))
    
    @event_filter.command("nai查询用户")
    async def cmd_admin_query_user(self, event: AstrMessageEvent):
        """查询用户额度（管理员）"""
        if not self._check_permission(event):
            yield event.plain_result("权限不足，仅管理员可使用此命令")
            return
        
        args = event.message_str.removeprefix("nai查询用户").strip()
        if not args:
            yield event.plain_result("请指定用户ID，例如：nai查询用户 123456")
            return
        
        user_id = args.split()[0]
        quota = self.user_manager.get_quota(user_id)
        
        status = ""
        if self.user_manager.is_blacklisted(user_id):
            status = "（黑名单）"
        elif self.user_manager.is_whitelisted(user_id):
            status = "（白名单）"
        
        yield event.plain_result(f"用户 {user_id}{status} 的额度：{quota} 次")
    
    @event_filter.command("nai设置额度")
    async def cmd_set_quota(self, event: AstrMessageEvent):
        """设置用户额度（管理员）"""
        if not self._check_permission(event):
            yield event.plain_result("权限不足，仅管理员可使用此命令")
            return
        
        args = event.message_str.removeprefix("nai设置额度").strip().split()
        if len(args) < 2:
            yield event.plain_result("请指定用户ID和额度，例如：nai设置额度 123456 100")
            return
        
        user_id = args[0]
        try:
            quota = int(args[1])
        except ValueError:
            yield event.plain_result("额度必须是整数")
            return
        
        self.user_manager.set_quota(user_id, quota)
        yield event.plain_result(f"已将用户 {user_id} 的额度设置为 {quota} 次")
    
    @event_filter.command("nai增加额度")
    async def cmd_add_quota(self, event: AstrMessageEvent):
        """增加用户额度（管理员）"""
        if not self._check_permission(event):
            yield event.plain_result("权限不足，仅管理员可使用此命令")
            return
        
        args = event.message_str.removeprefix("nai增加额度").strip().split()
        if len(args) < 2:
            yield event.plain_result("请指定用户ID和额度，例如：nai增加额度 123456 10")
            return
        
        user_id = args[0]
        try:
            amount = int(args[1])
        except ValueError:
            yield event.plain_result("额度必须是整数")
            return
        
        new_quota = self.user_manager.add_quota(user_id, amount)
        yield event.plain_result(f"已为用户 {user_id} 增加 {amount} 次额度，当前额度：{new_quota} 次")

    # ========== 预设命令 ==========
    
    @event_filter.command("nai预设列表")
    async def cmd_preset_list(self, event: AstrMessageEvent):
        """查看预设列表"""
        presets = self.preset_manager.list_presets()
        if not presets:
            yield event.plain_result("暂无预设，管理员可使用 nai预设添加 命令添加预设")
            return
        
        result = "📝 预设列表：\n" + "\n".join(f"• {title}" for title in presets)
        result += f"\n\n使用方式：\nnai\ns1=预设名"
        yield event.plain_result(result)
    
    @event_filter.command("nai预设查看")
    async def cmd_preset_view(self, event: AstrMessageEvent):
        """查看预设详细内容"""
        args = event.message_str.removeprefix("nai预设查看").strip()
        if not args:
            yield event.plain_result("请指定预设名称，例如：nai预设查看 猫娘")
            return
        
        title = args.split()[0]
        preset = self.preset_manager.get_preset(title)
        
        if preset is None:
            yield event.plain_result(f"预设 #{title} 不存在")
            return
        
        # 使用代码块包裹以防平台解析错误或截断
        yield event.plain_result(f"📝 预设 #{title}\n\n```\n{preset.content}\n```")
    
    @event_filter.command("nai预设添加")
    async def cmd_preset_add(self, event: AstrMessageEvent):
        """添加预设（管理员）"""
        if not self._check_permission(event):
            yield event.plain_result("权限不足，仅管理员可使用此命令")
            return
        
        # 解析：第一行是 "nai预设添加 标题"，后面的行是内容
        full_text = event.message_str
        lines = full_text.split('\n', 1)
        
        # 从第一行提取标题
        first_line = lines[0].removeprefix("nai预设添加").strip()
        if not first_line:
            yield event.plain_result(
                "请指定预设标题和内容，格式：\n"
                "nai预设添加 标题名\n"
                "这里是预设内容..."
            )
            return
        
        title = first_line
        
        # 获取内容（第二行开始）
        if len(lines) < 2 or not lines[1].strip():
            yield event.plain_result(
                f"请在标题后换行添加预设内容，格式：\n"
                f"nai预设添加 {title}\n"
                f"这里是预设内容..."
            )
            return
        
        content = lines[1]
        
        # 检查是否已存在
        if self.preset_manager.get_preset(title) is not None:
            yield event.plain_result(
                f"预设 #{title} 已存在，如需修改请先删除再添加"
            )
            return
        
        self.preset_manager.add_preset(title, content)
        yield event.plain_result(f"✅ 预设 #{title} 添加成功！\n\n预览：\n{content[:200]}{'...' if len(content) > 200 else ''}")
    
    @event_filter.command("nai预设删除")
    async def cmd_preset_delete(self, event: AstrMessageEvent):
        """删除预设（管理员）"""
        if not self._check_permission(event):
            yield event.plain_result("权限不足，仅管理员可使用此命令")
            return
        
        args = event.message_str.removeprefix("nai预设删除").strip()
        if not args:
            yield event.plain_result("请指定预设名称，例如：nai预设删除 猫娘")
            return
        
        title = args.split()[0]
        
        if self.preset_manager.delete_preset(title):
            yield event.plain_result(f"✅ 预设 #{title} 已删除")
        else:
            yield event.plain_result(f"预设 #{title} 不存在")

    # ========== 角色保持命令 ==========

    @event_filter.command("cs")
    async def cmd_cs(self, event: AstrMessageEvent):
        """角色保持：创建/列表"""
        async for result in handle_cs(self, event):
            yield result

    @event_filter.command("dcs")
    async def cmd_dcs(self, event: AstrMessageEvent):
        """角色保持删除"""
        async for result in handle_dcs(self, event):
            yield result

    @event_filter.command("scs")
    async def cmd_scs(self, event: AstrMessageEvent):
        """查询角色保持外貌提示词"""
        async for result in handle_scs(self, event):
            yield result

    @event_filter.command("ccs")
    async def cmd_ccs(self, event: AstrMessageEvent):
        """修改角色保持外貌提示词"""
        async for result in handle_ccs(self, event):
            yield result

    # ========== nai画图命令（直接调用插件AI） ==========
    
    def _parse_presets_from_params(
        self,
        raw_params: str,
    ) -> tuple[list[str], dict[str, str], list[str]]:
        """从参数中解析预设列表和其他参数
        
        Returns:
            (预设名列表按优先级排序, 其他参数字典)
        """
        import re
        preset_pattern = re.compile(r'^s(\d+)$')
        cs_pattern = re.compile(r'^cs(\d+)$')
        
        presets: list[tuple[int, str]] = []  # (编号, 预设名)
        cs_entries: dict[int, str] = {}
        other_params: dict[str, str] = {}
        
        for line in raw_params.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                
                if key == "s":
                    preset_num = 1
                else:
                    match = preset_pattern.match(key)
                    preset_num = int(match.group(1)) if match else 0

                if preset_num:
                    presets.append((preset_num, value))
                    continue

                if key == "cs":
                    cs_num = 1
                else:
                    cs_match = cs_pattern.match(key)
                    cs_num = int(cs_match.group(1)) if cs_match else 0

                if cs_num:
                    existing = cs_entries.get(cs_num)
                    if existing and existing != value:
                        raise ValueError(f"cs{cs_num} 重复且不一致")
                    cs_entries[cs_num] = value
                else:
                    other_params[key] = value
        
        # 按编号排序
        presets.sort(key=lambda x: x[0])
        cs_names = [name for _, name in sorted(cs_entries.items(), key=lambda x: x[0])]
        return [name for _, name in presets], other_params, cs_names
    
    @event_filter.command("nai画图")
    async def cmd_nai_draw(self, event: AstrMessageEvent):
        """使用插件 AI 直接画图
        
        格式：
        nai画图
        s1=xxx
        s2=xxx
        ds=画一个可爱的女孩
        """
        async for result in handle_nai_draw(self, event, WAITING_REPLIES):
            yield result

    # ========== 自动画图命令 ==========
    
    @event_filter.command("nai自动画图关")
    async def cmd_auto_draw_off(self, event: AstrMessageEvent):
        """关闭自动画图"""
        async for result in handle_auto_draw_off(self, event):
            yield result
    
    @event_filter.command("nai自动画图开")
    async def cmd_auto_draw_on(self, event: AstrMessageEvent):
        """开启自动画图
        
        格式：
        nai自动画图开
        s1=xxx
        s2=xxx
        """
        async for result in handle_auto_draw_on(self, event):
            yield result
    
    @event_filter.command("nai自动画图")
    async def cmd_auto_draw(self, event: AstrMessageEvent):
        """查看或设置自动画图状态
        
        不带参数：显示当前状态
        带参数：设置预设并开启
        
        格式：
        nai自动画图             → 显示状态
        nai自动画图             → 设置预设（同时开启）
        s1=xxx
        """
        async for result in handle_auto_draw(self, event):
            yield result

    # ========== 画图命令 ==========

    @event_filter.command(COMMAND)
    async def cmd_nai(self, event: AstrMessageEvent):
        """砂糖画图"""
        async for result in handle_cmd_nai(self, event, WAITING_REPLIES):
            yield result

    # ========== 自动画图钩子 ==========
    
    @event_filter.on_llm_response(priority=50)
    async def on_llm_response_auto_draw(self, event: AstrMessageEvent, resp: LLMResponse):
        """监听主 AI 回复，自动生成图片"""
        await handle_llm_response_auto_draw(self, event, resp)

