const ui = {
  serviceStatus: document.querySelector("#serviceStatus"),
  messages: document.querySelector("#messages"),
  chatForm: document.querySelector("#chatForm"),
  messageInput: document.querySelector("#messageInput"),
  sendButton: document.querySelector("#sendButton"),
  confirmButton: document.querySelector("#confirmButton"),
  cancelButton: document.querySelector("#cancelButton"),
  stopButton: document.querySelector("#stopButton"),
  refreshState: document.querySelector("#refreshState"),
  agentStatus: document.querySelector("#agentStatus"),
  currentGoal: document.querySelector("#currentGoal"),
  finalAnswer: document.querySelector("#finalAnswer"),
  queueSize: document.querySelector("#queueSize"),
  currentTask: document.querySelector("#currentTask"),
  steps: document.querySelector("#steps"),
  memoryLookups: document.querySelector("#memoryLookups"),
  eventLog: document.querySelector("#eventLog"),
  memoryForm: document.querySelector("#memoryForm"),
  memoryQuery: document.querySelector("#memoryQuery"),
  retrievalMethod: document.querySelector("#retrievalMethod"),
  targetAgent: document.querySelector("#targetAgent"),
  memoryResult: document.querySelector("#memoryResult"),
  buildVector: document.querySelector("#buildVector"),
  rebuildVector: document.querySelector("#rebuildVector"),
  vectorResult: document.querySelector("#vectorResult"),
};

let renderedMessages = 0;
let socket = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || data.message || `HTTP ${response.status}`);
  return data;
}

function text(value, fallback = "-") {
  const normalized = value === null || value === undefined ? "" : String(value).trim();
  return normalized || fallback;
}

function setStatus(label, mode = "") {
  ui.serviceStatus.textContent = label;
  ui.serviceStatus.className = `status-pill ${mode}`.trim();
}

function appendLog(type, payload = {}) {
  const item = document.createElement("div");
  item.className = "log-item";

  const title = document.createElement("strong");
  title.textContent = type;
  item.appendChild(title);

  const body = document.createElement("span");
  body.textContent = text(payload.message || payload.answer || payload.error || payload.task_id, "");
  item.appendChild(body);

  ui.eventLog.prepend(item);
  while (ui.eventLog.children.length > 80) ui.eventLog.lastChild.remove();
}

function renderMessages(messages = []) {
  if (messages.length === renderedMessages) return;
  ui.messages.innerHTML = "";
  for (const message of messages) {
    const node = document.createElement("article");
    node.className = `message ${message.role || "assistant"}`;
    node.textContent = message.content || "";
    ui.messages.appendChild(node);
  }
  renderedMessages = messages.length;
  ui.messages.scrollTop = ui.messages.scrollHeight;
}

function statusClass(status) {
  if (status === "completed") return "ok";
  if (status === "failed") return "error";
  if (status === "in_progress" || status === "planned") return "active";
  if (String(status || "").includes("waiting")) return "warn";
  return "";
}

function renderSteps(steps = []) {
  ui.steps.innerHTML = "";
  if (!steps.length) {
    const empty = document.createElement("li");
    empty.className = "empty-row";
    empty.textContent = "暂无执行步骤";
    ui.steps.appendChild(empty);
    return;
  }

  for (const step of steps) {
    const node = document.createElement("li");
    node.className = "step-item";

    const header = document.createElement("div");
    header.className = "step-header";
    const name = document.createElement("strong");
    name.textContent = `${step.index || ""}. ${step.skill_name || step.decision_type || "step"}`;
    const badge = document.createElement("span");
    badge.className = `mini-badge ${statusClass(step.status)}`;
    badge.textContent = step.status || "-";
    header.append(name, badge);

    const meta = document.createElement("div");
    meta.className = "step-meta";
    meta.textContent = [
      step.sub_goal ? `目标：${step.sub_goal}` : "",
      step.scope ? `范围：${step.scope}` : "",
      step.reason ? `原因：${step.reason}` : "",
      step.result?.message ? `结果：${step.result.message}` : "",
      step.result?.error ? `错误：${step.result.error}` : "",
    ].filter(Boolean).join("\n");

    node.append(header, meta);
    ui.steps.appendChild(node);
  }
}

function compactMemoryItem(item) {
  return {
    scope: item.memory_scope || "",
    score: item.match_score ?? "",
    type: item.memory_type || "",
    method: item.retrieval_method || "",
    request: item.user_request || "",
    summary: item.summary || item.result || "",
  };
}

function renderMemoryItems(container, label, items = []) {
  const group = document.createElement("div");
  group.className = "memory-group";

  const heading = document.createElement("div");
  heading.className = "memory-group-heading";
  heading.textContent = `${label} (${items.length})`;
  group.appendChild(heading);

  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "memory-empty";
    empty.textContent = "无命中";
    group.appendChild(empty);
    container.appendChild(group);
    return;
  }

  for (const raw of items.slice(0, 5)) {
    const item = compactMemoryItem(raw);
    const node = document.createElement("article");
    node.className = "memory-hit";

    const top = document.createElement("div");
    top.className = "memory-hit-top";
    const request = document.createElement("strong");
    request.textContent = item.request || "未命名记忆";
    const score = document.createElement("span");
    score.className = "score";
    score.textContent = item.score === "" ? item.type : `${item.type} · ${item.score}`;
    top.append(request, score);

    const detail = document.createElement("p");
    detail.textContent = item.summary || "无摘要";

    const meta = document.createElement("small");
    meta.textContent = [item.method, raw.reuse_level, raw.site].filter(Boolean).join(" / ");

    node.append(top, detail, meta);
    group.appendChild(node);
  }
  container.appendChild(group);
}

function renderMemoryLookups(lookups = []) {
  ui.memoryLookups.innerHTML = "";
  if (!lookups.length) {
    const empty = document.createElement("div");
    empty.className = "empty-row";
    empty.textContent = "暂无 step 记忆检索记录";
    ui.memoryLookups.appendChild(empty);
    return;
  }

  for (const lookup of lookups.slice().reverse()) {
    const node = document.createElement("section");
    node.className = "lookup-card";

    const header = document.createElement("div");
    header.className = "lookup-header";
    const title = document.createElement("strong");
    title.textContent = `规划轮次 ${lookup.planning_round}`;
    const context = document.createElement("span");
    context.textContent = lookup.last_skill_name
      ? `上一步：${lookup.last_skill_name} (${lookup.last_success})`
      : "任务开始";
    header.append(title, context);
    node.appendChild(header);

    renderMemoryItems(node, "goal", lookup.goal || []);
    renderMemoryItems(node, "current_step", lookup.current_step || []);
    ui.memoryLookups.appendChild(node);
  }
}

function renderState(state = {}) {
  const status = state.status || "-";
  ui.agentStatus.textContent = status;
  ui.agentStatus.className = `state-badge ${statusClass(status)}`;
  ui.currentGoal.textContent = text(state.current_goal);
  ui.finalAnswer.textContent = text(state.final_answer);
  ui.queueSize.textContent = String(state.queue_size ?? 0);
  ui.currentTask.textContent = text(state.current_task?.message);
  renderSteps(state.steps || []);
  renderMemoryLookups(state.memory_lookups || []);
  renderMessages(state.messages || []);

  const waiting = state.status === "waiting_user_confirmation";
  ui.confirmButton.hidden = !waiting;
  ui.cancelButton.hidden = !waiting;
  ui.stopButton.disabled = !state.current_task;
}

async function refreshState() {
  try {
    const data = await api("/api/state");
    renderState(data.state || {});
    setStatus("在线", "ok");
  } catch (error) {
    setStatus("离线", "error");
  }
}

function connectSocket() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${window.location.host}/ws/events`);

  socket.addEventListener("open", () => setStatus("实时连接", "ok"));
  socket.addEventListener("close", () => {
    setStatus("重连中", "warn");
    setTimeout(connectSocket, 1200);
  });
  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    appendLog(payload.type, payload.data || {});
    if (payload.type === "state") renderState(payload.data || {});
    if (payload.data?.state) renderState(payload.data.state);
    if (["task_completed", "task_failed", "task_started", "task_queued", "cancel_requested"].includes(payload.type)) {
      refreshState();
    }
  });
}

async function enqueueMessage(message) {
  const trimmed = message.trim();
  if (!trimmed) return;
  ui.sendButton.disabled = true;
  try {
    await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({ message: trimmed }),
    });
    ui.messageInput.value = "";
    await refreshState();
  } catch (error) {
    appendLog("submit_failed", { error: error.message });
  } finally {
    ui.sendButton.disabled = false;
    ui.messageInput.focus();
  }
}

ui.chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await enqueueMessage(ui.messageInput.value);
});

ui.messageInput.addEventListener("keydown", async (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    await enqueueMessage(ui.messageInput.value);
  }
});

ui.confirmButton.addEventListener("click", () => enqueueMessage("y"));
ui.cancelButton.addEventListener("click", () => enqueueMessage("n"));

ui.stopButton.addEventListener("click", async () => {
  try {
    const data = await api("/api/cancel", { method: "POST", body: "{}" });
    appendLog("cancel", data);
    await refreshState();
  } catch (error) {
    appendLog("cancel_failed", { error: error.message });
  }
});

ui.refreshState.addEventListener("click", refreshState);

ui.memoryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = ui.memoryQuery.value.trim();
  if (!query) return;
  ui.memoryResult.textContent = "检索中...";
  try {
    const targetAgent = ui.targetAgent.value;
    const data = await api("/api/memory/search", {
      method: "POST",
      body: JSON.stringify({
        query,
        retrieval_method: ui.retrievalMethod.value,
        target_agent: targetAgent,
        memory_types: targetAgent === "page_explorer_agent" ? ["page_path"] : ["manager_experience", "page_path"],
        limit: 5,
      }),
    });
    ui.memoryResult.textContent = JSON.stringify(data.result, null, 2);
  } catch (error) {
    ui.memoryResult.textContent = error.message;
  }
});

async function buildVector(force) {
  ui.vectorResult.textContent = force ? "强制重建中..." : "增量构建中...";
  try {
    const data = await api("/api/vector/build", {
      method: "POST",
      body: JSON.stringify({ force }),
    });
    ui.vectorResult.textContent = JSON.stringify(data.result, null, 2);
  } catch (error) {
    ui.vectorResult.textContent = error.message;
  }
}

ui.buildVector.addEventListener("click", () => buildVector(false));
ui.rebuildVector.addEventListener("click", () => buildVector(true));

connectSocket();
refreshState();
setInterval(refreshState, 4000);
