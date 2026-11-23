const palette = [
  { key: "deep_burgundy", name: "深酒紅", color: "#552525" },
  { key: "indigo", name: "靛藍", color: "#3c3f86" },
  { key: "dusty_rose", name: "灰玫瑰", color: "#a87a78" },
  { key: "gold", name: "金黃", color: "#e0cc17" },
  { key: "pale_yellow", name: "淺黃", color: "#f1e34d" },
  { key: "bright_blue", name: "亮藍", color: "#3b41b3" },
  { key: "rose_brown", name: "玫瑰棕", color: "#80494c" },
  { key: "navy", name: "深藍", color: "#171a39" },
];

const state = {
  sessionId: null,
  source: null,
  labels: null,
  labelData: null,
  regions: [],
  colorMap: {},
  transparent: new Set(),
  selectedRegion: null,
  selectedPalette: null,
  mode: "regions",
  repairMode: false,
  strokes: [],
  currentStroke: null,
  background: null,
  fileName: "",
  zoom: 1,
  segmentBusy: false,
  segmentRevision: null,
  references: [],
  aiConfig: null,
  aiRequest: null,
  aiOperation: null,
  aiPalette: null,
  reviewRegions: new Set(),
  manualRegions: new Set(),
};

const $ = (id) => document.getElementById(id);
const canvas = $("artCanvas");
const ctx = canvas.getContext("2d", { willReadFrequently: true });
const canvasViewport = $("canvasViewport");
const canvasPlane = $("canvasPlane");
const regionOverlay = $("regionOverlay");
const regionOverlayCtx = regionOverlay.getContext("2d");
const labelCanvas = document.createElement("canvas");
const labelCtx = labelCanvas.getContext("2d", { willReadFrequently: true });
let toastTimer = null;
let boardJob = null;
let boardFrame = null;

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 2600);
}

function setBusy(busy) {
  state.segmentBusy = busy;
  $("loadingState").classList.toggle("hidden", !busy);
  $("emptyState").classList.toggle("hidden", busy || !!state.source);
  $("resegmentButton").disabled = busy || !state.sessionId;
  $("fileInput").disabled = busy;
  $("resetButton").disabled = busy;
  updateZoomControls(busy);
  updateAutoColorControls();
}

function setAIStatus(message, isError = false) {
  const status = $("aiStatus");
  status.textContent = message;
  status.hidden = !message;
  status.classList.toggle("error", isError);
}

async function loadAIConfig() {
  try {
    const response = await fetch("/api/auto-color/config");
    if (!response.ok) throw new Error();
    applyAPIConfig(await response.json());
    if (!state.aiConfig.configured) $("apiSettings").open = true;
  } catch {
    $("aiModel").textContent = "無法讀取 API 設定";
    $("aiScope").textContent = "請重新啟動新版伺服器後再試。";
  }
  updateAutoColorControls();
}

function applyAPIConfig(config) {
  state.aiConfig = config;
  $("apiBaseUrl").value = config.base_url;
  $("apiFormat").value = config.api_format;
  $("apiOutputFormat").value = config.output_format;
  $("apiModelInput").value = config.model;
  $("apiKey").value = "";
  $("apiKey").placeholder = config.configured ? "已設定；留空可保留目前金鑰" : "輸入 API 金鑰";
  $("apiKeyHint").textContent = config.key_source === "environment"
    ? "使用環境變數中的金鑰；可輸入新金鑰取代。"
    : config.configured ? "已儲存於本機程序記憶體，重啟後會清除。" : "金鑰只保留在本機程序記憶體。";
  $("apiConnectionBadge").textContent = config.configured ? "已設定" : "尚未設定";
  $("aiModel").textContent = config.configured ? (config.model || "請選擇模型名稱") : "請先設定 AI 連線";
  $("aiScope").textContent = config.configured
    ? "上色時會將線稿、區域資訊與參考圖片傳送至所選 AI 服務。"
    : "請展開進階 AI 連線設定，輸入金鑰與模型名稱。";
  updateAutoColorControls();
}

function apiSettingsDirty() {
  const config = state.aiConfig;
  return !config || !!$("apiKey").value || $("apiBaseUrl").value.trim() !== config.base_url ||
    $("apiFormat").value !== config.api_format || $("apiOutputFormat").value !== config.output_format ||
    $("apiModelInput").value.trim() !== config.model;
}

function noticeAPISettingsChanged() {
  if (apiSettingsDirty()) setAPISettingsStatus("設定已變更，請按「儲存設定」再上色。");
  updateAutoColorControls();
}

function setAPISettingsStatus(message, isError = false) {
  const status = $("apiSettingsStatus");
  status.textContent = message;
  status.classList.toggle("error", isError);
}

async function saveAPISettings(clearKey = false) {
  const response = await fetch("/api/auto-color/config", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      base_url: $("apiBaseUrl").value.trim(),
      api_key: clearKey ? "" : $("apiKey").value,
      model: $("apiModelInput").value.trim(),
      api_format: $("apiFormat").value,
      output_format: $("apiOutputFormat").value,
      clear_api_key: clearKey,
    }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "無法儲存 API 設定。");
  applyAPIConfig(result);
  setAPISettingsStatus(clearKey ? "已清除本次工作階段的金鑰。" : "設定已儲存；請選擇支援圖片輸入的模型。");
  return result;
}

async function fetchAPIModels() {
  const button = $("fetchModels");
  button.disabled = true;
  setAPISettingsStatus("正在取得模型清單…");
  try {
    await saveAPISettings();
    const response = await fetch("/api/auto-color/models", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "無法取得模型清單。");
    const list = $("apiModelList");
    list.replaceChildren(...result.models.map((model) => {
      const option = document.createElement("option");
      option.value = model;
      return option;
    }));
    setAPISettingsStatus(result.models.length
      ? `取得 ${result.models.length} 個模型；點模型欄選擇或手動輸入。清單不代表均支援圖片。`
      : "服務沒有列出模型；請手動輸入模型 ID。");
    if (result.models.length) $("apiModelInput").focus();
  } catch (error) {
    setAPISettingsStatus(`${error.message} 您仍可手動輸入模型 ID。`, true);
  } finally {
    button.disabled = false;
  }
}

function updateAutoColorControls() {
  const pending = !!state.aiRequest;
  const operation = state.aiOperation;
  const resumingColor = operation?.kind === "coloring" && !pending;
  const resumingProbe = operation?.kind === "probe" && !pending;
  const max = state.aiConfig?.max_references ?? 3;
  $("referenceCount").textContent = `${state.references.length} / ${max}`;
  $("referenceInput").disabled = pending || !!operation || state.references.length >= max;
  $("referenceList").querySelectorAll("button").forEach((button) => { button.disabled = pending || !!operation; });
  const button = $("autoColorButton");
  button.firstChild.textContent = pending && operation?.kind === "coloring" ? "AI 正在上色… " :
    resumingColor ? "繼續查詢… " : pending ? "其他 AI 工作執行中… " : "依參考圖片上色 ";
  const canColor = state.aiConfig?.configured && state.aiConfig?.model && !apiSettingsDirty() &&
    state.segmentRevision && state.regions.length && state.references.length;
  button.disabled = state.segmentBusy || pending || !!(operation && !resumingColor) || (!resumingColor && !canColor);
  const testButton = $("testApi");
  const hasModel = !!$("apiModelInput").value.trim();
  const hasKey = !!state.aiConfig?.configured || !!$("apiKey").value.trim();
  testButton.textContent = pending && operation?.kind === "probe" ? "圖片辨識測試中…" :
    resumingProbe ? "繼續查詢測試" : pending ? "其他 AI 工作執行中…" : "測試圖片辨識";
  testButton.disabled = pending || !!(operation && !resumingProbe) || (!resumingProbe && (!hasKey || !hasModel));
}

function renderReferences() {
  const list = $("referenceList");
  list.replaceChildren();
  state.references.forEach(({ file, url }, index) => {
    const row = document.createElement("li");
    const image = document.createElement("img");
    image.src = url;
    image.alt = "";
    const info = document.createElement("div");
    info.className = "reference-info";
    const name = document.createElement("span");
    name.className = "reference-name";
    name.textContent = file.name;
    name.title = file.name;
    const role = document.createElement("small");
    role.className = "reference-role";
    role.textContent = index ? `參考圖片 ${index + 1}` : "主要參考圖片";
    info.append(name, role);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `移除參考圖片 ${file.name}`);
    remove.title = "移除參考圖片";
    remove.addEventListener("click", () => {
      URL.revokeObjectURL(url);
      state.references.splice(index, 1);
      setAIStatus("");
      renderReferences();
    });
    row.append(image, info, remove);
    list.append(row);
  });
  updateAutoColorControls();
}

function addReferences(event) {
  const max = state.aiConfig?.max_references ?? 3;
  const maxBytes = state.aiConfig?.max_bytes_per_reference ?? 8 * 1024 * 1024;
  let issue = "";
  for (const file of event.target.files) {
    if (state.references.some((entry) => entry.file.name === file.name &&
        entry.file.size === file.size && entry.file.lastModified === file.lastModified)) continue;
    if (state.references.length >= max) { issue = `最多加入 ${max} 張參考圖片。`; break; }
    if (!/^image\/(png|jpeg|webp)$/.test(file.type) &&
        (file.type || !/\.(png|jpe?g|webp)$/i.test(file.name))) {
      issue = "參考圖片需為 PNG、JPEG 或 WebP。";
      continue;
    }
    if (file.size > maxBytes) { issue = "單張參考圖片不可超過 8 MB。"; continue; }
    state.references.push({ file, url: URL.createObjectURL(file) });
  }
  event.target.value = "";
  setAIStatus(issue, !!issue);
  renderReferences();
}

function encodeReference(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.slice(reader.result.indexOf(",") + 1));
    reader.onerror = () => reject(new Error(`無法讀取參考圖片：${file.name}`));
    reader.readAsDataURL(file);
  });
}

function cancelAutoColor() {
  if (state.aiOperation) state.aiOperation.apply = false;
  if (state.aiRequest && state.aiOperation?.kind === "coloring") {
    setAIStatus("線稿或分區已變更；前一筆判讀會繼續，但完成後不會套用。", false);
  } else if (!state.aiOperation) {
    setAIStatus("");
  }
}

function applyAIAssignments(result) {
  const colors = {};
  const transparency = new Set();
  for (const id of state.manualRegions) {
    if (state.colorMap[id]) colors[id] = state.colorMap[id];
    if (state.transparent.has(id)) transparency.add(id);
  }
  let painted = 0;
  let backgrounds = 0;
  state.aiPalette = result.color_groups.map((group, index) => {
    for (const id of group.region_ids) {
      if (state.manualRegions.has(id)) continue;
      colors[id] = group.color;
      painted++;
    }
    return { key: "ai-" + index, name: group.name, color: group.color };
  });
  for (const id of result.background_ids) {
    if (state.manualRegions.has(id)) continue;
    transparency.add(id);
    backgrounds++;
  }
  state.colorMap = colors;
  state.transparent = transparency;
  state.reviewRegions = new Set(result.uncertain_ids.filter((id) => !state.manualRegions.has(id)));
  state.selectedPalette = null;
  $("reviewOnly").checked = false;
  renderPalette();
  renderRegions();
  state.zoom = 1;
  updateCanvasSize();
  setView(painted || backgrounds ? "color" : "regions");
  setAIStatus("已上色 " + painted + " 區 · 透明背景 " + backgrounds + " 區 · 建議檢查 " + state.reviewRegions.size + " 區");
}

class AIJobError extends Error {}

function newAIJobId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function readAIJobResponse(response) {
  let result;
  try { result = await response.json(); }
  catch (error) { if (response.ok) throw error; result = {}; }
  if (!response.ok) throw new AIJobError(result.error || "本機 AI 工作無法啟動。", result.code);
  return result;
}

async function getAIJob(operation) {
  if (!operation.sent) {
    operation.sent = true;
    return readAIJobResponse(await fetch(operation.path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: operation.body,
    }));
  }
  const response = await fetch("/api/auto-color/jobs/" + encodeURIComponent(operation.id), { cache: "no-store" });
  if (response.status === 404) {
    throw new AIJobError("找不到前一筆工作記錄；為避免重複計費，沒有重送模型請求。請確認本機伺服器是否曾重新啟動，再開始新工作。", "job_not_found");
  }
  return readAIJobResponse(response);
}

async function runAIJob(operation) {
  if (state.aiRequest) return;
  state.aiRequest = operation;
  updateAutoColorControls();
  try {
    if (!operation.body && operation.kind === "coloring") {
      setAIStatus("正在準備線稿與參考圖片…");
      const references = await Promise.all(state.references.map(({ file }) => encodeReference(file)));
      if (!operation.apply) { state.aiOperation = null; return; }
      operation.id = newAIJobId();
      operation.body = JSON.stringify({ request_id: operation.id, session_id: operation.sessionId,
                                        revision: operation.revision, references });
    }
    while (true) {
      const job = await getAIJob(operation);
      if (job.status === "failed") throw new AIJobError(job.error?.error || "AI 上色失敗。", job.error?.code);
      if (job.status === "completed") {
        state.aiOperation = null;
        if (operation.kind === "probe") {
          setAPISettingsStatus(job.result?.message || "圖片 API 測試完成。");
        } else if (operation.apply && state.sessionId === operation.sessionId &&
                   state.segmentRevision === job.result?.revision) {
          applyAIAssignments(job.result);
        } else {
          setAIStatus("前一筆判讀已完成，但線稿或分區已變更，結果未套用。");
        }
        return;
      }
      if (job.status !== "queued" && job.status !== "running") {
        throw new AIJobError("本機回傳未知的 AI 工作狀態。", "invalid_job_status");
      }
      const progress = job.status === "queued" ? "AI 工作已排入佇列…" : "雲端 API 正在判讀圖片…";
      if (operation.kind === "probe") setAPISettingsStatus(progress);
      else setAIStatus(progress);
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  } catch (error) {
    if (error instanceof AIJobError) state.aiOperation = null;
    const message = error instanceof TypeError || error instanceof SyntaxError
      ? "本機連線暫時中斷；工作 ID " + (operation.id || "準備中") + " 已保留。按「繼續查詢」會查同一筆工作，不會重送模型請求。"
      : error.message || "AI 上色失敗。";
    if (operation.kind === "probe") setAPISettingsStatus(message, true);
    else setAIStatus(message, true);
  } finally {
    if (state.aiRequest === operation) state.aiRequest = null;
    updateAutoColorControls();
  }
}

async function autoColor() {
  if (state.aiRequest) return;
  let operation = state.aiOperation;
  if (operation) {
    if (operation.kind !== "coloring") return;
    await runAIJob(operation);
    return;
  }
  if ($("autoColorButton").disabled) return;
  operation = { id: null, kind: "coloring", path: "/api/auto-color", body: null,
                sessionId: state.sessionId, revision: state.segmentRevision, apply: true, sent: false };
  state.aiOperation = operation;
  await runAIJob(operation);
}

async function probeAPI() {
  if (state.aiRequest) return;
  let operation = state.aiOperation;
  if (operation) {
    if (operation.kind !== "probe") return;
    await runAIJob(operation);
    return;
  }
  if ($("testApi").disabled) return;
  try {
    if (apiSettingsDirty()) await saveAPISettings();
  } catch (error) {
    setAPISettingsStatus(error.message || "無法儲存 API 設定。", true);
    return;
  }
  const id = newAIJobId();
  operation = { id, kind: "probe", path: "/api/auto-color/test",
                body: JSON.stringify({ request_id: id }), apply: true, sent: false };
  state.aiOperation = operation;
  await runAIJob(operation);
}

function updateZoomControls(busy = false) {
  const enabled = !!state.source && !busy;
  ["zoomOut", "zoomIn", "zoomReset"].forEach((id) => { $(id).disabled = !enabled; });
  $("zoomValue").value = `${Math.round(state.zoom * 100)}%`;
}

function updateCanvasSize() {
  if (!state.source || !canvas.width || !canvas.height) return;
  const padding = state.zoom > 1 ? 24 : 0;
  const availableWidth = Math.max(1, canvasViewport.clientWidth - padding);
  const availableHeight = Math.max(1, canvasViewport.clientHeight - padding);
  const fitScale = Math.min(availableWidth / canvas.width, availableHeight / canvas.height);
  const cssScale = fitScale * state.zoom;
  canvasPlane.style.width = `${Math.max(1, Math.round(canvas.width * cssScale))}px`;
  canvasPlane.style.height = `${Math.max(1, Math.round(canvas.height * cssScale))}px`;
  canvasViewport.classList.toggle("zoomed", state.zoom > 1.001);
  $("zoomValue").value = `${Math.round(state.zoom * 100)}%`;
}

function setZoom(nextZoom) {
  state.zoom = Math.max(0.75, Math.min(3, nextZoom));
  updateCanvasSize();
  render();
}

function focusRegion(region) {
  const focusZoom = region.area <= 1500 ? 3 : region.area <= 7000 ? 2 : 1.5;
  if (state.zoom < focusZoom) setZoom(focusZoom);
  requestAnimationFrame(() => {
    const cssScale = canvas.getBoundingClientRect().width / canvas.width;
    const viewportStyle = getComputedStyle(canvasViewport);
    const paddingLeft = parseFloat(viewportStyle.paddingLeft) || 0;
    const paddingTop = parseFloat(viewportStyle.paddingTop) || 0;
    canvasViewport.scrollTo({
      left: Math.max(0, paddingLeft + region.center[0] * cssScale - canvasViewport.clientWidth / 2),
      top: Math.max(0, paddingTop + region.center[1] * cssScale - canvasViewport.clientHeight / 2),
      behavior: "instant",
    });
    render();
  });
}

function setImage(img) {
  state.source = img;
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  regionOverlay.width = img.naturalWidth;
  regionOverlay.height = img.naturalHeight;
  labelCanvas.width = img.naturalWidth;
  labelCanvas.height = img.naturalHeight;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0);
  $("dimensions").textContent = `${img.naturalWidth} × ${img.naturalHeight}`;
  $("stage").classList.add("has-image");
  updateCanvasSize();
}

async function loadImage(blob, name) {
  cancelAutoColor();
  clearBoardFrame();
  setBusy(true);
  state.sessionId = null;
  state.segmentRevision = null;
  state.regions = [];
  state.labelData = null;
  state.fileName = name;
  $("fileName").textContent = name;
  clearColorAssignments();
  state.selectedRegion = null;
  state.strokes = [];
  renderRegions();
  const localUrl = URL.createObjectURL(blob);
  const img = new Image();
  img.onload = async () => {
    URL.revokeObjectURL(localUrl);
    setImage(img);
    try {
      const threshold = Number($("thresholdRange").value);
      const closeSize = Number($("closeRange").value);
      const response = await fetch(`/api/segment?threshold=${threshold}&close=${closeSize}`, {
        method: "POST", headers: { "Content-Type": blob.type || "image/jpeg" }, body: blob,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "無法分析這張圖片");
      state.sessionId = result.id;
      await refreshSegmentation();
      $("canvasHint").classList.remove("hidden");
      $("hintText").textContent = "選擇顏色，再點選區域";
      $("repairToggle").disabled = false;
      $("clearColors").disabled = false;
      $("exportButton").disabled = false;
      $("exportCompositeButton").disabled = false;
      updateZoomControls(false);
      render();
    } catch (error) {
      toast(error.message);
    } finally {
      setBusy(false);
    }
  };
  img.onerror = () => { URL.revokeObjectURL(localUrl); setBusy(false); toast("無法讀取這張圖片。"); };
  img.src = localUrl;
}

function hashColor(id) {
  const colors = ["#d3ae82", "#8ca09a", "#b1a0be", "#d69a8c", "#92a9c1", "#c2b68b", "#9d8e84", "#86aaa2"];
  return colors[(id * 5 + 3) % colors.length];
}

async function refreshSegmentation() {
  const res = await fetch(`/api/session/${state.sessionId}/regions`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "讀取分區失敗");
  const mapResponse = await fetch(`/api/session/${state.sessionId}/raster?revision=${encodeURIComponent(data.revision)}`);
  if (!mapResponse.ok) throw new Error("讀取分區遮罩失敗");
  const mapImg = await createImageBitmap(await mapResponse.blob(), { colorSpaceConversion: "none" });
  labelCtx.clearRect(0, 0, labelCanvas.width, labelCanvas.height);
  labelCtx.drawImage(mapImg, 0, 0);
  mapImg.close();
  clearBoardFrame();
  state.regions = data.regions;
  state.segmentRevision = data.revision;
  state.labelData = labelCtx.getImageData(0, 0, labelCanvas.width, labelCanvas.height).data;
  $("regionCount").textContent = `${data.regions.length} 個區域 · ${data.dropped_count ? `略過 ${data.dropped_count} 個極小區` : "已更新"}`;
  $("regionSummary").textContent = `${data.width} × ${data.height}  /  ${data.regions.length} REGIONS`;
  updateAutoColorControls();
  renderRegions();
  render();
}

function activePalette() {
  return state.aiPalette?.length ? state.aiPalette : palette;
}

function clearColorAssignments() {
  state.colorMap = {};
  state.transparent.clear();
  state.manualRegions.clear();
  state.reviewRegions.clear();
  state.aiPalette = null;
  state.selectedPalette = null;
  $("reviewOnly").checked = false;
  renderPalette();
}

function setManualColor(id, color) {
  state.colorMap[id] = color;
  state.transparent.delete(id);
  state.manualRegions.add(id);
  state.reviewRegions.delete(id);
}

function applySelectedSwatch(id) {
  const item = activePalette().find((entry) => entry.key === state.selectedPalette);
  if (item) setManualColor(id, item.color);
}

function renderPalette() {
  const root = $("palette");
  root.innerHTML = "";
  const hasAIColors = !!state.aiPalette?.length;
  $("paletteHeading").textContent = hasAIColors ? "AI 建議配色" : "選擇顏色";
  $("paletteDetail").textContent = hasAIColors ? `${state.aiPalette.length} 種顏色 · 根據參考圖片` : "選取顏色後，點選區域套用";
  for (const item of activePalette()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `swatch${state.selectedPalette === item.key ? " selected" : ""}`;
    button.title = `${item.name} ${item.color}`;
    const fill = document.createElement("i");
    fill.className = "swatch-color";
    fill.style.backgroundColor = item.color;
    const label = document.createElement("span");
    label.className = "swatch-label";
    label.textContent = item.name;
    button.append(fill, label);
    button.addEventListener("click", () => {
      state.selectedPalette = item.key;
      if (state.selectedRegion !== null) {
        setManualColor(state.selectedRegion, item.color);
        renderRegions();
        render();
      }
      renderPalette();
    });
    root.append(button);
  }
}

function renderRegions() {
  const root = $("regionList");
  $("reviewFilter").hidden = !state.reviewRegions.size;
  $("reviewCount").value = state.reviewRegions.size;
  if (!state.reviewRegions.size) $("reviewOnly").checked = false;
  const reviewOnly = $("reviewOnly").checked;
  const query = $("regionSearch").value.trim().replace(/^#/, "");
  const visible = state.regions.filter(({ id }) =>
    (!query || String(id).includes(query)) && (!reviewOnly || state.reviewRegions.has(id)));
  if (!visible.length) {
    root.innerHTML = `<div class="region-empty">${state.regions.length ? "沒有符合的區域。" : "選擇線稿後，點選區域即可上色。"}</div>`;
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const region of visible) {
    const row = document.createElement("div");
    row.className = `region-row${state.selectedRegion === region.id ? " selected" : ""}`;
    row.dataset.id = region.id;
    const color = state.colorMap[region.id];
    const name = state.transparent.has(region.id) ? "透明背景" :
      activePalette().find((item) => item.color === color?.toLowerCase())?.name || color || "尚未上色";
    row.innerHTML = `<span class="region-num">#${region.id}</span><span class="region-info"><span class="region-main"><span class="region-name"></span>${state.reviewRegions.has(region.id) ? '<span class="review-tag">需要檢查</span>' : ""}${region.touches_border ? '<span class="edge-tag">延伸至邊緣</span>' : ""}</span><span class="region-area">${region.area.toLocaleString()} 像素</span></span><input class="region-color" type="color" aria-label="區域 ${region.id} 顏色" value="${color || "#ffffff"}" title="設定區域顏色"><input class="transparent-toggle" type="checkbox" aria-label="區域 ${region.id} 設為透明背景" title="設為透明背景" ${state.transparent.has(region.id) ? "checked" : ""}>`;
    row.querySelector(".region-name").textContent = name;
    row.addEventListener("click", (event) => {
      if (event.target.matches("input")) return;
      state.selectedRegion = region.id;
      if (state.reviewRegions.has(region.id) && state.mode !== "regions") setView("regions");
      applySelectedSwatch(region.id);
      renderRegions();
      focusRegion(region);
      render();
    });
    const colorInput = row.querySelector(".region-color");
    colorInput.addEventListener("change", () => {
      setManualColor(region.id, colorInput.value);
      state.selectedRegion = region.id;
      renderRegions();
      render();
    });
    row.querySelector(".transparent-toggle").addEventListener("change", (event) => {
      event.target.checked ? state.transparent.add(region.id) : state.transparent.delete(region.id);
      state.manualRegions.add(region.id);
      state.reviewRegions.delete(region.id);
      renderRegions();
      render();
    });
    fragment.append(row);
  }
  root.replaceChildren(fragment);
}

function hexRgb(hex) {
  return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
}

function renderRegionsView() {
  ctx.drawImage(state.source, 0, 0);
  if (!state.labelData) return;
  const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const pixels = image.data;
  const rgbById = state.regions.map(({ id }) => hexRgb(hashColor(id)));
  for (let i = 0, p = 0; i < state.labelData.length; i += 4, p += 4) {
    const id = state.labelData[i];
    if (!id) continue;
    const [r, g, b] = rgbById[id - 1];
    pixels[p] = Math.round(pixels[p] * 0.73 + r * 0.27);
    pixels[p + 1] = Math.round(pixels[p + 1] * 0.73 + g * 0.27);
    pixels[p + 2] = Math.round(pixels[p + 2] * 0.73 + b * 0.27);
  }
  ctx.putImageData(image, 0, 0);
}

function renderRegionLabels() {
  regionOverlayCtx.clearRect(0, 0, regionOverlay.width, regionOverlay.height);
  if (!state.source || !state.labelData || state.mode !== "regions") return;
  const cssScale = canvas.getBoundingClientRect().width / canvas.width;
  if (state.zoom > 1.001 && cssScale > 0) {
    drawZoomedRegionLabels(cssScale);
    return;
  }

  const labelCtx = regionOverlayCtx;
  labelCtx.save();
  const scale = canvas.width / canvas.getBoundingClientRect().width;
  labelCtx.font = `600 ${Math.max(12, 13 * scale)}px Consolas, monospace`;
  labelCtx.textAlign = "center";
  labelCtx.textBaseline = "middle";
  const occupied = [];
  const candidates = [...state.regions].sort((a, b) => b.area - a.area);
  for (const region of candidates) {
    if (region.area < 9000 && state.selectedRegion !== region.id) continue;
    const [x, y] = region.center;
    const label = String(region.id);
    const pad = 4 * scale;
    const metrics = labelCtx.measureText(label);
    const w = metrics.width + pad * 2;
    const h = 18 * scale;
    const box = [x - w / 2, y - h / 2, x + w / 2, y + h / 2];
    const overlaps = occupied.some((other) => box[0] < other[2] && box[2] > other[0] && box[1] < other[3] && box[3] > other[1]);
    if (overlaps && state.selectedRegion !== region.id) continue;
    occupied.push(box);
    labelCtx.fillStyle = state.selectedRegion === region.id ? "#465d4b" : "#fbfaf0dc";
    labelCtx.beginPath();
    labelCtx.roundRect(x - w / 2, y - h / 2, w, h, 4 * scale);
    labelCtx.fill();
    labelCtx.fillStyle = state.selectedRegion === region.id ? "#fff" : "#3e5140";
    labelCtx.fillText(label, x, y + scale * 0.5);
  }
  labelCtx.restore();
}

function drawZoomedRegionLabels(cssScale) {
  const labelCtx = regionOverlayCtx;
  const scrollLeft = canvasViewport.scrollLeft;
  const scrollTop = canvasViewport.scrollTop;
  const viewWidth = canvasViewport.clientWidth;
  const viewHeight = canvasViewport.clientHeight;
  const viewportStyle = getComputedStyle(canvasViewport);
  const paddingLeft = parseFloat(viewportStyle.paddingLeft) || 0;
  const paddingTop = parseFloat(viewportStyle.paddingTop) || 0;
  const sourceLeft = (scrollLeft - paddingLeft) / cssScale;
  const sourceTop = (scrollTop - paddingTop) / cssScale;
  const sourceRight = (scrollLeft + viewWidth - paddingLeft) / cssScale;
  const sourceBottom = (scrollTop + viewHeight - paddingTop) / cssScale;
  const fontCss = Math.min(19, 11 + (state.zoom - 1) * 5);
  const gap = 3;
  const directions = [[0, 0]];
  for (let radius = 16; radius <= 192; radius += 16) {
    directions.push(
      [0, -radius], [radius, 0], [0, radius], [-radius, 0],
      [radius, -radius], [radius, radius], [-radius, radius], [-radius, -radius],
      [radius * 0.5, -radius], [radius, -radius * 0.5],
      [radius * 0.5, radius], [-radius, radius * 0.5],
      [-radius * 0.5, radius], [-radius, -radius * 0.5],
    );
  }

  const candidates = state.regions
    .filter((region) => {
      const [x, y] = region.center;
      return (x >= sourceLeft && x <= sourceRight && y >= sourceTop && y <= sourceBottom) || region.id === state.selectedRegion;
    })
    .sort((a, b) => {
      if (a.id === state.selectedRegion) return -1;
      if (b.id === state.selectedRegion) return 1;
      return a.area - b.area;
    });

  const occupied = [];
  labelCtx.save();
  labelCtx.font = `600 ${fontCss / cssScale}px Consolas, monospace`;
  labelCtx.textAlign = "center";
  labelCtx.textBaseline = "middle";

  for (const region of candidates) {
    const [sourceX, sourceY] = region.center;
    const anchorX = paddingLeft + sourceX * cssScale - scrollLeft;
    const anchorY = paddingTop + sourceY * cssScale - scrollTop;
    const label = String(region.id);
    const width = labelCtx.measureText(label).width * cssScale + 10;
    const height = fontCss + 8;
    let position = null;

    for (const [offsetX, offsetY] of directions) {
      const x = anchorX + offsetX;
      const y = anchorY + offsetY;
      const box = [x - width / 2, y - height / 2, x + width / 2, y + height / 2];
      if (box[0] < 2 || box[1] < 2 || box[2] > viewWidth - 2 || box[3] > viewHeight - 2) continue;
      if (occupied.some((other) => box[0] < other[2] + gap && box[2] + gap > other[0] && box[1] < other[3] + gap && box[3] + gap > other[1])) continue;
      position = { x, y, box };
      break;
    }

    if (!position) {
      const x = Math.min(Math.max(anchorX, width / 2 + 2), viewWidth - width / 2 - 2);
      const y = Math.min(Math.max(anchorY, height / 2 + 2), viewHeight - height / 2 - 2);
      const box = [x - width / 2, y - height / 2, x + width / 2, y + height / 2];
      position = { x, y, box };
    }
    occupied.push(position.box);

    const targetX = (position.x + scrollLeft - paddingLeft) / cssScale;
    const targetY = (position.y + scrollTop - paddingTop) / cssScale;
    if (Math.hypot(position.x - anchorX, position.y - anchorY) > 7) {
      labelCtx.beginPath();
      labelCtx.moveTo(sourceX, sourceY);
      labelCtx.lineTo(targetX, targetY);
      labelCtx.strokeStyle = state.selectedRegion === region.id ? "#465d4b" : "#788176";
      labelCtx.lineWidth = Math.max(1, 1.2 / cssScale);
      labelCtx.stroke();
    }

    const widthSource = width / cssScale;
    const heightSource = height / cssScale;
    labelCtx.fillStyle = state.selectedRegion === region.id ? "#465d4b" : "#fbfaf0f2";
    labelCtx.beginPath();
    labelCtx.roundRect(targetX - widthSource / 2, targetY - heightSource / 2, widthSource, heightSource, 4 / cssScale);
    labelCtx.fill();
    labelCtx.strokeStyle = state.selectedRegion === region.id ? "#465d4b" : "#d9ddd3";
    labelCtx.lineWidth = Math.max(1, 1 / cssScale);
    labelCtx.stroke();
    labelCtx.fillStyle = state.selectedRegion === region.id ? "#fff" : "#3e5140";
    labelCtx.fillText(label, targetX, targetY);
  }
  labelCtx.restore();
}

function buildColorLayer() {
  const image = ctx.createImageData(canvas.width, canvas.height);
  const out = image.data;
  const map = state.labelData;
  const white = [255, 255, 255];
  const colors = Array.from({ length: 256 }, (_, id) => state.colorMap[id] ? hexRgb(state.colorMap[id]) : white);
  for (let p = 0; p < out.length; p += 4) {
    const id = map[p];
    const gray = map[p + 1];
    if (!id) {
      out[p] = out[p + 1] = out[p + 2] = 0;
      out[p + 3] = Math.max(0, 255 - gray);
      continue;
    }
    if (state.transparent.has(id)) {
      out[p + 3] = 0;
      continue;
    }
    const color = colors[id];
    out[p] = Math.round(color[0] * gray / 255);
    out[p + 1] = Math.round(color[1] * gray / 255);
    out[p + 2] = Math.round(color[2] * gray / 255);
    out[p + 3] = 255;
  }
  return image;
}

function drawColorLayer(target, bitmap = null, includeBackground = true) {
  target.clearRect(0, 0, canvas.width, canvas.height);
  if (bitmap) target.drawImage(bitmap, 0, 0);
  else target.putImageData(buildColorLayer(), 0, 0);
  if (includeBackground && state.background) {
    const bg = state.background;
    const scale = Math.max(canvas.width / bg.naturalWidth, canvas.height / bg.naturalHeight);
    const w = bg.naturalWidth * scale;
    const h = bg.naturalHeight * scale;
    target.globalCompositeOperation = "destination-over";
    target.drawImage(bg, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
    target.globalCompositeOperation = "source-over";
  }
}

function setCompositorStatus(message, usingBoard = false) {
  const status = $("compositorStatus");
  status.textContent = message;
  status.dataset.usingBoard = String(usingBoard);
}

async function loadCompositorConfig() {
  const control = $("boardCompositor");
  try {
    const response = await fetch("/api/compositor/config");
    if (!response.ok) throw new Error();
    const config = await response.json();
    control.disabled = !config.enabled;
    control.title = config.renderer ? "裝置加速已連線" :
      config.enabled ? "等待裝置連線" : "目前未啟用裝置加速";
  } catch {
    control.title = "無法讀取裝置加速設定";
  }
}

function boardPayload() {
  return JSON.stringify({ session_id: state.sessionId, revision: state.segmentRevision,
    colors: state.colorMap, transparent: [...state.transparent] });
}

function clearBoardFrame() {
  boardJob?.controller.abort();
  boardJob = null;
  boardFrame?.bitmap?.close();
  boardFrame = null;
}

function getBoardFrame(key) {
  if (boardJob?.key === key) return boardJob.promise;
  if (boardFrame?.key === key) return Promise.resolve(boardFrame);
  clearBoardFrame();
  const controller = new AbortController();
  const job = { key, controller };
  boardJob = job;
  job.promise = (async () => {
    try {
      const response = await fetch("/api/compositor/render", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: key, signal: controller.signal,
      });
      if (!response.ok) throw new Error((await response.json()).error);
      const blob = await response.blob();
      const bitmap = await createImageBitmap(blob);
      if (controller.signal.aborted || bitmap.width !== canvas.width || bitmap.height !== canvas.height) {
        bitmap.close();
        if (controller.signal.aborted) return null;
        throw new Error("裝置回傳的圖片尺寸不符。");
      }
      boardFrame = { key, bitmap, blob, renderer: response.headers.get("X-Color-Renderer") };
    } catch (error) {
      if (controller.signal.aborted) return null;
      boardFrame = { key, error: error.message || "裝置無法連線。" };
    } finally {
      if (boardJob === job) boardJob = null;
    }
    return boardFrame;
  })();
  return job.promise;
}

function renderColorPreview() {
  if (!state.labelData) return;
  const control = $("boardCompositor");
  if (!control.checked) {
    drawColorLayer(ctx);
    setCompositorStatus("合成方式：本機瀏覽器");
    return;
  }
  const key = boardPayload();
  if (boardFrame?.key !== key) setCompositorStatus("裝置加速合成中…");
  getBoardFrame(key).then((frame) => {
    if (!frame || state.mode !== "color" || !control.checked || key !== boardPayload() || frame !== boardFrame) return;
    drawColorLayer(ctx, frame.bitmap);
    if (frame.bitmap) setCompositorStatus("合成方式：裝置 GPU 加速", true);
    else setCompositorStatus(`${frame.error} 已改用本機瀏覽器`);
  });
}

function render() {
  if (!state.source) return;
  if (state.mode === "regions") renderRegionsView();
  else if (state.mode === "color") renderColorPreview();
  else ctx.drawImage(state.source, 0, 0);
  renderRegionLabels();
  if (state.repairMode && state.mode === "regions") drawStrokes();
  $("canvasHint").classList.toggle("hidden", state.mode !== "regions");
  $("stage").classList.toggle("background-preview", state.mode === "color" && !!state.background);
}

function drawStrokes() {
  if (!state.currentStroke && !state.strokes.length) return;
  const cssScale = canvas.width / canvas.getBoundingClientRect().width;
  ctx.save();
  ctx.strokeStyle = "#d75f48";
  ctx.lineWidth = Number($("brushRange").value) * cssScale;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const stroke of [...state.strokes, ...(state.currentStroke ? [state.currentStroke] : [])]) {
    if (stroke.points.length < 2) continue;
    ctx.beginPath();
    ctx.moveTo(stroke.points[0][0], stroke.points[0][1]);
    for (const point of stroke.points.slice(1)) ctx.lineTo(point[0], point[1]);
    ctx.stroke();
  }
  ctx.restore();
}

async function resegment() {
  if (!state.sessionId || state.segmentBusy) return;
  cancelAutoColor();
  setBusy(true);
  try {
    const scale = canvas.width / canvas.getBoundingClientRect().width;
    const strokes = state.strokes.map((stroke) => ({
      points: stroke.points,
      width: Math.max(1, Math.round(Number($("brushRange").value) * scale)),
    }));
    const response = await fetch("/api/resegment", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: state.sessionId, threshold: Number($("thresholdRange").value), close_size: Number($("closeRange").value), strokes }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "重新分析失敗");
    clearColorAssignments();
    state.selectedRegion = null;
    await refreshSegmentation();
    toast("分區已更新；請重新確認配色");
  } catch (error) { toast(error.message); }
  finally { setBusy(false); }
}

function pointerPosition(event) {
  const rect = canvas.getBoundingClientRect();
  return [Math.round((event.clientX - rect.left) * canvas.width / rect.width), Math.round((event.clientY - rect.top) * canvas.height / rect.height)];
}

canvas.addEventListener("pointerdown", (event) => {
  if (!state.source || state.segmentBusy || state.mode !== "regions") return;
  if (state.repairMode) {
    canvas.setPointerCapture(event.pointerId);
    state.currentStroke = { points: [pointerPosition(event)] };
    render();
    return;
  }
  const [x, y] = pointerPosition(event);
  const index = y * canvas.width + x;
  const id = state.labelData?.[index * 4] || 0;
  if (!id) return;
  state.selectedRegion = id;
  applySelectedSwatch(id);
  renderRegions();
  render();
});

canvas.addEventListener("pointermove", (event) => {
  if (!state.currentStroke) return;
  const point = pointerPosition(event);
  const previous = state.currentStroke.points.at(-1);
  state.currentStroke.points.push(point);
  const scale = canvas.width / canvas.getBoundingClientRect().width;
  ctx.save();
  ctx.strokeStyle = "#d75f48";
  ctx.lineWidth = Number($("brushRange").value) * scale;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.beginPath();
  ctx.moveTo(previous[0], previous[1]);
  ctx.lineTo(point[0], point[1]);
  ctx.stroke();
  ctx.restore();
});

canvas.addEventListener("pointerup", () => {
  if (!state.currentStroke) return;
  if (state.currentStroke.points.length > 1) state.strokes.push(state.currentStroke);
  state.currentStroke = null;
  $("undoStroke").disabled = !state.strokes.length;
  $("clearStrokes").disabled = !state.strokes.length;
  drawStrokes();
  resegment();
});

$("fileInput").addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (file) loadImage(file, file.name);
  event.target.value = "";
});

$("thresholdRange").addEventListener("input", (event) => { $("thresholdValue").value = event.target.value; });
$("closeRange").addEventListener("input", (event) => { $("closeValue").value = `${event.target.value} px`; });
$("brushRange").addEventListener("input", (event) => { $("brushValue").value = event.target.value; });
$("zoomOut").addEventListener("click", () => setZoom(state.zoom - 0.25));
$("zoomIn").addEventListener("click", () => setZoom(state.zoom + 0.25));
$("zoomReset").addEventListener("click", () => setZoom(1));
$("resegmentButton").addEventListener("click", resegment);
$("regionSearch").addEventListener("input", renderRegions);
$("reviewOnly").addEventListener("change", renderRegions);
$("referenceInput").addEventListener("change", addReferences);
$("autoColorButton").addEventListener("click", autoColor);
$("testApi").addEventListener("click", probeAPI);
for (const id of ["apiKey", "apiBaseUrl", "apiModelInput"]) $(id).addEventListener("input", noticeAPISettingsChanged);
for (const id of ["apiFormat", "apiOutputFormat"]) $(id).addEventListener("change", noticeAPISettingsChanged);
$("toggleApiKey").addEventListener("click", () => {
  const input = $("apiKey");
  const reveal = input.type === "password";
  input.type = reveal ? "text" : "password";
  $("toggleApiKey").textContent = reveal ? "隱藏" : "顯示";
  $("toggleApiKey").setAttribute("aria-label", reveal ? "隱藏 API 金鑰" : "顯示 API 金鑰");
});
$("railwayPreset").addEventListener("click", () => {
  $("apiBaseUrl").value = "https://asiasouth.up.railway.app/v1";
  noticeAPISettingsChanged();
});
$("saveApiSettings").addEventListener("click", async () => {
  try { await saveAPISettings(); } catch (error) { setAPISettingsStatus(error.message, true); }
});
$("clearApiKey").addEventListener("click", async () => {
  try { await saveAPISettings(true); } catch (error) { setAPISettingsStatus(error.message, true); }
});
$("fetchModels").addEventListener("click", fetchAPIModels);
$("boardCompositor").addEventListener("change", () => {
  clearBoardFrame();
  if (state.mode === "color" && state.labelData) renderColorPreview();
  else setCompositorStatus($("boardCompositor").checked ? "裝置加速待命" : "合成方式：本機瀏覽器");
});
$("clearColors").addEventListener("click", () => {
  cancelAutoColor();
  clearColorAssignments();
  updateAutoColorControls();
  renderRegions();
  render();
});
$("repairToggle").addEventListener("click", () => {
  state.repairMode = !state.repairMode;
  $("repairToggle").textContent = state.repairMode ? "完成修補" : "開始修補";
  $("repairToggle").classList.toggle("active", state.repairMode);
  $("stage").classList.toggle("tool-repair", state.repairMode);
  $("hintText").textContent = state.repairMode ? "沿著缺口補上線條，再檢查區域" : "選擇顏色，再點選區域";
  render();
});
$("undoStroke").addEventListener("click", () => { state.strokes.pop(); $("undoStroke").disabled = !state.strokes.length; $("clearStrokes").disabled = !state.strokes.length; resegment(); });
$("clearStrokes").addEventListener("click", () => { state.strokes = []; $("undoStroke").disabled = true; $("clearStrokes").disabled = true; resegment(); });

$("backgroundInput").addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => { state.background = img; $("backgroundInput").parentElement.firstChild.textContent = "✓ 背景已載入 "; if (state.mode === "color") render(); toast("背景預覽已更新"); URL.revokeObjectURL(url); };
  img.src = url;
});

function setView(mode) {
  document.querySelectorAll(".view-button").forEach((item) => item.classList.toggle("active", item.dataset.view === mode));
  state.mode = mode;
  render();
}

document.querySelectorAll(".view-button").forEach((button) =>
  button.addEventListener("click", () => setView(button.dataset.view)));

function download(blob, filename) {
  if (!blob) return toast("無法輸出圖片");
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function exportImage(includeBackground) {
  if (!state.labelData || state.segmentBusy) return;
  if (includeBackground && !state.background) return toast("請先加入背景預覽圖片。");
  const name = includeBackground ? "colored-image-with-background.png" : "colored-image-transparent.png";
  let frame = null;
  if ($("boardCompositor").checked) {
    const key = boardPayload();
    frame = await getBoardFrame(key);
    if (!frame || frame !== boardFrame || key !== boardPayload() || state.segmentBusy) return;
    if (frame.blob && !includeBackground) return download(frame.blob, name);
    if (frame.error) toast("裝置加速暫不可用，已改用本機輸出。");
  }
  const output = document.createElement("canvas");
  output.width = canvas.width;
  output.height = canvas.height;
  drawColorLayer(output.getContext("2d"), frame?.bitmap, includeBackground);
  output.toBlob((blob) => download(blob, name), "image/png");
}

$("exportButton").addEventListener("click", () => exportImage(false));
$("exportCompositeButton").addEventListener("click", () => exportImage(true));

$("resetButton").addEventListener("click", () => {
  cancelAutoColor();
  clearBoardFrame();
  setCompositorStatus($("boardCompositor").checked ? "裝置加速待命" : "合成方式：本機瀏覽器");
  state.sessionId = null; state.source = null; state.labels = null; state.labelData = null; state.regions = [];
  state.segmentRevision = null;
  state.selectedRegion = null;
  clearColorAssignments();
  for (const { url } of state.references) URL.revokeObjectURL(url);
  state.references = [];
  renderReferences();
  state.strokes = []; state.background = null;
  canvas.width = canvas.height = 1;
  regionOverlay.width = regionOverlay.height = 1;
  ctx.clearRect(0, 0, 1, 1);
  regionOverlayCtx.clearRect(0, 0, 1, 1);
  $("fileName").textContent = "尚未選擇圖片"; $("dimensions").textContent = "—";
  $("regionCount").textContent = "選擇線稿後顯示"; $("regionSummary").textContent = "等待圖片";
  $("regionSearch").value = "";
  renderRegions();
  $("repairToggle").disabled = true; $("repairToggle").textContent = "開始修補";
  $("undoStroke").disabled = true; $("clearStrokes").disabled = true;
  $("clearColors").disabled = true; $("exportButton").disabled = true; $("exportCompositeButton").disabled = true;
  $("canvasHint").classList.add("hidden"); $("emptyState").classList.remove("hidden");
  $("stage").classList.remove("has-image", "background-preview", "tool-repair");
  state.repairMode = false;
  document.querySelectorAll(".view-button").forEach((button) => button.classList.toggle("active", button.dataset.view === "regions"));
  state.mode = "regions"; state.zoom = 1;
  canvas.style.width = "";
  canvas.style.height = "";
  canvasPlane.style.width = "";
  canvasPlane.style.height = "";
  canvasViewport.classList.remove("zoomed");
  updateZoomControls(false);
});

renderPalette();
loadAIConfig();
loadCompositorConfig();
new ResizeObserver(() => updateCanvasSize()).observe(canvasViewport);
let regionLabelFrame = 0;
canvasViewport.addEventListener("scroll", () => {
  if (regionLabelFrame || state.zoom <= 1.001 || state.mode !== "regions") return;
  regionLabelFrame = requestAnimationFrame(() => {
    regionLabelFrame = 0;
    renderRegionLabels();
  });
}, { passive: true });
updateZoomControls(false);
