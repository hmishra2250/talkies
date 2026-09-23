const state = {
  items: [],
  query: "",
  status: "",
  hasMore: false,
  nextBeforeId: null,
  loadingMore: false,
  feedError: "",
  editingIds: new Set(),
  drafts: new Map(),
  messages: new Map(),
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
    refreshHistory({ reset: true });
  }, 180),
);
nodes.statusFilter?.addEventListener("change", () => {
  state.status = nodes.statusFilter.value;
  refreshHistory({ reset: true });
});
nodes.historyList?.addEventListener("click", (event) => handleHistoryClick(event));
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
  nodes.wpm.textContent = stats.words_per_minute ? `${stats.words_per_minute} wpm` : "—";
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

async function refreshHistory({ reset = false, append = false } = {}) {
  if (reset) {
    state.items = [];
    state.hasMore = false;
    state.nextBeforeId = null;
    state.feedError = "";
    state.editingIds.clear();
    state.drafts.clear();
    state.messages.clear();
  }
  if (append && state.loadingMore) {
    return;
  }
  const params = new URLSearchParams({ limit: "30" });
  if (state.query) {
    params.set("query", state.query);
  }
  if (state.status) {
    params.set("status", state.status);
  }
  if (append && state.nextBeforeId) {
    params.set("before_id", String(state.nextBeforeId));
  }
  state.loadingMore = append;
  state.feedError = "";
  try {
    const response = await getJson(`/api/history?${params}`);
    const items = response.items || [];
    state.items = append ? [...state.items, ...items] : items;
    state.hasMore = Boolean(response.has_more);
    state.nextBeforeId = response.next_before_id || null;
  } catch (error) {
    state.feedError = error.message;
  } finally {
    state.loadingMore = false;
    renderHistory();
  }
}

function renderHistory() {
  nodes.historyList.replaceChildren();
  if (state.items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = state.feedError || "No transcripts";
    nodes.historyList.append(empty);
    return;
  }
  for (const item of state.items) {
    nodes.historyList.append(renderTranscriptCard(item));
  }
  if (state.feedError) {
    const error = document.createElement("p");
    error.className = "feed-error";
    error.textContent = state.feedError;
    nodes.historyList.append(error);
  }
  if (state.hasMore) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "load-more";
    more.dataset.action = "load-more";
    more.disabled = state.loadingMore;
    more.textContent = state.loadingMore ? "Loading older..." : "Load older";
    nodes.historyList.append(more);
  }
}

function renderTranscriptCard(item) {
  const card = document.createElement("article");
  card.className = "transcript-card";
  card.setAttribute("role", "listitem");
  card.dataset.id = String(item.id);

  const header = document.createElement("div");
  header.className = "transcript-card-header";
  const meta = document.createElement("p");
  meta.className = "transcript-meta";
  meta.textContent = `${formatDate(item.created_at)} - ${item.mode} - ${item.status} - ${wordLabel(item)}`;
  const actions = document.createElement("div");
  actions.className = "transcript-actions";
  const editing = state.editingIds.has(item.id);
  actions.append(
    actionButton("copy", "Copy"),
    actionButton("status", item.status === "cancelled" ? "Restore" : "Mark cancelled"),
    actionButton("edit", editing ? "Save" : "Edit"),
  );
  if (editing) {
    actions.append(actionButton("discard", "Discard"));
  }
  header.append(meta, actions);

  const body = document.createElement("div");
  body.className = "transcript-body";
  if (state.editingIds.has(item.id)) {
    const label = document.createElement("label");
    label.className = "editor-label";
    label.htmlFor = `transcript-${item.id}`;
    label.textContent = "Transcript text";
    const editor = document.createElement("textarea");
    editor.id = `transcript-${item.id}`;
    editor.className = "transcript-editor";
    editor.spellcheck = true;
    editor.value = state.drafts.get(item.id) ?? item.text ?? "";
    editor.addEventListener("input", () => {
      state.drafts.set(item.id, editor.value);
      autosizeTextarea(editor);
    });
    body.append(label, editor);
    queueMicrotask(() => autosizeTextarea(editor));
  } else if (compactText(item.text)) {
    const text = document.createElement("p");
    text.className = "transcript-text";
    text.textContent = item.text;
    body.append(text);
  } else {
    const empty = document.createElement("p");
    empty.className = "transcript-empty";
    empty.textContent = item.audio_path
      ? "No speech was captured in the transcript. The audio is still available below."
      : "No speech was captured in this transcript.";
    body.append(empty);
  }

  if (item.audio_path) {
    const audio = document.createElement("details");
    audio.className = "audio-section";
    const summary = document.createElement("summary");
    summary.textContent = "Audio recording";
    const player = document.createElement("audio");
    player.controls = true;
    player.preload = "none";
    player.src = `/api/transcripts/${item.id}/audio`;
    audio.append(summary, player);
    body.append(audio);
  }

  const message = document.createElement("p");
  message.className = "entry-state";
  message.setAttribute("role", "status");
  message.setAttribute("aria-live", "polite");
  message.textContent = state.messages.get(item.id) || "";
  card.append(header, body, message);
  return card;
}

function actionButton(action, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary-button";
  button.dataset.action = action;
  button.textContent = label;
  return button;
}

async function handleHistoryClick(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) {
    return;
  }
  if (button.dataset.action === "load-more") {
    await refreshHistory({ append: true });
    return;
  }
  const card = button.closest(".transcript-card");
  const item = state.items.find((candidate) => String(candidate.id) === card?.dataset.id);
  if (!item) {
    return;
  }
  if (button.dataset.action === "copy") {
    await pasteSelected(item);
  } else if (button.dataset.action === "status") {
    await setSelectedStatus(item, item.status === "cancelled" ? "saved" : "cancelled");
  } else if (button.dataset.action === "edit") {
    if (state.editingIds.has(item.id)) {
      const editor = card.querySelector(".transcript-editor");
      const text = editor?.value || "";
      state.drafts.set(item.id, text);
      await saveCorrection(item, text);
    } else {
      state.editingIds.add(item.id);
      state.drafts.set(item.id, item.text || "");
      renderHistory();
    }
  } else if (button.dataset.action === "discard") {
    state.editingIds.delete(item.id);
    state.drafts.delete(item.id);
    state.messages.set(item.id, "Edit discarded");
    renderHistory();
  }
}

function autosizeTextarea(editor) {
  editor.style.height = "auto";
  editor.style.height = `${Math.max(editor.scrollHeight, 160)}px`;
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

async function saveCorrection(item, text) {
  state.messages.set(item.id, "Saving");
  renderHistory();
  try {
    const response = await sendJson(`/api/transcripts/${item.id}`, "PATCH", {
      text,
    });
    state.editingIds.delete(item.id);
    state.drafts.delete(item.id);
    state.messages.set(response.item.id, "Saved");
    updateSelected(response.item);
    await refreshStats();
  } catch (error) {
    state.messages.set(item.id, error.message);
    renderHistory();
  }
}

async function setSelectedStatus(item, status) {
  state.messages.set(item.id, status === "cancelled" ? "Marking cancelled" : "Restoring");
  renderHistory();
  try {
    const response = await sendJson(`/api/transcripts/${item.id}/status`, "POST", {
      status,
    });
    state.messages.set(response.item.id, status === "cancelled" ? "Marked cancelled" : "Restored");
    updateSelected(response.item);
    await refreshStats();
  } catch (error) {
    state.messages.set(item.id, error.message);
    renderHistory();
  }
}

async function pasteSelected(item) {
  state.messages.set(item.id, "Copying");
  renderHistory();
  try {
    await sendJson(`/api/transcripts/${item.id}/paste`, "POST", {});
    state.messages.set(item.id, "Copied. Paste in your app.");
    renderHistory();
  } catch (error) {
    state.messages.set(item.id, error.message);
    renderHistory();
  }
}

function updateSelected(item) {
  const index = state.items.findIndex((candidate) => candidate.id === item.id);
  if (index >= 0) {
    state.items[index] = item;
  }
  renderHistory();
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

function wordLabel(item) {
  const words = Number(item?.words || 0);
  return `${formatNumber(words)} ${words === 1 ? "word" : "words"}`;
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
