/**
 * TedaBot Dashboard API 对接层
 *
 * 功能：
 * 1. 从后端 /api/* 端点拉取实时数据
 * 2. 自动更新页面 DOM 中的 KPI、场景列表、队列指标、日志流
 * 3. 定时轮询刷新（dashboard 5s / logs 3s）
 * 4. API 不可用时静默保留原有设计稿数据，不破坏页面
 */

(function () {
  'use strict';

  const REFRESH_DASHBOARD = 5000;  // 仪表盘刷新间隔（ms）
  const REFRESH_LOGS = 3000;       // 日志刷新间隔（ms）

  // ---------- 通用工具 ----------

  async function fetchJSON(url) {
    try {
      const resp = await fetch(url, { cache: 'no-store' });
      if (!resp.ok) return null;
      return await resp.json();
    } catch (e) {
      return null;  // 网络错误 / 服务未启动 → 静默失败
    }
  }

  function setText(selector, text) {
    const el = document.querySelector(selector);
    if (el) el.textContent = text;
  }

  function setTextAll(selector, values) {
    const els = document.querySelectorAll(selector);
    els.forEach((el, i) => {
      if (values[i] !== undefined) el.textContent = values[i];
    });
  }

  // ---------- 仪表盘页面 ----------

  async function refreshDashboard() {
    const stats = await fetchJSON('/api/stats');
    if (!stats) return;

    // KPI 卡片：场景数 / 回复数 / 队列待发 / 运行时长
    const kpis = document.querySelectorAll('.kpi-value');
    if (kpis.length >= 4) {
      kpis[0].textContent = String(stats.scenarios.active);
      kpis[1].textContent = stats.replies.total.toLocaleString();
      kpis[2].textContent = String(stats.queue.pending);
      kpis[3].textContent = stats.uptime;
    }

    // 发送队列面板
    const queueEls = document.querySelectorAll('.queue-count');
    if (queueEls.length >= 4) {
      queueEls[0].textContent = String(stats.queue.pending);
      queueEls[1].textContent = stats.queue.sent.toLocaleString();
      queueEls[2].textContent = String(stats.queue.failed);
      queueEls[3].textContent = String(stats.queue.dropped);
    }

    // 活跃场景列表
    const scenarios = await fetchJSON('/api/scenarios');
    if (scenarios && scenarios.length > 0) {
      const activeScenes = scenarios.filter(s => s.last_ts > 0).slice(0, 5);
      const sceneEls = document.querySelectorAll('.scenario-name');
      sceneEls.forEach((el, i) => {
        if (activeScenes[i]) {
          el.textContent = activeScenes[i].key;
          // 更新对应的元数据（如果存在）
          const row = el.closest('.scenario-row, .timeline-item, li, div');
          if (row) {
            const meta = row.querySelector('.scenario-meta, .text-\\[11px\\], .scenario-info');
            if (meta) {
              meta.textContent = `${activeScenes[i].messages}条记忆 · ${activeScenes[i].replies}次回复 · ${activeScenes[i].last_active}`;
            }
          }
        }
      });
    }
  }

  // ---------- 日志页面 ----------

  async function refreshLogs() {
    const data = await fetchJSON('/api/logs?lines=150');
    if (!data || !data.lines) return;

    // 查找日志流容器（logs.html 中的 .log-stream 或类似元素）
    const logContainer = document.querySelector('.log-stream, .log-list, [data-log-stream]');
    if (!logContainer) return;

    // 清空并重建日志行
    logContainer.innerHTML = '';
    const frag = document.createDocumentFragment();

    for (const entry of data.lines) {
      const line = document.createElement('div');
      line.className = 'log-line';

      const ts = document.createElement('span');
      ts.className = 'log-ts';
      ts.textContent = entry.timestamp || '';

      const badge = document.createElement('span');
      badge.className = `log-badge log-${entry.level.toLowerCase()}`;
      badge.textContent = entry.level;

      const msg = document.createElement('span');
      msg.className = 'log-msg';
      msg.textContent = entry.raw;

      line.appendChild(ts);
      line.appendChild(badge);
      line.appendChild(msg);
      frag.appendChild(line);
    }

    logContainer.appendChild(frag);

    // 自动滚动到底部
    logContainer.scrollTop = logContainer.scrollHeight;
  }

  // ---------- 场景页面 ----------

  async function refreshScenarios() {
    const scenarios = await fetchJSON('/api/scenarios');
    if (!scenarios) return;

    // 查找场景表格/列表容器
    const tableBody = document.querySelector('.scenario-table tbody, .scenario-list, [data-scenario-list]');
    if (!tableBody) return;

    // 保留设计稿结构，仅更新数据行
    // 具体实现取决于页面 DOM 结构
  }

  // ---------- 初始化 ----------

  function init() {
    const path = window.location.pathname;
    const page = path.split('/').pop() || 'dashboard.html';

    if (page === 'dashboard.html' || page === '' || page === '/') {
      refreshDashboard();
      setInterval(refreshDashboard, REFRESH_DASHBOARD);
    } else if (page === 'logs.html') {
      refreshLogs();
      setInterval(refreshLogs, REFRESH_LOGS);
    } else if (page === 'scenarios.html') {
      refreshScenarios();
      setInterval(refreshScenarios, REFRESH_DASHBOARD);
    }
  }

  // DOM 就绪后启动
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
