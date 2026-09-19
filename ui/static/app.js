// Talks to the FastAPI backend in server.py. No framework — plain DOM.
"use strict";

const STATUS_META = {
  ok: ["badge--ok", "fetched"],
  cached: ["badge--cached", "cached"],
  timeout: ["badge--timeout", "timeout"],
  error: ["badge--error", "error"],
};

// A small colorful touch, not a source of truth — purely decorative.
const SOURCE_EMOJI = {
  wikipedia: "📖",
  arxiv: "📄",
  web: "🌐",
};

function sourceEmoji(origin) {
  return SOURCE_EMOJI[origin] || "🔎";
}

const state = {
  history: [], // [{ question, result }], newest first
  activeIndex: null,
  filter: "",
};

const el = {
  messages: document.getElementById("messages"),
  emptyState: document.getElementById("empty-state"),
  sampleQuestions: document.getElementById("sample-questions"),
  history: document.getElementById("history"),
  composer: document.getElementById("composer"),
  question: document.getElementById("question"),
  sendBtn: document.getElementById("send-btn"),
  newChat: document.getElementById("new-chat"),
  toggleSearch: document.getElementById("toggle-search"),
  historySearch: document.getElementById("history-search"),
  clearHistory: document.getElementById("clear-history"),
  settingsToggle: document.getElementById("settings-toggle"),
  settings: document.getElementById("settings"),
  offline: document.getElementById("offline"),
  useCache: document.getElementById("use-cache"),
  sequential: document.getElementById("sequential"),
  activeConfig: document.getElementById("active-config"),
  sidebar: document.getElementById("sidebar"),
  sidebarBackdrop: document.getElementById("sidebar-backdrop"),
  mobileMenu: document.getElementById("mobile-menu"),
  mascot: document.getElementById("mascot"),
  mascotSpeech: document.getElementById("mascot-speech"),
};

function selectedSources() {
  return Array.from(document.querySelectorAll(".sources input:checked")).map(
    (input) => input.value
  );
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function loadSettings() {
  try {
    const res = await fetch("/api/settings");
    if (!res.ok) return;
    const cfg = await res.json();
    el.activeConfig.innerHTML = `
      <dt>LLM</dt><dd>${cfg.llm_provider} / ${cfg.llm_model}</dd>
      <dt>Web search</dt><dd>${cfg.web_search_provider}</dd>
      <dt>Cache</dt><dd>${cfg.cache_backend} (${cfg.cache_ttl_seconds}s)</dd>
      <dt>Semaphore</dt><dd>${cfg.max_concurrent_requests}</dd>
      <dt>Retries</dt><dd>${cfg.retry_max_attempts}</dd>
    `;
  } catch (err) {
    // Non-fatal: the config panel is informational only.
    console.warn("could not load /api/settings", err);
  }
}

async function loadSampleQuestions() {
  try {
    const res = await fetch("/api/sample-questions");
    if (!res.ok) return;
    const data = await res.json();
    const questions = data.questions || [];
    el.sampleQuestions.innerHTML = questions
      .map(
        (q) => `
          <button type="button" class="sample-chip" data-question="${escapeHtml(q.text)}">
            <span class="sample-chip__text">${escapeHtml(q.text)}</span>
            <span class="sample-chip__meta">${escapeHtml(q.difficulty)} &middot; ${escapeHtml((q.expected_sources || []).join(", "))}</span>
          </button>`
      )
      .join("");
  } catch (err) {
    console.warn("could not load /api/sample-questions", err);
  }
}

el.sampleQuestions.addEventListener("click", (event) => {
  const chip = event.target.closest(".sample-chip");
  if (!chip) return;
  el.question.value = chip.dataset.question;
  autoResize();
  el.question.focus();
});

function autoResize() {
  el.question.style.height = "auto";
  el.question.style.height = `${Math.min(el.question.scrollHeight, 160)}px`;
}

function renderHistory() {
  el.history.innerHTML = "";
  const needle = state.filter.trim().toLowerCase();

  state.history.forEach((entry, i) => {
    if (needle && !entry.question.toLowerCase().includes(needle)) return;

    const item = document.createElement("div");
    item.className = "history__item" + (i === state.activeIndex ? " is-active" : "");

    const label = document.createElement("button");
    label.type = "button";
    label.className = "history__label";
    label.textContent = entry.question;
    label.title = entry.question;
    label.addEventListener("click", () => {
      state.activeIndex = i;
      renderConversation();
      renderHistory();
      closeMobileSidebar();
    });

    const del = document.createElement("button");
    del.type = "button";
    del.className = "history__delete";
    del.textContent = "×";
    del.setAttribute("aria-label", "Delete this question");
    del.addEventListener("click", (event) => {
      event.stopPropagation();
      state.history.splice(i, 1);
      if (state.activeIndex === i) state.activeIndex = null;
      else if (state.activeIndex !== null && state.activeIndex > i) state.activeIndex -= 1;
      renderHistory();
      renderConversation();
    });

    item.appendChild(label);
    item.appendChild(del);
    el.history.appendChild(item);
  });
}

function badgeFor(status) {
  const [cls, label] = STATUS_META[status] || ["badge--error", status];
  return `<span class="badge ${cls}">${label}</span>`;
}

function renderCitationsBox(answer) {
  if (!answer || !answer.citations || answer.citations.length === 0) return "";
  const rows = answer.citations
    .map(
      (c) => `
        <div class="insight-row">
          <span class="insight-row__arrow">&rarr;</span>
          <span class="insight-row__label">[${c.index}] ${sourceEmoji(c.origin)} ${escapeHtml(c.origin)}:</span>
          <span class="insight-row__desc">
            <a href="${c.url}" target="_blank" rel="noopener">${escapeHtml(c.title)}</a>
          </span>
        </div>`
    )
    .join("");
  return `
    <div class="insight-box">
      <p class="insight-box__title">References</p>
      ${rows}
    </div>`;
}

function renderSourceTable(result) {
  const rows = result.outcomes
    .map(
      (o) => `
        <tr>
          <td>${sourceEmoji(o.source)} ${escapeHtml(o.source)}</td>
          <td>${badgeFor(o.status)}</td>
          <td>${o.elapsed_seconds.toFixed(2)}</td>
          <td>${o.excerpts}</td>
          <td>${o.error ? escapeHtml(o.error).slice(0, 120) : "&mdash;"}</td>
        </tr>`
    )
    .join("");
  return `
    <div class="source-table-wrap">
      <p class="section-label">Per-source breakdown</p>
      <table class="source-table">
        <thead>
          <tr><th>Source</th><th>Status</th><th>Time (s)</th><th>Excerpts</th><th>Error</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function sparkleBurst(container) {
  const emojis = ["✨", "⭐", "💫"];
  for (let i = 0; i < 6; i += 1) {
    const s = document.createElement("span");
    s.className = "sparkle";
    s.textContent = emojis[i % emojis.length];
    s.style.left = `${10 + Math.random() * 80}%`;
    s.style.animationDelay = `${(Math.random() * 0.15).toFixed(2)}s`;
    container.appendChild(s);
    s.addEventListener("animationend", () => s.remove());
  }
}

function renderResult(question, result, index) {
  const userMsg = document.createElement("div");
  userMsg.className = "msg msg--user";
  userMsg.innerHTML = `<div class="bubble">${escapeHtml(question)}</div>`;

  const assistantMsg = document.createElement("div");
  assistantMsg.className = "msg msg--assistant";

  const okCount = result.outcomes.filter((o) => o.status === "ok" || o.status === "cached").length;
  const isError = !result.answer;

  let textHtml;
  if (isError) {
    const reason = okCount
      ? "synthesis failed; sources were retrieved."
      : "no sources were retrieved.";
    textHtml = `No answer could be produced (${reason})`;
  } else {
    textHtml = escapeHtml(result.answer.answer);
  }

  const degradedNote = result.degraded
    ? `<div class="degraded-note">Degraded answer: at least one source failed; the answer uses the rest.</div>`
    : "";

  const citationCount = result.answer ? result.answer.citations.length : 0;

  assistantMsg.innerHTML = `
    <div class="assistant-card${isError ? " is-error" : ""}">
      <div class="assistant-card__header">
        <span class="assistant-card__label">Answer</span>
      </div>
      <div class="assistant-card__text">${textHtml}</div>

      <div class="metrics-row">
        <div class="metric">
          <div class="metric__value">${okCount}/${result.outcomes.length}</div>
          <div class="metric__label">Sources</div>
        </div>
        <div class="metric">
          <div class="metric__value">${result.elapsed_seconds.toFixed(2)}s</div>
          <div class="metric__label">Wall time</div>
        </div>
        <div class="metric">
          <div class="metric__value">${citationCount}</div>
          <div class="metric__label">Citations</div>
        </div>
      </div>

      ${degradedNote}
      ${renderCitationsBox(result.answer)}
      ${renderSourceTable(result)}

      <button type="button" class="btn btn--ghost download-btn" data-history-index="${index}">
        Download result as JSON
      </button>
    </div>
  `;

  el.messages.appendChild(userMsg);
  el.messages.appendChild(assistantMsg);
  el.messages.scrollTop = el.messages.scrollHeight;

  if (!isError) {
    const card = assistantMsg.querySelector(".assistant-card");
    if (card) sparkleBurst(card);
  }
}

function renderConversation() {
  el.messages.innerHTML = "";
  if (state.activeIndex === null || !state.history[state.activeIndex]) {
    el.messages.appendChild(el.emptyState);
    return;
  }
  const entry = state.history[state.activeIndex];
  renderResult(entry.question, entry.result, state.activeIndex);
}

function downloadJson(filename, dataObj) {
  const blob = new Blob([JSON.stringify(dataObj, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

el.messages.addEventListener("click", (event) => {
  const btn = event.target.closest(".download-btn");
  if (!btn) return;
  const entry = state.history[Number(btn.dataset.historyIndex)];
  if (!entry) return;
  downloadJson("research_result.json", entry.result);
});

function showTyping() {
  const wrap = document.createElement("div");
  wrap.className = "msg msg--assistant";
  wrap.id = "typing-indicator";
  wrap.innerHTML = `<div class="assistant-card"><div class="typing"><span></span><span></span><span></span></div></div>`;
  el.messages.appendChild(wrap);
  el.messages.scrollTop = el.messages.scrollHeight;
}

function hideTyping() {
  const node = document.getElementById("typing-indicator");
  if (node) node.remove();
}

async function submitQuestion(question) {
  const sources = selectedSources();
  if (sources.length === 0) {
    window.alert("Select at least one source.");
    return;
  }

  el.sendBtn.disabled = true;
  if (el.emptyState.isConnected) el.emptyState.remove();
  showTyping();

  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        sources: sources.join(","),
        use_cache: el.useCache.checked,
        sequential: el.sequential.checked,
        offline: el.offline.checked,
      }),
    });

    hideTyping();

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      window.alert(body.detail || `Request failed (${res.status})`);
      return;
    }

    const result = await res.json();
    state.history.unshift({ question, result });
    state.activeIndex = 0;
    renderHistory();
    renderConversation();
  } catch (err) {
    hideTyping();
    window.alert(`Network error: ${err.message}`);
  } finally {
    el.sendBtn.disabled = false;
  }
}

// ------------------------------ mobile sidebar ----------------------------

function openMobileSidebar() {
  el.sidebar.classList.add("is-open");
  el.sidebarBackdrop.classList.add("is-visible");
  el.mobileMenu.setAttribute("aria-expanded", "true");
}

function closeMobileSidebar() {
  el.sidebar.classList.remove("is-open");
  el.sidebarBackdrop.classList.remove("is-visible");
  el.mobileMenu.setAttribute("aria-expanded", "false");
}

el.mobileMenu.addEventListener("click", () => {
  if (el.sidebar.classList.contains("is-open")) closeMobileSidebar();
  else openMobileSidebar();
});

el.sidebarBackdrop.addEventListener("click", closeMobileSidebar);

// ---------------------------------- wiring --------------------------------

el.question.addEventListener("input", autoResize);

el.question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    el.composer.requestSubmit();
  }
});

el.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = el.question.value.trim();
  if (!question) return;
  el.question.value = "";
  autoResize();
  submitQuestion(question);
});

el.newChat.addEventListener("click", () => {
  state.activeIndex = null;
  el.question.value = "";
  autoResize();
  renderConversation();
  renderHistory();
  el.question.focus();
  closeMobileSidebar();
});

el.toggleSearch.addEventListener("click", () => {
  el.historySearch.classList.toggle("is-visible");
  if (el.historySearch.classList.contains("is-visible")) el.historySearch.focus();
});

el.historySearch.addEventListener("input", () => {
  state.filter = el.historySearch.value;
  renderHistory();
});

el.clearHistory.addEventListener("click", () => {
  if (state.history.length === 0) return;
  if (!window.confirm("Clear all conversations?")) return;
  state.history = [];
  state.activeIndex = null;
  renderHistory();
  renderConversation();
});

el.settingsToggle.addEventListener("click", () => {
  el.settings.classList.toggle("is-open");
});

// ------------------------------------ mascot ------------------------------

const MASCOT_GREETINGS = [
  "Hi! Ask me anything 👋",
  "Beep boop — ready when you are!",
  "3 sources, 1 brain. Let's go!",
  "Try a sample question below ➜",
];
let mascotGreetIndex = 0;
let mascotHideTimer = null;

el.mascot.addEventListener("click", () => {
  el.mascotSpeech.textContent = MASCOT_GREETINGS[mascotGreetIndex % MASCOT_GREETINGS.length];
  mascotGreetIndex += 1;
  el.mascotSpeech.classList.add("is-visible");
  clearTimeout(mascotHideTimer);
  mascotHideTimer = setTimeout(() => el.mascotSpeech.classList.remove("is-visible"), 2600);
});

loadSettings();
loadSampleQuestions();
