const state = {
  userId: 1,
  users: [],
  currentUser: null,
  activeView: "dashboard",
  businessType: "orders",
  businessItems: [],
  businessDetailRequest: 0,
  selectedBusinessDetail: null,
  businessDetailTrigger: null,
  dashboardTrend: [],
  dashboardTrendIndex: null,
  dashboardTrendTrigger: null,
  approvals: [],
  selectedApproval: null,
  risk: null,
  notifications: [
    {
      id: 1,
      level: "critical",
      title: "航空铝板库存低于安全线",
      summary: "预计缺口 520kg，可能影响 3 张高优先级订单。",
      time: "5 分钟前",
      detail: "当前可用库存无法覆盖已排产需求，最早交付节点仅剩 3 天。",
      impact: "影响智造科技、星河自动化与宏远装备，涉及订单金额约 142.9 万元。",
      action: "查看供应链风险",
      view: "risk",
      unread: true,
      expanded: false,
    },
    {
      id: 2,
      level: "warning",
      title: "高优先级订单即将到达交付节点",
      summary: "SO-202608-0208 距计划交付仅剩 2 天。",
      time: "18 分钟前",
      detail: "该订单当前处于生产中，需要确认缺料处置结果和最终出货窗口。",
      impact: "客户等级为战略客户，订单金额 68.8 万元，延迟可能影响本月准时交付率。",
      action: "前往业务工作台",
      view: "business",
      unread: true,
      expanded: false,
    },
  ],
  preferences: {
    fontSize: "standard",
    theme: "light",
    radius: "standard",
    compact: false,
    reducedMotion: false,
    highContrast: false,
  },
};

const viewTitles = {
  dashboard: "经营总览",
  ai: "AI 指挥中心",
  risk: "供应链风险",
  business: "业务工作台",
  approvals: "审批中心",
  audit: "审计与设置",
};

const businessColumns = {
  orders: [
    ["order_number", "订单号", "strong"], ["customer_name", "客户"], ["customer_tier", "客户等级", "tag"],
    ["due_date", "交付日期"], ["total_amount", "订单金额", "money"], ["priority", "优先级", "priority"], ["status", "状态", "status"],
  ],
  materials: [
    ["code", "物料编码", "strong"], ["name", "物料名称"], ["specification", "规格"], ["current_stock", "当前库存", "number"],
    ["safety_stock", "安全库存", "number"], ["unit", "单位"], ["unit_cost", "单位成本", "money"],
  ],
  suppliers: [
    ["code", "供应商编码", "strong"], ["name", "供应商"], ["rating", "评级", "tag"], ["lead_days", "交期（天）", "number"],
    ["on_time_rate", "准时率", "percent"], ["contact", "业务联系人"],
  ],
  purchases: [
    ["po_number", "采购单号", "strong"], ["supplier_name", "供应商"], ["status", "状态", "status"],
    ["expected_date", "预计到货"], ["total_amount", "金额", "money"], ["created_at", "创建时间", "datetime"],
  ],
  shipments: [
    ["shipment_number", "出货单号", "strong"], ["order_number", "销售订单"], ["customer_name", "客户"],
    ["status", "状态", "status"], ["shipped_at", "出货时间", "datetime"],
  ],
  invoices: [
    ["invoice_number", "发票号", "strong"], ["order_number", "销售订单"], ["customer_name", "客户"],
    ["amount", "金额", "money"], ["status", "状态", "status"], ["created_at", "创建时间", "datetime"],
  ],
};

const businessDetailMeta = {
  orders: { label: "订单", primary: "order_number", secondary: ["customer_name", "customer_tier"] },
  materials: { label: "物料", primary: "code", secondary: ["name", "specification"] },
  suppliers: { label: "供应商", primary: "code", secondary: ["name", "rating"] },
  purchases: { label: "采购单", primary: "po_number", secondary: ["supplier_name", "status"] },
  shipments: { label: "出货单", primary: "shipment_number", secondary: ["order_number", "customer_name"] },
  invoices: { label: "发票", primary: "invoice_number", secondary: ["order_number", "customer_name"] },
};

const businessLinkFields = {
  orders: ["order_number", "customer_name"],
  materials: ["code", "name"],
  suppliers: ["code", "name"],
  purchases: ["po_number", "supplier_name"],
  shipments: ["shipment_number", "order_number"],
  invoices: ["invoice_number", "order_number"],
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  document.getElementById("today-chip").textContent = formatToday();
  renderNotifications();
  applyPreferences();
  bindEvents();
  try {
    const [{ users }, health] = await Promise.all([api("/api/users", { userless: true }), api("/api/health", { userless: true })]);
    state.users = users;
    state.currentUser = users[0];
    renderUserSwitcher();
    renderProviderStatus(health.ai);
    await Promise.all([loadDashboard(), loadRisk(), loadApprovals()]);
  } catch (error) {
    toast(error.message, "error");
  }
}

function bindEvents() {
  document.addEventListener("click", (event) => {
    const commandTarget = event.target.closest("[data-command]");
    if (commandTarget) {
      const command = commandTarget.dataset.command;
      switchView("ai");
      document.getElementById("command-input").value = command;
      runCommand(command);
      return;
    }
    const viewTarget = event.target.closest("[data-view]");
    if (viewTarget) {
      switchView(viewTarget.dataset.view);
      return;
    }
    const dashboardTarget = event.target.closest("[data-dashboard-target]");
    if (dashboardTarget) {
      navigateFromDashboard(dashboardTarget.dataset.dashboardTarget);
      return;
    }
    if (event.target.closest("[data-close-modal]")) closeApprovalModal();
    if (!event.target.closest("#preferences-panel") && !event.target.closest("#preferences-button")) closePreferences();
  });

  document.getElementById("notification-button").addEventListener("click", toggleNotificationDrawer);
  document.getElementById("notification-close").addEventListener("click", closeNotificationDrawer);
  document.getElementById("notification-backdrop").addEventListener("click", closeNotificationDrawer);
  document.getElementById("mark-all-read").addEventListener("click", markAllNotificationsRead);
  document.getElementById("notification-list").addEventListener("click", handleNotificationClick);
  document.getElementById("preferences-button").addEventListener("click", togglePreferences);
  document.getElementById("preferences-close").addEventListener("click", closePreferences);
  document.getElementById("preferences-form").addEventListener("change", updatePreferences);
  document.getElementById("preferences-reset").addEventListener("click", resetPreferences);

  document.getElementById("user-select").addEventListener("change", async (event) => {
    state.userId = Number(event.target.value);
    state.currentUser = state.users.find((user) => user.id === state.userId);
    updateCurrentUserUI();
    toast(`已切换为${state.currentUser.title}，AI 权限同步更新`);
    await loadView(state.activeView);
  });

  document.getElementById("command-form").addEventListener("submit", (event) => {
    event.preventDefault();
    runCommand(document.getElementById("command-input").value);
  });
  document.getElementById("command-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      document.getElementById("command-form").requestSubmit();
    }
  });
  document.getElementById("clear-chat").addEventListener("click", resetChat);
  document.getElementById("business-tabs").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-type]");
    if (!button) return;
    selectBusinessType(button.dataset.type);
  });
  document.getElementById("business-search").addEventListener("input", filterBusinessTable);
  document.getElementById("business-table").addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-business-type][data-business-id]");
    if (row) openBusinessDetail(row.dataset.businessType, Number(row.dataset.businessId), row);
  });
  document.getElementById("business-table").addEventListener("keydown", (event) => {
    const row = event.target.closest("tr[data-business-type][data-business-id]");
    if (row && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      openBusinessDetail(row.dataset.businessType, Number(row.dataset.businessId), row);
    }
  });
  document.getElementById("business-detail-modal").addEventListener("click", (event) => {
    if (event.target === event.currentTarget || event.target.closest("[data-close-business-detail]")) closeBusinessDetail();
  });
  document.getElementById("trend-chart").addEventListener("click", (event) => {
    const column = event.target.closest("[data-trend-index]");
    if (column) openDashboardTrend(Number(column.dataset.trendIndex), column);
  });
  document.getElementById("trend-chart").addEventListener("keydown", (event) => {
    const column = event.target.closest("[data-trend-index]");
    if (column && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      openDashboardTrend(Number(column.dataset.trendIndex), column);
    }
  });
  document.getElementById("dashboard-trend-modal").addEventListener("click", (event) => {
    const destination = event.target.closest("[data-trend-destination]");
    if (destination) {
      closeDashboardTrend({ restoreFocus: false });
      navigateFromDashboard(destination.dataset.trendDestination);
      return;
    }
    if (event.target === event.currentTarget || event.target.closest("[data-close-dashboard-trend]")) closeDashboardTrend();
  });
  document.getElementById("settings-form").addEventListener("submit", saveSettings);
  document.getElementById("refresh-audit").addEventListener("click", loadAudit);
  document.getElementById("reset-demo").addEventListener("click", resetDemo);
  document.getElementById("approve-approval").addEventListener("click", () => decideSelectedApproval("approved"));
  document.getElementById("reject-approval").addEventListener("click", () => decideSelectedApproval("rejected"));
  document.getElementById("mobile-menu").addEventListener("click", () => document.querySelector(".sidebar").classList.toggle("open"));
  document.getElementById("global-search").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      switchView("business");
      document.getElementById("business-search").value = event.target.value;
      filterBusinessTable();
    }
  });
  document.getElementById("export-report").addEventListener("click", exportBrief);
  document.addEventListener("keydown", (event) => {
    const dashboardTarget = event.target.closest("[data-dashboard-target]");
    if (dashboardTarget && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      navigateFromDashboard(dashboardTarget.dataset.dashboardTarget);
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      document.getElementById("global-search").focus();
    }
    if (event.key === "Escape") {
      closeApprovalModal();
      closeBusinessDetail();
      closeDashboardTrend();
      closeNotificationDrawer();
      closePreferences();
    }
  });
}

function renderNotifications() {
  const unreadCount = state.notifications.filter((item) => item.unread).length;
  const badge = document.getElementById("notification-count");
  badge.textContent = unreadCount;
  badge.classList.toggle("hidden", unreadCount === 0);
  document.getElementById("notification-unread-label").textContent = `${unreadCount} 条未读`;
  document.getElementById("mark-all-read").disabled = unreadCount === 0;
  document.getElementById("notification-list").innerHTML = state.notifications.map((item) => `
    <article class="notification-item ${item.unread ? "unread" : ""} ${item.expanded ? "expanded" : ""}">
      <button class="notification-summary" data-notification-id="${item.id}" aria-expanded="${item.expanded}">
        <i class="notification-level ${item.level}"></i>
        <span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.summary)}</small><time>${escapeHtml(item.time)}</time></span>
        <b>${item.expanded ? "⌃" : "⌄"}</b>
      </button>
      <div class="notification-detail" aria-hidden="${!item.expanded}">
        <p>${escapeHtml(item.detail)}</p>
        <div><span>影响范围</span><strong>${escapeHtml(item.impact)}</strong></div>
        <button data-notification-view="${item.view}">${escapeHtml(item.action)} <span>→</span></button>
      </div>
    </article>
  `).join("");
}

function handleNotificationClick(event) {
  const action = event.target.closest("[data-notification-view]");
  if (action) {
    const view = action.dataset.notificationView;
    if (!document.getElementById(`view-${view}`)) {
      toast("目标业务页面暂不可用", "error");
      return;
    }
    closeNotificationDrawer();
    switchView(view);
    return;
  }
  const toggle = event.target.closest("[data-notification-id]");
  if (!toggle) return;
  const item = state.notifications.find((notification) => notification.id === Number(toggle.dataset.notificationId));
  if (!item) return;
  item.expanded = !item.expanded;
  item.unread = false;
  renderNotifications();
}

function markAllNotificationsRead() {
  state.notifications.forEach((item) => { item.unread = false; });
  renderNotifications();
}

function toggleNotificationDrawer() {
  const layer = document.getElementById("notification-layer");
  setNotificationDrawer(!layer.classList.contains("open"));
}

function setNotificationDrawer(open) {
  if (open) closePreferences();
  const layer = document.getElementById("notification-layer");
  layer.classList.toggle("open", open);
  layer.setAttribute("aria-hidden", String(!open));
  document.getElementById("notification-button").setAttribute("aria-expanded", String(open));
}

function closeNotificationDrawer() {
  setNotificationDrawer(false);
}

function togglePreferences() {
  const panel = document.getElementById("preferences-panel");
  setPreferencesOpen(!panel.classList.contains("open"));
}

function setPreferencesOpen(open) {
  if (open) closeNotificationDrawer();
  const panel = document.getElementById("preferences-panel");
  panel.classList.toggle("open", open);
  panel.setAttribute("aria-hidden", String(!open));
  document.getElementById("preferences-button").setAttribute("aria-expanded", String(open));
}

function closePreferences() {
  setPreferencesOpen(false);
}

function updatePreferences() {
  const form = document.getElementById("preferences-form");
  state.preferences = {
    fontSize: form.elements.fontSize.value,
    theme: form.elements.theme.value,
    radius: form.elements.radius.value,
    compact: form.elements.compact.checked,
    reducedMotion: form.elements.reducedMotion.checked,
    highContrast: form.elements.highContrast.checked,
  };
  applyPreferences();
}

function applyPreferences() {
  const { fontSize, theme, radius, compact, reducedMotion, highContrast } = state.preferences;
  document.body.dataset.uiFont = fontSize;
  document.body.dataset.uiTheme = theme;
  document.querySelector('meta[name="theme-color"]').content = theme === "dark" ? "#0b1220" : "#f5f7fa";
  document.body.dataset.uiRadius = radius;
  document.body.dataset.uiDensity = compact ? "compact" : "comfortable";
  document.body.dataset.uiMotion = reducedMotion ? "reduced" : "full";
  document.body.dataset.uiContrast = highContrast ? "high" : "standard";
}

function resetPreferences() {
  document.getElementById("preferences-form").reset();
  state.preferences = {
    fontSize: "standard",
    theme: "light",
    radius: "standard",
    compact: false,
    reducedMotion: false,
    highContrast: false,
  };
  applyPreferences();
  toast("个性化设置已恢复默认");
}

async function api(path, options = {}) {
  const method = options.method || "GET";
  const url = new URL(path, window.location.origin);
  const fetchOptions = { method, headers: { "Content-Type": "application/json" } };
  if (method === "GET" && !options.userless) url.searchParams.set("user_id", state.userId);
  if (method !== "GET") fetchOptions.body = JSON.stringify({ ...(options.body || {}), user_id: state.userId });
  const response = await fetch(url, fetchOptions);
  let data;
  try { data = await response.json(); } catch { data = { error: "服务返回了不可解析的数据" }; }
  if (!response.ok) {
    const error = new Error(data.error || `请求失败 (${response.status})`);
    error.data = data;
    throw error;
  }
  return data;
}

function renderUserSwitcher() {
  const select = document.getElementById("user-select");
  select.innerHTML = state.users.map((user) => `<option value="${user.id}">${escapeHtml(user.name)} · ${escapeHtml(user.title)}</option>`).join("");
  select.value = String(state.userId);
  updateCurrentUserUI();
}

function updateCurrentUserUI() {
  const user = state.currentUser;
  if (!user) return;
  ["user-avatar", "ai-user-avatar"].forEach((id) => {
    const element = document.getElementById(id);
    element.textContent = user.name.slice(0, 1);
    element.style.background = user.avatar_color;
  });
  document.getElementById("user-display").textContent = `${user.name} · ${user.title}`;
  document.getElementById("welcome-name").textContent = user.name;
  document.getElementById("ai-user-name").textContent = user.name;
  document.getElementById("ai-user-role").textContent = user.title;
}

function switchView(view) {
  closeDashboardTrend({ restoreFocus: false });
  state.activeView = view;
  document.querySelectorAll(".view").forEach((element) => element.classList.toggle("active", element.id === `view-${view}`));
  document.querySelectorAll(".nav-item").forEach((element) => element.classList.toggle("active", element.dataset.view === view));
  document.getElementById("page-title").textContent = viewTitles[view] || "Aether ERP";
  document.querySelector(".sidebar").classList.remove("open");
  window.scrollTo({ top: 0, behavior: "smooth" });
  loadView(view);
}

function selectBusinessType(type, { load = true } = {}) {
  const button = document.querySelector(`#business-tabs button[data-type="${type}"]`);
  if (!button) {
    toast("未找到对应的业务模块", "error");
    return false;
  }
  document.querySelectorAll("#business-tabs button").forEach((item) => item.classList.toggle("active", item === button));
  state.businessType = type;
  if (load) loadBusiness();
  return true;
}

function navigateFromDashboard(target) {
  if (["orders", "shipments"].includes(target)) {
    if (!selectBusinessType(target, { load: false })) return;
    switchView("business");
    return;
  }
  if (["risk", "approvals"].includes(target)) {
    switchView(target);
    return;
  }
  toast("暂时无法打开对应内容", "error");
}

async function loadView(view) {
  try {
    if (view === "dashboard") await loadDashboard();
    if (view === "risk") await loadRisk();
    if (view === "business") await loadBusiness();
    if (view === "approvals") await loadApprovals();
    if (view === "audit") await Promise.all([loadAudit(), loadSettings()]);
  } catch (error) {
    showViewError(view, error);
  }
}

async function loadDashboard() {
  const data = await api("/api/dashboard");
  const metrics = data.metrics;
  document.getElementById("metric-order-amount").textContent = compactMoney(metrics.open_order_amount);
  document.getElementById("metric-order-count").textContent = `${metrics.order_count} 张订单处于执行周期`;
  document.getElementById("metric-risks").textContent = `${metrics.active_risks} 项`;
  document.getElementById("metric-approvals").textContent = `${metrics.pending_approvals} 项`;
  document.getElementById("risk-nav-count").textContent = metrics.active_risks;
  document.getElementById("approval-nav-count").textContent = metrics.pending_approvals;
  state.dashboardTrend = Array.isArray(data.trend) ? data.trend : [];
  document.getElementById("trend-chart").innerHTML = state.dashboardTrend.length ? state.dashboardTrend.map((item, index) => {
    const month = item.month || `第 ${index + 1} 期`;
    const orders = trendNumber(item.orders);
    const delivery = trendNumber(item.delivery);
    const label = `${month}，订单指数${orders === null ? "暂无数据" : number(orders)}，准时交付率${delivery === null ? "暂无数据" : `${number(delivery)}%`}，打开月度详情`;
    return `<div class="trend-column" role="button" tabindex="0" data-trend-index="${index}" aria-label="${escapeHtml(label)}"><i class="${orders === null ? "is-empty" : ""}" style="height:${trendBarHeight(orders)}%" aria-hidden="true"></i><i class="${delivery === null ? "is-empty" : ""}" style="height:${trendBarHeight(delivery)}%" aria-hidden="true"></i><span>${escapeHtml(month)}</span></div>`;
  }).join("") : `<div class="trend-chart-empty">暂无趋势数据</div>`;
  document.getElementById("dashboard-orders").innerHTML = data.orders.slice(0, 4).map((order) => `
    <tr><td><strong>${escapeHtml(order.order_number)}</strong></td><td>${escapeHtml(order.customer_name)}</td><td>${escapeHtml(order.due_date)}</td>
    <td>${formatMoney(order.total_amount)}</td><td>${priorityTag(order.priority)}</td><td>${statusTag(order.status)}</td></tr>
  `).join("");
}

function trendNumber(value) {
  const parsed = Number(value);
  return value === null || value === undefined || value === "" || !Number.isFinite(parsed) ? null : parsed;
}

function trendBarHeight(value) {
  return value === null ? 0 : Math.max(0, Math.min(100, value));
}

function trendComparison(currentValue, previousValue, suffix = "") {
  const current = trendNumber(currentValue);
  const previous = trendNumber(previousValue);
  if (current === null) return `<span class="dashboard-trend-delta muted">暂无数据</span>`;
  if (previous === null) return `<span class="dashboard-trend-delta muted">暂无上月数据</span>`;
  const delta = Number((current - previous).toFixed(1));
  const direction = delta > 0 ? "up" : delta < 0 ? "down" : "neutral";
  const symbol = delta > 0 ? "↑" : delta < 0 ? "↓" : "→";
  return `<span class="dashboard-trend-delta ${direction}">较上月 ${symbol} ${number(Math.abs(delta))}${suffix}</span>`;
}

function openDashboardTrend(index, trigger) {
  const item = state.dashboardTrend[index];
  if (!item) {
    toast("该月份暂无可查看的数据", "error");
    return;
  }
  const previous = index > 0 ? state.dashboardTrend[index - 1] : null;
  const month = item.month || `第 ${index + 1} 期`;
  const orders = trendNumber(item.orders);
  const delivery = trendNumber(item.delivery);
  state.dashboardTrendIndex = index;
  state.dashboardTrendTrigger = trigger;
  document.getElementById("dashboard-trend-title").textContent = `${month}经营详情`;
  document.getElementById("dashboard-trend-subtitle").textContent = "订单与交付月度汇总";
  document.getElementById("dashboard-trend-period").textContent = month;
  document.getElementById("dashboard-trend-summary").innerHTML = `
    <article class="dashboard-trend-metric orders"><span>订单指数</span><strong>${orders === null ? "暂无数据" : number(orders)}</strong>${trendComparison(orders, previous?.orders)}</article>
    <article class="dashboard-trend-metric delivery"><span>准时交付率</span><strong>${delivery === null ? "暂无数据" : `${number(delivery)}%`}</strong>${trendComparison(delivery, previous?.delivery, "%")}</article>`;
  const modal = document.getElementById("dashboard-trend-modal");
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  modal.querySelector(".modal-close").focus();
}

function closeDashboardTrend({ restoreFocus = true } = {}) {
  const modal = document.getElementById("dashboard-trend-modal");
  if (!modal || modal.classList.contains("hidden")) return;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  state.dashboardTrendIndex = null;
  const trigger = state.dashboardTrendTrigger;
  state.dashboardTrendTrigger = null;
  if (restoreFocus && trigger?.isConnected) trigger.focus();
}

async function loadRisk() {
  const data = await api("/api/risk");
  state.risk = data;
  const metrics = data.metrics;
  document.getElementById("dash-shortage").innerHTML = `${number(metrics.shortage)}<small>kg</small>`;
  document.getElementById("risk-shortage").textContent = `${number(metrics.shortage)}kg`;
  document.getElementById("risk-orders-count").textContent = `${metrics.affected_orders} 张`;
  document.getElementById("risk-critical-count").textContent = `${metrics.critical_orders} 张`;
  renderImpactGraph(data.chain);
  document.getElementById("risk-explanation").innerHTML = data.explanation.map((text, index) => `<div class="explain-row"><i>${index + 1}</i><span>${escapeHtml(text)}</span></div>`).join("");
  document.getElementById("risk-recommendation").innerHTML = `<small>AI RECOMMENDATION</small><strong>建议补货 ${number(data.recommendation.quantity)}kg</strong><p>${escapeHtml(data.recommendation.reason)}优先供应商：${escapeHtml(data.recommendation.supplier_name)}。</p>`;
  document.getElementById("risk-orders").innerHTML = data.affected_orders.map((order) => `
    <tr><td><strong>${escapeHtml(order.task_number)}</strong><br><small>${escapeHtml(order.order_number)}</small></td><td>${escapeHtml(order.customer_name)}</td>
    <td><span class="tag blue">${escapeHtml(order.customer_tier)}</span></td><td>${number(order.required_qty)} kg</td><td>${escapeHtml(order.due_date)}</td>
    <td>${priorityTag(order.priority)}</td><td>${formatMoney(order.total_amount)}</td></tr>
  `).join("");
}

function renderImpactGraph(chain) {
  document.getElementById("impact-graph").innerHTML = `
    <div class="impact-node"><span>供应来源</span><strong>${escapeHtml(chain.source.label)}</strong><small>${escapeHtml(chain.source.meta)}</small></div>
    <div class="impact-node risk"><span>风险节点</span><strong>${escapeHtml(chain.risk.label)}</strong><small>${escapeHtml(chain.risk.meta)}</small></div>
    <div class="order-node-list">${chain.orders.map((order) => `<div class="order-node"><strong>${escapeHtml(order.label)}</strong><span>${escapeHtml(order.meta)}</span></div>`).join("")}</div>
  `;
}

async function runCommand(command) {
  command = String(command || "").trim();
  if (!command) return;
  const input = document.getElementById("command-input");
  const button = document.getElementById("send-command");
  appendMessage("user", command);
  input.value = "";
  button.disabled = true;
  const thinking = appendThinking();
  try {
    const response = await api("/api/ai/command", { method: "POST", body: { command } });
    thinking.remove();
    appendMessage("assistant", buildAIReply(response));
    renderExecution(response);
    if (response.execution.mode === "approval") {
      await loadApprovals();
      toast(`审批单 #${response.execution.approval.id} 已创建`);
    }
    if (response.plan.action === "risk.analyze") await loadRisk();
  } catch (error) {
    thinking.remove();
    appendMessage("assistant", `操作被系统拦截：${error.message}`);
    renderExecutionError(error);
    toast(error.message, "error");
  } finally {
    button.disabled = false;
    input.focus();
  }
}

function buildAIReply(response) {
  const { plan, execution, provider_label: provider } = response;
  if (execution.mode === "approval") {
    return `${plan.summary} 已创建审批单 #${execution.approval.id}，在管理员批准前不会写入正式业务数据。当前由${provider}完成计划生成。`;
  }
  if (plan.action === "risk.analyze") {
    const metrics = execution.result.metrics;
    return `分析完成：${execution.result.material.name}预计缺口 ${number(metrics.shortage)}kg，影响 ${metrics.affected_orders} 张订单，其中 ${metrics.critical_orders} 张为关键订单。计算依据与影响链已同步到供应链风险中心。`;
  }
  if (plan.action === "permissions.describe") {
    const info = execution.result;
    const labels = info.permissions.slice(0, 6).map((item) => item.label).join("、");
    return `${info.message} 当前可执行：${labels}${info.permissions.length > 6 ? "等操作" : ""}。`;
  }
  if (plan.action === "dashboard.get") return "经营总览已读取。当前首要事项仍是航空铝板缺料风险与待审批的补货方案。";
  return plan.summary;
}

function appendMessage(role, text) {
  const messages = document.getElementById("messages");
  const element = document.createElement("div");
  element.className = `message ${role}`;
  if (role === "assistant") {
    element.innerHTML = `<span class="message-avatar">A</span><div class="message-content"><span class="message-label">Aether Copilot</span><p>${escapeHtml(text)}</p></div>`;
  } else {
    element.innerHTML = `<div class="message-content"><span class="message-label">${escapeHtml(state.currentUser.name)}</span><p>${escapeHtml(text)}</p></div>`;
  }
  messages.appendChild(element);
  messages.scrollTop = messages.scrollHeight;
  return element;
}

function appendThinking() {
  const messages = document.getElementById("messages");
  const element = document.createElement("div");
  element.className = "message assistant";
  element.innerHTML = `<span class="message-avatar">A</span><div class="message-content"><span class="message-label">正在生成受控计划</span><div class="thinking-dots"><i></i><i></i><i></i></div></div>`;
  messages.appendChild(element);
  messages.scrollTop = messages.scrollHeight;
  return element;
}

function resetChat() {
  document.getElementById("messages").innerHTML = `<div class="message assistant"><span class="message-avatar">A</span><div class="message-content"><span class="message-label">Aether Copilot</span><p>上下文已清空。你可以重新发起供应链分析或业务执行指令。</p></div></div>`;
  document.getElementById("execution-empty").classList.remove("hidden");
  document.getElementById("execution-content").classList.add("hidden");
  document.getElementById("execution-title").textContent = "等待业务指令";
}

function renderExecution(response) {
  const { plan, provider_trail: trail, execution } = response;
  document.getElementById("execution-empty").classList.add("hidden");
  document.getElementById("execution-content").classList.remove("hidden");
  document.getElementById("execution-title").textContent = plan.summary;
  const risk = document.getElementById("execution-risk");
  risk.textContent = `${plan.risk_level.toUpperCase()} RISK`;
  risk.className = `risk-pill ${plan.risk_level}`;
  document.getElementById("plan-steps").innerHTML = plan.steps.map((step) => `
    <div class="step-item ${step.status === "pending" ? "pending" : ""}"><span class="step-dot">${step.status === "pending" ? "…" : "✓"}</span><strong>${escapeHtml(step.name)}</strong><small>${escapeHtml(step.detail || "")}</small></div>
  `).join("");
  document.getElementById("provider-trail").innerHTML = `<p>AI PROVIDER FALLBACK</p>${trail.map((item) => `<div class="trail-row ${item.status}"><span><i></i>${providerName(item.provider)}</span><b>${trailStatus(item)}</b></div>`).join("")}`;
  let resultHtml;
  if (execution.mode === "approval") {
    resultHtml = `<strong>等待人工审批</strong>审批单 #${execution.approval.id} 已写入审批中心，正式数据尚未变更。<button class="result-cta" data-view="approvals">前往审批中心 →</button>`;
  } else {
    resultHtml = `<strong>执行完成 · ${response.duration_ms}ms</strong>动作已通过权限与参数校验，结果已写入审计日志。`;
  }
  document.getElementById("execution-result").innerHTML = resultHtml;
}

function renderExecutionError(error) {
  document.getElementById("execution-empty").classList.add("hidden");
  document.getElementById("execution-content").classList.remove("hidden");
  document.getElementById("execution-title").textContent = "动作被安全网关拦截";
  const risk = document.getElementById("execution-risk");
  risk.textContent = "DENIED";
  risk.className = "risk-pill high";
  document.getElementById("plan-steps").innerHTML = `<div class="step-item"><span class="step-dot">✓</span><strong>识别业务意图</strong><small>已识别受控业务动作</small></div><div class="step-item pending"><span class="step-dot">!</span><strong>权限校验失败</strong><small>${escapeHtml(error.message)}</small></div>`;
  document.getElementById("provider-trail").innerHTML = "";
  document.getElementById("execution-result").innerHTML = `<strong>未执行任何数据变更</strong>本次拦截已写入审计日志。`;
}

async function loadBusiness() {
  const container = document.getElementById("business-table");
  container.innerHTML = `<div class="empty-state"><div><span>◌</span><strong>正在读取业务数据</strong></div></div>`;
  try {
    const data = await api(`/api/business?type=${encodeURIComponent(state.businessType)}`);
    state.businessItems = data.items;
    renderBusinessTable();
  } catch (error) {
    state.businessItems = [];
    container.innerHTML = `<div class="empty-state"><div><span>🔒</span><strong>当前角色无权查看此业务域</strong><p>${escapeHtml(error.message)}</p></div></div>`;
  }
}

function renderBusinessTable() {
  const columns = businessColumns[state.businessType];
  const items = state.businessItems;
  if (!items.length) {
    document.getElementById("business-table").innerHTML = `<div class="empty-state"><div><span>◇</span><strong>暂无业务数据</strong></div></div>`;
    return;
  }
  document.getElementById("business-table").innerHTML = `<table><thead><tr>${columns.map(([, label]) => `<th>${label}</th>`).join("")}</tr></thead><tbody>${items.map((item) => {
    const meta = businessDetailMeta[state.businessType];
    const interactive = ` class="business-detail-row" data-business-type="${state.businessType}" data-business-id="${Number(item.id)}" tabindex="0" role="button" aria-label="查看${meta.label} ${escapeHtml(item[meta.primary])} 详情"`;
    return `<tr${interactive}>${columns.map(([key,, type]) => `<td>${formatBusinessCell(item, key, type)}</td>`).join("")}</tr>`;
  }).join("")}</tbody></table>`;
  filterBusinessTable();
}

function formatBusinessCell(item, key, type) {
  const value = formatCell(item[key], type);
  if (businessLinkFields[state.businessType]?.includes(key)) {
    return `<span class="business-cell-link">${value}</span>`;
  }
  return value;
}

function filterBusinessTable() {
  const term = document.getElementById("business-search").value.trim().toLowerCase();
  document.querySelectorAll("#business-table tbody tr").forEach((row) => {
    row.style.display = !term || row.textContent.toLowerCase().includes(term) ? "" : "none";
  });
}

async function openBusinessDetail(type, recordId, trigger = null) {
  const meta = businessDetailMeta[type];
  if (!meta) return;
  const modal = document.getElementById("business-detail-modal");
  const body = document.getElementById("business-detail-body");
  const listItem = state.businessItems.find((item) => item.id === recordId);
  const requestId = ++state.businessDetailRequest;
  state.selectedBusinessDetail = { type, recordId };
  state.businessDetailTrigger = trigger;
  setBusinessDetailHeading(
    listItem?.[meta.primary] || `${meta.label}详情`,
    listItem ? meta.secondary.map((key) => listItem[key]).filter(Boolean).join(" · ") : `正在读取${meta.label}信息`,
  );
  body.innerHTML = `<div class="order-detail-loading"><span></span><strong>正在读取完整${meta.label}信息</strong><small>同步基础信息与关联业务数据</small></div>`;
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  modal.querySelector(".modal-close").focus();
  try {
    const endpoint = type === "orders" ? `/api/orders/${recordId}` : `/api/business/${type}/${recordId}`;
    const detail = await api(endpoint);
    if (requestId !== state.businessDetailRequest || state.selectedBusinessDetail?.type !== type || state.selectedBusinessDetail?.recordId !== recordId) return;
    if (type === "orders") renderOrderDetail(detail);
    else renderBusinessEntityDetail(type, detail);
  } catch (error) {
    if (requestId !== state.businessDetailRequest) return;
    body.innerHTML = `<div class="order-detail-error"><span>!</span><strong>${meta.label}详情读取失败</strong><p>${escapeHtml(error.message)}</p><button class="button secondary" data-close-business-detail>关闭</button></div>`;
  }
}

function closeBusinessDetail() {
  const modal = document.getElementById("business-detail-modal");
  if (modal.classList.contains("hidden")) return;
  state.businessDetailRequest += 1;
  state.selectedBusinessDetail = null;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  const trigger = state.businessDetailTrigger;
  state.businessDetailTrigger = null;
  if (trigger?.isConnected) trigger.focus();
}

function setBusinessDetailHeading(title, subtitle) {
  document.getElementById("business-detail-title").textContent = title;
  document.getElementById("business-detail-subtitle").textContent = subtitle;
}

function renderOrderDetail(detail) {
  const { order, items, production_tasks: tasks, shipments, invoices, risks } = detail;
  setBusinessDetailHeading(order.order_number, `${order.customer_name} · ${order.customer_tier}`);
  const itemRows = items.map((item) => `
    <tr><td><strong>${escapeHtml(item.product_name)}</strong><small>${number(item.quantity)} 件</small></td><td><strong>${escapeHtml(item.material_name)}</strong><small>${escapeHtml(item.material_code)} · ${escapeHtml(item.material_specification)}</small></td><td>${number(item.material_qty_per_unit)} ${escapeHtml(item.material_unit)}</td><td><strong>${number(item.required_material_qty)} ${escapeHtml(item.material_unit)}</strong></td></tr>
  `).join("");
  const taskCards = tasks.map((task) => {
    const progress = task.required_qty ? Math.min(100, Math.round(task.completed_qty / task.required_qty * 100)) : 0;
    return `<article class="order-progress-card"><div><span>${escapeHtml(task.task_number)}</span>${statusTag(task.status)}</div><strong>${escapeHtml(task.material_name)} · ${escapeHtml(task.material_code)}</strong><p><span>完成 ${number(task.completed_qty)} / ${number(task.required_qty)} ${escapeHtml(task.material_unit)}</span><b>${progress}%</b></p><div class="order-progress-track"><i style="width:${progress}%"></i></div></article>`;
  }).join("");
  const shipmentCards = shipments.map((item) => `<div class="fulfillment-record"><span>出货单</span><strong>${escapeHtml(item.shipment_number)}</strong><small>${statusTag(item.status)} ${formatDateTime(item.shipped_at)}</small></div>`).join("");
  const invoiceCards = invoices.map((item) => `<div class="fulfillment-record"><span>发票</span><strong>${escapeHtml(item.invoice_number)}</strong><small>${statusTag(item.status)} ${formatMoney(item.amount)} · ${formatDateTime(item.created_at)}</small></div>`).join("");
  const riskCards = risks.map((risk) => `<article class="order-risk-record"><div><span>${escapeHtml(risk.event_code)} · ${escapeHtml(risk.severity)}</span>${statusTag(risk.status)}</div><strong>${escapeHtml(risk.material_name)}存在${escapeHtml(risk.event_type)}</strong><p>${escapeHtml(risk.description)}</p><small>预计缺口 ${number(risk.shortage_qty)} ${escapeHtml(risk.material_unit)} · ${formatDateTime(risk.created_at)}</small></article>`).join("");

  document.getElementById("business-detail-body").innerHTML = `
    <div class="order-detail-hero"><div><span>订单金额</span><strong>${formatMoney(order.total_amount)}</strong></div><div><span>当前状态</span>${statusTag(order.status)}</div><div><span>优先级</span>${priorityTag(order.priority)}</div><div><span>交付节点</span><strong>${escapeHtml(order.due_date)}</strong><small>${dueDateStatus(order.due_date)}</small></div></div>
    <div class="order-detail-grid">
      <section class="order-detail-section"><div class="order-detail-section-head"><span>ORDER</span><h3>订单信息</h3></div><dl class="order-detail-fields"><div><dt>订单编号</dt><dd>${escapeHtml(order.order_number)}</dd></div><div><dt>创建时间</dt><dd>${formatDateTime(order.created_at)}</dd></div><div><dt>交付日期</dt><dd>${escapeHtml(order.due_date)}</dd></div><div><dt>订单状态</dt><dd>${escapeHtml(order.status)}</dd></div></dl></section>
      <section class="order-detail-section"><div class="order-detail-section-head"><span>CUSTOMER</span><h3>客户信息</h3></div><dl class="order-detail-fields"><div><dt>客户编码</dt><dd>${escapeHtml(order.customer_code)}</dd></div><div><dt>客户名称</dt><dd>${escapeHtml(order.customer_name)}</dd></div><div><dt>客户等级</dt><dd>${escapeHtml(order.customer_tier)}</dd></div><div><dt>所属行业</dt><dd>${escapeHtml(order.customer_industry)}</dd></div><div class="wide"><dt>业务联系人</dt><dd>${escapeHtml(order.customer_contact)}</dd></div></dl></section>
    </div>
    <section class="order-detail-section"><div class="order-detail-section-head"><span>ITEMS & MATERIALS</span><h3>产品与物料</h3></div><div class="order-detail-table"><table><thead><tr><th>产品</th><th>关联物料</th><th>单位用量</th><th>需求总量</th></tr></thead><tbody>${itemRows || `<tr><td colspan="4">暂无产品明细</td></tr>`}</tbody></table></div></section>
    <section class="order-detail-section"><div class="order-detail-section-head"><span>PRODUCTION</span><h3>生产进度</h3></div><div class="order-progress-list">${taskCards || detailEmpty("暂无生产任务")}</div></section>
    <div class="order-detail-grid">
      <section class="order-detail-section"><div class="order-detail-section-head"><span>FULFILLMENT</span><h3>出货与发票</h3></div><div class="fulfillment-list">${shipmentCards || detailEmpty("暂无出货记录")}${invoiceCards || detailEmpty("暂无发票记录")}</div></section>
      <section class="order-detail-section"><div class="order-detail-section-head"><span>RISK</span><h3>关联风险</h3></div><div class="order-risk-list">${riskCards || detailEmpty("暂无关联风险")}</div></section>
    </div>`;
}

function renderBusinessEntityDetail(type, detail) {
  const renderers = {
    materials: renderMaterialDetail,
    suppliers: renderSupplierDetail,
    purchases: renderPurchaseDetail,
    shipments: renderShipmentDetail,
    invoices: renderInvoiceDetail,
  };
  const renderer = renderers[type];
  if (!renderer) throw new Error("暂不支持该业务详情");
  renderer(detail);
}

function renderMaterialDetail({ record: material, purchases, production, risks }) {
  const inventoryValue = material.current_stock * material.unit_cost;
  const stockGap = material.current_stock - material.safety_stock;
  const health = stockHealth(material.current_stock, material.safety_stock);
  setBusinessDetailHeading(material.code, `${material.name} · ${material.specification}`);
  const purchaseRows = purchases.map((item) => `<tr><td><strong>${escapeHtml(item.po_number)}</strong><small>${escapeHtml(item.supplier_name)}</small></td><td>${statusTag(item.status)}</td><td>${number(item.quantity)} / ${number(item.received_quantity)} ${escapeHtml(material.unit)}</td><td><strong>${number(item.pending_quantity)} ${escapeHtml(material.unit)}</strong></td><td>${escapeHtml(item.expected_date)}</td></tr>`).join("");
  const productionRows = production.map((item) => `<tr><td><strong>${escapeHtml(item.task_number)}</strong><small>${escapeHtml(item.order_number)}</small></td><td>${escapeHtml(item.customer_name)}</td><td>${number(item.completed_qty)} / ${number(item.required_qty)} ${escapeHtml(material.unit)}</td><td>${statusTag(item.status)}</td><td>${escapeHtml(item.due_date)}</td></tr>`).join("");
  document.getElementById("business-detail-body").innerHTML = `
    <div class="order-detail-hero"><div><span>当前库存</span><strong>${number(material.current_stock)} ${escapeHtml(material.unit)}</strong></div><div><span>安全库存</span><strong>${number(material.safety_stock)} ${escapeHtml(material.unit)}</strong></div><div><span>库存状态</span><span class="tag ${health.cls}">${health.label}</span></div><div><span>库存金额</span><strong>${formatMoney(inventoryValue)}</strong></div></div>
    <div class="order-detail-grid">
      ${detailSection("MATERIAL", "物料信息", detailFields([["物料编码", material.code], ["物料名称", material.name], ["规格型号", material.specification], ["计量单位", material.unit], ["单位成本", formatMoney(material.unit_cost)]]))}
      ${detailSection("INVENTORY", "库存分析", detailFields([["当前库存", `${number(material.current_stock)} ${material.unit}`], ["安全库存", `${number(material.safety_stock)} ${material.unit}`], ["安全线差值", `${stockGap >= 0 ? "+" : ""}${number(stockGap)} ${material.unit}`], ["库存状态", health.label], ["库存价值", formatMoney(inventoryValue)]]))}
    </div>
    ${detailSection("INBOUND PURCHASE", "采购在途", detailTable(["采购单 / 供应商", "状态", "订购 / 已到", "未到数量", "预计到货"], purchaseRows, "暂无采购在途记录"))}
    ${detailSection("PRODUCTION DEMAND", "生产需求", detailTable(["工单 / 订单", "客户", "完成 / 需求", "状态", "交付日期"], productionRows, "暂无生产需求"))}
    ${detailSection("RISK", "风险事件", renderRiskRecords(risks, material))}`;
}

function renderSupplierDetail({ record: supplier, stats, purchases, materials }) {
  setBusinessDetailHeading(supplier.code, `${supplier.name} · ${supplier.rating} 级供应商`);
  const purchaseRows = purchases.map((item) => `<tr><td><strong>${escapeHtml(item.po_number)}</strong><small>${formatDateTime(item.created_at)}</small></td><td>${statusTag(item.status)}</td><td>${escapeHtml(item.expected_date)}</td><td><strong>${formatMoney(item.total_amount)}</strong></td></tr>`).join("");
  const materialRows = materials.map((item) => `<tr><td><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.code)} · ${escapeHtml(item.specification)}</small></td><td>${number(item.purchased_quantity)} ${escapeHtml(item.unit)}</td><td>${number(item.received_quantity)} ${escapeHtml(item.unit)}</td><td><strong>${number(item.pending_quantity)} ${escapeHtml(item.unit)}</strong></td></tr>`).join("");
  document.getElementById("business-detail-body").innerHTML = `
    <div class="order-detail-hero"><div><span>供应商评级</span><span class="tag blue">${escapeHtml(supplier.rating)} 级</span></div><div><span>标准交期</span><strong>${number(supplier.lead_days)} 天</strong></div><div><span>准时交付率</span><strong>${number(supplier.on_time_rate)}%</strong></div><div><span>累计采购金额</span><strong>${formatMoney(stats.total_amount)}</strong></div></div>
    <div class="order-detail-grid">
      ${detailSection("SUPPLIER", "供应商信息", detailFields([["供应商编码", supplier.code], ["供应商名称", supplier.name], ["供应商评级", `${supplier.rating} 级`], ["业务联系人", supplier.contact], ["标准交期", `${number(supplier.lead_days)} 天`]]))}
      ${detailSection("PURCHASE SUMMARY", "采购汇总", detailFields([["采购单数量", `${number(stats.purchase_count)} 张`], ["未完成采购", `${number(stats.open_count)} 张`], ["累计采购金额", formatMoney(stats.total_amount)], ["平均订单金额", formatMoney(stats.average_amount)], ["准时交付率", `${number(supplier.on_time_rate)}%`]]))}
    </div>
    ${detailSection("PURCHASE HISTORY", "采购历史", detailTable(["采购单", "状态", "预计到货", "采购金额"], purchaseRows, "暂无采购记录"))}
    ${detailSection("SUPPLIED MATERIALS", "供应物料", detailTable(["物料", "累计采购", "已到货", "未到数量"], materialRows, "暂无供应物料记录"))}`;
}

function renderPurchaseDetail({ record: purchase, items, risks }) {
  const ordered = items.reduce((sum, item) => sum + Number(item.quantity || 0), 0);
  const received = items.reduce((sum, item) => sum + Number(item.received_quantity || 0), 0);
  const progress = progressPercent(received, ordered);
  setBusinessDetailHeading(purchase.po_number, `${purchase.supplier_name} · ${purchase.status}`);
  const itemRows = items.map((item) => {
    const itemProgress = progressPercent(item.received_quantity, item.quantity);
    return `<tr><td><strong>${escapeHtml(item.material_name)}</strong><small>${escapeHtml(item.material_code)} · ${escapeHtml(item.material_specification)}</small></td><td>${number(item.quantity)} ${escapeHtml(item.material_unit)}</td><td>${number(item.received_quantity)} ${escapeHtml(item.material_unit)}</td><td>${number(item.pending_quantity)} ${escapeHtml(item.material_unit)}</td><td><strong>${itemProgress}%</strong><div class="mini-progress"><i style="width:${itemProgress}%"></i></div></td></tr>`;
  }).join("");
  document.getElementById("business-detail-body").innerHTML = `
    <div class="order-detail-hero"><div><span>采购金额</span><strong>${formatMoney(purchase.total_amount)}</strong></div><div><span>当前状态</span>${statusTag(purchase.status)}</div><div><span>预计到货</span><strong>${escapeHtml(purchase.expected_date)}</strong></div><div><span>到货进度</span><strong>${progress}%</strong><div class="hero-progress"><i style="width:${progress}%"></i></div></div></div>
    <div class="order-detail-grid">
      ${detailSection("PURCHASE", "采购信息", detailFields([["采购单号", purchase.po_number], ["创建时间", formatDateTime(purchase.created_at)], ["预计到货", purchase.expected_date], ["采购状态", purchase.status], ["采购金额", formatMoney(purchase.total_amount)]]))}
      ${detailSection("SUPPLIER", "供应商信息", detailFields([["供应商编码", purchase.supplier_code], ["供应商名称", purchase.supplier_name], ["供应商评级", `${purchase.supplier_rating} 级`], ["标准交期", `${number(purchase.supplier_lead_days)} 天`], ["准时交付率", `${number(purchase.supplier_on_time_rate)}%`], ["业务联系人", purchase.supplier_contact]]))}
    </div>
    ${detailSection("PURCHASE ITEMS", "采购明细", detailTable(["物料", "订购数量", "已到货", "未到数量", "完成率"], itemRows, "暂无采购明细"))}
    ${detailSection("RISK", "关联风险", renderRiskRecords(risks))}`;
}

function renderShipmentDetail({ record: shipment, items, invoices }) {
  setBusinessDetailHeading(shipment.shipment_number, `${shipment.order_number} · ${shipment.customer_name}`);
  const itemRows = fulfillmentItemRows(items);
  const invoiceRecords = invoices.map((item) => `<div class="fulfillment-record"><span>发票</span><strong>${escapeHtml(item.invoice_number)}</strong><small>${statusTag(item.status)} ${formatMoney(item.amount)} · ${formatDateTime(item.created_at)}</small></div>`).join("");
  document.getElementById("business-detail-body").innerHTML = `
    <div class="order-detail-hero"><div><span>出货状态</span>${statusTag(shipment.status)}</div><div><span>出货时间</span><strong>${formatDateTime(shipment.shipped_at)}</strong></div><div><span>订单金额</span><strong>${formatMoney(shipment.order_amount)}</strong></div><div><span>计划交付</span><strong>${escapeHtml(shipment.due_date)}</strong><small>${dueDateStatus(shipment.due_date)}</small></div></div>
    <div class="order-detail-grid">
      ${detailSection("SHIPMENT & ORDER", "出货与订单", detailFields([["出货单号", shipment.shipment_number], ["出货状态", shipment.status], ["销售订单", shipment.order_number], ["订单状态", shipment.order_status], ["订单优先级", shipment.order_priority], ["订单金额", formatMoney(shipment.order_amount)]]))}
      ${detailSection("CUSTOMER", "客户信息", detailFields([["客户编码", shipment.customer_code], ["客户名称", shipment.customer_name], ["客户等级", shipment.customer_tier], ["所属行业", shipment.customer_industry], ["业务联系人", shipment.customer_contact]]))}
    </div>
    ${detailSection("PRODUCTS", "产品明细", detailTable(["产品", "数量", "关联物料", "物料规格"], itemRows, "暂无产品明细"))}
    ${detailSection("INVOICE", "发票状态", `<div class="fulfillment-list">${invoiceRecords || detailEmpty("暂无关联发票")}</div>`)}`;
}

function renderInvoiceDetail({ record: invoice, items, shipments }) {
  setBusinessDetailHeading(invoice.invoice_number, `${invoice.order_number} · ${invoice.customer_name}`);
  const itemRows = fulfillmentItemRows(items);
  const shipmentRecords = shipments.map((item) => `<div class="fulfillment-record"><span>出货单</span><strong>${escapeHtml(item.shipment_number)}</strong><small>${statusTag(item.status)} ${formatDateTime(item.shipped_at)}</small></div>`).join("");
  document.getElementById("business-detail-body").innerHTML = `
    <div class="order-detail-hero"><div><span>开票金额</span><strong>${formatMoney(invoice.amount)}</strong></div><div><span>发票状态</span>${statusTag(invoice.status)}</div><div><span>订单金额</span><strong>${formatMoney(invoice.order_amount)}</strong></div><div><span>开票时间</span><strong>${formatDateTime(invoice.created_at)}</strong></div></div>
    <div class="order-detail-grid">
      ${detailSection("INVOICE & ORDER", "发票与订单", detailFields([["发票号码", invoice.invoice_number], ["发票状态", invoice.status], ["销售订单", invoice.order_number], ["订单状态", invoice.order_status], ["订单优先级", invoice.order_priority], ["计划交付", invoice.due_date]]))}
      ${detailSection("CUSTOMER", "客户信息", detailFields([["客户编码", invoice.customer_code], ["客户名称", invoice.customer_name], ["客户等级", invoice.customer_tier], ["所属行业", invoice.customer_industry], ["业务联系人", invoice.customer_contact]]))}
    </div>
    ${detailSection("PRODUCTS", "产品明细", detailTable(["产品", "数量", "关联物料", "物料规格"], itemRows, "暂无产品明细"))}
    ${detailSection("SHIPMENT", "出货状态", `<div class="fulfillment-list">${shipmentRecords || detailEmpty("暂无关联出货记录")}</div>`)}`;
}

function detailSection(kicker, title, content) {
  return `<section class="order-detail-section"><div class="order-detail-section-head"><span>${escapeHtml(kicker)}</span><h3>${escapeHtml(title)}</h3></div>${content}</section>`;
}

function detailFields(fields) {
  return `<dl class="order-detail-fields">${fields.map(([label, value], index) => `<div class="${index === fields.length - 1 && fields.length % 2 ? "wide" : ""}"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value ?? "—")}</dd></div>`).join("")}</dl>`;
}

function detailTable(headers, rows, emptyMessage) {
  if (!rows) return detailEmpty(emptyMessage);
  return `<div class="order-detail-table"><table><thead><tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderRiskRecords(risks, material = null) {
  if (!risks.length) return detailEmpty("暂无关联风险");
  return `<div class="order-risk-list">${risks.map((risk) => `<article class="order-risk-record"><div><span>${escapeHtml(risk.event_code)} · ${escapeHtml(risk.severity)}</span>${statusTag(risk.status)}</div><strong>${escapeHtml(material?.name || risk.material_name || "关联物料")}存在${escapeHtml(risk.event_type)}</strong><p>${escapeHtml(risk.description)}</p><small>预计缺口 ${number(risk.shortage_qty)} ${escapeHtml(material?.unit || risk.material_unit || "")} · ${formatDateTime(risk.created_at)}</small></article>`).join("")}</div>`;
}

function fulfillmentItemRows(items) {
  return items.map((item) => `<tr><td><strong>${escapeHtml(item.product_name)}</strong></td><td>${number(item.quantity)} 件</td><td><strong>${escapeHtml(item.material_name)}</strong><small>${escapeHtml(item.material_code)}</small></td><td>${escapeHtml(item.material_specification)}</td></tr>`).join("");
}

function progressPercent(done, total) {
  return Number(total) > 0 ? Math.min(100, Math.round(Number(done || 0) / Number(total) * 100)) : 0;
}

function stockHealth(current, safety) {
  if (Number(current) < Number(safety)) return { label: "低于安全库存", cls: "red" };
  if (Number(current) <= Number(safety) * 1.2) return { label: "接近安全线", cls: "amber" };
  return { label: "库存充足", cls: "green" };
}

function detailEmpty(message) {
  return `<div class="order-detail-empty"><span>✓</span><small>${escapeHtml(message)}</small></div>`;
}

function dueDateStatus(value) {
  const due = new Date(`${value}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((due - today) / 86400000);
  if (days === 0) return "今天交付";
  return days > 0 ? `剩余 ${days} 天` : `已到期 ${Math.abs(days)} 天`;
}

async function loadApprovals() {
  const { approvals } = await api("/api/approvals");
  state.approvals = approvals;
  const pending = approvals.filter((item) => item.status === "pending").length;
  const completed = approvals.length - pending;
  document.getElementById("pending-count").textContent = pending;
  document.getElementById("completed-count").textContent = completed;
  document.getElementById("approval-nav-count").textContent = pending;
  const container = document.getElementById("approval-list");
  container.innerHTML = approvals.length ? approvals.map(renderApprovalCard).join("") : `<div class="empty-state"><div><span>✓</span><strong>没有待处理审批</strong></div></div>`;
  container.querySelectorAll("[data-approval-id]").forEach((button) => button.addEventListener("click", () => openApprovalModal(Number(button.dataset.approvalId))));
}

function renderApprovalCard(item) {
  const data = Object.entries(item.payload || {}).slice(0, 5).map(([key, value]) => `<span>${escapeHtml(fieldLabel(key))}：${escapeHtml(Array.isArray(value) ? value.join("、") : value)}</span>`).join("");
  const actions = item.status === "pending"
    ? state.currentUser.role === "admin"
      ? `<button class="button primary" data-approval-id="${item.id}">查看并审批</button><small>批准后由 AI 执行业务事务</small>`
      : `<span class="tag amber">等待管理员审批</span><small>当前角色可查看但无审批权</small>`
    : `<span class="tag ${item.status === "approved" ? "green" : "red"}">${item.status === "approved" ? "已批准并执行" : "已驳回"}</span><small>${escapeHtml(item.decision_note || "审批流程已结束")}</small>`;
  return `<article class="approval-card ${item.status}"><div class="approval-main"><div class="approval-top"><div class="approval-title"><span class="approval-icon">${item.action_type.startsWith("purchase") ? "＋" : "↗"}</span><div><strong>${escapeHtml(item.action_label)}</strong><small>#${item.id} · ${escapeHtml(item.requester_name || "系统")} 发起 · ${formatDateTime(item.created_at)}</small></div></div><span class="tag red">HIGH RISK</span></div><p class="approval-reason">${escapeHtml(item.reason)}</p><div class="approval-data">${data}</div></div><div class="approval-actions">${actions}</div></article>`;
}

function openApprovalModal(id) {
  const item = state.approvals.find((approval) => approval.id === id);
  if (!item) return;
  state.selectedApproval = item;
  document.getElementById("modal-title").textContent = item.action_label;
  document.getElementById("modal-body").innerHTML = `<div class="modal-body-card"><p><strong>申请原因：</strong>${escapeHtml(item.reason)}</p><p><strong>发起人：</strong>${escapeHtml(item.requester_name)}</p><p><strong>执行参数：</strong>${escapeHtml(JSON.stringify(item.payload))}</p><p><strong>安全说明：</strong>批准后将在单个 SQLite 事务中执行，并写入完整审计记录。</p></div>`;
  document.getElementById("approval-note").value = "已核对风险影响与执行参数，同意按方案执行。";
  document.getElementById("approval-modal").classList.remove("hidden");
}

function closeApprovalModal() {
  document.getElementById("approval-modal").classList.add("hidden");
  state.selectedApproval = null;
}

async function decideSelectedApproval(decision) {
  if (!state.selectedApproval) return;
  const note = document.getElementById("approval-note").value.trim();
  try {
    const result = await api(`/api/approvals/${state.selectedApproval.id}/decision`, { method: "POST", body: { decision, note } });
    closeApprovalModal();
    toast(decision === "approved" ? "审批通过，AI 已完成业务执行" : "审批已驳回");
    await Promise.all([loadApprovals(), loadDashboard(), loadRisk()]);
    if (result.execution?.purchase_order) toast(`采购单 ${result.execution.purchase_order} 已生成`);
  } catch (error) { toast(error.message, "error"); }
}

async function loadAudit() {
  const container = document.getElementById("audit-list");
  try {
    const { audits } = await api("/api/audits?limit=80");
    container.innerHTML = audits.map((item) => {
      const detail = item.details.message || item.details.command || item.details.reason || JSON.stringify(item.details);
      const typeClass = item.status === "denied" ? "denied" : ["warning", "pending"].includes(item.status) ? "warning" : "";
      return `<div class="audit-item ${typeClass}"><span class="audit-dot">${auditIcon(item.event_type)}</span><div class="audit-line"><strong>${escapeHtml(actionLabel(item.action))}</strong><time>${formatDateTime(item.timestamp)}</time></div><p>${escapeHtml(detail)}</p><div class="audit-meta"><span>${escapeHtml(item.user_name || "系统")}</span><span>${escapeHtml(item.role)}</span><span>${escapeHtml(providerName(item.provider || "local"))}</span><span>${item.duration_ms}ms</span></div></div>`;
    }).join("");
  } catch (error) {
    container.innerHTML = `<div class="empty-state"><div><span>🔒</span><strong>无法读取审计记录</strong><p>${escapeHtml(error.message)}</p></div></div>`;
  }
}

async function loadSettings() {
  const form = document.getElementById("settings-form");
  const locked = document.getElementById("settings-locked");
  if (state.currentUser.role !== "admin") {
    form.classList.add("hidden");
    locked.classList.remove("hidden");
    return;
  }
  form.classList.remove("hidden");
  locked.classList.add("hidden");
  try {
    const { settings, status } = await api("/api/settings");
    document.getElementById("external-enabled").checked = Boolean(settings.external_enabled);
    document.getElementById("external-url").value = settings.external_base_url || "";
    document.getElementById("external-model").value = settings.external_model || "";
    document.getElementById("external-key").placeholder = settings.external_api_key_masked || "输入 API 密钥";
    document.getElementById("ollama-enabled").checked = Boolean(settings.ollama_enabled);
    document.getElementById("ollama-url").value = settings.ollama_base_url || "";
    document.getElementById("ollama-model").value = settings.ollama_model || "";
    renderProviderStatus(status);
  } catch (error) { toast(error.message, "error"); }
}

async function saveSettings(event) {
  event.preventDefault();
  const settings = {
    external_enabled: document.getElementById("external-enabled").checked,
    external_base_url: document.getElementById("external-url").value.trim(),
    external_model: document.getElementById("external-model").value.trim(),
    ollama_enabled: document.getElementById("ollama-enabled").checked,
    ollama_base_url: document.getElementById("ollama-url").value.trim(),
    ollama_model: document.getElementById("ollama-model").value.trim(),
  };
  const key = document.getElementById("external-key").value.trim();
  if (key) settings.external_api_key = key;
  try {
    const response = await api("/api/settings", { method: "POST", body: { settings } });
    renderProviderStatus(response.status);
    document.getElementById("external-key").value = "";
    toast("AI 设置已保存，新的降级顺序立即生效");
  } catch (error) { toast(error.message, "error"); }
}

function renderProviderStatus(status) {
  const providers = [status.external, status.ollama, status.rules];
  document.getElementById("provider-stack").innerHTML = providers.map((item, index) => {
    const ready = item.enabled && item.configured;
    const stateLabel = index === 2 ? "已就绪" : ready ? "已启用" : item.enabled ? "待配置" : "未启用";
    return `<span class="provider-badge ${ready ? "ready" : index < 2 ? "fallback" : ""}"><i></i>${escapeHtml(item.label)} · ${stateLabel}</span>`;
  }).join("");
  document.getElementById("sidebar-ai-mode").textContent = "三级降级已启用";
}

async function resetDemo() {
  if (state.currentUser.role !== "admin") { toast("只有管理员可以重置演示数据", "error"); return; }
  if (!window.confirm("确定恢复初始演示数据吗？这只会重建演示数据库，不会修改源码和原视频。")) return;
  try {
    await api("/api/reset", { method: "POST", body: { confirm: "RESET" } });
    toast("演示数据已恢复到初始状态");
    await Promise.all([loadDashboard(), loadRisk(), loadApprovals(), loadAudit()]);
  } catch (error) { toast(error.message, "error"); }
}

function exportBrief() {
  const risk = state.risk;
  const text = [
    "Aether AI ERP · 经营决策简报", new Date().toLocaleString("zh-CN"), "",
    "【首要风险】航空铝板 6061-T6 原材料短缺",
    `预计缺口：${risk ? number(risk.metrics.shortage) : 520}kg`,
    `受影响订单：${risk ? risk.metrics.affected_orders : 3} 张`,
    "建议动作：向华东铝业集团加急补货 700kg，并同步关键客户交付窗口。", "",
    "本简报由本地 Aether AI ERP 演示系统生成。",
  ].join("\n");
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `Aether经营简报-${new Date().toISOString().slice(0, 10)}.txt`;
  link.click();
  URL.revokeObjectURL(url);
  toast("经营简报已导出");
}

function showViewError(view, error) {
  toast(error.message, "error");
  if (view === "audit") document.getElementById("audit-list").innerHTML = `<div class="empty-state"><div><span>!</span><strong>${escapeHtml(error.message)}</strong></div></div>`;
}

function toast(message, type = "success") {
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.innerHTML = `<i>${type === "error" ? "!" : "✓"}</i><span>${escapeHtml(message)}</span>`;
  document.getElementById("toast-stack").appendChild(item);
  setTimeout(() => item.remove(), 3600);
}

function formatCell(value, type) {
  if (type === "strong") return `<strong>${escapeHtml(value)}</strong>`;
  if (type === "money") return formatMoney(value);
  if (type === "percent") return `${number(value)}%`;
  if (type === "number") return number(value);
  if (type === "priority") return priorityTag(value);
  if (type === "status") return statusTag(value);
  if (type === "tag") return `<span class="tag blue">${escapeHtml(value)}</span>`;
  if (type === "datetime") return formatDateTime(value);
  return escapeHtml(value ?? "—");
}

function priorityTag(value) {
  const cls = value === "紧急" ? "red" : value === "高" ? "amber" : "gray";
  return `<span class="tag ${cls}">${escapeHtml(value)}</span>`;
}

function statusTag(value) {
  const green = ["已完成", "已出货", "已开票", "已确认", "approved"].includes(value);
  const amber = ["待审批", "待出货", "待承运", "生产中", "运输中", "pending", "缓解中"].includes(value);
  return `<span class="tag ${green ? "green" : amber ? "amber" : "blue"}">${escapeHtml(value)}</span>`;
}

function formatMoney(value) { return `¥ ${Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`; }
function compactMoney(value) { return `¥ ${(Number(value || 0) / 10000).toLocaleString("zh-CN", { maximumFractionDigits: 1 })}万`; }
function number(value) { return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 1 }); }
function formatDateTime(value) { return value ? String(value).replace("T", " ").slice(0, 16) : "—"; }
function formatToday() { return new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "short" }).format(new Date()); }
function providerName(value) { return ({ external: "外部 AI", ollama: "本地 Ollama", rules: "规则引擎", local: "本地系统" })[value] || value; }
function trailStatus(item) { return item.status === "success" ? `${item.duration_ms || 0}ms` : item.status === "skipped" ? "跳过" : "已降级"; }
function auditIcon(type) { return ({ ai: "AI", approval: "✓", permission: "!", risk: "△", system: "◇", action: "→" })[type] || "·"; }
function actionLabel(action) {
  return ({
    "demo.initialized": "演示环境初始化", "demo.reset": "演示数据重置", "risk.detected": "检测到供应链风险",
    "risk.analyze": "AI 分析供应链风险", "purchase.create_replenishment": "补货采购审批",
    "fulfillment.ship_and_invoice": "出货与开票审批", "settings.update": "更新 AI 设置",
    "dashboard.get": "读取经营总览", "permissions.describe": "查询身份权限",
  })[action] || action;
}
function fieldLabel(key) { return ({ material_code: "物料", quantity: "数量", supplier_id: "供应商", affected_orders: "影响订单", order_ids: "订单 ID" })[key] || key; }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]); }
