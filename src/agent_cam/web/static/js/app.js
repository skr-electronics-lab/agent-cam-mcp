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

  // Canvas Drawing & Overlays State
  let activeTool = "select"; // "select", "draw_region", "draw_mask"
  let selectedRegionName = null;
  let dragAction = null; // null, "create", "move", "resize"
  let resizeHandle = null; // "nw", "ne", "se", "sw", "n", "s", "e", "w"
  let dragOffset = { x: 0, y: 0 };
  let pendingRect = null;
  const HANDLE_SIZE = 8;

  const streamWrapper = document.getElementById("stream-wrapper");
  const coordsBadge = document.getElementById("stream-coords");
  const namingDialog = document.getElementById("region-naming-dialog");
  const dialogNameInput = document.getElementById("dialog-region-name");
  const btnDeleteSelected = document.getElementById("btn-delete-selected");
  const zoomSelect = document.getElementById("zoom-select");
  const btnSnapshot = document.getElementById("btn-snapshot");

  // Mode buttons
  const btnModeSelect = document.getElementById("mode-select");
  const btnModeDrawRegion = document.getElementById("mode-draw-region");
  const btnModeDrawQuad = document.getElementById("mode-draw-quad");
  const btnModeDrawMask = document.getElementById("mode-draw-mask");
  let quadPoints = [];
  let mousePos = null;

  function setToolMode(mode) {
    activeTool = mode;
    btnModeSelect.classList.toggle("active", mode === "select");
    btnModeDrawRegion.classList.toggle("active", mode === "draw_region");
    if (btnModeDrawQuad) btnModeDrawQuad.classList.toggle("active", mode === "draw_quad");
    btnModeDrawMask.classList.toggle("active", mode === "draw_mask");
    quadPoints = [];

    if (mode === "select") {
      canvas.style.cursor = "default";
      document.getElementById("canvas-status-hint").textContent = "Mode: SELECT. Click any region to inspect, move, or drag handles to resize.";
    } else if (mode === "draw_region") {
      canvas.style.cursor = "crosshair";
      document.getElementById("canvas-status-hint").textContent = "Mode: DRAW REGION. Click and drag to create a new inspection region.";
    } else if (mode === "draw_quad") {
      canvas.style.cursor = "crosshair";
      document.getElementById("canvas-status-hint").textContent = "Mode: DRAW QUAD (Warp). Click 4 corners (TL, TR, BR, BL) on the surface.";
    } else if (mode === "draw_mask") {
      canvas.style.cursor = "crosshair";
      document.getElementById("canvas-status-hint").textContent = "Mode: PRIVACY MASK. Click and drag to permanently redact an area.";
    }
    renderCanvas();
  }

  btnModeSelect.addEventListener("click", () => setToolMode("select"));
  btnModeDrawRegion.addEventListener("click", () => setToolMode("draw_region"));
  if (btnModeDrawQuad) btnModeDrawQuad.addEventListener("click", () => setToolMode("draw_quad"));
  btnModeDrawMask.addEventListener("click", () => setToolMode("draw_mask"));

  // Zoom control
  zoomSelect.addEventListener("change", (e) => {
    const scale = parseFloat(e.target.value) || 1.0;
    streamWrapper.style.transform = `scale(${scale})`;
  });

  // Snapshot download
  btnSnapshot.addEventListener("click", () => {
    const tempCanvas = document.createElement("canvas");
    tempCanvas.width = mjpegStream.naturalWidth || canvas.width || 640;
    tempCanvas.height = mjpegStream.naturalHeight || canvas.height || 480;
    const tCtx = tempCanvas.getContext("2d");
    tCtx.drawImage(mjpegStream, 0, 0, tempCanvas.width, tempCanvas.height);
    const link = document.createElement("a");
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    link.download = `agent-cam-snapshot-${ts}.jpg`;
    link.href = tempCanvas.toDataURL("image/jpeg", 0.95);
    link.click();
  });

  // Delete selected region
  btnDeleteSelected.addEventListener("click", async () => {
    if (!selectedRegionName) return;
    try {
      await api(`/api/regions/${selectedRegionName}`, { method: "DELETE" });
      selectedRegionName = null;
      btnDeleteSelected.style.display = "none";
      await loadRegions();
    } catch (e) {
      alert("Error deleting region: " + e.message);
    }
  });

  // Resize canvas exactly to match stream image dimensions
  function syncCanvasSize() {
    if (!mjpegStream.clientWidth || !mjpegStream.clientHeight) return;
    if (canvas.width !== mjpegStream.clientWidth || canvas.height !== mjpegStream.clientHeight) {
      canvas.width = mjpegStream.clientWidth;
      canvas.height = mjpegStream.clientHeight;
      canvas.style.width = mjpegStream.clientWidth + "px";
      canvas.style.height = mjpegStream.clientHeight + "px";
      renderCanvas();
    }
  }

  const resizeObserver = new ResizeObserver(() => syncCanvasSize());
  resizeObserver.observe(mjpegStream);
  mjpegStream.addEventListener("load", syncCanvasSize);

  // Geometry Helpers
  function getPixelBox(normBox) {
    return {
      x: normBox.x * canvas.width,
      y: normBox.y * canvas.height,
      w: normBox.w * canvas.width,
      h: normBox.h * canvas.height,
    };
  }

  function getNormBox(pixelBox) {
    const clamp = (val) => Math.max(0, Math.min(1, val));
    const x = clamp(pixelBox.x / canvas.width);
    const y = clamp(pixelBox.y / canvas.height);
    const w = clamp(pixelBox.w / canvas.width);
    const h = clamp(pixelBox.h / canvas.height);
    return {
      x: Number(x.toFixed(4)),
      y: Number(y.toFixed(4)),
      w: Number(Math.min(1 - x, w).toFixed(4)),
      h: Number(Math.min(1 - y, h).toFixed(4)),
    };
  }

  function getHandles(b) {
    return {
      nw: { x: b.x, y: b.y },
      ne: { x: b.x + b.w, y: b.y },
      se: { x: b.x + b.w, y: b.y + b.h },
      sw: { x: b.x, y: b.y + b.h },
      n: { x: b.x + b.w / 2, y: b.y },
      s: { x: b.x + b.w / 2, y: b.y + b.h },
      w: { x: b.x, y: b.y + b.h / 2 },
      e: { x: b.x + b.w, y: b.y + b.h / 2 },
    };
  }

  function hitTestHandle(px, py, b) {
    const handles = getHandles(b);
    for (const [key, pt] of Object.entries(handles)) {
      if (Math.abs(px - pt.x) <= HANDLE_SIZE && Math.abs(py - pt.y) <= HANDLE_SIZE) {
        return key;
      }
    }
    return null;
  }

  function hitTestBox(px, py, b) {
    return px >= b.x && px <= b.x + b.w && py >= b.y && py <= b.y + b.h;
  }

  function renderCanvas() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 1. Draw Privacy Masks (black redacted rectangles with hazard border)
    privacyMasks.forEach(mask => {
      const b = getPixelBox(mask);
      ctx.fillStyle = "rgba(0, 0, 0, 0.95)";
      ctx.fillRect(b.x, b.y, b.w, b.h);
      ctx.strokeStyle = "#E5484D";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(b.x, b.y, b.w, b.h);
      ctx.setLineDash([]);
      ctx.fillStyle = "#E5484D";
      ctx.font = "10px " + getComputedStyle(document.body).fontFamily;
      ctx.fillText("PRIVACY MASK", b.x + 4, b.y + 12);
    });

    // 2. Draw Defined Regions
    Object.values(definedRegions).forEach(reg => {
      const isSelected = reg.name === selectedRegionName;

      if (reg.points && reg.points.length === 4) {
        const pts = reg.points.map(p => ({
          x: p[0] * canvas.width,
          y: p[1] * canvas.height
        }));
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].y);
        for (let i = 1; i < pts.length; i++) {
          ctx.lineTo(pts[i].x, pts[i].y);
        }
        ctx.closePath();
        ctx.strokeStyle = isSelected ? "#00D7FF" : "rgba(0, 215, 255, 0.75)";
        ctx.lineWidth = isSelected ? 2 : 1.5;
        ctx.stroke();

        ctx.fillStyle = isSelected ? "rgba(0, 215, 255, 0.16)" : "rgba(0, 215, 255, 0.08)";
        ctx.fill();

        // Draw 4 corner pins
        pts.forEach((pt) => {
          ctx.beginPath();
          ctx.arc(pt.x, pt.y, isSelected ? 5 : 3.5, 0, Math.PI * 2);
          ctx.fillStyle = isSelected ? "#FFFFFF" : "#00D7FF";
          ctx.fill();
          ctx.strokeStyle = "#007ACC";
          ctx.lineWidth = 1.5;
          ctx.stroke();
        });

        // Badge label
        ctx.fillStyle = isSelected ? "#00D7FF" : "rgba(0, 215, 255, 0.9)";
        ctx.font = "11px " + getComputedStyle(document.body).fontFamily;
        const text = ` [QUAD] ${reg.name} `;
        const tw = ctx.measureText(text).width;
        ctx.fillRect(pts[0].x, Math.max(0, pts[0].y - 16), tw + 4, 16);
        ctx.fillStyle = "#000000";
        ctx.fillText(text, pts[0].x + 2, Math.max(12, pts[0].y - 4));
        return;
      }

      const b = getPixelBox(reg);

      // Box outline
      ctx.strokeStyle = isSelected ? "#3B82F6" : "rgba(59, 130, 246, 0.75)";
      ctx.lineWidth = isSelected ? 2 : 1.5;
      ctx.strokeRect(b.x, b.y, b.w, b.h);

      // Semi-transparent fill for selected region
      if (isSelected) {
        ctx.fillStyle = "rgba(59, 130, 246, 0.12)";
        ctx.fillRect(b.x, b.y, b.w, b.h);
      }

      // Badge label
      ctx.fillStyle = isSelected ? "#3B82F6" : "rgba(59, 130, 246, 0.9)";
      ctx.font = "11px " + getComputedStyle(document.body).fontFamily;
      const text = ` ${reg.name} `;
      const tw = ctx.measureText(text).width;
      ctx.fillRect(b.x, Math.max(0, b.y - 16), tw + 4, 16);
      ctx.fillStyle = "#FFFFFF";
      ctx.fillText(text, b.x + 2, Math.max(12, b.y - 4));

      // Draw 8 handles if selected
      if (isSelected) {
        const handles = getHandles(b);
        ctx.fillStyle = "#FFFFFF";
        ctx.strokeStyle = "#3B82F6";
        ctx.lineWidth = 1.5;
        for (const pt of Object.values(handles)) {
          ctx.fillRect(pt.x - 4, pt.y - 4, 8, 8);
          ctx.strokeRect(pt.x - 4, pt.y - 4, 8, 8);
        }
      }
    });

    // 3. Draw in-progress quad points
    if (activeTool === "draw_quad" && quadPoints.length > 0) {
      ctx.strokeStyle = "#00D7FF";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(quadPoints[0].x * canvas.width, quadPoints[0].y * canvas.height);
      for (let i = 1; i < quadPoints.length; i++) {
        ctx.lineTo(quadPoints[i].x * canvas.width, quadPoints[i].y * canvas.height);
      }
      if (mousePos) {
        ctx.lineTo(mousePos.x, mousePos.y);
      }
      ctx.stroke();

      quadPoints.forEach((pt, idx) => {
        ctx.beginPath();
        ctx.arc(pt.x * canvas.width, pt.y * canvas.height, 5, 0, Math.PI * 2);
        ctx.fillStyle = "#00D7FF";
        ctx.fill();
        ctx.fillStyle = "#FFFFFF";
        ctx.fillText(String(idx + 1), pt.x * canvas.width + 8, pt.y * canvas.height - 4);
      });
    }

    // 4. Draw active drawing box
    if (dragAction === "create" && currentRect) {
      ctx.strokeStyle = activeTool === "draw_mask" ? "#E5484D" : "#00D7FF";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(currentRect.x, currentRect.y, currentRect.w, currentRect.h);
      ctx.setLineDash([]);
      ctx.fillStyle = activeTool === "draw_mask" ? "rgba(229, 72, 77, 0.2)" : "rgba(0, 215, 255, 0.15)";
      ctx.fillRect(currentRect.x, currentRect.y, currentRect.w, currentRect.h);
    }
  }

  // Pointer Interactions
  canvas.addEventListener("mousedown", (e) => {
    syncCanvasSize();
    const rect = canvas.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;

    if (activeTool === "draw_quad") {
      const normX = px / canvas.width;
      const normY = py / canvas.height;
      quadPoints.push({ x: normX, y: normY });
      if (quadPoints.length === 4) {
        document.getElementById("dialog-title").textContent = "Name 4-Point Quad Region";
        dialogNameInput.value = "";
        const parentInput = document.getElementById("dialog-region-parent");
        if (parentInput) parentInput.value = "";
        namingDialog.style.display = "flex";
        namingDialog.style.top = Math.min(canvas.height - 140, Math.max(10, py)) + "px";
        namingDialog.style.left = Math.min(canvas.width - 240, Math.max(10, px)) + "px";
        dialogNameInput.focus();
      } else {
        document.getElementById("canvas-status-hint").textContent = `Quad: Point ${quadPoints.length}/4 placed. Click next corner.`;
      }
      renderCanvas();
      return;
    }

    if (activeTool === "select") {
      // Check handles of currently selected region first
      if (selectedRegionName && definedRegions[selectedRegionName]) {
        const selBox = getPixelBox(definedRegions[selectedRegionName]);
        const handle = hitTestHandle(px, py, selBox);
        if (handle) {
          dragAction = "resize";
          resizeHandle = handle;
          startX = px;
          startY = py;
          return;
        }
      }

      // Check hit on existing regions
      let clickedRegion = null;
      for (const reg of Object.values(definedRegions)) {
        const b = getPixelBox(reg);
        if (hitTestBox(px, py, b)) {
          clickedRegion = reg;
          break;
        }
      }

      if (clickedRegion) {
        selectedRegionName = clickedRegion.name;
        dragAction = "move";
        const b = getPixelBox(clickedRegion);
        dragOffset = { x: px - b.x, y: py - b.y };
        btnDeleteSelected.style.display = "inline-block";
        document.getElementById("inspector-region-select").value = selectedRegionName;
        renderCanvas();
        runMeasure(selectedRegionName);
        updateRectifiedPreview(selectedRegionName);
        return;
      } else {
        // Deselect
        selectedRegionName = null;
        btnDeleteSelected.style.display = "none";
        document.getElementById("inspector-region-select").value = "";
        renderCanvas();
        updateRectifiedPreview(null);
      }
    } else if (activeTool === "draw_region" || activeTool === "draw_mask") {
      dragAction = "create";
      startX = px;
      startY = py;
      currentRect = { x: startX, y: startY, w: 0, h: 0 };
    }
  });

  canvas.addEventListener("mousemove", (e) => {
    const rect = canvas.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;

    // Handle cursor style updates in select mode
    if (activeTool === "select" && !dragAction) {
      if (selectedRegionName && definedRegions[selectedRegionName]) {
        const selBox = getPixelBox(definedRegions[selectedRegionName]);
        const handle = hitTestHandle(px, py, selBox);
        if (handle) {
          const cursors = {
            nw: "nwse-resize", se: "nwse-resize",
            ne: "nesw-resize", sw: "nesw-resize",
            n: "ns-resize", s: "ns-resize",
            w: "ew-resize", e: "ew-resize",
          };
          canvas.style.cursor = cursors[handle] || "default";
          return;
        }
      }

      let isInside = false;
      for (const reg of Object.values(definedRegions)) {
        if (hitTestBox(px, py, getPixelBox(reg))) {
          isInside = true;
          break;
        }
      }
      canvas.style.cursor = isInside ? "move" : "default";
      return;
    }

    if (!dragAction) return;

    if (dragAction === "create") {
      currentRect.x = Math.min(startX, px);
      currentRect.y = Math.min(startY, py);
      currentRect.w = Math.abs(px - startX);
      currentRect.h = Math.abs(py - startY);

      coordsBadge.style.display = "block";
      coordsBadge.textContent = `${Math.round(currentRect.w)}x${Math.round(currentRect.h)} px (${Math.round((currentRect.w/canvas.width)*100)}% x ${Math.round((currentRect.h/canvas.height)*100)}%)`;
      renderCanvas();
    } else if (dragAction === "move" && selectedRegionName && definedRegions[selectedRegionName]) {
      const reg = definedRegions[selectedRegionName];
      const curPixel = getPixelBox(reg);
      let newX = px - dragOffset.x;
      let newY = py - dragOffset.y;
      newX = Math.max(0, Math.min(canvas.width - curPixel.w, newX));
      newY = Math.max(0, Math.min(canvas.height - curPixel.h, newY));

      const updatedNorm = getNormBox({ x: newX, y: newY, w: curPixel.w, h: curPixel.h });
      reg.x = updatedNorm.x;
      reg.y = updatedNorm.y;

      coordsBadge.style.display = "block";
      coordsBadge.textContent = `X:${Math.round(newX)} Y:${Math.round(newY)} (${Math.round(updatedNorm.x*100)}%, ${Math.round(updatedNorm.y*100)}%)`;
      renderCanvas();
    } else if (dragAction === "resize" && selectedRegionName && definedRegions[selectedRegionName]) {
      const reg = definedRegions[selectedRegionName];
      const b = getPixelBox(reg);

      if (resizeHandle.includes("e")) b.w = Math.max(10, px - b.x);
      if (resizeHandle.includes("s")) b.h = Math.max(10, py - b.y);
      if (resizeHandle.includes("w")) {
        const right = b.x + b.w;
        b.x = Math.min(px, right - 10);
        b.w = right - b.x;
      }
      if (resizeHandle.includes("n")) {
        const bottom = b.y + b.h;
        b.y = Math.min(py, bottom - 10);
        b.h = bottom - b.y;
      }

      const updatedNorm = getNormBox(b);
      Object.assign(reg, updatedNorm);

      coordsBadge.style.display = "block";
      coordsBadge.textContent = `${Math.round(b.w)}x${Math.round(b.h)} px`;
      renderCanvas();
    }
  });

  window.addEventListener("mouseup", async () => {
    coordsBadge.style.display = "none";

    if (dragAction === "create") {
      dragAction = null;
      if (!currentRect || currentRect.w < 10 || currentRect.h < 10) {
        currentRect = null;
        renderCanvas();
        return;
      }

      pendingRect = { ...currentRect };
      currentRect = null;
      renderCanvas();

      const norm = getNormBox(pendingRect);

      if (activeTool === "draw_region") {
        // Position dialog neatly near the box inside the streamWrapper
        namingDialog.style.display = "flex";
        dialogNameInput.value = "";
        dialogNameInput.focus();

        const dialogTop = Math.min(canvas.height - 120, Math.max(10, pendingRect.y + pendingRect.h + 8));
        const dialogLeft = Math.min(canvas.width - 240, Math.max(10, pendingRect.x));
        namingDialog.style.top = dialogTop + "px";
        namingDialog.style.left = dialogLeft + "px";
      } else if (activeTool === "draw_mask") {
        await savePrivacyMask(norm.x, norm.y, norm.w, norm.h);
        setToolMode("select");
      }
    } else if (dragAction === "move" || dragAction === "resize") {
      dragAction = null;
      if (selectedRegionName && definedRegions[selectedRegionName]) {
        const reg = definedRegions[selectedRegionName];
        await saveRegion(reg.name, reg.x, reg.y, reg.w, reg.h);
      }
    }
  });

  // Floating Dialog Action Handlers
  document.getElementById("dialog-save-btn").addEventListener("click", async () => {
    const name = dialogNameInput.value.trim();
    const parentInput = document.getElementById("dialog-region-parent");
    const parent = parentInput ? parentInput.value.trim() || null : null;

    if (!name) {
      alert("Please enter a region name.");
      dialogNameInput.focus();
      return;
    }
    namingDialog.style.display = "none";

    if (activeTool === "draw_quad" && quadPoints.length === 4) {
      const xs = quadPoints.map(p => p.x);
      const ys = quadPoints.map(p => p.y);
      const minX = Math.min(...xs);
      const minY = Math.min(...ys);
      const maxX = Math.max(...xs);
      const maxY = Math.max(...ys);
      const pts = quadPoints.map(p => [p.x, p.y]);
      quadPoints = [];
      await saveRegion(name, minX, minY, maxX - minX, maxY - minY, pts, parent);
      selectedRegionName = name;
      btnDeleteSelected.style.display = "inline-block";
      setToolMode("select");
      updateRectifiedPreview(name);
    } else if (pendingRect) {
      const norm = getNormBox(pendingRect);
      pendingRect = null;
      await saveRegion(name, norm.x, norm.y, norm.w, norm.h, null, parent);
      selectedRegionName = name;
      btnDeleteSelected.style.display = "inline-block";
      setToolMode("select");
      updateRectifiedPreview(name);
    }
  });

  document.getElementById("dialog-cancel-btn").addEventListener("click", () => {
    namingDialog.style.display = "none";
    pendingRect = null;
    quadPoints = [];
    renderCanvas();
  });

  dialogNameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      document.getElementById("dialog-save-btn").click();
    } else if (e.key === "Escape") {
      document.getElementById("dialog-cancel-btn").click();
    }
  });

  async function saveRegion(name, x, y, w, h, points = null, parent = null) {
    try {
      const body = {
        name,
        x,
        y,
        w,
        h,
        camera: currentCameraId,
        units: "normalized",
        points: points || undefined,
        parent: parent || undefined,
      };
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
        li.style.padding = "4px 8px";
        li.style.borderRadius = "var(--radius)";
        li.style.border = "1px solid var(--border)";
        li.style.backgroundColor = r.name === selectedRegionName ? "var(--raised)" : "transparent";

        const isQuad = r.points && r.points.length === 4;
        const tag = isQuad ? "[QUAD]" : "[RECT]";
        const parentTag = r.parent ? `<span style="font-size:10px; color:var(--muted); margin-left:4px;">(${r.parent})</span>` : "";

        li.innerHTML = `
          <span style="font-weight:500; cursor:pointer;" class="region-item-name"><span style="color:var(--accent); font-size:10px; margin-right:4px;">${tag}</span>${r.name}${parentTag}</span>
          <button class="btn btn-sm btn-danger" data-name="${r.name}">Del</button>
        `;

        li.querySelector(".region-item-name").addEventListener("click", () => {
          selectedRegionName = r.name;
          btnDeleteSelected.style.display = "inline-block";
          inspectorSelect.value = r.name;
          renderCanvas();
          runMeasure(r.name);
          updateRectifiedPreview(r.name);
        });

        li.querySelector("button").addEventListener("click", async (e) => {
          e.stopPropagation();
          await api(`/api/regions/${r.name}`, { method: "DELETE" });
          if (selectedRegionName === r.name) {
            selectedRegionName = null;
            btnDeleteSelected.style.display = "none";
          }
          loadRegions();
        });
        list.appendChild(li);

        const opt = document.createElement("option");
        opt.value = r.name;
        opt.textContent = r.name;
        inspectorSelect.appendChild(opt);
      });

      if (selectedRegionName && definedRegions[selectedRegionName]) {
        inspectorSelect.value = selectedRegionName;
      }
      renderCanvas();
    } catch (e) {}
  }

  document.getElementById("inspector-region-select").addEventListener("change", (e) => {
    selectedRegionName = e.target.value || null;
    btnDeleteSelected.style.display = selectedRegionName ? "inline-block" : "none";
    renderCanvas();
    if (selectedRegionName) {
      runMeasure(selectedRegionName);
    }
  });

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
        li.style.padding = "4px 8px";
        li.style.borderRadius = "var(--radius)";
        li.style.border = "1px solid var(--border)";
        li.innerHTML = `<span>Mask (${Math.round(m.w*100)}% x ${Math.round(m.h*100)}%)</span> <button class="btn btn-sm btn-danger" data-id="${m.id}">Del</button>`;
        li.querySelector("button").addEventListener("click", async () => {
          await api(`/api/privacy_masks/${m.id}`, { method: "DELETE" });
          loadPrivacyMasks();
        });
        list.appendChild(li);
      });
      renderCanvas();
    } catch (e) {}
  }

  // Live Measure Runner
  async function runMeasure(regName) {
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
      console.warn("Measurement call failed:", e);
    }
  }

  document.getElementById("btn-run-measure").addEventListener("click", () => {
    const regName = document.getElementById("inspector-region-select").value;
    runMeasure(regName);
    updateRectifiedPreview(regName);
  });

  // Rectified Orthogonal View Preview
  function updateRectifiedPreview(regName) {
    const sec = document.getElementById("rectified-preview-section");
    const img = document.getElementById("rectified-preview-img");
    const lbl = document.getElementById("rectified-res-label");
    if (!sec || !img) return;

    if (!regName || !definedRegions[regName]) {
      sec.style.display = "none";
      return;
    }
    sec.style.display = "block";
    img.src = `/api/regions/${encodeURIComponent(regName)}/rectified?t=${Date.now()}`;
    img.onload = () => {
      if (lbl) lbl.textContent = `${img.naturalWidth} x ${img.naturalHeight} px`;
    };
  }

  document.getElementById("btn-refresh-rectified")?.addEventListener("click", () => {
    if (selectedRegionName) updateRectifiedPreview(selectedRegionName);
  });

  // Live LED & Frequency Analyzer
  document.getElementById("btn-run-led-analysis")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-run-led-analysis");
    const regName = selectedRegionName || document.getElementById("inspector-region-select").value || null;
    btn.disabled = true;
    btn.textContent = "Sampling (1.5s)...";
    try {
      const res = await api("/api/led/analyze", {
        method: "POST",
        body: JSON.stringify({
          camera: currentCameraId || "0",
          region: regName || undefined,
          duration_s: 1.5,
        }),
      });
      document.getElementById("led-state-label").textContent = (res.state || "OFF").toUpperCase();
      document.getElementById("led-freq-label").textContent = res.frequency_hz ? `${res.frequency_hz} Hz` : "-- Hz";
      document.getElementById("led-duty-label").textContent = res.duty_cycle ? `${res.duty_cycle}%` : "--%";
      document.getElementById("led-color-label").textContent = (res.color_name || "--").toUpperCase();

      const rgb = res.dominant_rgb || [0, 0, 0];
      const swatch = document.getElementById("led-swatch-display");
      if (swatch) swatch.style.background = `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
    } catch (e) {
      alert("LED analysis failed: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Analyze Blink / Frequency";
    }
  });

  // Camera Optics Controls & Sliders
  const ctrlBrightness = document.getElementById("ctrl-brightness");
  const ctrlContrast = document.getElementById("ctrl-contrast");
  const ctrlExposure = document.getElementById("ctrl-exposure");

  async function applyOpticsSettings(settings) {
    try {
      await api(`/api/cameras/${currentCameraId || "0"}/controls`, {
        method: "POST",
        body: JSON.stringify(settings),
      });
    } catch (e) {
      console.warn("Optics setting failed:", e);
    }
  }

  ctrlBrightness?.addEventListener("change", (e) => {
    const v = parseInt(e.target.value, 10);
    document.getElementById("val-ctrl-brightness").textContent = v;
    applyOpticsSettings({ brightness: v });
  });

  ctrlContrast?.addEventListener("change", (e) => {
    const v = parseInt(e.target.value, 10);
    document.getElementById("val-ctrl-contrast").textContent = v;
    applyOpticsSettings({ contrast: v });
  });

  ctrlExposure?.addEventListener("change", (e) => {
    const v = parseInt(e.target.value, 10);
    document.getElementById("val-ctrl-exposure").textContent = v;
    applyOpticsSettings({ exposure: v, lock_auto: true });
  });

  document.getElementById("preset-anti-glare")?.addEventListener("click", () => {
    if (ctrlBrightness) ctrlBrightness.value = 110;
    if (ctrlContrast) ctrlContrast.value = 160;
    if (ctrlExposure) ctrlExposure.value = -7;
    document.getElementById("val-ctrl-brightness").textContent = 110;
    document.getElementById("val-ctrl-contrast").textContent = 160;
    document.getElementById("val-ctrl-exposure").textContent = -7;
    applyOpticsSettings({ brightness: 110, contrast: 160, exposure: -7, lock_auto: true });
  });

  document.getElementById("preset-pcb-contrast")?.addEventListener("click", () => {
    if (ctrlBrightness) ctrlBrightness.value = 130;
    if (ctrlContrast) ctrlContrast.value = 195;
    if (ctrlExposure) ctrlExposure.value = -5;
    document.getElementById("val-ctrl-brightness").textContent = 130;
    document.getElementById("val-ctrl-contrast").textContent = 195;
    document.getElementById("val-ctrl-exposure").textContent = -5;
    applyOpticsSettings({ brightness: 130, contrast: 195, exposure: -5, lock_auto: true });
  });

  document.getElementById("preset-reset-optics")?.addEventListener("click", () => {
    if (ctrlBrightness) ctrlBrightness.value = 128;
    if (ctrlContrast) ctrlContrast.value = 128;
    if (ctrlExposure) ctrlExposure.value = -5;
    document.getElementById("val-ctrl-brightness").textContent = 128;
    document.getElementById("val-ctrl-contrast").textContent = 128;
    document.getElementById("val-ctrl-exposure").textContent = -5;
    applyOpticsSettings({ brightness: 128, contrast: 128, lock_auto: false });
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
    setToolMode("select");
    await fetchAuthToken();
    setupWebSocket();
    await loadRegions();
    await loadPrivacyMasks();
  }

  init();
})();
