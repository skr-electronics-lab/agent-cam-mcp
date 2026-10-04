// Agent Cam MCP Tool - Frontend Dashboard Logic (Vanilla JS, Zero Dependencies)

(function () {
  let authToken = "";
  let currentCameraId = "0";
  let activeView = "live";
  let drawingMode = "region"; // "region" or "mask"
  let isDrawing = false;
  let startX = 0, startY = 0;
  let currentRect = null;
  let definedRegions = {};
  let privacyMasks = [];

  // DOM Elements
  const mjpegStream = document.getElementById("mjpeg-stream");
  const canvas = document.getElementById("region-canvas");
  const ctx = canvas.getContext("2d");
  const cameraSelect = document.getElementById("camera-select");
  const pauseBtn = document.getElementById("pause-toggle-btn");
  const pauseLabel = document.getElementById("pause-btn-label");
  const daemonDot = document.getElementById("daemon-dot");
  const activeAgentsCount = document.getElementById("active-agents-count");
  const bufferMetric = document.getElementById("buffer-metric");
  const sidebarUptime = document.getElementById("sidebar-uptime");
  const themeToggleBtn = document.getElementById("theme-toggle-btn");

  // Fetch Auth Token
  async function fetchAuthToken() {
    try {
      const res = await fetch("/api/token");
      if (res.ok) {
        const data = await res.json();
        authToken = data.token;
      }
    } catch (err) {
      console.error("Failed to fetch auth token:", err);
    }
  }

  // API Helper
  async function api(path, options = {}) {
    options.headers = options.headers || {};
    if (authToken) {
      options.headers["x-auth-token"] = authToken;
    }
    const res = await fetch(path, options);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`API Error ${res.status}: ${text}`);
    }
    return await res.json();
  }

  // Navigation
  function setupNavigation() {
    document.querySelectorAll(".nav-item").forEach(item => {
      item.addEventListener("click", () => {
        document.querySelectorAll(".nav-item").forEach(i => i.classList.remove("active"));
        document.querySelectorAll(".view-panel").forEach(p => p.classList.remove("active"));

        item.classList.add("active");
        activeView = item.dataset.view;
        const panel = document.getElementById(`view-${activeView}`);
        if (panel) panel.classList.add("active");

        if (activeView === "cameras") loadCamerasTable();
        if (activeView === "activity") loadActivityTable();
        if (activeView === "baselines") loadBaselinesTable();
        if (activeView === "adapters") loadAdaptersView();
        if (activeView === "settings") loadSettings();
      });
    });
  }

  // Theme Toggle
  themeToggleBtn.addEventListener("click", () => {
    const html = document.documentElement;
    const current = html.getAttribute("data-theme");
    const next = current === "dark" ? "light" : "dark";
    html.setAttribute("data-theme", next);
  });

  // Pause Access Toggle
  pauseBtn.addEventListener("click", async () => {
    try {
      const res = await api("/api/pause", { method: "POST", body: JSON.stringify({}) });
      updatePauseUI(res.paused);
    } catch (err) {
      alert("Failed to toggle pause: " + err.message);
    }
  });

  function updatePauseUI(isPaused) {
    if (isPaused) {
      pauseBtn.classList.add("active");
      pauseLabel.textContent = "Agent Access PAUSED";
    } else {
      pauseBtn.classList.remove("active");
      pauseLabel.textContent = "Pause Agent Access";
    }
  }

  // WebSocket for Live Heartbeats
  function setupWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

    ws.onopen = () => {
      daemonDot.className = "status-dot";
      document.getElementById("daemon-text").textContent = "Connected";
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "heartbeat") {
          activeAgentsCount.textContent = data.active_agents;
          bufferMetric.textContent = `${data.buffer_fill_s}s (${data.buffer_ram_mb}MB)`;
          sidebarUptime.textContent = `Uptime: ${Math.round(data.uptime)}s`;
          updatePauseUI(data.paused);
          updateCameraSelect(data.cameras);
        }
      } catch (e) {}
    };

    ws.onclose = () => {
      daemonDot.className = "status-dot err";
      document.getElementById("daemon-text").textContent = "Disconnected";
      setTimeout(setupWebSocket, 2000);
    };
  }

  function updateCameraSelect(cameras) {
    if (!cameras) return;
    const currentVal = cameraSelect.value;
    cameraSelect.innerHTML = "";
    cameras.forEach(c => {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = `${c.name} (${c.id}) [${c.state}]`;
      cameraSelect.appendChild(opt);
    });
    if (currentVal && Array.from(cameraSelect.options).some(o => o.value === currentVal)) {
      cameraSelect.value = currentVal;
    } else if (cameras.length > 0) {
      cameraSelect.value = cameras[0].id;
      changeCamera(cameras[0].id);
    }
  }

  cameraSelect.addEventListener("change", (e) => {
    changeCamera(e.target.value);
  });

  function changeCamera(camId) {
    currentCameraId = camId;
    mjpegStream.src = `/stream/${camId}?t=${Date.now()}`;
    loadRegions();
  }

  // Canvas Drawing & Overlays
  function resizeCanvas() {
    const rect = mjpegStream.getBoundingClientRect();
    canvas.width = rect.width;
    canvas.height = rect.height;
    renderCanvas();
  }

  window.addEventListener("resize", resizeCanvas);
  mjpegStream.addEventListener("load", resizeCanvas);

  function renderCanvas() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Draw Privacy Masks (black rectangles)
    privacyMasks.forEach(mask => {
      const x = mask.x * canvas.width;
      const y = mask.y * canvas.height;
      const w = mask.w * canvas.width;
      const h = mask.h * canvas.height;
      ctx.fillStyle = "rgba(0, 0, 0, 0.85)";
      ctx.fillRect(x, y, w, h);
      ctx.strokeStyle = "#E5484D";
      ctx.lineWidth = 1;
      ctx.strokeRect(x, y, w, h);
    });

    // Draw Defined Regions
    Object.values(definedRegions).forEach(reg => {
      const x = reg.x * canvas.width;
      const y = reg.y * canvas.height;
      const w = reg.w * canvas.width;
      const h = reg.h * canvas.height;

      ctx.strokeStyle = "#3B82F6";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(x, y, w, h);

      ctx.fillStyle = "rgba(59, 130, 246, 0.9)";
      ctx.font = "11px " + getComputedStyle(document.body).fontFamily;
      ctx.fillText(` ${reg.name} `, x + 2, y + 12);
    });

    // Draw Current Interactive Drag Box
    if (isDrawing && currentRect) {
      ctx.strokeStyle = drawingMode === "mask" ? "#E5484D" : "#00D7FF";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(currentRect.x, currentRect.y, currentRect.w, currentRect.h);
      ctx.setLineDash([]);
    }
  }

  canvas.addEventListener("mousedown", (e) => {
    const rect = canvas.getBoundingClientRect();
    startX = e.clientX - rect.x;
    startY = e.clientY - rect.y;
    isDrawing = true;
    currentRect = { x: startX, y: startY, w: 0, h: 0 };
  });

  canvas.addEventListener("mousemove", (e) => {
    if (!isDrawing) return;
    const rect = canvas.getBoundingClientRect();
    const currX = e.clientX - rect.x;
    const currY = e.clientY - rect.y;
    currentRect.x = Math.min(startX, currX);
    currentRect.y = Math.min(startY, currY);
    currentRect.w = Math.abs(currX - startX);
    currentRect.h = Math.abs(currY - startY);
    renderCanvas();
  });

  canvas.addEventListener("mouseup", async () => {
    if (!isDrawing || !currentRect || currentRect.w < 10 || currentRect.h < 10) {
      isDrawing = false;
      currentRect = null;
      renderCanvas();
      return;
    }
    isDrawing = false;

    // Convert to normalized coordinates
    const normX = Number((currentRect.x / canvas.width).toFixed(4));
    const normY = Number((currentRect.y / canvas.height).toFixed(4));
    const normW = Number((currentRect.w / canvas.width).toFixed(4));
    const normH = Number((currentRect.h / canvas.height).toFixed(4));

    if (drawingMode === "region") {
      const name = prompt("Enter a unique name for this region (e.g. status_led, pcb_core):");
      if (name && name.trim()) {
        await saveRegion(name.trim(), normX, normY, normW, normH);
      }
    } else {
      const confirmMask = confirm("Apply permanent black privacy mask to this area?");
      if (confirmMask) {
        await savePrivacyMask(normX, normY, normW, normH);
      }
    }
    currentRect = null;
    renderCanvas();
  });

  document.getElementById("mode-draw-region").addEventListener("click", () => {
    drawingMode = "region";
    document.getElementById("canvas-status-hint").textContent = "Drawing Mode: REGION. Click and drag on camera preview.";
  });

  document.getElementById("mode-draw-mask").addEventListener("click", () => {
    drawingMode = "mask";
    document.getElementById("canvas-status-hint").textContent = "Drawing Mode: PRIVACY MASK. Click and drag to redact area.";
  });

  async function saveRegion(name, x, y, w, h) {
    try {
      const body = { name, x, y, w, h, camera: currentCameraId, units: "normalized" };
      await api("/api/regions", { method: "POST", body: JSON.stringify(body) });
      await loadRegions();
    } catch (e) {
      alert("Error saving region: " + e.message);
    }
  }

  async function savePrivacyMask(x, y, w, h) {
    try {
      const id = "mask-" + Date.now();
      const body = { id, x, y, w, h, camera: currentCameraId, units: "normalized" };
      await api("/api/privacy_masks", { method: "POST", body: JSON.stringify(body) });
      await loadPrivacyMasks();
    } catch (e) {
      alert("Error saving mask: " + e.message);
    }
  }

  async function loadRegions() {
    try {
      const data = await api("/api/regions");
      definedRegions = data.regions || {};

      const list = document.getElementById("defined-regions-list");
      const inspectorSelect = document.getElementById("inspector-region-select");
      list.innerHTML = "";
      inspectorSelect.innerHTML = '<option value="">[Whole Frame]</option>';

      Object.values(definedRegions).forEach(r => {
        const li = document.createElement("li");
        li.style.display = "flex";
        li.style.justifyContent = "space-between";
        li.style.alignItems = "center";
        li.innerHTML = `<span>[${r.name}]</span> <button class="btn btn-sm btn-danger" data-name="${r.name}">Del</button>`;
        li.querySelector("button").addEventListener("click", async () => {
          await api(`/api/regions/${r.name}`, { method: "DELETE" });
          loadRegions();
        });
        list.appendChild(li);

        const opt = document.createElement("option");
        opt.value = r.name;
        opt.textContent = r.name;
        inspectorSelect.appendChild(opt);
      });
      renderCanvas();
    } catch (e) {}
  }

  async function loadPrivacyMasks() {
    try {
      const data = await api("/api/privacy_masks");
      privacyMasks = data.masks || [];
      const list = document.getElementById("privacy-masks-list");
      list.innerHTML = "";
      privacyMasks.forEach(m => {
        const li = document.createElement("li");
        li.style.display = "flex";
        li.style.justifyContent = "space-between";
        li.style.alignItems = "center";
        li.innerHTML = `<span>Mask (${Math.round(m.w*100)}%x${Math.round(m.h*100)}%)</span> <button class="btn btn-sm btn-danger" data-id="${m.id}">Del</button>`;
        li.querySelector("button").addEventListener("click", async () => {
          await api(`/api/privacy_masks/${m.id}`, { method: "DELETE" });
          loadPrivacyMasks();
        });
        list.appendChild(li);
      });
      renderCanvas();
    } catch (e) {}
  }

  // Live Measure Button
  document.getElementById("btn-run-measure").addEventListener("click", async () => {
    const regName = document.getElementById("inspector-region-select").value;
    try {
      const res = await api("/api/mcp/call_tool", {
        method: "POST",
        body: JSON.stringify({
          name: "measure",
          arguments: { camera: currentCameraId, region: regName || undefined }
        })
      });
      if (res.content && res.content[0]) {
        const data = JSON.parse(res.content[0].text);
        document.getElementById("m-brightness").textContent = data.mean_brightness;
        document.getElementById("m-contrast").textContent = data.contrast;
        document.getElementById("m-lit-pct").textContent = data.lit_pixel_percentage + "%";
        document.getElementById("m-edges").textContent = data.edge_density + "%";
        document.getElementById("m-sharpness").textContent = data.sharpness;
        document.getElementById("m-motion").textContent = data.motion_level;
      }
    } catch (e) {
      alert("Measurement failed: " + e.message);
    }
  });

  // Save Baseline
  document.getElementById("btn-save-as-baseline").addEventListener("click", async () => {
    const name = prompt("Enter a name for this baseline (e.g. initial_rig_state):");
    if (!name || !name.trim()) return;
    try {
      await api("/api/mcp/call_tool", {
        method: "POST",
        body: JSON.stringify({
          name: "save_baseline",
          arguments: { name: name.trim(), camera: currentCameraId }
        })
      });
      alert(`Baseline '${name}' saved successfully.`);
    } catch (e) {
      alert("Failed saving baseline: " + e.message);
    }
  });

  // Cameras View
  async function loadCamerasTable() {
    try {
      const data = await api("/api/cameras");
      const tbody = document.getElementById("cameras-table-body");
      tbody.innerHTML = "";
      data.cameras.forEach(c => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td class="num-cell">${c.id}</td>
          <td><strong>${c.name}</strong></td>
          <td>${c.source_type}</td>
          <td class="num-cell">${c.width}x${c.height}</td>
          <td class="num-cell">${c.fps}</td>
          <td><span class="status-dot ${c.state === 'streaming' ? '' : 'warn'}"></span> ${c.state}</td>
          <td>${c.last_error ? `${c.last_error} - ${c.last_error_fix}` : 'None'}</td>
        `;
        tbody.appendChild(tr);
      });
    } catch (e) {}
  }

  document.getElementById("btn-refresh-cameras").addEventListener("click", () => loadCamerasTable());
  document.getElementById("add-ip-cam-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("ip-cam-name").value;
    const url = document.getElementById("ip-cam-url").value;
    try {
      await api("/api/cameras/ip", { method: "POST", body: JSON.stringify({ name, url }) });
      document.getElementById("ip-cam-name").value = "";
      document.getElementById("ip-cam-url").value = "";
      loadCamerasTable();
    } catch (err) {
      alert("Failed adding IP camera: " + err.message);
    }
  });

  // Timeline View
  document.getElementById("btn-load-timeline").addEventListener("click", async () => {
    const sec = parseInt(document.getElementById("timeline-window-select").value, 10);
    try {
      const res = await api("/api/mcp/call_tool", {
        method: "POST",
        body: JSON.stringify({
          name: "get_timeline",
          arguments: { last_seconds: sec, camera: currentCameraId, include_events: true }
        })
      });
      if (res.content && res.content[1]) {
        document.getElementById("timeline-img").src = "data:image/jpeg;base64," + res.content[1].data;
      }
    } catch (e) {
      alert("Failed fetching timeline: " + e.message);
    }
  });

  // Activity View
  async function loadActivityTable() {
    try {
      const data = await api("/api/activity");
      const tbody = document.getElementById("activity-table-body");
      tbody.innerHTML = "";
      data.activity.forEach(a => {
        const tr = document.createElement("tr");
        const dt = new Date(a.timestamp * 1000).toLocaleTimeString();
        tr.innerHTML = `
          <td class="num-cell">${dt}</td>
          <td>${a.client}</td>
          <td><strong>${a.tool}</strong></td>
          <td class="num-cell">${a.duration_ms}ms</td>
          <td><span class="status-dot ${a.status === 'success' ? '' : 'err'}"></span> ${a.status}</td>
          <td>${a.result_summary || '-'}</td>
          <td>${a.has_image ? `<button class="btn btn-sm view-img-btn" data-id="${a.id}">View Image</button>` : '-'}</td>
        `;
        const btn = tr.querySelector(".view-img-btn");
        if (btn) {
          btn.addEventListener("click", () => showImageModal(`/api/activity/${a.id}/image`, `${a.tool} Evidence`));
        }
        tbody.appendChild(tr);
      });
    } catch (e) {}
  }
  document.getElementById("btn-refresh-activity").addEventListener("click", loadActivityTable);

  // Baselines View
  async function loadBaselinesTable() {
    try {
      const data = await api("/api/baselines");
      const tbody = document.getElementById("baselines-table-body");
      tbody.innerHTML = "";
      data.baselines.forEach(b => {
        const tr = document.createElement("tr");
        const dt = new Date(b.created_at * 1000).toLocaleDateString();
        tr.innerHTML = `
          <td><strong>${b.name}</strong></td>
          <td>${b.camera}</td>
          <td>${b.region || '[Whole Frame]'}</td>
          <td>${dt}</td>
          <td class="num-cell">${b.width}x${b.height}</td>
          <td><button class="btn btn-sm btn-danger del-base-btn" data-name="${b.name}">Delete</button></td>
        `;
        tr.querySelector(".del-base-btn").addEventListener("click", async () => {
          await api(`/api/baselines/${b.name}`, { method: "DELETE" });
          loadBaselinesTable();
        });
        tbody.appendChild(tr);
      });
    } catch (e) {}
  }

  // Adapters View
  async function loadAdaptersView() {
    try {
      const statusData = await api("/api/status");
      const tbody = document.getElementById("adapters-table-body");
      tbody.innerHTML = "";
      Object.entries(statusData.adapters || {}).forEach(([name, isRunning]) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${name}</strong></td>
          <td>Hardware Stream</td>
          <td><span class="status-dot ${isRunning ? '' : 'warn'}"></span> ${isRunning ? 'Streaming' : 'Disabled'}</td>
        `;
        tbody.appendChild(tr);
      });

      // Approvals
      const appData = await api("/api/approvals");
      const appContainer = document.getElementById("approvals-container");
      if (!appData.approvals || appData.approvals.length === 0) {
        appContainer.innerHTML = '<p style="color:var(--muted);">No actions currently pending approval.</p>';
      } else {
        appContainer.innerHTML = "";
        appData.approvals.forEach(req => {
          const div = document.createElement("div");
          div.className = "metric-box";
          div.style.marginBottom = "10px";
          div.innerHTML = `
            <div style="font-weight:600; margin-bottom:4px;">Action: ${req.action_name}</div>
            <div style="font-size:12px; color:var(--muted); margin-bottom:8px;">Requested by: ${req.client}</div>
            <button class="btn btn-sm btn-primary app-btn" data-id="${req.id}" data-action="true">Approve</button>
            <button class="btn btn-sm btn-danger app-btn" data-id="${req.id}" data-action="false">Deny</button>
          `;
          div.querySelectorAll(".app-btn").forEach(b => {
            b.addEventListener("click", async () => {
              const approved = b.dataset.action === "true";
              await api(`/api/approvals/${req.id}`, { method: "POST", body: JSON.stringify({ approved }) });
              loadAdaptersView();
            });
          });
          appContainer.appendChild(div);
        });
      }
    } catch (e) {}
  }

  // Settings View
  async function loadSettings() {
    try {
      const cfg = await api("/api/settings");
      document.getElementById("cfg-image-width").value = cfg.default_image_width;
      document.getElementById("cfg-jpeg-quality").value = cfg.jpeg_quality;
      document.getElementById("cfg-idle-seconds").value = cfg.idle_release_seconds;
      document.getElementById("cfg-approval-mode").value = cfg.approval_mode;
    } catch (e) {}
  }

  document.getElementById("settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      default_image_width: parseInt(document.getElementById("cfg-image-width").value, 10),
      jpeg_quality: parseInt(document.getElementById("cfg-jpeg-quality").value, 10),
      idle_release_seconds: parseFloat(document.getElementById("cfg-idle-seconds").value),
      approval_mode: document.getElementById("cfg-approval-mode").value,
    };
    try {
      await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
      alert("Settings saved successfully.");
    } catch (err) {
      alert("Failed saving settings: " + err.message);
    }
  });

  // Doctor Diagnostics
  document.getElementById("btn-run-doctor-dashboard").addEventListener("click", async () => {
    const resBox = document.getElementById("doctor-results");
    resBox.textContent = "Running diagnostics checks...";
    try {
      const res = await api("/api/doctor");
      let out = "";
      res.checks.forEach(c => {
        out += `[${c.status}] ${c.name}: ${c.message}\n`;
        if (c.fix) out += `       Fix: ${c.fix}\n`;
      });
      out += `\nResult: ${res.passed ? 'ALL CHECKS PASSED' : 'SOME CHECKS FAILED'}`;
      resBox.textContent = out;
    } catch (e) {
      resBox.textContent = "Error running doctor: " + e.message;
    }
  });

  // Copy Snippet buttons
  document.querySelectorAll(".copy-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const targetId = btn.dataset.target;
      const el = document.getElementById(targetId);
      if (el) {
        navigator.clipboard.writeText(el.innerText.replace("Copy", "").trim());
        const orig = btn.textContent;
        btn.textContent = "Copied!";
        setTimeout(() => { btn.textContent = orig; }, 1500);
      }
    });
  });

  // Image Modal
  function showImageModal(src, title) {
    document.getElementById("modal-img").src = src;
    document.getElementById("modal-title").textContent = title || "Evidence Image";
    document.getElementById("image-modal").classList.add("active");
  }

  document.getElementById("modal-close").addEventListener("click", () => {
    document.getElementById("image-modal").classList.remove("active");
  });

  // Initialization
  async function init() {
    setupNavigation();
    await fetchAuthToken();
    setupWebSocket();
    await loadRegions();
    await loadPrivacyMasks();
  }

  init();
})();
