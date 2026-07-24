const state = {
  caseId: null,
  caseInfo: null,
  events: [],
  lyrics: {},
  eventSource: null,
  ph: [],
  renderQueued: false,
};

const roles = ["supervisor", "generator", "reviewer"];
const roleNames = {
  supervisor: "总控",
  generator: "生成",
  reviewer: "审核",
  hard_gate: "代码硬门",
  delivery: "交付",
};
const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? "").replace(
  /[&<>"]/g,
  char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[char],
);

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async function boot() {
  state.ph = await api("/api/ph-cases");
  for (const item of state.ph) {
    $("#preset").insertAdjacentHTML("beforeend", `<option>${esc(item.id)}</option>`);
  }
  $("#preset").onchange = loadPreset;
  $("#caseForm").onsubmit = createCase;
  $("#continueForm").onsubmit = manualContinue;
  $("#stopBtn").onclick = () => act("stop");
  $("#cancelBtn").onclick = () => act("cancel");
  $("#copyBtn").onclick = () => navigator.clipboard.writeText($("#finalLyric").textContent);
  window.addEventListener("resize", scheduleTimelineRender);
  await refreshRecent();
}

function loadPreset() {
  const item = state.ph.find(candidate => candidate.id === $("#preset").value);
  if (!item) return;
  $("#reference").value = item.reference_lyrics;
  $("#golden").value = item.golden_line;
  $("#style").value = item.style;
}

async function createCase(event) {
  event.preventDefault();
  try {
    const created = await api("/api/cases", {
      method: "POST",
      body: JSON.stringify({
        reference_lyrics: $("#reference").value,
        golden_line: $("#golden").value,
        style: $("#style").value,
        requirements: $("#requirements").value,
        forbidden_words: $("#forbidden").value,
        max_repairs: +$("#maxRepairs").value,
      }),
    });
    document.querySelector(".composer").open = false;
    await openCase(created.case_id);
  } catch (error) {
    alert(error.message);
  }
}

async function act(name) {
  if (!state.caseId) return;
  try {
    await api(`/api/cases/${state.caseId}/${name}`, { method: "POST" });
    setTimeout(() => openCase(state.caseId), 400);
  } catch (error) {
    alert(error.message);
  }
}

async function manualContinue(event) {
  event.preventDefault();
  if (!state.caseId) return;
  try {
    await api(`/api/cases/${state.caseId}/continue`, {
      method: "POST",
      body: JSON.stringify({
        target: $("#continueTarget").value,
        instruction: $("#continueInstruction").value,
      }),
    });
    $("#continueInstruction").value = "";
    setTimeout(() => openCase(state.caseId), 300);
  } catch (error) {
    alert(error.message);
  }
}

async function refreshRecent() {
  const cases = await api("/api/cases");
  $("#taskCount").textContent = cases.length;
  $("#recentList").innerHTML = cases.map(renderTaskItem).join("")
    || '<span class="empty">暂无生产任务</span>';
  document.querySelectorAll(".task-main").forEach(element => {
    element.onclick = () => openCase(element.dataset.id);
  });
}

function renderTaskItem(item) {
  const active = item.case_id === state.caseId;
  const rounds = item.rounds?.length
    ? item.rounds.map(round => {
      const stateClass = round.hard_pass ? "pass" : "reject";
      return `<div class="round-row ${stateClass}">
        <i></i>
        <span>歌词 v${round.version}</span>
        <small>${esc(round.status)}</small>
      </div>`;
    }).join("")
    : '<span class="round-empty">尚未形成歌词版本</span>';
  return `<article class="task-item ${active ? "active" : ""}">
    <div class="task-main" data-id="${esc(item.case_id)}">
      <div class="task-title-row">
        <i class="task-status ${esc(item.status)}"></i>
        <div>
          <b class="task-name">${esc(item.task_title || "未命名歌词任务")}</b>
          <span class="task-case">${esc(item.case_id)}</span>
        </div>
      </div>
      <div class="task-meta">
        <span>${esc(item.task_style || "未指定风格")}</span>
        <span>·</span>
        <span>${item.turn_count || 0} 个 Agent 轮次</span>
      </div>
    </div>
    <div class="task-rounds ${active ? "" : "collapsed"}">
      <span class="task-rounds-label">生产版本 / 第二层</span>
      ${rounds}
    </div>
  </article>`;
}

async function openCase(id) {
  state.caseId = id;
  state.events = [];
  state.lyrics = {};
  if (state.eventSource) state.eventSource.close();
  const [info, events] = await Promise.all([
    api(`/api/cases/${id}`),
    api(`/api/cases/${id}/journal`),
  ]);
  state.caseInfo = info;
  state.events = events;
  renderState(info);
  renderTimeline();
  state.eventSource = new EventSource(
    `/api/cases/${id}/events?after=${events.at(-1)?.event_id || 0}`,
  );
  state.eventSource.addEventListener("journal", event => {
    const item = JSON.parse(event.data);
    if (state.events.some(existing => existing.event_id === item.event_id)) return;
    state.events.push(item);
    if (item.event_type === "case_state") {
      state.caseInfo = { ...state.caseInfo, ...item.payload };
      renderState(state.caseInfo);
    }
    scheduleTimelineRender();
  });
  await refreshRecent();
}

function renderState(info) {
  $("#taskTitle").textContent = info.task_title || "未命名歌词任务";
  $("#caseTitle").textContent = `${info.case_id} · ${info.task_style || "未指定风格"}`;
  $("#globalStatus").textContent = info.status;
  $("#liveDot").classList.toggle("live", info.status === "running");
  $("#versionChip").textContent = `v${info.content_version}`;
  $("#turnChip").textContent = `${info.turn_count || countTurns()} 个 Agent 轮次`;
  $("#roundChip").textContent = `${info.repair_count} 次返修`;
  document.querySelectorAll(".lane-head").forEach(element => {
    const active = element.dataset.role === info.current_role;
    element.classList.toggle("running", active);
    element.querySelector(".agent-state").textContent = active ? "运行中" : "等待";
  });
  state.lyrics = info.lyrics || state.lyrics;
  renderBlocking(info);
  renderArtifacts(info);
}

function renderBlocking(info) {
  const banner = $("#blockingBanner");
  const blocking = info.status === "waiting_human" ? info.blocking : null;
  banner.hidden = !blocking;
  $("#continueForm").classList.toggle("available", Boolean(blocking));
  $("#continueForm").querySelectorAll("input, select, button").forEach(element => {
    element.disabled = !blocking;
  });
  if (!blocking) return;
  const failedChecks = blocking.details?.failed_checks?.length
    ? `未通过：${blocking.details.failed_checks.map(checkName).join("、")}`
    : failedChecksFromState(info);
  const budget = Number.isInteger(blocking.details?.repair_count)
    ? `返修 ${blocking.details.repair_count}/${blocking.details.max_repairs}`
    : `返修 ${info.repair_count}/${info.max_repairs}`;
  $("#blockingReason").textContent = blocking.reason;
  $("#blockingDetails").textContent = [failedChecks, budget, blockingNextStep(blocking.code)]
    .filter(Boolean)
    .join(" · ");
  $("#blockingCode").textContent = blocking.code;
}

function failedChecksFromState(info) {
  const failed = Object.entries(info.hard_validation?.checks || {})
    .filter(([, passed]) => !passed)
    .map(([name]) => checkName(name));
  return failed.length ? `未通过：${failed.join("、")}` : "";
}

function checkName(name) {
  return {
    exactly_16_lines: "必须为16行",
    four_stanzas_of_four: "必须为4段×4行",
    golden_line_only_at_9_and_13: "金句位置",
    no_punctuation: "不得含标点",
    no_non_golden_duplicate: "非金句不得重复",
    no_forbidden_words: "不得含禁用词",
  }[name] || name;
}

function blockingNextStep(code) {
  if (code === "repair_budget_exhausted") return "下一步：人工评估后结束或补充处理指令";
  if (code === "hard_gate_no_safe_scope") return "下一步：人工确认问题行后再继续";
  return "下一步：检查原因并决定是否人工继续";
}

function countTurns() {
  return new Set(
    state.events.filter(event => event.event_type === "turn_started").map(event => event.turn_id),
  ).size;
}

function scheduleTimelineRender() {
  if (state.renderQueued) return;
  state.renderQueued = true;
  requestAnimationFrame(() => {
    state.renderQueued = false;
    renderTimeline();
  });
}

function buildTurns(events) {
  const turns = [];
  const turnMap = new Map();
  const pendingRoutes = { supervisor: [], generator: [], reviewer: [] };
  let lastTurn = null;

  for (const event of events) {
    const payload = event.payload || {};
    if (event.event_type === "route") {
      const route = { ...payload, eventId: event.event_id };
      if (roles.includes(payload.target)) pendingRoutes[payload.target].push(route);
      if (lastTurn && payload.source === lastTurn.role) lastTurn.outbound = route;
      continue;
    }
    if (event.event_type === "turn_queued" || event.event_type === "turn_started") {
      if (!event.turn_id) continue;
      let turn = turnMap.get(event.turn_id);
      if (!turn) {
        turn = {
          id: event.turn_id,
          role: payload.role,
          status: event.status || "queued",
          version: event.content_version || 0,
          thinking: "",
          stream: "",
          normalized: null,
          inbound: null,
          outbound: null,
          firstEventId: event.event_id,
        };
        turn.inbound = pendingRoutes[turn.role]?.shift() || null;
        turnMap.set(event.turn_id, turn);
        turns.push(turn);
      }
      turn.status = event.status || turn.status;
      turn.sessionId = payload.session_id || turn.sessionId;
      lastTurn = turn;
      continue;
    }
    const turn = turnMap.get(event.turn_id);
    if (!turn) continue;
    lastTurn = turn;
    if (event.event_type === "actual_model_input") {
      turn.role = payload.role || turn.role;
      turn.systemPrompt = payload.system_prompt;
      turn.taskPrompt = payload.task_prompt;
      turn.skill = payload.skill;
      turn.sessionId = payload.session_id;
    } else if (event.event_type === "thinking_delta") {
      turn.thinking += payload.text || "";
    } else if (event.event_type === "text_delta") {
      turn.stream += payload.text || "";
    } else if (event.event_type === "message_completed") {
      turn.finalOutput = payload.final_output;
      turn.sessionId = payload.session_id || turn.sessionId;
      turn.status = "completed";
    } else if (event.event_type === "business_output_normalized") {
      turn.normalized = payload.result;
      turn.adapter = payload.adapter;
    } else if (event.event_type === "semantic_output_invalid") {
      turn.error = payload.error || "业务输出语义不完整";
      turn.partial = payload.raw_final;
      turn.status = "failed";
    } else if (event.event_type === "contract_invalid") {
      turn.error = payload.error || "历史输出格式无法识别";
      turn.partial = payload.raw_final;
      turn.status = "failed";
    } else if (event.event_type === "turn_terminal") {
      turn.error = payload.reason || payload.attempt_status || "未形成 completed 输出";
      turn.partial = payload.partial;
      turn.status = event.status || "incomplete";
    }
  }
  return turns;
}

function renderTimeline() {
  if (!state.caseId) return;
  const turns = buildTurns(state.events);
  const timeline = $("#timeline");
  if (!turns.length) {
    timeline.innerHTML = '<div class="timeline-empty"><span>01</span><strong>等待首个 Agent 轮次</strong><p>业务输入、thinking 和最终输出会聚合在同一张卡片</p></div>';
    $("#links").innerHTML = $("#links defs")?.outerHTML || "";
    return;
  }
  const blocker = state.caseInfo?.status === "waiting_human"
    ? renderBlockerRow(state.caseInfo.blocking, turns.length)
    : "";
  timeline.innerHTML = turns.map((turn, index) => renderTurnRow(turn, index)).join("") + blocker;
  requestAnimationFrame(() => drawTurnLinks(turns));
}

function renderBlockerRow(blocking, turnCount) {
  if (!blocking) return "";
  return `<div class="blocker-row">
    <article class="flow-blocker-card">
      <span class="turn-number">${String(turnCount + 1).padStart(2, "0")}</span>
      <div>
        <p class="eyebrow">CODE GATE · 已安全暂停</p>
        <strong>${esc(blocking.reason)}</strong>
        <span>后续 Agent 未被调用，当前产物不会继续流转</span>
      </div>
      <b>${esc(blocking.code)}</b>
    </article>
  </div>`;
}

function renderTurnRow(turn, index) {
  const slots = roles.map(role => {
    if (role !== turn.role) return '<div class="turn-slot"></div>';
    return `<div class="turn-slot">${renderTurnCard(turn, index)}</div>`;
  }).join("");
  return `<div class="turn-row" data-turn="${esc(turn.id)}">${slots}</div>`;
}

function renderTurnCard(turn, index) {
  const incoming = turn.inbound?.message || (
    index === 0 ? "用户提交本次歌词生产物料" : "系统调度进入本轮"
  );
  const modelInput = [
    turn.systemPrompt ? `SYSTEM\n${turn.systemPrompt}` : "",
    turn.taskPrompt ? `TASK\n${turn.taskPrompt}` : "",
  ].filter(Boolean).join("\n\n");
  const output = turn.finalOutput || turn.stream || turn.partial || "等待模型输出";
  const statusName = {
    queued: "排队",
    running: "进行中",
    completed: "已完成",
    failed: "失败",
    incomplete: "不完整",
    orphaned: "未知",
    cancelled: "已取消",
  }[turn.status] || turn.status;
  const outbound = turn.outbound
    ? `<div class="route-out ${esc(turn.outbound.kind || "")}">
        <span>系统路由</span>
        <b>${esc(roleNames[turn.outbound.source] || turn.outbound.source)} → ${esc(roleNames[turn.outbound.target] || turn.outbound.target)}</b>
      </div>`
    : "";
  const error = turn.error
    ? `<div class="turn-section"><div class="section-label"><span>未流转原因</span></div><pre>${esc(turn.error)}</pre></div>`
    : "";
  const normalized = turn.normalized
    ? `<div class="turn-section"><details><summary>Middleware 归一化业务结果 · ${esc(turn.adapter || "")}</summary><pre>${esc(JSON.stringify(turn.normalized, null, 2))}</pre></details></div>`
    : "";
  return `<article class="turn-card ${esc(turn.status)}" id="turn-${esc(turn.id)}">
    <header class="turn-head">
      <span class="turn-number">${String(index + 1).padStart(2, "0")}</span>
      <div class="turn-heading">
        <strong>${esc(roleNames[turn.role])} Agent · 第 ${index + 1} 轮</strong>
        <small>${esc(shortSession(turn.sessionId))}</small>
      </div>
      <span class="state-tag ${esc(turn.status)}">${esc(statusName)}</span>
    </header>
    <div class="turn-body">
      <div class="turn-section">
        <div class="section-label"><span>本轮业务输入</span><span>EVENT #${turn.firstEventId}</span></div>
        <pre class="business-input">${esc(incoming)}</pre>
      </div>
      <div class="turn-section">
        <details>
          <summary>脱敏后的实际模型输入</summary>
          <pre>${esc(modelInput || "等待实际模型输入")}</pre>
        </details>
      </div>
      <div class="turn-section">
        <details>
          <summary>实时思考流 · 敏感本机诊断</summary>
          <pre>${esc(turn.thinking || "本轮暂无 thinking 增量")}</pre>
        </details>
      </div>
      <div class="turn-section">
        <div class="section-label"><span>本轮最终产出</span><span>${esc(turn.skill || "")}</span></div>
        <pre>${esc(output)}</pre>
      </div>
      ${normalized}
      ${error}
      ${outbound}
    </div>
  </article>`;
}

function drawTurnLinks(turns) {
  const svg = $("#links");
  svg.querySelectorAll("path.route-path, text").forEach(node => node.remove());
  const boardBox = $("#laneBoard").getBoundingClientRect();
  const headHeight = document.querySelector(".lane-heads").offsetHeight;
  svg.setAttribute("viewBox", `0 0 ${boardBox.width} ${Math.max(1, boardBox.height - headHeight)}`);

  for (let index = 1; index < turns.length; index += 1) {
    const previous = document.getElementById(`turn-${turns[index - 1].id}`);
    const current = document.getElementById(`turn-${turns[index].id}`);
    if (!previous || !current) continue;
    const from = previous.getBoundingClientRect();
    const to = current.getBoundingClientRect();
    const x1 = from.left + from.width / 2 - boardBox.left;
    const y1 = from.bottom - boardBox.top - headHeight;
    const x2 = to.left + to.width / 2 - boardBox.left;
    const y2 = to.top - boardBox.top - headHeight;
    const midY = y1 + Math.max(14, (y2 - y1) / 2);
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("class", `route-path ${turns[index - 1].outbound?.kind || ""}`);
    path.setAttribute("d", `M ${x1} ${y1} L ${x1} ${midY} L ${x2} ${midY} L ${x2} ${y2 - 5}`);
    svg.appendChild(path);
    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("x", String((x1 + x2) / 2));
    label.setAttribute("y", String(midY - 5));
    label.setAttribute("text-anchor", "middle");
    label.textContent = turns[index - 1].outbound?.kind === "repair" ? "打回" : "系统";
    svg.appendChild(label);
  }
}

function shortSession(sessionId) {
  if (!sessionId) return "Session 待建立";
  return sessionId.length > 34 ? `…${sessionId.slice(-32)}` : sessionId;
}

function renderArtifacts(info) {
  const lyrics = info.lyrics || {};
  const versions = Object.keys(lyrics).map(Number).sort((a, b) => a - b);
  $("#versionTabs").innerHTML = versions.map(version => (
    `<button data-v="${version}">v${version}</button>`
  )).join("");
  document.querySelectorAll("#versionTabs button").forEach(button => {
    button.onclick = () => showVersion(+button.dataset.v);
  });
  if (versions.length) showVersion(versions.at(-1));
  if (versions.length > 1) {
    renderDiff(lyrics[String(versions.at(-2))], lyrics[String(versions.at(-1))]);
  } else {
    $("#diffView").className = "diff empty";
    $("#diffView").textContent = "产生返修版本后显示";
  }
  $("#finalLyric").textContent = info.final_lyric || "等待总控交付";
}

function showVersion(version) {
  document.querySelectorAll("#versionTabs button").forEach(button => {
    button.classList.toggle("active", +button.dataset.v === version);
  });
  $("#lyricView").textContent = state.lyrics[String(version)] || "";
}

function renderDiff(before, after) {
  const left = before.split(/\n/).filter(Boolean);
  const right = after.split(/\n/).filter(Boolean);
  $("#diffView").className = "diff";
  $("#diffView").innerHTML = left.map((line, index) => (
    `<div class="diff-row ${line !== right[index] ? "changed" : ""}">
      <b>${index + 1}</b>
      <del>${esc(line)}</del>
      <ins>${esc(right[index])}</ins>
    </div>`
  )).join("");
}

boot().catch(error => {
  $("#globalStatus").textContent = error.message;
});
