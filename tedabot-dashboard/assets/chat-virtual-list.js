/**
 * VirtualList — 轻量消息列表虚拟滚动器
 *
 * 原理：只渲染视口附近（± buffer）的消息行，行内绝对定位
 * （transform: translateY），上下用总高度撑开滚动区域，
 * 使长列表（数百条消息）只维持数十个 DOM 节点。
 *
 * 行高在渲染后实测回填（rAF 中 measure），未测量的行使用估算值。
 * 单文件零依赖，可脱离聊天模块独立复用。
 */
(function (global) {
  'use strict';

  /** @typedef {{id: string, type: string, [k: string]: any}} ListItem */

  class VirtualList {
    /**
     * @param {HTMLElement} scrollEl  滚动容器（overflow-y: auto）
     * @param {HTMLElement} innerEl   高度撑开层（position: relative）
     * @param {HTMLElement} windowEl  渲染窗口（行插入到这里）
     * @param {(item: ListItem, idx: number) => HTMLElement} buildRow 行构建函数
     */
    constructor(scrollEl, innerEl, windowEl, buildRow, opts) {
      this.scrollEl = scrollEl;
      this.innerEl = innerEl;
      this.windowEl = windowEl;
      this.buildRow = buildRow;
      this.estimate = (opts && opts.estimate) || 76;   // 未测量行高估算值
      this.buffer = (opts && opts.buffer) || 8;        // 视口外上下预渲染行数
      /** @type {ListItem[]} */ this.items = [];
      this.heights = [];                               // 实测/估算行高
      this.offsets = [];                               // offsets[i] = 第 i 行顶部偏移
      this.totalHeight = 0;
      this._rendered = new Map();                      // idx → row element
      this._measuring = false;

      this.scrollEl.addEventListener('scroll', () => this._onScroll(), { passive: true });
    }

    /** 全量替换数据源（清空/切换人设卡时使用） */
    setItems(items) {
      this.items = items;
      this.heights = new Array(items.length);
      this._relayout();
      this._render();
      this._measureAsync();
    }

    /** 尾部追加一条消息 */
    append(item) {
      this.items.push(item);
      this.heights.push(undefined);
      this._relayout();
      this._render();
      this._measureAsync();
    }

    /** 原位更新一条消息（如状态 sending → sent），并重建已渲染的行 */
    updateItem(id, patch) {
      const idx = this.items.findIndex(it => it.id === id);
      if (idx < 0) return;
      Object.assign(this.items[idx], patch);
      // 文本变化的补丁行高会变，需要重测；状态等补丁行高不变
      if ('text' in patch || 'thinking' in patch) {
        this.heights[idx] = undefined;
        this._relayout();
      }
      this._rerenderRow(idx);
    }

    /** 获取消息索引 */
    indexOf(id) { return this.items.findIndex(it => it.id === id); }

    /** 滚动到底部（立即模式，供自动跟随使用） */
    scrollToBottom(immediate) {
      this.scrollEl.scrollTop = this.scrollEl.scrollHeight;
      if (!immediate) return;
    }

    /** 视口是否接近底部（80px 内），用于自动跟随判断 */
    isNearBottom() {
      const el = this.scrollEl;
      return el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    }

    // ------------------------------------------------------------------
    //  内部实现
    // ------------------------------------------------------------------

    /** 依据 heights 重算 offsets 与总高度（估算值兜底未测行） */
    _relayout() {
      let acc = 0;
      this.offsets = new Array(this.items.length);
      for (let i = 0; i < this.items.length; i++) {
        this.offsets[i] = acc;
        acc += this.heights[i] !== undefined ? this.heights[i] : this.estimate;
      }
      this.totalHeight = acc;
      this.innerEl.style.height = this.totalHeight + 'px';
    }

    _onScroll() { this._render(); }

    /** 重建单个已渲染行（状态更新时保持 DOM 与数据同步） */
    _rerenderRow(idx) {
      const old = this._rendered.get(idx);
      if (!old) return;
      const row = this.buildRow(this.items[idx], idx);
      row.style.position = 'absolute';
      row.style.left = '0';
      row.style.right = '0';
      row.style.top = '0';
      row.style.transform = 'translateY(' + this.offsets[idx] + 'px)';
      row.dataset.vIdx = String(idx);
      old.replaceWith(row);
      this._rendered.set(idx, row);
      this._measureAsync();
    }

    /** 计算可视窗口 [start, end) 并只渲染窗口内的行 */
    _render() {
      const el = this.scrollEl;
      const top = el.scrollTop - this.buffer * this.estimate;
      const bottom = el.scrollTop + el.clientHeight + this.buffer * this.estimate;

      // 二分查找首个 offset >= top 的行
      let start = 0, end = this.items.length;
      while (start < end) {
        const mid = (start + end) >> 1;
        if (this.offsets[mid] < top) start = mid + 1; else end = mid;
      }
      let last = start;
      while (last < this.items.length && this.offsets[last] < bottom) last++;

      // 移除窗口外行
      for (const [idx, row] of this._rendered) {
        if (idx < start || idx >= last) { row.remove(); this._rendered.delete(idx); }
      }
      // 渲染窗口内缺失的行
      for (let i = start; i < last; i++) {
        if (this._rendered.has(i)) continue;
        const row = this.buildRow(this.items[i], i);
        row.style.position = 'absolute';
        row.style.left = '0';
        row.style.right = '0';
        row.style.top = '0';
        row.style.transform = 'translateY(' + this.offsets[i] + 'px)';
        row.dataset.vIdx = String(i);
        this.windowEl.appendChild(row);
        this._rendered.set(i, row);
      }
    }

    /** 渲染帧后实测行高并回填（最多两轮，收敛即止） */
    _measureAsync() {
      if (this._measuring) return;
      this._measuring = true;
      requestAnimationFrame(() => {
        this._measuring = false;
        let changed = false;
        for (const [idx, row] of this._rendered) {
          const h = row.offsetHeight;
          if (h > 0 && this.heights[idx] !== h) {
            this.heights[idx] = h;
            changed = true;
          }
        }
        if (changed) {
          this._relayout();
          this._render();
        }
      });
    }
  }

  global.ChatVirtualList = VirtualList;
})(window);
