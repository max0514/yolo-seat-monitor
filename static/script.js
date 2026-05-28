/* =========================================================
   YOLO::DETECT — frontend logic
   ========================================================= */

const $ = (id) => document.getElementById(id);
const FOCUS_CLASSES = new Set(["person", "chair", "couch", "dining table", "laptop", "book", "backpack"]);

// ----- state -----
const state = {
  currentFile: null,
  currentDataURL: null,    // for webcam frame
  busy: false,
  liveTimer: null,
  webcamStream: null,
};

// ----- clock -----
function tickClock() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  $("clock").textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
setInterval(tickClock, 1000);
tickClock();

// ----- status indicator -----
function setStatus(text, kind = "ok") {
  $("status-text").textContent = text;
  const dot = $("status-dot");
  dot.classList.remove("busy", "error");
  if (kind === "busy") dot.classList.add("busy");
  if (kind === "error") dot.classList.add("error");
}

// ----- log -----
function log(msg, cls = "") {
  const el = document.createElement("div");
  el.className = "log-line " + cls;
  const t = new Date();
  const ts = `${String(t.getHours()).padStart(2,"0")}:${String(t.getMinutes()).padStart(2,"0")}:${String(t.getSeconds()).padStart(2,"0")}`;
  el.innerHTML = `<span class="lc">[${ts}]</span> ${msg}`;
  const logEl = $("log");
  // clear placeholder on first real entry
  if (logEl.children.length === 1 && logEl.children[0].textContent.includes("awaiting")) {
    logEl.innerHTML = "";
  }
  logEl.insertBefore(el, logEl.firstChild);
  while (logEl.children.length > 80) logEl.removeChild(logEl.lastChild);
}

// ----- tabs -----
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
  });
});

// ----- sliders -----
$("conf-slider").addEventListener("input", e => $("conf-val").textContent = parseFloat(e.target.value).toFixed(2));
$("iou-slider").addEventListener("input", e => $("iou-val").textContent = parseFloat(e.target.value).toFixed(2));

// ----- dropzone -----
const dz = $("dropzone");
const fileInput = $("file-input");

dz.addEventListener("click", () => fileInput.click());
dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("drag-over"); });
dz.addEventListener("dragleave", () => dz.classList.remove("drag-over"));
dz.addEventListener("drop", e => {
  e.preventDefault();
  dz.classList.remove("drag-over");
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", e => {
  if (e.target.files.length) handleFile(e.target.files[0]);
});

function handleFile(file) {
  if (!file.type.startsWith("image/")) {
    log(`<span class="lp">!</span> 檔案不是圖片`, "new");
    return;
  }
  state.currentFile = file;
  $("btn-detect").disabled = false;
  log(`loaded → <span class="ln">${file.name}</span> (${(file.size/1024).toFixed(1)} KB)`, "new");

  // 預覽到 canvas
  const reader = new FileReader();
  reader.onload = e => {
    const img = $("result-img");
    img.src = e.target.result;
    $("canvas-frame").classList.add("has-image");
    $("output-meta").textContent = "PREVIEW · 等待推論";
  };
  reader.readAsDataURL(file);
}

// ----- detect button -----
$("btn-detect").addEventListener("click", async () => {
  if (!state.currentFile || state.busy) return;
  const form = new FormData();
  form.append("file", state.currentFile);
  form.append("model", $("model-select").value);
  form.append("conf", $("conf-slider").value);
  form.append("iou", $("iou-slider").value);
  await callDetect(form);
});

// ----- 共用推論呼叫 -----
async function callDetect(body, opts = {}) {
  state.busy = true;
  setStatus("INFERRING", "busy");
  $("btn-detect").disabled = true;

  try {
    const init = { method: "POST" };
    if (body instanceof FormData) {
      init.body = body;
    } else {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(body);
    }

    const res = await fetch("/api/detect", init);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "推論失敗");
    renderResult(data, opts);
    if (!opts.silent) {
      log(`✓ ${data.num_detections} objects · ${data.elapsed_ms}ms · model=<span class="ln">${data.model}</span>`, "new");
    }
    setStatus("READY");
  } catch (err) {
    log(`<span class="lp">!</span> ${err.message}`, "new");
    setStatus("ERROR", "error");
  } finally {
    state.busy = false;
    $("btn-detect").disabled = !state.currentFile;
  }
}

// ----- 渲染結果 -----
function renderResult(data, opts = {}) {
  // 影像
  const img = $("result-img");
  img.src = data.annotated_image;
  $("canvas-frame").classList.add("has-image");
  $("btn-download").disabled = false;
  $("btn-download").onclick = () => {
    const a = document.createElement("a");
    a.href = data.annotated_image;
    a.download = `yolo_${data.result_id}.jpg`;
    a.click();
  };

  // meta
  const [h, w] = data.image_shape;
  $("output-meta").textContent = `${w}×${h} · ${data.model} · ${data.elapsed_ms}ms`;
  $("footer-info").textContent = `id:${data.result_id} · ${data.num_detections} detections`;

  // KPIs：依是否有 ROI 切換意義
  const lib = data.library;
  const seats = data.seats;
  $("kpi-persons").textContent = lib.persons;

  if (seats.defined) {
    // ROI 模式
    $("kpi-label-2").textContent = "TOTAL SEATS";
    $("kpi-2").textContent = seats.total;
    $("kpi-label-3").textContent = "VACANT";
    $("kpi-3").textContent = seats.vacant;
    $("kpi-label-4").textContent = "OCC RATE";
    $("kpi-4").textContent = seats.occupancy_rate !== null ? (seats.occupancy_rate * 100).toFixed(0) + "%" : "—";

    $("no-roi-hint").style.display = "none";
    $("seat-map-wrap").style.display = "block";
    $("seat-map-info").textContent =
      `${seats.occupied}/${seats.total} 佔用 · 閾值 ${seats.threshold}`;
    renderSeatGrid(seats.status);
  } else {
    // 沒 ROI：回退到 chair-based 粗估
    $("kpi-label-2").textContent = "CHAIRS";
    $("kpi-2").textContent = lib.chairs_or_couches;
    $("kpi-label-3").textContent = "VACANT (est.)";
    $("kpi-3").textContent = lib.estimated_vacant_seats === null ? "—" : lib.estimated_vacant_seats;
    $("kpi-label-4").textContent = "TOTAL DETECT";
    $("kpi-4").textContent = data.num_detections;

    $("seat-map-wrap").style.display = "none";
    $("no-roi-hint").style.display = "flex";
  }

  // 類別分布
  const listEl = $("class-list");
  const stats = data.stats;
  const entries = Object.entries(stats).sort((a,b) => b[1]-a[1]);
  if (entries.length === 0) {
    listEl.innerHTML = '<div class="placeholder">— no objects detected —</div>';
  } else {
    const max = entries[0][1];
    listEl.innerHTML = entries.map(([name, count]) => {
      const pct = (count / max * 100).toFixed(0);
      const focus = FOCUS_CLASSES.has(name) ? "focus" : "";
      return `
        <div class="class-row ${focus}">
          <span class="class-name">${name}</span>
          <span class="class-count">${count}</span>
          <span class="class-bar"><span class="class-bar-fill" style="transform:scaleX(${pct/100})"></span></span>
        </div>`;
    }).join("");
  }

  // 偵測明細 log
  if (!opts.silent) {
    const top = data.detections.slice(0, 5);
    top.forEach(d => {
      log(`  → <span class="ln">${d.class}</span> conf=<span class="lp">${(d.confidence*100).toFixed(1)}%</span>`);
    });
    if (seats.defined) {
      log(`  ⟫ seats: <span class="ln">${seats.occupied}/${seats.total}</span> 佔用`, "new");
    }
  }
}

function renderSeatGrid(statusList) {
  const grid = $("seat-grid");
  if (!statusList || statusList.length === 0) {
    grid.innerHTML = '<div class="placeholder">— no seats —</div>';
    return;
  }
  grid.innerHTML = statusList.map(s => {
    const cls = s.occupied ? "occupied" : "vacant";
    const tag = s.occupied ? "OCC" : "VAC";
    const cov = (s.coverage * 100).toFixed(0);
    return `
      <div class="seat-cell ${cls}" title="覆蓋率 ${cov}%">
        <span class="seat-id">${escapeHtml(s.label || s.id)}</span>
        <span class="seat-tag">${tag}</span>
        <span class="seat-cov">${cov}%</span>
      </div>`;
  }).join("");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

// =========================================================
// Webcam
// =========================================================
const video = $("webcam");
const overlay = $("webcam-overlay");

$("btn-cam-start").addEventListener("click", async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
    state.webcamStream = stream;
    video.srcObject = stream;
    overlay.classList.add("hidden");
    $("btn-cam-start").disabled = true;
    $("btn-cam-stop").disabled = false;
    log(`📷 webcam started`, "new");
  } catch (err) {
    log(`<span class="lp">!</span> 無法存取攝影機: ${err.message}`, "new");
  }
});

$("btn-cam-stop").addEventListener("click", () => {
  stopLiveLoop();
  if (state.webcamStream) {
    state.webcamStream.getTracks().forEach(t => t.stop());
    state.webcamStream = null;
  }
  video.srcObject = null;
  overlay.classList.remove("hidden");
  $("btn-cam-start").disabled = false;
  $("btn-cam-stop").disabled = true;
  $("live-mode").checked = false;
  log(`📷 webcam stopped`, "new");
});

// LIVE 模式
$("live-mode").addEventListener("change", e => {
  if (e.target.checked) {
    if (!state.webcamStream) {
      log(`<span class="lp">!</span> 請先啟動 webcam`, "new");
      e.target.checked = false;
      return;
    }
    startLiveLoop();
  } else {
    stopLiveLoop();
  }
});

function startLiveLoop() {
  if (state.liveTimer) return;
  log(`▶ LIVE detection ON (1.5s interval)`, "new");
  const tick = async () => {
    if (!state.webcamStream || state.busy) return;
    const dataURL = captureFrame();
    if (!dataURL) return;
    await callDetect(
      {
        image: dataURL,
        model: $("model-select").value,
        conf: parseFloat($("conf-slider").value),
        iou: parseFloat($("iou-slider").value),
      },
      { silent: true }
    );
  };
  tick();
  state.liveTimer = setInterval(tick, 1500);
}

function stopLiveLoop() {
  if (state.liveTimer) {
    clearInterval(state.liveTimer);
    state.liveTimer = null;
    log(`■ LIVE detection OFF`, "new");
  }
}

function captureFrame() {
  if (!video.videoWidth) return null;
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0);
  return canvas.toDataURL("image/jpeg", 0.85);
}

// =========================================================
// ESP32-CAM
// =========================================================
const esp32State = { liveTimer: null };

$("esp32-interval").addEventListener("input", e => {
  $("esp32-interval-val").textContent = parseFloat(e.target.value).toFixed(1) + "s";
});

async function esp32Detect(opts = {}) {
  const url = $("esp32-url").value.trim();
  if (!url) { log(`<span class="lp">!</span> ESP32 URL is empty`, "new"); return; }

  state.busy = true;
  setStatus("CAPTURING", "busy");

  try {
    const res = await fetch("/api/capture-detect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        model: $("model-select").value,
        conf: parseFloat($("conf-slider").value),
        iou: parseFloat($("iou-slider").value),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Capture failed");

    // show preview
    const preview = $("esp32-preview");
    preview.src = data.annotated_image;
    preview.style.display = "block";
    $("esp32-overlay").classList.add("hidden");

    renderResult(data, opts);
    if (!opts.silent) {
      log(`📡 ESP32 → ${data.num_detections} objects · ${data.elapsed_ms}ms`, "new");
    }
    setStatus("READY");
  } catch (err) {
    log(`<span class="lp">!</span> ESP32: ${err.message}`, "new");
    setStatus("ERROR", "error");
  } finally {
    state.busy = false;
  }
}

$("btn-esp32-snap").addEventListener("click", () => {
  if (!state.busy) esp32Detect();
});

$("btn-esp32-start").addEventListener("click", () => {
  if (esp32State.liveTimer) return;
  const intervalMs = parseFloat($("esp32-interval").value) * 1000;
  log(`▶ ESP32 LIVE ON (${(intervalMs/1000).toFixed(1)}s interval)`, "new");

  const tick = async () => {
    if (state.busy) return;
    await esp32Detect({ silent: true });
  };
  tick();
  esp32State.liveTimer = setInterval(tick, intervalMs);

  $("btn-esp32-start").disabled = true;
  $("btn-esp32-stop").disabled = false;
});

$("btn-esp32-stop").addEventListener("click", () => {
  if (esp32State.liveTimer) {
    clearInterval(esp32State.liveTimer);
    esp32State.liveTimer = null;
    log(`■ ESP32 LIVE OFF`, "new");
  }
  $("btn-esp32-start").disabled = false;
  $("btn-esp32-stop").disabled = true;
});

// ----- init -----
log("system initialized", "new");
log("YOLOv8 inference terminal ready");
