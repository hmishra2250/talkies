const state = {
  items: [],
  selectedId: null,
  selectedItem: null,
  query: "",
  status: "",
};

const nodes = {
  refreshButton: document.querySelector("#refreshButton"),
  serviceState: document.querySelector("#serviceState"),
  wordsToday: document.querySelector("#wordsToday"),
  wordsWeek: document.querySelector("#wordsWeek"),
  wpm: document.querySelector("#wpm"),
  transcriptCount: document.querySelector("#transcriptCount"),
  searchInput: document.querySelector("#searchInput"),
  statusFilter: document.querySelector("#statusFilter"),
  historyList: document.querySelector("#historyList"),
  detailMeta: document.querySelector("#detailMeta"),
  detailTitle: document.querySelector("#detailTitle"),
  audioPlayer: document.querySelector("#audioPlayer"),
  editor: document.querySelector("#editor"),
  editState: document.querySelector("#editState"),
  saveButton: document.querySelector("#saveButton"),
  copyButton: document.querySelector("#copyButton"),
  cancelButton: document.querySelector("#cancelButton"),
  pasteButton: document.querySelector("#pasteButton"),
  modelSetting: document.querySelector("#modelSetting"),
  pasteSetting: document.querySelector("#pasteSetting"),
  audioSetting: document.querySelector("#audioSetting"),
  breakdown: document.querySelector("#breakdown"),
  dictionary: document.querySelector("#dictionary"),
};

nodes.refreshButton?.addEventListener("click", () => refresh());
nodes.searchInput?.addEventListener(
  "input",
  debounce(() => {
    state.query = nodes.searchInput.value;
    refreshHistory();
  }, 180),
);
nodes.statusFilter?.addEventListener("change", () => {
  state.status = nodes.statusFilter.value;
  refreshHistory();
});
nodes.saveButton?.addEventListener("click", () => saveCorrection());
nodes.copyButton?.addEventListener("click", () => copySelected());
nodes.cancelButton?.addEventListener("click", () => setSelectedStatus("cancelled"));
nodes.pasteButton?.addEventListener("click", () => pasteSelected());

refresh();

async function refresh() {
  await Promise.all([refreshHealth(), refreshStats(), refreshSettings(), refreshHistory()]);
}

async function refreshHealth() {
  try {
    const response = await getJson("/api/health");
    const service = response.service || {};
    const bits = [service.state || "ready"];
    if (service.model) {
      bits.push(service.model);
    }
    nodes.serviceState.textContent = bits.join(" - ");
  } catch (error) {
    nodes.serviceState.textContent = error.message;
  }
}

async function refreshStats() {
  const response = await getJson("/api/stats");
  const stats = response.stats || {};
  nodes.wordsToday.textContent = formatNumber(stats.words_today || 0);
  nodes.wordsWeek.textContent = formatNumber(stats.words_week || 0);
  nodes.wpm.textContent = stats.words_per_minute ? `${stats.words_per_minute} wpm` : "-";
  nodes.transcriptCount.textContent = formatNumber(stats.count || stats.items || 0);
  renderBreakdown(stats.by_status || {});
}

async function refreshSettings() {
  const response = await getJson("/api/settings");
  const settings = response.settings || {};
  nodes.modelSetting.textContent = settings.model || "-";
  nodes.pasteSetting.textContent = valueLabel(settings.paste);
  nodes.audioSetting.textContent = settings.keep_dictation_audio ? "Kept" : "Temporary";
  renderDictionary(settings.replacements || []);
}

async function refreshHistory() {
  const params = new URLSearchParams({ limit: "80" });
  if (state.query) {
    params.set("query", state.query);
  }
  if (state.status) {
    params.set("status", state.status);
  }
  const response = await getJson(`/api/history?${params}`);
  state.items = response.items || [];
  if (!state.items.some((item) => item.id === state.selectedId)) {
    state.selectedId = state.items[0]?.id || null;
  }
  state.selectedItem = state.items.find((item) => item.id === state.selectedId) || null;
  renderHistory();
  renderDetail();
}

function renderHistory() {
  nodes.historyList.replaceChildren();
  if (state.items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "No transcripts";
    nodes.historyList.append(empty);
    return;
  }
  for (const item of state.items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-item";
    button.setAttribute("role", "listitem");
    button.setAttribute("aria-selected", String(item.id === state.selectedId));
    button.addEventListener("click", () => {
      state.selectedId = item.id;
      state.selectedItem = item;
      renderHistory();
      renderDetail();
    });

    const title = document.createElement("strong");
    title.textContent = compactText(item.text) || "Untitled";
    const meta = document.createElement("span");
    meta.textContent = `${formatDate(item.created_at)} - ${item.mode} - ${item.status}`;
    button.append(title, meta);
    nodes.historyList.append(button);
  }
}

function renderDetail() {
  const item = state.selectedItem;
  if (!item) {
    nodes.detailMeta.textContent = "No item selected";
    nodes.detailTitle.textContent = "History";
    nodes.editor.value = "";
    nodes.editor.disabled = true;
    nodes.audioPlayer.hidden = true;
    nodes.audioPlayer.removeAttribute("src");
    nodes.editState.textContent = "Ready";
    return;
  }
  nodes.editor.disabled = false;
  nodes.detailMeta.textContent = `${formatDate(item.created_at)} - ${item.mode} - ${item.status}`;
  nodes.detailTitle.textContent = `${item.words} words`;
  nodes.editor.value = item.text;
  nodes.editState.textContent = "Ready";
  if (item.audio_path) {
    nodes.audioPlayer.hidden = false;
    nodes.audioPlayer.src = `/api/transcripts/${item.id}/audio`;
  } else {
    nodes.audioPlayer.hidden = true;
    nodes.audioPlayer.removeAttribute("src");
  }
}

function renderBreakdown(statuses) {
  nodes.breakdown.replaceChildren();
  const entries = Object.entries(statuses);
  if (entries.length === 0) {
    const empty = document.createElement("div");
    empty.className = "breakdown-row";
    empty.textContent = "No history";
    nodes.breakdown.append(empty);
    return;
  }
  for (const [status, count] of entries) {
    const row = document.createElement("div");
    row.className = "breakdown-row";
    const label = document.createElement("span");
    label.textContent = titleCase(status);
    const value = document.createElement("strong");
    value.textContent = String(count);
    row.append(label, value);
    nodes.breakdown.append(row);
  }
}

function renderDictionary(replacements) {
  nodes.dictionary.replaceChildren();
  if (replacements.length === 0) {
    const empty = document.createElement("div");
    empty.className = "dictionary-row";
    empty.textContent = "No replacements";
    nodes.dictionary.append(empty);
    return;
  }
  for (const entry of replacements) {
    const row = document.createElement("div");
    row.className = "dictionary-row";
    const phrase = document.createElement("span");
    phrase.textContent = entry.phrase;
    const replacement = document.createElement("strong");
    replacement.textContent = entry.replacement;
    row.append(phrase, replacement);
    nodes.dictionary.append(row);
  }
}

async function saveCorrection() {
  if (!state.selectedItem) {
    return;
  }
  nodes.editState.textContent = "Saving";
  try {
    const response = await sendJson(`/api/transcripts/${state.selectedItem.id}`, "PATCH", {
      text: nodes.editor.value,
    });
    updateSelected(response.item);
    nodes.editState.textContent = "Saved";
    await refreshStats();
  } catch (error) {
    nodes.editState.textContent = error.message;
  }
}

async function copySelected() {
  if (!state.selectedItem) {
    return;
  }
  await navigator.clipboard.writeText(nodes.editor.value);
  nodes.editState.textContent = "Copied";
}

async function setSelectedStatus(status) {
  if (!state.selectedItem) {
    return;
  }
  const response = await sendJson(`/api/transcripts/${state.selectedItem.id}/status`, "POST", {
    status,
  });
  updateSelected(response.item);
  await refreshStats();
}

async function pasteSelected() {
  if (!state.selectedItem) {
    return;
  }
  try {
    await sendJson(`/api/transcripts/${state.selectedItem.id}/paste`, "POST", {});
    nodes.editState.textContent = "Pasted";
    await setSelectedStatus("pasted");
  } catch (error) {
    nodes.editState.textContent = error.message;
  }
}

function updateSelected(item) {
  const index = state.items.findIndex((candidate) => candidate.id === item.id);
  if (index >= 0) {
    state.items[index] = item;
  }
  state.selectedId = item.id;
  state.selectedItem = item;
  renderHistory();
  renderDetail();
}

async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  return readResponse(response);
}

async function sendJson(path, method, body) {
  const response = await fetch(path, {
    method,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  return readResponse(response);
}

async function readResponse(response) {
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function debounce(callback, delay) {
  let timer = null;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => callback(...args), delay);
  };
}

function compactText(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function formatDate(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(value);
}

function valueLabel(value) {
  if (value === true) {
    return "On";
  }
  if (value === false) {
    return "Off";
  }
  return "-";
}

function titleCase(value) {
  return String(value).replace(/(^|\s)\S/g, (letter) => letter.toUpperCase());
}
