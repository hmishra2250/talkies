const state = {
  items: [],
  selectedId: null,
  selectedItem: null,
  query: "",
  status: "",
  hotkeyCaptureTimer: null,
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
  cancelButton: document.querySelector("#cancelButton"),
  pasteButton: document.querySelector("#pasteButton"),
  modelSetting: document.querySelector("#modelSetting"),
  pasteSetting: document.querySelector("#pasteSetting"),
  audioSetting: document.querySelector("#audioSetting"),
  hotkeyCurrent: document.querySelector("#hotkeyCurrent"),
  hotkeyBinding: document.querySelector("#hotkeyBinding"),
  hotkeyPreset: document.querySelector("#hotkeyPreset"),
  hotkeyState: document.querySelector("#hotkeyState"),
  clearHotkeyButton: document.querySelector("#clearHotkeyButton"),
  captureHotkeyButton: document.querySelector("#captureHotkeyButton"),
  saveHotkeyButton: document.querySelector("#saveHotkeyButton"),
  breakdown: document.querySelector("#breakdown"),
  dictionary: document.querySelector("#dictionary"),
  dictionaryForm: document.querySelector("#dictionaryForm"),
  dictionaryState: document.querySelector("#dictionaryState"),
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
nodes.cancelButton?.addEventListener("click", () =>
  setSelectedStatus(state.selectedItem?.status === "cancelled" ? "saved" : "cancelled"),
);
nodes.pasteButton?.addEventListener("click", () => pasteSelected());
nodes.clearHotkeyButton?.addEventListener("click", () => {
  nodes.hotkeyBinding.value = "";
  nodes.hotkeyState.textContent = "Cleared locally";
});
nodes.hotkeyPreset?.addEventListener("change", () => {
  if (nodes.hotkeyPreset.value) {
    nodes.hotkeyBinding.value = nodes.hotkeyPreset.value;
    nodes.hotkeyState.textContent = "Ready to save";
  }
});
nodes.captureHotkeyButton?.addEventListener("click", () => startHotkeyCapture());
nodes.saveHotkeyButton?.addEventListener("click", () => saveHotkey());
nodes.dictionaryForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const phrase = nodes.dictionaryForm.elements.phrase.value.trim();
  const replacement = nodes.dictionaryForm.elements.replacement.value.trim();
  await updateReplacement(phrase, replacement);
});

refresh();

async function refresh() {
  await Promise.all([
    refreshHealth(),
    refreshStats(),
    refreshSettings(),
    refreshHotkey(),
    refreshHistory(),
  ]);
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

async function refreshHotkey() {
  if (!nodes.hotkeyBinding) {
    return;
  }
  try {
    const response = await getJson("/api/hotkey");
    renderHotkey(response);
  } catch (error) {
    nodes.hotkeyCurrent.textContent = "-";
    nodes.hotkeyState.textContent = `Hotkey unavailable: ${error.message}`;
  }
}

function renderHotkey(payload) {
  const binding = payload.binding || payload.hotkey?.binding || "";
  const recommendation = payload.recommendation || payload.hotkey?.recommendation || "";
  const reason = payload.recommendation_reason || payload.hotkey?.recommendation_reason || "";
  const available = payload.available || payload.hotkey?.available || [];
  renderHotkeyPresets(available);
  nodes.hotkeyCurrent.textContent = binding || "Unset";
  nodes.hotkeyBinding.value = binding;
  nodes.hotkeyState.textContent = recommendation
    ? [`Suggested: ${recommendation}`, reason].filter(Boolean).join(" - ")
    : "Ready";
}

function renderHotkeyPresets(available) {
  if (!Array.isArray(available) || available.length === 0) {
    return;
  }
  const current = nodes.hotkeyPreset.value;
  const options = [new Option("Choose a key", "")];
  for (const item of available) {
    if (typeof item === "string") {
      options.push(new Option(item, item));
    } else if (item?.binding) {
      options.push(new Option(item.label || item.binding, item.binding));
    }
  }
  nodes.hotkeyPreset.replaceChildren(...options);
  if (current && options.some((option) => option.value === current)) {
    nodes.hotkeyPreset.value = current;
  }
}

async function startHotkeyCapture() {
  clearHotkeyCaptureTimer();
  nodes.captureHotkeyButton.disabled = true;
  nodes.hotkeyState.textContent = "Press the key you want to use";
  try {
    const response = await sendJson("/api/hotkey/capture", "POST", {});
    if (response.error || response.supported === false) {
      throw new Error(response.error || "Hotkey capture is not supported on this session");
    }
    const done = renderHotkeyCapture(response);
    if (done) {
      nodes.captureHotkeyButton.disabled = false;
    } else {
      state.hotkeyCaptureTimer = window.setInterval(pollHotkeyCapture, 700);
    }
  } catch (error) {
    nodes.hotkeyState.textContent = `Capture unavailable: ${error.message}`;
    nodes.captureHotkeyButton.disabled = false;
  }
}

async function pollHotkeyCapture() {
  try {
    const response = await getJson("/api/hotkey/capture");
    const done = renderHotkeyCapture(response);
    if (done) {
      clearHotkeyCaptureTimer();
      nodes.captureHotkeyButton.disabled = false;
    }
  } catch (error) {
    clearHotkeyCaptureTimer();
    nodes.hotkeyState.textContent = `Capture failed: ${error.message}`;
    nodes.captureHotkeyButton.disabled = false;
  }
}

function renderHotkeyCapture(payload) {
  const status = payload.state || payload.status || payload.capture?.state || "waiting";
  const binding = payload.binding || payload.capture?.binding || "";
  if (binding) {
    nodes.hotkeyBinding.value = binding;
    nodes.hotkeyState.textContent = `Detected ${binding}`;
    return true;
  }
  if (status === "error" || payload.supported === false) {
    nodes.hotkeyState.textContent =
      payload.error || payload.message || "Hotkey capture is not supported on this session";
    return true;
  }
  if (status === "timeout") {
    nodes.hotkeyState.textContent = "Capture timed out";
    return true;
  }
  nodes.hotkeyState.textContent = payload.message || "Waiting for a key press";
  return false;
}

async function saveHotkey() {
  const binding = nodes.hotkeyBinding.value.trim();
  if (!binding) {
    nodes.hotkeyState.textContent = "Choose a key before saving";
    return;
  }
  nodes.saveHotkeyButton.disabled = true;
  nodes.hotkeyState.textContent = "Saving";
  try {
    const response = await sendJson("/api/hotkey", "PUT", { binding });
    renderHotkey(response);
    nodes.hotkeyPreset.value = "";
    nodes.hotkeyCurrent.textContent = binding;
    nodes.hotkeyState.textContent = "Saved";
  } catch (error) {
    nodes.hotkeyState.textContent = error.message;
  } finally {
    nodes.saveHotkeyButton.disabled = false;
  }
}

function clearHotkeyCaptureTimer() {
  if (state.hotkeyCaptureTimer) {
    window.clearInterval(state.hotkeyCaptureTimer);
    state.hotkeyCaptureTimer = null;
  }
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
  nodes.cancelButton.disabled = !item;
  nodes.pasteButton.disabled = !item;
  nodes.saveButton.disabled = !item;
  nodes.cancelButton.textContent = item?.status === "cancelled" ? "Restore" : "Mark cancelled";
  nodes.cancelButton.setAttribute("aria-label", nodes.cancelButton.textContent);
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
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-replacement";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove ${entry.phrase}`);
    remove.addEventListener("click", () => updateReplacement(entry.phrase, null));
    row.append(phrase, replacement, remove);
    nodes.dictionary.append(row);
  }
}

async function updateReplacement(phrase, replacement) {
  try {
    const response = await sendJson("/api/replacements", "POST", { phrase, replacement });
    renderDictionary(response.settings.replacements || []);
    nodes.dictionaryState.textContent = replacement === null ? "Removed" : "Saved";
    if (replacement !== null) {
      nodes.dictionaryForm.reset();
    }
  } catch (error) {
    nodes.dictionaryState.textContent = error.message;
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

async function setSelectedStatus(status) {
  if (!state.selectedItem) {
    return;
  }
  try {
    const response = await sendJson(`/api/transcripts/${state.selectedItem.id}/status`, "POST", {
      status,
    });
    updateSelected(response.item);
    nodes.editState.textContent = status === "cancelled" ? "Marked cancelled" : "Restored";
    await refreshStats();
  } catch (error) {
    nodes.editState.textContent = error.message;
  }
}

async function pasteSelected() {
  if (!state.selectedItem) {
    return;
  }
  try {
    await sendJson(`/api/transcripts/${state.selectedItem.id}/paste`, "POST", {});
    nodes.editState.textContent = "Copied. Paste in your app.";
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
