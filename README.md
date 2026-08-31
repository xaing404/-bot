# TedaBot — Python 微信群聊机器人（wxauto 方案）

基于 **wxauto**（Windows UI 自动化）+ **OpenAI 兼容接口** 的微信群聊/私聊自动回复机器人。
不受微信网页协议限制，任何可登录 PC 微信的账号均可使用。

## 功能特性

- 群聊/私聊消息监听，捕获内容、发送者、时间戳
- 关键词触发：精确匹配 + 模糊匹配（相似度可调）
- @机器人触发（wxauto4 消息属性 + 文本双重判定）
- **主动互动引擎**：无需关键词，根据群聊上下文按频率策略主动参与话题
  - 三种模式：`keyword` 只关键词回复 / `context` 只上下文主动 / `hybrid` 混合
  - 频率控制：消息数量阈值、最小间隔（防刷屏）、最大间隔（保活跃度）
  - 每日/每周发言上限、免重复相似度检查、敏感词过滤
- 角色卡系统：YAML 内联或 JSON 文件（`{{user}}` 自动替换为发送者昵称）
- AI 对话：OpenAI 兼容接口（OpenAI/DeepSeek/本地代理/ollama 等）
- 多轮上下文：按会话独立保存，超限自动淘汰
- 频率限制 + 指数退避重试，API 异常不影响整体运行
- 群聊白名单、敏感词过滤、滚动日志
- watchdog：主循环异常自动重启（连续崩溃 10 次停止）

## 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10/11 |
| Python | 3.9 - 3.12 |
| 微信 PC 客户端 | **4.x**（wxauto4 免费版最高支持 4.1.8.107） |
| 网络 | 可访问所选 AI 接口 |

> 注：微信已强制升级至 4.x，老的 wxauto（3.9.11 方案）已废弃，本项目现使用 wxauto4。

## 部署步骤

1. **安装依赖**

   ```powershell
   cd D:\21089\下载\Compressed\chatbot\teda_bot
   pip install -r requirements.txt
   ```

2. **修改配置** `config.yaml`：

   - `ai.base_url / api_key / model`：你的 AI 接口信息
   - `wechat.group_whitelist`：允许响应的群名（与微信显示**完全一致**）
   - `wechat.private_whitelist`：需要监听的私聊好友昵称
   - `trigger.keywords`：触发关键词
   - `roles`：角色卡（支持 JSON 文件，`{{user}}` 自动替换为发送者昵称）
   - `bot.name` 可留空：会自动读取你的微信昵称用于 @识别

3. **启动**

   ```powershell
   python main.py
   ```

4. **强烈建议：双击白名单群聊，打开独立聊天窗口**
   - 有独立窗口时机器人轮询该窗口，不干扰主窗口正常使用
   - 没有独立窗口时，机器人会轮流切换主窗口到各会话拉取消息，
     此期间请不要手动操作微信主窗口

## 配置项速查

| 配置 | 说明 |
|---|---|
| `trigger.fuzzy` / `fuzzy_threshold` | 模糊匹配开关 / 相似度阈值（0~1） |
| `trigger.reply_on_at` | 被 @ 时是否回复 |
| `trigger.reply_private` | 私聊是否无需关键词直接回复 |
| `proactive.mode` | 主动互动模式：keyword / context / hybrid |
| `proactive.message_threshold` | 每累计多少条群消息后主动发言一次 |
| `proactive.min_interval` / `max_interval` | 两次主动发言最小间隔（防刷屏）/ 最大间隔（保活跃） |
| `proactive.daily_cap` / `weekly_cap` | 每日 / 每周主动发言上限 |
| `proactive.context_window` | 参与上下文分析的最近消息条数 |
| `proactive.repeat_threshold` | 与近期内容相似度超过该值则放弃（防重复） |
| `ai.request_interval` | 两次 AI 请求最小间隔（秒） |
| `ai.max_context_rounds` | 每会话保留的最大对话轮数 |
| `ai.max_retries` / `retry_backoff` | API 重试次数 / 退避基数 |
| `safety.block_words` | 敏感词表，命中即忽略 |
| `roles.default` | 默认角色卡名，对应 `roles.cards` 下的键 |
| `logging.level` / `logging.file` | 日志级别 / 日志文件路径 |

## 目录结构

```
teda_bot/
├── main.py            # 入口：watchdog 主循环 + 线程池异步回复
├── config.yaml        # 全部配置
├── requirements.txt
├── bot/
│   ├── wechat_client.py   # wxauto 封装：监听/发送/@识别
│   ├── handler.py         # 消息管线：过滤→触发→AI→回复
│   ├── proactive.py       # 主动互动引擎：上下文积累→频率策略→生成发言
│   ├── matcher.py         # 关键词精确/模糊匹配
│   ├── role_cards.py      # 角色卡系统
│   ├── ai_backend.py      # OpenAI 兼容接口 + 上下文管理
│   ├── rate_limit.py      # 频率限制
│   ├── safety.py          # 敏感词过滤
│   └── logger.py          # 滚动日志
└── tests/             # pytest 单元测试
```

## 测试

```powershell
python -m pytest tests/ -v
```

## 运行与排障

- 日志输出到 `logs/bot.log`（5MB 滚动，保留 3 份），收发消息、AI 调用、异常均有记录
- 启动报 `添加监听失败`：检查群名/好友昵称是否与微信显示完全一致
- 无回复：确认消息含关键词或 @了 `bot.name`；查看日志中「捕获消息」是否出现
- AI 报错：确认 `api_key`/`base_url` 正确；失败会自动重试 3 次后跳过该消息
- 程序崩溃会自动重启；连续 10 次崩溃停止，请查日志

## 注意事项与免责声明

- wxauto4 基于 UIAutomation，仅用于学习交流，请勿用于生产/商业用途
- 自动回复行为请遵守微信用户协议，高频滥用有账号风控风险
- 免费版 wxauto4 无后台监听，采用轮询模式；微信客户端升级超过 4.1.8.107 后可能失效
- 无独立窗口时机器人会切换主窗口拉取消息，期间请勿手动操作微信主窗口
