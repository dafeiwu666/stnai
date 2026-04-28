# 砂糖画图插件 使用文档

## 📖 概述

砂糖画图是一个基于 NovelAI 的 AI 绘图插件，支持文生图、图生图、氛围转移、角色保持等多种功能。

---

## 🎨 画图命令

### 基础命令 `/nai`

直接使用提示词绘图（所有参数必须使用 `key=value` 格式）：

```
/nai
tag=1girl, coffee shop, smile
```

#### 使用预设

```
/nai
s1=预设名
```

#### 使用多个预设

预设按优先级生效（直接参数 > s1 > s2 > ...）：

```
/nai
s1=基础预设
s2=风格预设
tag=额外的标签
```

#### 使用自定义参数

换行定义多个参数：

```
/nai
tag=1girl, coffee shop
model=nai-diffusion-4-5-curated
画面尺寸=竖图
```

#### 使用提示词包装器

可以使用前置/后置提示词来包装主提示词：

```
/nai
prepend_tag=best quality, masterpiece
tag=1girl, coffee shop
append_tag=solo, simple background
append_negative=lowres, bad anatomy
prepend_negative=extra limbs
```

最终生成的提示词：
- 正向：`best quality, masterpiece, 1girl, coffee shop, solo, simple background`
- 负面：`lowres, bad anatomy, extra limbs`

---

### AI 画图命令 `nai画图`

使用 AI 自动解析描述生成参数：

```
/nai画图
s1=预设名
ds=画一个在咖啡店微笑的女孩
```

- `s1`、`s2` 等：按优先级使用多个预设
- `ds`：自然语言描述，AI 会自动转换为绘图参数

#### 识图（可选）

`/nai画图` 和 `/nai自动画图` 支持“把你发送的图片作为参考”交给**高级参数模型**进行识图（多模态）。

- 使用方式：发送命令时带上图片即可（同一条消息内）。
- 前提条件：配置里开启 `llm.enable_vision=true`，并确保 `llm.advanced_arg_generation_provider` 指向一个支持多模态的模型/提供商。
- 调试日志：每次触发会输出形如 `[nai][vision] ...` 的日志；如果 provider 不支持多模态，会有 `falling back to text-only` 的 warning。

#### 引用消息作为参考

当发送 `/nai画图` 命令时引用他人的消息，被引用的消息内容会自动作为参考添加到描述中：

**示例：**
```
[引用某条消息：一个在海边的场景]
/nai画图
s1=猫娘
ds=穿着泳装，微笑着
```

**实际传给 AI 的内容：**
```
[猫娘预设内容]

参考：一个在海边的场景

穿着泳装，微笑着
```

---

### 自动画图

监听主 AI 回复，自动生成配图。

#### 查看状态
```
/nai自动画图
```

#### 开启自动画图
```
/nai自动画图开
s1=预设名
```

或设置多个预设：
```
/nai自动画图开
s1=基础预设
s2=风格预设
```

#### 关闭自动画图
```
/nai自动画图关
```

#### 工作原理

当用户提问时，主 AI 的回复会自动作为画图参考：

**示例：**
```
用户：今天天气怎么样？
AI：今天是晴天，万里无云，非常适合出游！
```

**实际传给画图AI的内容：**
```
[预设内容，如果有]

参考：今天是晴天，万里无云，非常适合出游！
```

> ⚠️ 自动画图的额度由开启者承担

---

## 📝 预设管理

### 查看预设列表
```
/nai预设列表
```

### 查看预设内容
```
/nai预设查看 预设名
```

### 添加预设（管理员）

基本格式：
```
/nai预设添加 预设名
```

**示例 1 - 简单预设：**
```
/nai预设添加 猫娘
tag=1girl, cat ears, cat tail, cute
negative=bad quality, lowres
size=竖图
```

**示例 2 - 完整预设：**
```
/nai预设添加 赛博朋克风格
tag=cyberpunk, neon lights, futuristic city, night
negative=nature, outdoor, daytime
model=nai-diffusion-4-5-curated
steps=28
scale=6
prepend_tag=best quality, masterpiece
append_negative=bad anatomy, bad hands
```

**示例 3 - 多角色预设：**
```
/nai预设添加 双人对话
tag=2girls, conversation
role=A2|1girl, long hair, smile
role=D2|1girl, short hair, serious
negative=bad quality
size=横图
```

### 删除预设（管理员）
```
/nai预设删除 预设名
```

---

## 💰 额度系统

### 每日签到
```
/nai签到
```

### 查询额度
```
/查询额度
```

---

## 🔧 管理员命令

### 黑名单管理
```
/nai黑名单添加 用户ID
/nai黑名单移除 用户ID
/nai黑名单列表
```

### 白名单管理
```
/nai白名单添加 用户ID
/nai白名单移除 用户ID
/nai白名单列表
```

### 额度管理
```
/nai查询用户 用户ID
/nai设置额度 用户ID 次数
/nai增加额度 用户ID 次数
```

---

## 🖼️ 图片输出方式

生成的图片会以**合并转发消息**的形式发送，可以避免图片在聊天列表中过于显眼。

---

## 🖼️ 图片引用功能

发送指令时附带的图片会按顺序加入引用列表。

### 图生图(i2i)
```
/nai 1girl
i2i=true

[图片]
```

### 氛围转移(vibe_transfer)
```
/nai 1girl
vibe_transfer=true
vibe_transfer_info_extract=0.8

[图片]
```

### 角色保持(character_keep)
```
/nai 1girl
character_keep=true

[图片]
```

---

## ⚙️ 支持的自定义参数

| 参数 | 别名 | 说明 |
|------|------|------|
| `tag` | 正向提示词 | 期望生成的图片内容 |
| `negative` `ne` | 反向提示词 | 不想出现的内容 |
| `prepend_tag` `a_tag` | 前置正向/前置正向提示词 | 添加到正向提示词最前方 |
| `append_tag` `b_tag` | 后置正向/后置正向提示词 | 添加到正向提示词最后方 |
| `prepend_negative` `a_ne` | 前置负面/前置负面提示词 | 添加到负面提示词最前方 |
| `append_negative` `b_ne` | 后置负面/后置负面提示词 | 添加到负面提示词最后方 |
| `model` | 模型 | 选择绘图模型 |
| `artist` | 画师/画师串 | 指定画师风格 |
| `size` | 画面尺寸 | 竖图`portrait`/横图`landscape`/方图`square` 或 WxH(白名单专用) |
| `seed` | 种子 | 固定随机种子 |
| `steps` | 采样步数 | 1-50，默认23 (28以上为白名单专用)|
| `scale` | 提示词引导值 | 默认5 |
| `cfg` | 缩放引导值 | 默认0 |
| `sampler` | 采样器 | 选择采样方法 |
| `noise_schedule` `n_s` | 噪声调度 | karras等 |
| `other` | 高级配置 | SMEA等设置 |
| `i2i` | 图生图 | 引用图片进行重绘 |
| `i2i_force` `i_f` | 重绘力度 | 0-1，默认0.6 |
| `vibe_transfer` `v_t` | 氛围转移 | 参考图片风格 |
| `vibe_transfer_info_extract` `v_t_i_e` | 氛围转移信息提取度 | 0-1 |
| `vibe_transfer_ref_strength` `v_t_r_s` | 氛围转移参考强度 | 0-1 |
| `role` | 角色/多角色 | 多角色控制 |
| `character_keep` `c_k` | 角色保持/ck | 保持角色特征 |
| `character_keep_vibe` `c_k_v` | 角色保持氛围 | true/false |
| `character_keep_strength` `c_k_s` | 角色保持强度 | 0-1 |

---

## 📋 可用模型

- `nai-diffusion-3` `nai3` - NAI3 标准模型
- `nai-diffusion-furry-3` `nai3_furry` - NAI3 Furry模型
- `nai-diffusion-4-full` `nai4_full` - NAI4 完整版
- `nai-diffusion-4-curated-preview` `nai4_c_p` - NAI4 精选预览版
- `nai-diffusion-4-5-curated` `nai4.5_c` - NAI4.5 精选版
- `nai-diffusion-4-5-full` `nai4.5_full` - NAI4.5 完整版

---

## 🎯 多角色控制(role)

格式：`role=位置|正向提示词|反向提示词`

位置网格（5x5）：
```
     A    B    C    D    E
  ┌────┬────┬────┬────┬────┐
1 │ A1 │ B1 │ C1 │ D1 │ E1 │
  ├────┼────┼────┼────┼────┤
2 │ A2 │ B2 │ C2 │ D2 │ E2 │
  ├────┼────┼────┼────┼────┤
3 │ A3 │ B3 │ C3 │ D3 │ E3 │
  ├────┼────┼────┼────┼────┤
4 │ A4 │ B4 │ C4 │ D4 │ E4 │
  ├────┼────┼────┼────┼────┤
5 │ A5 │ B5 │ C5 │ D5 │ E5 │
  └────┴────┴────┴────┴────┘
```

示例：
```
/nai 2girls
role=A2|1girl, cute, smile
role=D2|1girl, cool|bad anatomy
```
