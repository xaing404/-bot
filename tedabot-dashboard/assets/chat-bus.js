/**
 * TedaChatBus — AI 聊天模块跨模块通信层（基于 CustomEvent）
 *
 * 主应用（宿主页面 / 其他脚本）与聊天模块之间通过 window 上的
 * CustomEvent 进行双向通信。所有事件名以 `tb-chat:` 为前缀，
 * detail 载荷在派发前经过 schema 校验，非法数据会降级为
 * `tb-chat:error` 事件并丢弃，保证两端接口安全。
 *
 * 事件协议（完整文档见 docs/chat-module-api.md）：
 * ── 模块 → 宿主（模块派发，宿主监听）─────────────────
 *   tb-chat:ready            {}                        模块初始化完成
 *   tb-chat:message-sent     {id, text, ts}            用户消息已发出
 *   tb-chat:reply-received   {id, text, thinking, ts}  AI 回复已到达
 *   tb-chat:error            {code, message}           模块内错误
 *   tb-chat:role-changed     {role, roleName}          人设卡已切换
 *   tb-chat:cleared          {role}                    聊天已清空
 *   tb-chat:theme-changed    {theme: 'dark'|'light'}   主题已切换
 * ── 宿主 → 模块（宿主派发，模块监听）─────────────────
 *   tb-chat:send             {text}                    让模块发送一条消息
 *   tb-chat:set-role         {role}                    让模块切换人设卡
 *   tb-chat:clear            {}                        让模块清空聊天
 *
 * 权限控制：事件仅在 window 同源上下文内传播，不产生任何网络
 * 或持久化副作用；宿主若需注入消息，只能通过上述三个入站事件，
 * 无法直接触碰会话密钥与 AI 配置（配置仅存在于后端）。
 */
(function (global) {
  'use strict';

  /** 事件载荷 schema：k → {type, required}；type ∈ string|number|object */
  var SCHEMAS = {
    'tb-chat:ready': {},
    'tb-chat:message-sent': { id: 'string', text: 'string', ts: 'number' },
    'tb-chat:reply-received': { id: 'string', text: 'string', thinking: 'string', ts: 'number' },
    'tb-chat:error': { code: 'string', message: 'string' },
    'tb-chat:role-changed': { role: 'string', roleName: 'string' },
    'tb-chat:cleared': { role: 'string' },
    'tb-chat:theme-changed': { theme: 'string' },
    'tb-chat:send': { text: 'string' },
    'tb-chat:set-role': { role: 'string' },
    'tb-chat:clear': {}
  };

  /** 校验 detail 是否符合 schema，返回布尔值（错误细节走 console.warn） */
  function validate(event, detail) {
    var schema = SCHEMAS[event];
    if (!schema) return false;              // 未注册的事件名不允许派发
    if (!detail || typeof detail !== 'object') return false;
    for (var key in schema) {
      var expect = schema[key];
      var val = detail[key];
      if (val === undefined || val === null) return false;
      if (expect === 'string' && typeof val !== 'string') return false;
      if (expect === 'number' && typeof val !== 'number') return false;
    }
    return true;
  }

  var TedaChatBus = {
    /** 派发事件（自动校验载荷；非法载荷静默丢弃并降级为 error 事件） */
    emit: function (event, detail) {
      if (!validate(event, detail)) {
        console.warn('[TedaChatBus] 事件载荷校验失败，已丢弃:', event, detail);
        // error 事件自身不再校验，防止递归
        if (event !== 'tb-chat:error') {
          global.dispatchEvent(new CustomEvent('tb-chat:error', {
            detail: { code: 'E_PAYLOAD', message: '事件载荷校验失败: ' + event }
          }));
        }
        return false;
      }
      global.dispatchEvent(new CustomEvent(event, { detail: detail }));
      return true;
    },

    /** 订阅事件，返回解绑函数 */
    on: function (event, handler) {
      global.addEventListener(event, handler);
      var self = this;
      return function off() { self.off(event, handler); };
    },

    /** 取消订阅 */
    off: function (event, handler) {
      global.removeEventListener(event, handler);
    }
  };

  global.TedaChatBus = TedaChatBus;
})(window);
