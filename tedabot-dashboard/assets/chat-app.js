/**
 * ChatApp — AI 聊天模块 UI 控制器
 *
 * 职责：消息气泡渲染（用户右/AI左）、思考内容展示块（打字效果）、
 * 发送状态反馈与重试、自动滚动、骨架屏、人设卡快速切换、
 * 主题切换、清空二次确认、复制消息、CustomEvent 通信接入。
 *
 * 依赖（按序引入）：chat-bus.js → chat-virtual-list.js → chat-store.js → 本文件
 */
(function (global) {
  'use strict';

  const { escapeHtml, renderMarkdown } = global.MarkdownLite;

  // ---------- 小工具 ----------
  const $ = (sel) => document.querySelector(sel);
  const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  const timeStr = (ts) => {
    const d = new Date(ts || Date.now());
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  };
  const svg = (path, size) =>
    '<svg width="' + (size || 12) + '" height="' + (size || 12) + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + path + '</svg>';
  const ICON = {
    copy: '<path d="M8 8h12v12H8z"/><path d="M16 8V4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    retry: '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>',
    chev: '<path d="m9 18 6-6-6-6"/>',
    brain: '<path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.96 4 4 0 0 0 .556 6.588A4 4 0 0 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.96 4 4 0 0 1-.556 6.588A4 4 0 0 1 12 18Z"/>'
  };
  const STATUS_LABEL = { sending: '发送中', sent: '已发送', failed: '发送失败', ok: '' };

  class ChatApp {
    constructor() {
      this.api = new global.ChatApi('');
      this.store = new global.ChatStore();
      this.bus = global.TedaChatBus;
      this.roles = [];                 // [{id, name}]
      this.pending = false;            // 是否有请求在途
      this._abort = null;
      this._skeletonId = null;

      this.scrollEl = $('#chat-scroll');
      this.innerEl = $('#chat-inner');
      this.windowEl = $('#vp-window');
      this.list = new global.ChatVirtualList(
        this.scrollEl, this.innerEl, this.windowEl,
        (item) => this._buildRow(item), { estimate: 76, buffer: 8 }
      );
    }

    // ==================================================================
    //  初始化
    // ==================================================================

    async init() {
      this._applyTheme(this.store.theme, false);
      this._bindTopbar();
      this._bindComposer();
      this._bindBus();

      try {
        const data = await this.api.getRoles();
        this.roles = data.roles || [];
        if (!this.store.activeRole || !this.roles.some(r => r.id === this.store.activeRole)) {
          this.store.setActiveRole(data.default || (this.roles[0] && this.roles[0].id));
        }
      } catch (e) {
        this._toast('角色卡加载失败：' + e.message, true);
      }
      this._renderRoleMenu();

      await this._openSession(this.store.activeRole, this.store.toServerContext(this.store.activeRole));
      this.list.scrollToBottom(true);

      this.bus.emit('tb-chat:ready', {});
    }

    /** 创建服务端会话并渲染本地历史（role 切换 / 刷新恢复共用） */
    async _openSession(role, serverHistory) {
      try {
        const data = await this.api.createSession({ role, history: serverHistory });
        this.store.setSessionId(data.session_id);
        if (data.role) this.store.setActiveRole(data.role);
        this._updateRoleLabel();
      } catch (e) {
        this._toast('会话创建失败：' + e.message, true);
      }
      this.list.setItems(this.store.history(this.store.activeRole).slice());
    }

    // ==================================================================
    //  顶栏：返回 / 角色 / 主题 / 清空
    // ==================================================================

    _bindTopbar() {
      // 返回主界面：先播放 200ms 退场动画再跳转（入场 280ms + 退场 200ms 均 ≤300ms）
      $('#btn-back').addEventListener('click', () => {
        document.getElementById('chat-app').classList.remove('page-enter');
        document.getElementById('chat-app').classList.add('page-leave');
        setTimeout(() => { global.location.href = 'dashboard.html'; }, 200);
      });

      // 人设卡下拉
      $('#btn-role').addEventListener('click', (e) => {
        e.stopPropagation();
        $('#role-menu').classList.toggle('hidden');
      });
      document.addEventListener('click', () => $('#role-menu').classList.add('hidden'));

      // 主题切换
      $('#btn-theme').addEventListener('click', () => {
        this._applyTheme(this.store.theme === 'dark' ? 'light' : 'dark', true);
      });

      // 清空：二次确认
      $('#btn-clear').addEventListener('click', () => $('#confirm-mask').classList.add('visible'));
      $('#confirm-cancel').addEventListener('click', () => $('#confirm-mask').classList.remove('visible'));
      $('#confirm-mask').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) e.currentTarget.classList.remove('visible');
      });
      $('#confirm-ok').addEventListener('click', () => this._doClear());
    }

    _renderRoleMenu() {
      const menu = $('#role-menu');
      menu.innerHTML = '';
      const active = this.store.activeRole;
      for (const r of this.roles) {
        const btn = document.createElement('button');
        btn.className = 'role-item' + (r.id === active ? ' active' : '');
        btn.innerHTML = '<span>' + escapeHtml(r.name || r.id) + '</span>' +
          '<span class="role-check">' + svg(ICON.check, 13) + '</span>';
        btn.addEventListener('click', () => {
          menu.classList.add('hidden');
          if (r.id !== active) this._switchRole(r.id);
        });
        menu.appendChild(btn);
      }
    }

    _updateRoleLabel() {
      const card = this.roles.find(r => r.id === this.store.activeRole);
      $('#role-label').textContent = card ? (card.name || card.id) : '角色卡';
    }

    /** 快速切换人设卡：各自保留独立历史，服务端以新会话承载 */
    async _switchRole(roleId) {
      const old = this.store.activeRole;
      this.store.setActiveRole(roleId);
      this._renderRoleMenu();
      await this._openSession(roleId, this.store.toServerContext(roleId));
      this.list.scrollToBottom(true);
      const card = this.roles.find(r => r.id === roleId);
      this.bus.emit('tb-chat:role-changed', { role: roleId, roleName: (card && card.name) || roleId });
      this._toast('已切换：' + ((card && card.name) || roleId) + '（原「' + old + '」历史已保留）');
    }

    _applyTheme(theme, notify) {
      document.documentElement.classList.toggle('light', theme === 'light');
      document.documentElement.classList.toggle('dark', theme !== 'light');
      $('#icon-moon').style.display = theme === 'dark' ? '' : 'none';
      $('#icon-sun').style.display = theme === 'dark' ? 'none' : '';
      this.store.setTheme(theme);
      if (notify) this.bus.emit('tb-chat:theme-changed', { theme });
    }

    async _doClear() {
      $('#confirm-mask').classList.remove('visible');
      const role = this.store.activeRole;
      try { if (this.store.sessionId) await this.api.clearSession(this.store.sessionId); } catch (e) { /* 离线也可清本地 */ }
      this.store.clearHistory(role);
      await this._openSession(role, []);
      this.bus.emit('tb-chat:cleared', { role });
      this._toast('聊天已清空');
    }

    // ==================================================================
    //  输入区
    // ==================================================================

    _bindComposer() {
      const input = $('#chat-input');
      const send = $('#btn-send');

      // 自动伸缩高度
      const resize = () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 180) + 'px';
      };
      input.addEventListener('input', () => {
        resize();
        $('#char-count').textContent = input.value.length + ' / 4000';
      });

      // Enter 发送 / Shift+Enter 换行（输入法组合中不触发）
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
          e.preventDefault();
          this._submit();
        }
      });
      send.addEventListener('click', () => this._submit());

      // 格式化按钮：对选区包裹标记，无选区则插入模板
      document.querySelectorAll('.fmt-btn').forEach((btn) => {
        btn.addEventListener('click', () => this._applyFormat(btn.dataset.fmt));
      });
    }

    _applyFormat(kind) {
      const input = $('#chat-input');
      const { selectionStart: s, selectionEnd: e, value } = input;
      const sel = value.slice(s, e) || '';
      const wrap = {
        bold: ['**', '**'], italic: ['*', '*'], code: ['`', '`'],
        codeblock: ['```\n', '\n```']
      }[kind];
      if (!wrap) return;
      const text = value.slice(0, s) + wrap[0] + (sel || (kind === 'codeblock' ? '' : '文本')) + wrap[1] + value.slice(e);
      input.value = text;
      const caret = s + wrap[0].length + (sel || (kind === 'codeblock' ? 0 : 2)).length;
      input.focus();
      input.setSelectionRange(caret, caret);
      input.dispatchEvent(new Event('input'));
    }

    /** 发送入口（按钮 / Enter / 宿主事件共用） */
    async _submit(text) {
      const input = $('#chat-input');
      const content = (text !== undefined ? text : input.value).trim();
      if (!content) return;
      if (this.pending) { this._toast('上一条回复生成中，请稍候…'); return; }
      if (text === undefined) { input.value = ''; input.dispatchEvent(new Event('input')); }

      const role = this.store.activeRole;
      const msg = { id: uid(), role: 'user', text: content, ts: Date.now(), status: 'sending' };
      this.store.appendMessage(role, msg);
      this._appendAndFollow(msg);
      this.bus.emit('tb-chat:message-sent', { id: msg.id, text: content, ts: msg.ts });

      await this._requestReply(role, msg);
    }

    /** 失败重试：复用原消息重新请求 */
    async _retry(msgId) {
      const role = this.store.activeRole;
      const msg = this.store.history(role).find(m => m.id === msgId);
      if (!msg || this.pending) return;
      msg.status = 'sending';
      this.store.updateMessage(role, msgId, { status: 'sending' });
      this.list.updateItem(msgId, { status: 'sending' });
      await this._requestReply(role, msg);
    }

    /** 请求 AI 回复：骨架屏 → 成功追加回复 / 失败标记重试 */
    async _requestReply(role, userMsg) {
      this.pending = true;
      this._setSendState('sending');

      // 骨架屏（若视口在底部则跟随显示）
      const pinned = this.list.isNearBottom();
      this._skeletonId = uid();
      this.list.append({ id: this._skeletonId, type: 'skeleton' });
      if (pinned) this.list.scrollToBottom(true);

      this._abort = new AbortController();
      const timer = setTimeout(() => this._abort.abort(), 90000);  // 与后端 60s 超时 + 重试对齐
      try {
        const data = await this.api.send(this.store.sessionId, userMsg.text, this._abort.signal);
        this.store.updateMessage(role, userMsg.id, { status: 'sent' });
        this.list.updateItem(userMsg.id, { status: 'sent' });

        const reply = {
          id: uid(), role: 'ai', text: data.reply, thinking: data.thinking || '',
          ts: Date.now(), status: 'ok', elapsed: data.elapsed_ms || 0, _animated: false
        };
        this.store.appendMessage(role, reply);
        this._removeSkeleton();
        this.list.append(reply);
        if (this.list.isNearBottom()) this.list.scrollToBottom(true);
        this._setSendState('ok');
        this.bus.emit('tb-chat:reply-received', {
          id: reply.id, text: reply.text, thinking: reply.thinking, ts: reply.ts
        });
      } catch (e) {
        if (e.code === 'E_ABORTED') {
          // 超时取消：移除骨架，标记失败可重试
        }
        this.store.updateMessage(role, userMsg.id, { status: 'failed' });
        this.list.updateItem(userMsg.id, { status: 'failed' });
        this._removeSkeleton();
        this._setSendState('fail');
        this._toast(e.message || '发送失败', true);
        this.bus.emit('tb-chat:error', { code: e.code || 'E_UNKNOWN', message: e.message || '发送失败' });
      } finally {
        clearTimeout(timer);
        this._abort = null;
        this.pending = false;
        this._setSendState('');
      }
    }

    _removeSkeleton() {
      if (!this._skeletonId) return;
      // 骨架屏不属于持久消息：从列表中剔除
      const items = this.list.items.filter(it => it.id !== this._skeletonId);
      this._skeletonId = null;
      this.list.setItems(items);
    }

    _setSendState(state) {
      const btn = $('#btn-send');
      btn.classList.remove('sending', 'ok', 'fail');
      btn.disabled = state === 'sending';
      btn.querySelector('span, svg');
      const label = { sending: '生成中…', ok: '已发送', fail: '失败' }[state] || '发送';
      btn.innerHTML = (state === 'sending'
        ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.22-8.56"/></svg>'
        : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>')
        + label;
      if (state) btn.classList.add(state === 'sending' ? 'sending' : state);
    }

    _appendAndFollow(msg) {
      const pinned = this.list.isNearBottom();
      this.list.append(msg);
      if (pinned) this.list.scrollToBottom(true);
    }

    // ==================================================================
    //  行渲染：消息气泡 / 思考块 / 骨架屏
    // ==================================================================

    _buildRow(item) {
      if (item.type === 'skeleton') return this._buildSkeleton();
      const row = document.createElement('div');
      row.className = 'msg-row ' + (item.role === 'user' ? 'user' : 'ai');

      const bundle = document.createElement('div');
      bundle.className = 'msg-bundle';

      // AI 思考内容展示块（浅色背景 + 斜体，与正式回复明显区分）
      if (item.role === 'ai' && item.thinking) {
        bundle.appendChild(this._buildThinking(item));
      }

      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      if (item.role === 'user') {
        bubble.textContent = item.text;
      } else {
        bubble.innerHTML = renderMarkdown(item.text);
      }
      bundle.appendChild(bubble);

      // 元信息：时间戳 + 状态 + 复制/重试
      const meta = document.createElement('div');
      meta.className = 'msg-meta';
      const statusCls = item.role === 'user' ? (item.status === 'sent' ? 'sent' : item.status) : 'ok';
      meta.innerHTML =
        '<span>' + timeStr(item.ts) + '</span>' +
        (item.role === 'ai' && item.elapsed
          ? '<span>· ' + (item.elapsed / 1000).toFixed(1) + 's</span>' : '') +
        '<span class="msg-status ' + statusCls + '">' +
          (statusCls === 'sending' ? '发送中' : statusCls === 'sent' ? svg(ICON.check, 11) : '') +
          (statusCls === 'failed' ? '失败 <button class="btn-retry">重试</button>' : '') +
        '</span>' +
        '<button class="msg-copy" title="复制消息">' + svg(ICON.copy, 12) + '</button>';
      bundle.appendChild(meta);

      // 复制消息
      meta.querySelector('.msg-copy').addEventListener('click', () => {
        this._copyText(item.text);
      });
      // 重试
      const retryBtn = meta.querySelector('.btn-retry');
      if (retryBtn) retryBtn.addEventListener('click', () => this._retry(item.id));

      row.appendChild(bundle);
      return row;
    }

    _buildThinking(item) {
      const block = document.createElement('div');
      block.className = 'thinking-block';
      block.innerHTML =
        '<button class="thinking-toggle"><span class="chev">' + svg(ICON.chev, 11) + '</span>' +
        svg(ICON.brain, 12) + '<span>思考过程</span></button>' +
        '<div class="thinking-body"></div>';
      const body = block.querySelector('.thinking-body');

      // 打字效果：首次渲染时动态展开思考过程（总时长上限 1.4s，低端机友好）
      block.querySelector('.thinking-toggle').addEventListener('click', () => {
        block.classList.toggle('open');
      });
      if (!item._animated) {
        item._animated = true;
        block.classList.add('open');
        this._typewriter(body, item.thinking, 1400, () => block.classList.remove('open'));
      } else {
        body.textContent = item.thinking;
      }
      return block;
    }

    /** 打字机效果：把文本按帧逐字写入（requestAnimationFrame 驱动，60fps） */
    _typewriter(el, text, maxDuration, onDone) {
      el.classList.add('typing');
      const total = text.length;
      const start = performance.now();
      const step = (now) => {
        const p = Math.min(1, (now - start) / maxDuration);
        const n = Math.floor(total * p);
        el.textContent = text.slice(0, n);
        if (p < 1) { requestAnimationFrame(step); }
        else { el.classList.remove('typing'); el.textContent = text; if (onDone) onDone(); }
      };
      requestAnimationFrame(step);
    }

    _buildSkeleton() {
      const row = document.createElement('div');
      row.className = 'skeleton-row';
      row.innerHTML =
        '<div class="skeleton-bubble">' +
          '<div class="skeleton-line"></div><div class="skeleton-line"></div><div class="skeleton-line"></div>' +
        '</div>';
      return row;
    }

    // ==================================================================
    //  通用交互：复制 / Toast / 滚动
    // ==================================================================

    async _copyText(text) {
      try {
        await navigator.clipboard.writeText(text);
        this._toast('已复制');
      } catch (e) {
        // 兼容非 HTTPS / 旧浏览器：execCommand 回退
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); this._toast('已复制'); }
        catch (err) { this._toast('复制失败', true); }
        ta.remove();
      }
    }

    _toast(msg, isError) {
      const t = $('#toast');
      t.textContent = msg;
      t.classList.toggle('error', !!isError);
      t.classList.add('visible');
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => t.classList.remove('visible'), 2200);
    }

    // ==================================================================
    //  跨模块通信（宿主 → 模块 入站事件）
    // ==================================================================

    _bindBus() {
      this.bus.on('tb-chat:send', (e) => this._submit(e.detail.text));
      this.bus.on('tb-chat:set-role', (e) => {
        if (this.roles.some(r => r.id === e.detail.role)) this._switchRole(e.detail.role);
      });
      this.bus.on('tb-chat:clear', () => $('#btn-clear').click());

      // 滚动跟随：手动上滚时停止自动跟随，出现"回到底部"按钮
      const btnBottom = $('#btn-bottom');
      this.scrollEl.addEventListener('scroll', () => {
        btnBottom.classList.toggle('visible', !this.list.isNearBottom());
      }, { passive: true });
      btnBottom.addEventListener('click', () => this.list.scrollToBottom(true));
    }
  }

  // ---------- 启动 ----------
  const app = new ChatApp();
  global.TedaChat = {                        // 宿主可编程 API（与事件总线等价）
    send: (text) => app._submit(text),
    setRole: (role) => app._switchRole(role),
    clear: () => app._doClear(),
    bus: global.TedaChatBus
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => app.init());
  } else {
    app.init();
  }

  global.__chatAppInstance = app;  // 供单元测试访问
})(window);
