/**
 * ChatStore + ChatApi + MarkdownLite — 聊天模块数据层
 *
 * - ChatApi: 后端 REST 接口封装（/api/chat/*），统一错误对象 {code, message}
 * - ChatStore: 聊天状态与本地持久化（localStorage，刷新后恢复）
 * - MarkdownLite: 轻量 Markdown 渲染（粗体/斜体/行内代码/代码块），
 *   渲染前先做 HTML 转义，杜绝 XSS
 */
(function (global) {
  'use strict';

  // ==================================================================
  //  ChatApi — REST 接口封装
  // ==================================================================

  class ChatApi {
    constructor(baseUrl) { this.base = baseUrl || ''; }

    async _fetch(path, opts) {
      let resp;
      try {
        resp = await fetch(this.base + path, Object.assign({ cache: 'no-store' }, opts));
      } catch (e) {
        if (e && e.name === 'AbortError') throw { code: 'E_ABORTED', message: '请求已取消' };
        throw { code: 'E_NETWORK', message: '网络异常，请检查服务是否启动' };
      }
      let data = null;
      try { data = await resp.json(); } catch (e) { /* 非 JSON 响应 */ }
      if (!resp.ok) {
        throw {
          code: resp.status === 502 ? 'E_AI' : 'E_HTTP',
          message: (data && data.error) || ('请求失败 (' + resp.status + ')')
        };
      }
      return data;
    }

    /** 人设卡列表 */
    getRoles() { return this._fetch('/api/chat/roles'); }

    /**
     * 创建会话（history 用于刷新后恢复 AI 上下文）
     * @param {{role?: string, history?: Array<{role: string, content: string, ts?: number}>}} body
     */
    createSession(body) {
      return this._fetch('/api/chat/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {})
      });
    }

    /** 发送消息；signal 用于超时/取消 */
    send(sessionId, message, signal) {
      return this._fetch('/api/chat/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, message }),
        signal
      });
    }

    clearSession(sessionId) {
      return this._fetch('/api/chat/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId })
      });
    }
  }

  // ==================================================================
  //  ChatStore — 状态与本地持久化
  // ==================================================================

  const LS_KEY = 'tb-chat-v1';
  const MAX_PER_ROLE = 200;   // 每个人设卡本地最多保留的消息条数

  class ChatStore {
    constructor() { this._load(); }

    _load() {
      let saved = null;
      try { saved = JSON.parse(localStorage.getItem(LS_KEY)); } catch (e) { /* 损坏则重置 */ }
      this.data = Object.assign({ v: 1, sessionId: null, activeRole: null, theme: 'dark', histories: {} }, saved);
      if (typeof this.data.histories !== 'object' || !this.data.histories) this.data.histories = {};
    }

    save() {
      try { localStorage.setItem(LS_KEY, JSON.stringify(this.data)); }
      catch (e) { console.warn('[ChatStore] 本地保存失败（可能存储已满）', e); }
    }

    get theme() { return this.data.theme; }
    setTheme(t) { this.data.theme = t === 'light' ? 'light' : 'dark'; this.save(); }

    get activeRole() { return this.data.activeRole; }
    setActiveRole(role) { this.data.activeRole = role; this.save(); }

    get sessionId() { return this.data.sessionId; }
    setSessionId(id) { this.data.sessionId = id; this.save(); }

    /** 某人设卡的本地消息列表 */
    history(role) {
      if (!this.data.histories[role]) this.data.histories[role] = [];
      return this.data.histories[role];
    }

    appendMessage(role, msg) {
      const list = this.history(role);
      list.push(msg);
      if (list.length > MAX_PER_ROLE) list.splice(0, list.length - MAX_PER_ROLE);
      this.save();
    }

    /** 按 id 原位更新消息（状态等） */
    updateMessage(role, id, patch) {
      const msg = this.history(role).find(m => m.id === id);
      if (msg) { Object.assign(msg, patch); this.save(); }
    }

    /** 清空某人设卡的历史 */
    clearHistory(role) {
      this.data.histories[role] = [];
      this.save();
    }

    /** 把本地消息映射为服务端可恢复的上下文（只保留成功消息，截断到最近 16 条） */
    toServerContext(role) {
      return this.history(role)
        .filter(m => m.status === 'sent' || m.status === 'ok')
        .slice(-16)
        .map(m => ({ role: m.role === 'user' ? 'user' : 'assistant', content: m.text, ts: m.ts }));
    }
  }

  // ==================================================================
  //  MarkdownLite — 极简 Markdown（先转义再渲染，防 XSS）
  // ==================================================================

  function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function renderMarkdown(text) {
    const src = escapeHtml(String(text || ''));
    const blocks = [];
    // 1) 抽出 ``` 代码块，占位防内部语法被二次替换
    let out = src.replace(/```(?:[a-zA-Z0-9_+-]*)\n?([\s\S]*?)(?:```|$)/g, (_, code) => {
      blocks.push('<pre><code>' + code.replace(/\n$/, '') + '</code></pre>');
      return '\u0000B' + (blocks.length - 1) + '\u0000';
    });
    // 2) 行内代码
    out = out.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    // 3) 粗体（先于斜体，避免 ** 被斜体规则吞掉）
    out = out.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');
    // 4) 斜体
    out = out.replace(/\*([^*\n]+)\*/g, '<i>$1</i>');
    // 5) 换行
    out = out.replace(/\n/g, '<br>');
    // 6) 还原代码块
    out = out.replace(/\u0000B(\d+)\u0000/g, (_, i) => blocks[+i]);
    return out;
  }

  global.ChatApi = ChatApi;
  global.ChatStore = ChatStore;
  global.MarkdownLite = { escapeHtml, renderMarkdown };
})(window);
