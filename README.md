
## 📖 简介

本插件为 AstrBot 带来了强大的 **NovelAI** 绘图能力。它不仅仅是一个简单的 API 调用工具，还集成了 **LLM 辅助生图**、**自动绘图**、**额度管理**、**队列系统**以及 NovelAI 的高级功能（如氛围转移 Vibe Transfer、角色保持 Character Keep）。

对普通用户：开箱即用；对管理员：可控并发、配额与黑白名单，适合群里长期跑。

## ✨ 特性

- **🎨 多种绘图模式**：
	- **基础模式**（`/nai`）：直接使用 prompt 标签绘图。
	- **智能模式**（`/nai画图`）：使用自然语言描述，由 LLM 自动生成专业标签与参数。
	- **自动模式**（`/nai自动画图`）：监听主 AI 回复，自动为聊天内容配图。
- **🖼️ 高级功能支持**：
	- **图生图 (i2i)**：支持引用图片进行重绘。
	- **氛围转移 (Vibe Transfer)**：提取参考图的风格/构图。
	- **角色保持 (Character Keep)**：保持角色特征一致性。
	- **多角色控制 (Role)**：指定不同区域角色外貌。
	- **视觉识图（可选）**：将用户图片交给多模态模型分析并生成参数（需开启 `llm.enable_vision`）。
- **⚙️ 系统能力**：
	- **队列系统**：并发控制，超出自动排队。
	- **额度系统**：每日签到、额度限制，防止滥用。
	- **黑白名单**：管理员可精细控制用户权限。
	- **预设管理**：保存常用参数组合，快速复用。

## 📦 安装

1. 确保你已经安装了 [AstrBot](https://github.com/Soulter/AstrBot)。
2. 将本插件文件夹放入 AstrBot 的 `data/plugins/` 目录下。
3. 安装依赖：
	 ```bash
	 pip install cookit[pydantic] jsonref
	 # 可选：如果需要将帮助文档渲染为精美图片
	 pip install pillowmd
	 ```
4. 重启 AstrBot。

## 🔧 配置

在 AstrBot 管理面板或插件配置文件中进行设置：

| 配置项 | 说明 | 必填 |
| :--- | :--- | :--- |
| `request.tokens` | **授权 Token 列表**。支持多个 Token 轮询。Novel 官方秘钥与绘画授权 Key 均可。 | ✅ 是 |
| `request.base_url` | 画图接口地址（默认：`https://std.loliyc.com`）。 | ❌ 否 |
| `request.max_concurrent` | 最大并发请求数（超出会排队）。 | ❌ 否 |
| `request.max_queue_size` | 最大排队长度（超出将拒绝新请求）。 | ❌ 否 |
| `llm.advanced_arg_generation_provider` | 用于将自然语言转换为绘图参数的 LLM 模型提供商。 | ❌ 否 |
| `llm.enable_vision` | 是否启用视觉输入（需要模型支持 Vision），用于参考图分析。 | ❌ 否 |

## 💻 指令列表

### 🎨 绘图指令

| 指令 | 示例/说明 |
| :--- | :--- |
| `/nai` | 基础绘图。<br>`/nai\ntag=1girl, white hair` |
| `/nai画图` | AI 辅助绘图。<br>`/nai画图\nds=画一个在海边玩耍的白发少女` |
| `/nai自动画图` | 查看当前自动画图状态或设置。 |
| `/nai自动画图开` | 开启自动画图（消耗开启者额度）。<br>`/nai自动画图开\ns1=预设名` |
| `/nai自动画图关` | 关闭自动画图。 |

### 🧩 预设与辅助

| 指令 | 说明 |
| :--- | :--- |
| `/nai预设列表` | 查看所有可用预设。 |
| `/nai预设查看` | 查看指定预设的详细内容。<br>`/nai预设查看 预设名` |
| `/nai队列` | 查看当前绘图队列状态（处理中/排队中）。 |
| `/nai签到` | 每日签到获取绘图额度。 |
| `/查询额度` | 查询当前剩余绘图次数。 |

### 👮 管理员指令

| 指令 | 说明 |
| :--- | :--- |
| `/nai预设添加` | 添加新预设：`/nai预设添加 预设名` 后跟多行参数。 |
| `/nai预设删除` | 删除指定预设。 |
| `/nai黑名单添加` | `/nai黑名单添加 [用户ID]` |
| `/nai白名单添加` | 白名单用户拥有更高权限（如更高步数/自定义尺寸等）。 |
| `/nai设置额度` | `/nai设置额度 [用户ID] [数量]` |

## 📝 高级参数示例

在 `/nai` 或 `/nai画图` 命令中，你可以使用以下高级参数（支持换行）：

**1. 氛围转移 (Vibe Transfer)**
```text
/nai 1girl
vibe_transfer=true
vibe_transfer_info_extract=0.8
[附带一张图片]
```

**2. 角色保持 (Character Keep)**
```text
/nai 1girl
character_keep=true
[附带一张图片]
```

**3. 多角色控制**
```text
/nai 2girls
role=A2|1girl, pink hair|bad quality
role=D2|1girl, blue hair|bad quality
```
*(位置网格：A-E为横向，1-5为纵向，C3为中心)*

## 📚 详细文档

查看 [docs/USAGE.md](docs/USAGE.md) 获取完整使用说明。

## 📄 License

MIT License
