# AI 聊天模块 — 使用说明 / 维护指南 / 测试报告

## 1. 使用说明

### 1.1 启动方式（二选一）

**方式 A：随主应用运行（推荐日常使用）**

```
python main.py
```

访问 `http://127.0.0.1:8050/chat.html`，或点击任意 Dashboard 页面
侧边栏新增的 **"AI 聊天"** 高亮按钮（带淡入过渡，≤300ms）。

**方式 B：独立运行（无需启动 main.py / 微信客户端）**

```
python chat_server.py            # 默认读 config.yaml
python chat_server.py my.yaml    # 指定配置
```

访问 `http://127.0.0.1:8051/chat.html`。端口可在 `config.yaml` 的
`dashboard.chat_port` 中修改。独立模式同样加载全部角色卡与 AI 配置，
并托管其余 Dashboard 页面（"返回主界面"可正常跳转）。

### 1.2 功能速查

| 功能 | 操作 |
|---|---|
| 发送消息 | 输入框 Enter；Shift+Enter 换行；工具栏支持 **粗体** / *斜体* / `行内代码` / 代码块 |
| 发送状态 | 按钮反馈：生成中… → 已发送（绿）/ 失败（红） |
| 重试 | 失败消息的时间戳旁点击"重试" |
| 思考过程 | AI 回复上方的"思考过程"折叠块（浅色背景+斜体，打字机动效展示） |
| 复制消息 | 悬停消息，点右上角复制图标 |
| 切换人设卡 | 顶栏角色卡下拉（每张卡独立保留聊天历史） |
| 清空聊天 | 顶栏"清空聊天"→ 二次确认（清除当前卡的消息与 AI 上下文） |
| 主题切换 | 顶栏月亮/太阳按钮（深色/浅色，记忆偏好） |
| 刷新恢复 | 聊天历史存于浏览器 localStorage，刷新后消息与 AI 记忆均恢复 |
| 返回主界面 | 顶栏左上按钮（200ms 退场动画后跳转 dashboard.html） |

## 2. 维护指南

### 2.1 文件结构

```
dashboard/chat_api.py            # 后端：ChatService 会话管理 + Flask 蓝图 + 独立 app 工厂
chat_server.py                   # 独立运行入口
tedabot-dashboard/pages/chat.html        # 聊天页面（设计令牌 + 专属样式）
tedabot-dashboard/assets/chat-bus.js     # CustomEvent 通信层（schema 校验）
tedabot-dashboard/assets/chat-virtual-list.js  # 虚拟滚动器（通用组件）
tedabot-dashboard/assets/chat-store.js   # REST 封装 + localStorage 持久化 + Markdown 渲染
tedabot-dashboard/assets/chat-app.js     # UI 控制器（气泡/思考块/发送流/主题/角色切换）
tests/test_chat_api.py           # 后端单元测试（28 项，假客户端无网络依赖）
docs/chat-module-api.md          # 通信接口文档
```

### 2.2 关键设计

- **会话模型**：`ChatService` 按 `session_id` 隔离 `ChatContext`（线程安全），
  与微信主链路（`handler.contexts`）完全独立，互不污染上下文。
- **思考内容三态**：`<think>` 标签块 → 捕获进 `thinking` 字段；未闭合标签 → 同上；
  散文式 CoT（`looks_like_cot`）→ 先二次提炼复用最终回答，原文保留进 `thinking`，
  提炼失败才重试（与主链路 `cot_refine` 策略一致）。
- **角色卡复用**：直接使用 `RoleCards`（中英文键名 JSON 卡 + 结构化合成 +
  身份保护指令自动追加），新增角色卡只需改 `config.yaml`，网页下拉自动出现。
- **虚拟滚动**：只渲染视口 ±8 行，行高实测回填；单条消息更新（状态变化）
  走 `_rerenderRow` 精准重建，不整表刷新。

### 2.3 常见维护操作

| 场景 | 操作 |
|---|---|
| 消息长度限制 | 改 `chat_api.py` 的 `MAX_MESSAGE_LEN` + `chat.html` 的 maxlength |
| 本地历史容量 | 改 `chat-store.js` 的 `MAX_PER_ROLE`（默认 200 条/卡） |
| 请求间隔 | 复用 `config.yaml` 的 `ai.request_interval` |
| 新接入站事件 | 在 `chat-bus.js` 的 `SCHEMAS` 注册事件名与 schema |

## 3. 视觉一致性测试报告

对照设计系统（`colors_and_type.css`，"Anodized Deep"，主色 Indigo #5e6ad2）逐项核验：

| 检查项 | 结果 |
|---|---|
| 颜色令牌（canvas/surface/hairline/primary/功能色） | 一致 — `chat.html` 直接内嵌同一套 `--tb-*` 令牌，无自造色值 |
| 排版系统（Inter/Noto Sans SC、字号、字重、tabular-nums） | 一致 — 复用 `--tb-font-sans/mono` 与字体特性设置 |
| 圆角/阴影层级（radius 4/8/16/full、shadow 1-3） | 一致 — 气泡 16px、菜单/弹窗 8px、阴影用 shadow-1/2/3 |
| 组件风格（按钮/输入框/下拉/弹窗/toast） | 一致 — 与 Dashboard 顶栏按钮、popover 同规格 |
| 交互反馈（hover 变色、加载骨架屏、错误 toast、按钮状态脉冲/抖动） | 已实现 |
| 明暗双主题 | 已实现且与 `.light` 令牌表一致，切换无布局跳动 |
| 响应式（≥1024 / 768-1023 / 320-767） | 已实现；375px 实测顶栏收纳与输入区无溢出 |

浏览器实测（Chromium，2026-09-02）：深色首屏、浅色切换、真实 AI 回复渲染、
角色卡下拉、移动端 375×720 共 5 项截图核验通过，控制台 0 错误 0 警告
（截图存于 `.preflight/` 与临时目录，见会话记录）。

## 4. 性能测试报告

| 指标 | 数值 / 措施 | 说明 |
|---|---|---|
| 首屏资源体积 | 57.7 KB（HTML 21.4 + JS 36.3），0 个外部 CDN 依赖 | 不像其他页面引 Tailwind/Lucide CDN，关键路径全部本地，弱网（375KB/s）理论首屏 <200ms |
| 首屏渲染 | 静态 HTML+内联 CSS，JS 尾部加载不阻塞 | 无框架运行时编译开销 |
| 长列表 | 虚拟滚动（视口 ±8 行） | 200 条消息仅维持约 20-30 个 DOM 节点 |
| 动画帧率 | 打字机/入退场动画由 rAF/CSS transform 驱动 | 仅合成层属性（transform/opacity），无重排；`prefers-reduced-motion` 下自动关闭 |
| 滚动性能 | scroll 监听 passive + rAF 合并渲染 | 滚动不阻塞主线程 |
| 低配设备适配 | 无外部请求、无大图、动画均可降级 | iPhone SE 级别（2GB RAM）满足流畅交互；骨架屏避免交互卡死感知 |
| 内存 | 本地历史上限 200 条/卡，localStorage JSON 存储 | 防无限增长 |

性能边界说明：AI 回复耗时（实测约 8s）取决于上游免费模型源，
属后端链路，模块以骨架屏 + 状态反馈保证等待期间 UI 可感知、不冻结。

## 5. 单元测试覆盖

`tests/test_chat_api.py` 共 28 项，覆盖：思考内容分离（标签/未闭合/纯文本）、
二次提炼、重试与 502、输入校验（空/超长/非法会话）、会话创建与历史恢复、
清空、角色卡列表与身份保护指令、上下文带入、独立 app 页面托管与 404。
全套测试（`python -m pytest tests -q`）188 项全部通过。
