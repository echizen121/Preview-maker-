const $ = (id) => document.getElementById(id);

const fields = {
  background: $("background"),
  motion: $("motion"),
  bgm: $("bgm"),
  output: $("output"),
  width: $("width"),
  height: $("height"),
  fps: $("fps"),
  duration: $("duration"),
  model_x: $("model_x"),
  model_y: $("model_y"),
  model_scale: $("model_scale"),
  motion_start: $("motion_start"),
  motion_end: $("motion_end"),
  bgm_start: $("bgm_start"),
  bgm_end: $("bgm_end"),
  bgm_volume: $("bgm_volume"),
  video_bitrate: $("video_bitrate"),
};

const pairedControls = [
  ["model_x", "modelXRange"],
  ["model_y", "modelYRange"],
  ["model_scale", "scaleRange"],
  ["bgm_volume", "volumeRange"],
];

let isPlaying = false;
let timelineStartedAt = 0;
let timelineBaseTime = 0;
let timelineTimer = null;
let bgmSeekDragging = false;
const externalAssets = {
  background: "",
  motion: "",
  bgm: "",
};
let latestAssets = {
  backgrounds: [],
  motions: [],
  bgms: [],
  presets: [],
  outputs: [],
};
let motionProxySource = "";
let motionProxyBuilding = "";
let currentRenderJobId = "";
let renderPollTimer = null;

function log(message) {
  $("log").textContent = Array.isArray(message) ? message.join("\n") : message;
}

function option(label, value) {
  const node = document.createElement("option");
  node.textContent = label;
  node.value = value;
  return node;
}

function fillSelect(select, items, emptyLabel) {
  select.innerHTML = "";
  select.appendChild(option(emptyLabel, ""));
  items.forEach((item) => select.appendChild(option(item.path, item.path)));
}

function openTab(panelId) {
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === panelId);
  });
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === panelId);
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "APIエラーが発生しました。");
  }
  return data;
}

async function refreshAssets() {
  const assets = await api("/api/assets");
  latestAssets = assets;
  const current = {
    background: fields.background.value,
    motion: fields.motion.value,
    bgm: fields.bgm.value,
    preset: $("presetList").value,
  };
  fillSelect(fields.background, assets.backgrounds, "背景を選択");
  fillSelect(fields.motion, assets.motions, "Live2D動画を選択");
  fillSelect(fields.bgm, assets.bgms, "BGMなし");
  fillSelect($("presetList"), assets.presets, "プリセットを選択");
  fields.background.value = current.background;
  fields.motion.value = current.motion;
  fields.bgm.value = current.bgm;
  $("presetList").value = current.preset;
  renderAssetManager();
  renderOutputManager();
}

function srcFor(path) {
  if (!path) return "";
  return `/asset/${path.split("/").map(encodeURIComponent).join("/")}`;
}

function absoluteSrc(path) {
  return path ? new URL(path, window.location.href).href : "";
}

function assetUrl(path) {
  return srcFor(path);
}

function shouldUseMotionProxy(path) {
  return /\.(mov|mkv)$/i.test(path || "");
}

function formatSeconds(value) {
  return Number(value || 0).toFixed(1);
}

function presetFromForm() {
  return {
    project_name: "product_001",
    background: fields.background.value,
    motion: fields.motion.value,
    bgm: fields.bgm.value,
    output: fields.output.value,
    width: Number(fields.width.value),
    height: Number(fields.height.value),
    fps: Number(fields.fps.value),
    duration: Number(fields.duration.value),
    model_x: Number(fields.model_x.value),
    model_y: Number(fields.model_y.value),
    model_scale: Number(fields.model_scale.value),
    motion_start: Number(fields.motion_start.value || 0),
    motion_end: fields.motion_end.value === "" ? "" : Number(fields.motion_end.value),
    bgm_start: Number(fields.bgm_start.value || 0),
    bgm_end: fields.bgm_end.value === "" ? "" : Number(fields.bgm_end.value),
    bgm_volume: Number(fields.bgm_volume.value),
    video_bitrate: fields.video_bitrate.value || "6000k",
  };
}

function applyPreset(preset) {
  clearExternalAsset("background");
  clearExternalAsset("motion");
  clearExternalAsset("bgm");
  Object.entries(fields).forEach(([key, field]) => {
    if (preset[key] !== undefined) {
      field.value = preset[key] || "";
    }
  });
  syncRangesFromNumbers();
  updatePreview();
}

function syncRangesFromNumbers() {
  $("modelXRange").max = fields.width.value || 1920;
  $("modelYRange").max = fields.height.value || 1080;
  pairedControls.forEach(([numberId, rangeId]) => {
    $(rangeId).value = fields[numberId].value;
  });
  $("seek").max = fields.duration.value || 30;
}

function updateBgmSeek() {
  const bgm = $("bgmPreview");
  const seek = $("bgmSeek");
  const label = $("bgmSeekLabel");
  const hasBgm = Boolean((fields.bgm.value || externalAssets.bgm) && bgm.src && Number.isFinite(bgm.duration));
  seek.disabled = !hasBgm;
  seek.max = hasBgm ? bgm.duration : 0;
  if (!bgmSeekDragging) {
    seek.value = hasBgm ? fields.bgm_start.value || 0 : 0;
  }
  label.textContent = `${formatSeconds(seek.value)} / ${formatSeconds(hasBgm ? bgm.duration : 0)}秒`;
}

function segmentTime(outputTime, start, end, fallbackDuration) {
  const sourceStart = Math.max(0, Number(start || 0));
  const sourceEnd = end === "" || end === undefined || end === null ? sourceStart + fallbackDuration : Number(end);
  const length = Math.max(0.1, sourceEnd - sourceStart);
  return sourceStart + (Math.max(0, outputTime) % length);
}

function syncMediaToTimeline(outputTime) {
  const preset = presetFromForm();
  const motion = $("motionPreview");
  const bgm = $("bgmPreview");
  const duration = Math.max(1, preset.duration || 30);
  const clampedTime = Math.min(duration, Math.max(0, outputTime));

  if (motion.src && Number.isFinite(motion.duration)) {
    const motionTime = segmentTime(clampedTime, preset.motion_start, preset.motion_end, duration);
    if (Math.abs((motion.currentTime || 0) - motionTime) > 0.25) {
      motion.currentTime = Math.min(Math.max(0, motionTime), Math.max(0, motion.duration - 0.05));
    }
  }

  if (bgm.src && Number.isFinite(bgm.duration)) {
    const bgmTime = segmentTime(clampedTime, preset.bgm_start, preset.bgm_end, duration);
    if (Math.abs((bgm.currentTime || 0) - bgmTime) > 0.25) {
      bgm.currentTime = Math.min(Math.max(0, bgmTime), Math.max(0, bgm.duration - 0.05));
    }
  }
  updateBgmSeek();
}

function updatePreview() {
  const preset = presetFromForm();
  const backgroundSrc = externalAssets.background || srcFor(preset.background);
  const needsMotionProxy = !externalAssets.motion && shouldUseMotionProxy(preset.motion);
  if (needsMotionProxy && motionProxySource !== preset.motion && motionProxyBuilding !== preset.motion) {
    useMotionPreviewProxy().catch((error) => log(error.message));
  }
  const motionSrc = motionProxySource === preset.motion
    ? $("motionPreview").src
    : needsMotionProxy
      ? ""
      : externalAssets.motion || srcFor(preset.motion);
  const bgmSrc = externalAssets.bgm || srcFor(preset.bgm);
  if ($("backgroundPreview").src !== absoluteSrc(backgroundSrc)) {
    $("backgroundPreview").src = backgroundSrc;
  }
  if ($("motionPreview").src !== absoluteSrc(motionSrc)) {
    $("motionPreview").src = motionSrc;
  }
  if ($("bgmPreview").src !== absoluteSrc(bgmSrc)) {
    $("bgmPreview").src = bgmSrc;
  }
  $("bgmPreview").volume = Math.min(1, Math.max(0, preset.bgm_volume || 0));
  $("seek").max = preset.duration || 30;
  $("timeLabel").textContent = `${formatSeconds($("seek").value)} / ${formatSeconds(preset.duration || 30)}秒`;
  updateBgmSeek();

  const preview = $("preview").getBoundingClientRect();
  const scaleX = preview.width / (preset.width || 1920);
  const scaleY = preview.height / (preset.height || 1080);
  const motion = $("motionPreview");
  const naturalWidth = motion.videoWidth || 640;
  const naturalHeight = motion.videoHeight || 360;
  const displayWidth = naturalWidth * (preset.model_scale || 1) * scaleX;
  const displayHeight = naturalHeight * (preset.model_scale || 1) * scaleY;
  motion.style.width = `${displayWidth}px`;
  motion.style.height = `${displayHeight}px`;
  motion.style.left = `${(preset.model_x || 0) * scaleX - displayWidth / 2}px`;
  motion.style.top = `${(preset.model_y || 0) * scaleY - displayHeight / 2}px`;
}

function setTimelineTime(time) {
  const duration = Number(fields.duration.value || 30);
  const nextTime = Math.min(duration, Math.max(0, time));
  $("seek").value = nextTime;
  timelineBaseTime = nextTime;
  timelineStartedAt = performance.now();
  syncMediaToTimeline(nextTime);
  updatePreview();
}

function startTimelineTimer() {
  if (timelineTimer) window.clearInterval(timelineTimer);
  timelineTimer = window.setInterval(() => {
    if (!isPlaying) return;
    const duration = Number(fields.duration.value || 30);
    const elapsed = (performance.now() - timelineStartedAt) / 1000;
    const nextTime = timelineBaseTime + elapsed;
    if (nextTime >= duration) {
      setTimelineTime(duration);
      pause();
      return;
    }
    $("seek").value = nextTime;
    syncMediaToTimeline(nextTime);
    updatePreview();
  }, 100);
}

async function upload(kind, input, saveNameInput) {
  if (!input.files.length) {
    throw new Error("登録する外部ファイルを先に選択してください。");
  }
  const body = new FormData();
  body.append("file", input.files[0]);
  body.append("save_name", saveNameInput.value || "");
  const data = await api(`/api/upload/${kind}`, { method: "POST", body });
  await refreshAssets();
  fields[kind === "background" ? "background" : kind === "motion" ? "motion" : "bgm"].value = data.path;
  clearExternalAsset(kind);
  saveNameInput.value = "";
  input.value = "";
  updatePreview();
  renderAssetManager();
  log(`素材を保存しました: ${data.path}`);
  return data.path;
}

function clearExternalAsset(kind) {
  if (externalAssets[kind]) {
    URL.revokeObjectURL(externalAssets[kind]);
    externalAssets[kind] = "";
  }
}

function previewExternalAsset(kind, input) {
  if (!input.files.length) return;
  clearExternalAsset(kind);
  externalAssets[kind] = URL.createObjectURL(input.files[0]);
  pause();
  if (kind === "background") {
    fields.background.value = "";
  } else if (kind === "motion") {
    motionProxySource = "";
    fields.motion.value = "";
    $("seek").value = 0;
  } else if (kind === "bgm") {
    fields.bgm.value = "";
    fields.bgm_start.value = 0;
    $("bgmSeek").value = 0;
  }
  updatePreview();
  log(`外部ファイルをプレビューに反映しました。すぐ登録する場合は「アプリ内ライブラリへ登録」、動画作成時は自動登録されます: ${input.files[0].name}`);
}

async function useMotionPreviewProxy() {
  if (!fields.motion.value || externalAssets.motion || motionProxySource === fields.motion.value) return;
  const source = fields.motion.value;
  motionProxyBuilding = source;
  log(`ブラウザで直接再生できない動画です。プレビュー用WebMを作成中: ${source}`);
  try {
    const data = await api("/api/preview-proxy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: source }),
    });
    if (fields.motion.value !== source) return;
    motionProxySource = source;
    $("motionPreview").src = srcFor(data.path);
    $("motionPreview").load();
    log(`プレビュー用WebMを使用します: ${data.path}`);
  } finally {
    if (motionProxyBuilding === source) motionProxyBuilding = "";
  }
}

async function registerExternalAssetsForRender() {
  const jobs = [
    ["background", $("backgroundUpload"), $("backgroundSaveName")],
    ["motion", $("motionUpload"), $("motionSaveName")],
    ["bgm", $("bgmUpload"), $("bgmSaveName")],
  ].filter(([kind, input]) => externalAssets[kind] && input.files.length);

  if (!jobs.length) return;

  log("外部ファイルをアプリ内ライブラリへ自動登録中...");
  for (const [kind, input, saveNameInput] of jobs) {
    await upload(kind, input, saveNameInput);
  }
}

async function loadPreset() {
  const path = $("presetList").value || $("presetPath").value;
  if (!path) throw new Error("読み込むプリセットを指定してください。");
  const data = await api("/api/preset/load", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  applyPreset(data.preset);
  $("presetPath").value = path;
  log(`読み込みました: ${path}`);
}

async function savePreset(path) {
  const data = await api("/api/preset/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, preset: presetFromForm() }),
  });
  await refreshAssets();
  $("presetPath").value = data.path;
  log(`保存しました: ${data.path}`);
}

async function render() {
  try {
    await registerExternalAssetsForRender();
    log("レンダージョブ開始中");
    $("render").disabled = true;
    $("cancelRender").disabled = false;
    const data = await api("/api/render/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preset: presetFromForm() }),
    });
    currentRenderJobId = data.job_id;
    pollRenderJob().catch((error) => log(error.message));
    if (renderPollTimer) window.clearInterval(renderPollTimer);
    renderPollTimer = window.setInterval(() => {
      pollRenderJob().catch((error) => {
        log(error.message);
        finishRenderPolling();
      });
    }, 1000);
  } catch (error) {
    finishRenderPolling();
    throw error;
  }
}

async function pollRenderJob() {
  if (!currentRenderJobId) return;
  const data = await api(`/api/render/status/${currentRenderJobId}`);
  const lines = [...(data.logs || [])];
  if (data.output) lines.push(`出力先: ${data.output}`);
  if (data.error) lines.push(`エラー内容: ${data.error}`);
  log(lines);
  if (["done", "error", "canceled"].includes(data.status)) {
    finishRenderPolling();
    await refreshAssets();
    if (data.status === "done") {
      openTab("outputsPanel");
      if (data.output) previewOutput(data.output);
    }
  }
}

function finishRenderPolling() {
  if (renderPollTimer) window.clearInterval(renderPollTimer);
  renderPollTimer = null;
  currentRenderJobId = "";
  $("render").disabled = false;
  $("cancelRender").disabled = true;
}

async function cancelRender() {
  if (!currentRenderJobId) return;
  await api(`/api/render/cancel/${currentRenderJobId}`, { method: "POST" });
  log("キャンセル要求を送信しました。");
}

async function deleteAsset(path) {
  if (!window.confirm(`削除しますか？\n${path}`)) return;
  await api("/api/asset/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (fields.background.value === path) fields.background.value = "";
  if (fields.motion.value === path) fields.motion.value = "";
  if (fields.bgm.value === path) fields.bgm.value = "";
  if ($("outputPreview").dataset.path === path) {
    $("outputPreview").removeAttribute("src");
    $("outputPreview").dataset.path = "";
  }
  await refreshAssets();
  updatePreview();
  log(`削除しました: ${path}`);
}

async function renameAsset(path) {
  const currentName = path.split("/").pop() || path;
  const newName = window.prompt("新しい素材名", currentName);
  if (!newName) return;
  const data = await api("/api/asset/rename", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, new_name: newName }),
  });
  if (fields.background.value === data.old_path) fields.background.value = data.path;
  if (fields.motion.value === data.old_path) fields.motion.value = data.path;
  if (fields.bgm.value === data.old_path) fields.bgm.value = data.path;
  if ($("outputPreview").dataset.path === data.old_path) {
    $("outputPreview").src = assetUrl(data.path);
    $("outputPreview").dataset.path = data.path;
  }
  await refreshAssets();
  updatePreview();
  log(`名前を変更しました: ${data.old_path} -> ${data.path}`);
}

function useAsset(kind, path) {
  clearExternalAsset(kind);
  fields[kind].value = path;
  openTab("editorPanel");
  updatePreview();
  log(`編集に使用します: ${path}`);
}

function renderFileList(containerId, items, actionsFor) {
  const container = $(containerId);
  container.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "file-item";
    empty.textContent = "登録ファイルなし";
    container.appendChild(empty);
    return;
  }
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "file-item";

    const name = document.createElement("div");
    name.className = "file-name";
    name.textContent = item.name;
    name.title = item.path;
    row.appendChild(name);

    const actions = document.createElement("div");
    actions.className = "file-actions";
    actionsFor(item).forEach((action) => actions.appendChild(action));
    row.appendChild(actions);
    container.appendChild(row);
  });
}

function button(label, onClick) {
  const node = document.createElement("button");
  node.type = "button";
  node.textContent = label;
  node.addEventListener("click", onClick);
  return node;
}

function link(label, href, download = false) {
  const node = document.createElement("a");
  node.textContent = label;
  node.href = href;
  node.target = "_blank";
  if (download) node.download = "";
  return node;
}

function renderAssetManager() {
  renderFileList("assetBackgrounds", latestAssets.backgrounds || [], (item) => [
    button("編集に使う", () => useAsset("background", item.path)),
    button("名前変更", () => renameAsset(item.path).catch((error) => log(error.message))),
    link("開く", assetUrl(item.path)),
    button("削除", () => deleteAsset(item.path).catch((error) => log(error.message))),
  ]);
  renderFileList("assetMotions", latestAssets.motions || [], (item) => [
    button("編集に使う", () => useAsset("motion", item.path)),
    button("名前変更", () => renameAsset(item.path).catch((error) => log(error.message))),
    link("開く", assetUrl(item.path)),
    button("削除", () => deleteAsset(item.path).catch((error) => log(error.message))),
  ]);
  renderFileList("assetBgms", latestAssets.bgms || [], (item) => [
    button("編集に使う", () => useAsset("bgm", item.path)),
    button("名前変更", () => renameAsset(item.path).catch((error) => log(error.message))),
    link("開く", assetUrl(item.path)),
    button("削除", () => deleteAsset(item.path).catch((error) => log(error.message))),
  ]);
}

function previewOutput(path) {
  const preview = $("outputPreview");
  preview.src = assetUrl(path);
  preview.dataset.path = path;
  preview.load();
  log(`出力動画を確認します: ${path}`);
}

function renderOutputManager() {
  renderFileList("outputList", latestAssets.outputs || [], (item) => [
    button("再生確認", () => previewOutput(item.path)),
    button("名前変更", () => renameAsset(item.path).catch((error) => log(error.message))),
    link("開く", assetUrl(item.path)),
    link("ダウンロード", assetUrl(item.path), true),
    button("削除", () => deleteAsset(item.path).catch((error) => log(error.message))),
  ]);
}

function play() {
  isPlaying = true;
  timelineBaseTime = Number($("seek").value || 0);
  timelineStartedAt = performance.now();
  syncMediaToTimeline(timelineBaseTime);
  $("motionPreview").play();
  if (fields.bgm.value || externalAssets.bgm) $("bgmPreview").play();
  startTimelineTimer();
}

function pause() {
  isPlaying = false;
  $("motionPreview").pause();
  $("bgmPreview").pause();
}

function stop() {
  pause();
  $("seek").value = 0;
  setTimelineTime(0);
}

function bindEvents() {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => openTab(button.dataset.tab));
  });
  Object.values(fields).forEach((field) => field.addEventListener("input", () => {
    syncRangesFromNumbers();
    updatePreview();
  }));
  fields.background.addEventListener("change", () => {
    clearExternalAsset("background");
    updatePreview();
  });
  fields.motion.addEventListener("change", () => {
    clearExternalAsset("motion");
    motionProxySource = "";
    motionProxyBuilding = "";
    updatePreview();
  });
  fields.bgm.addEventListener("change", () => {
    clearExternalAsset("bgm");
    updatePreview();
  });
  pairedControls.forEach(([numberId, rangeId]) => {
    $(rangeId).addEventListener("input", () => {
      fields[numberId].value = $(rangeId).value;
      updatePreview();
    });
  });
  $("motionPreview").addEventListener("loadedmetadata", updatePreview);
  $("bgmPreview").addEventListener("loadedmetadata", () => {
    syncMediaToTimeline(Number($("seek").value || 0));
    updateBgmSeek();
  });
  $("bgmPreview").addEventListener("timeupdate", updateBgmSeek);
  $("motionPreview").addEventListener("error", () => {
    if (fields.motion.value) {
      useMotionPreviewProxy().catch((error) => log(error.message));
    }
  });
  $("backgroundPreview").addEventListener("error", () => {
    if (fields.background.value) {
      log(`背景画像をプレビュー表示できません: ${fields.background.value}`);
    }
  });
  $("playButton").addEventListener("click", play);
  $("pauseButton").addEventListener("click", pause);
  $("resetButton").addEventListener("click", stop);
  $("seek").addEventListener("input", () => {
    setTimelineTime(Number($("seek").value));
  });
  $("bgmSeek").addEventListener("pointerdown", () => {
    bgmSeekDragging = true;
    pause();
  });
  $("bgmSeek").addEventListener("pointerup", () => {
    bgmSeekDragging = false;
    updateBgmSeek();
  });
  $("bgmSeek").addEventListener("input", () => {
    const bgm = $("bgmPreview");
    if (!Number.isFinite(bgm.duration)) return;
    const nextTime = Math.min(Math.max(0, Number($("bgmSeek").value || 0)), bgm.duration);
    fields.bgm_start.value = nextTime.toFixed(1);
    bgm.currentTime = nextTime;
    $("seek").value = 0;
    timelineBaseTime = 0;
    timelineStartedAt = performance.now();
    $("bgmSeekLabel").textContent = `${formatSeconds(nextTime)} / ${formatSeconds(bgm.duration)}秒`;
    updatePreview();
  });
  $("backgroundUpload").addEventListener("change", (event) => previewExternalAsset("background", event.target));
  $("motionUpload").addEventListener("change", (event) => previewExternalAsset("motion", event.target));
  $("bgmUpload").addEventListener("change", (event) => previewExternalAsset("bgm", event.target));
  $("saveBackgroundAsset").addEventListener("click", () => {
    upload("background", $("backgroundUpload"), $("backgroundSaveName")).catch((error) => log(error.message));
  });
  $("saveMotionAsset").addEventListener("click", () => {
    upload("motion", $("motionUpload"), $("motionSaveName")).catch((error) => log(error.message));
  });
  $("saveBgmAsset").addEventListener("click", () => {
    upload("bgm", $("bgmUpload"), $("bgmSaveName")).catch((error) => log(error.message));
  });
  $("loadPreset").addEventListener("click", () => loadPreset().catch((error) => log(error.message)));
  $("savePreset").addEventListener("click", () => savePreset($("presetPath").value).catch((error) => log(error.message)));
  $("savePresetAs").addEventListener("click", () => {
    const path = window.prompt("保存先JSONパス", $("presetPath").value);
    if (path) savePreset(path).catch((error) => log(error.message));
  });
  $("render").addEventListener("click", () => render().catch((error) => log(error.message)));
  $("cancelRender").addEventListener("click", () => cancelRender().catch((error) => log(error.message)));
  $("refreshAssets").addEventListener("click", () => refreshAssets().catch((error) => log(error.message)));
  $("refreshOutputs").addEventListener("click", () => refreshAssets().catch((error) => log(error.message)));
  window.addEventListener("resize", updatePreview);
}

bindEvents();
refreshAssets()
  .then(() => {
    syncRangesFromNumbers();
    updatePreview();
  })
  .catch((error) => log(error.message));
