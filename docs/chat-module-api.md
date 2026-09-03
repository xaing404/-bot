# AI 聊天模块 — 通信接口文档

> 模块位置：`dashboard/chat_api.py`（后端）、`tedabot-dashboard/assets/chat-*.js`（前端）
> 独立入口：`chat_server.py`；主应用集成：`dashboard/server.py` 自动挂载

## 1. REST API

所有接口挂载在 Dashboard 主服务（默认 `http://127.0.0.1:8050`）或独立聊天服务（默认 `http://127.0.0.1:8051`，端口由 `dashboard.chat_port` 配置）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/chat/roles` | 人设卡列表 |
| POST | `/api/chat/session` | 创建会话（可携带历史恢复 AI 上下文） |
| POST | `/api/chat/send` | 发送消息并生成回复 |
| GET  | `/api/chat/history?session_id=` | 查询会话历史 |
| POST | `/api/chat/clear` | 清空会话上下文 |

### 1.1 GET /api/chat/roles

```json
{
  "default": "xiaoye",
  "roles": [{"id": "assistant", "name": "通用助手"}, {"id": "xiaoye", "name": "小野"}]
}
```

### 1.2 POST /api/chat/session

请求：

```json
{
  "role": "xiaoye",                  // 可选，缺省用 roles.default；不存在的卡自动回退默认
  "user": "网页用户",                 // 可选，{{user}} 占位符替换值
  "history": [                       // 可选，页面刷新后恢复 AI 记忆
    {"role": "user", "content": "早", "ts": 1725260000.0},
    {"role": "assistant", "content": "早呀", "ts": 1725260001.0}
  ]
}
```

响应：`{"session_id": "6d67e42cd0544587", "role": "xiaoye", "role_name": "小野", "history": [...]}`

### 1.3 POST /api/chat/send

请求：`{"session_id": "<id>", "message": "你好"}`

- 校验：`message` 必须为非空字符串，≤4000 字；否则 `400`
- `session_id` 不存在：`404`
- AI 重试耗尽：`502`（ body: `{"status": "error", "error": "AI 生成失败: ..."}`）

成功响应：

```json
{
  "status": "ok",
  "reply": "正式回复内容",
  "thinking": "思考过程（可能为空）",
  "role": "xiaoye",
  "elapsed_ms": 8953
}
```

> **约束**：`thinking` 仅用于网页"思考过程"展示块；微信发送链路永远不消费该字段，
> 思考内容不会进入主应用回复（项目硬约束）。

### 1.4 GET /api/chat/history

响应：`{"session_id": "...", "history": [{"role", "content", "ts"}, ...]}`；会话不存在返回 `404`。

### 1.5 POST /api/chat/clear

请求：`{"session_id": "<id>"}`；响应 `{"status": "ok"}`。

## 2. 前端通信协议（CustomEvent）

前端模块间通过 `window.TedaChatBus`（`chat-bus.js`）通信，基于 `window` 上的 `CustomEvent`。
所有事件名以 `tb-chat:` 为前缀；载荷派发前经 schema 校验，非法载荷被丢弃并降级派发
`tb-chat:error {code:'E_PAYLOAD'}`（错误处理与状态监控机制）。

### 2.1 模块 → 宿主（出站）

| 事件 | detail 载荷 | 触发时机 |
|---|---|---|
| `tb-chat:ready` | `{}` | 模块初始化完成 |
| `tb-chat:message-sent` | `{id: string, text: string, ts: number}` | 用户消息已发出 |
| `tb-chat:reply-received` | `{id, text, thinking, ts}` | AI 回复已渲染 |
| `tb-chat:error` | `{code: string, message: string}` | 网络/AI/载荷错误 |
| `tb-chat:role-changed` | `{role, roleName}` | 人设卡切换完成 |
| `tb-chat:cleared` | `{role}` | 聊天已清空 |
| `tb-chat:theme-changed` | `{theme: 'dark'\|'light'}` | 主题切换 |

### 2.2 宿主 → 模块（入站）

| 事件 | detail 载荷 | 效果 |
|---|---|---|
| `tb-chat:send` | `{text: string}` | 模块代为发送一条消息 |
| `tb-chat:set-role` | `{role: string}` | 切换到指定人设卡（卡不存在则忽略） |
| `tb-chat:clear` | `{}` | 触发清空流程（含二次确认） |

### 2.3 使用示例

```js
// 宿主页面监听聊天模块
TedaChatBus.on('tb-chat:reply-received', (e) => console.log(e.detail.text));

// 宿主驱动聊天模块
TedaChatBus.emit('tb-chat:send', { text: '你好' });
// 或使用可编程 API（与事件等价）
window.TedaChat.send('你好');
window.TedaChat.setRole('xiang');
```

## 3. 安全与权限模型

1. **同源边界**：CustomEvent 仅在当前页面 window 内传播，不跨域、不持久化。
2. **最小暴露**：宿主只能通过 3 个入站事件驱动模块，无法直接触达会话对象、
   AI 配置与 API Key（Key 仅存在于后端，`/api/config` 已脱敏）。
3. **输入校验**：REST 层（消息非空/长度≤4000/会话存在）+ 事件层（schema 校验）双重防线。
4. **XSS 防护**：前端渲染前先 HTML 转义，再应用 Markdown 子集（粗体/斜体/代码）。
5. **思考内容隔离**：`thinking` 字段单向流入前端展示块，任何写回链路均不消费。

## 4. 后端集成（主应用内启用）

`DashboardServer` 已自动注册聊天蓝图（[server.py](../dashboard/server.py)）——
`python main.py` 启动后访问 `http://127.0.0.1:8050/chat.html`。
独立模式则运行 `python chat_server.py`，访问 `http://127.0.0.1:8051/chat.html`。
两种模式共用同一套角色卡与 AI 后端，无需额外配置。
