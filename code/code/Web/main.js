const menuToggle = document.querySelector(".menu-toggle");
const mobileMenu = document.querySelector("#mobile-menu");
const mobileSheet = document.querySelector(".mobile-sheet");
const consoleElement = document.querySelector("#robot-console");
const consoleClose = document.querySelector(".console-close");
const consoleOpeners = document.querySelectorAll("[data-console-open]");
const connectionPill = document.querySelector(".connection-pill");
const connectionLabel = document.querySelector("#connection-label");
const commandVector = document.querySelector("#command-vector");
const toast = document.querySelector("#console-toast");
const motionKeys = [...document.querySelectorAll(".motion-key")];
const bagList = document.querySelector("#bag-list");
const selectedBagName = document.querySelector("#selected-bag-name");
const bagPlayState = document.querySelector("#bag-play-state");
const bagPlay = document.querySelector("#bag-play");
const bagPause = document.querySelector("#bag-pause");
const bagStop = document.querySelector("#bag-stop");
const refreshBagsButton = document.querySelector("#refresh-bags");
const modeBadge = document.querySelector("#console-mode");
const motionPanel = document.querySelector(".motion-panel");
const bagPanel = document.querySelector(".bag-panel");

const speedControls = {
  linear: {
    input: document.querySelector("#linear-speed"),
    output: document.querySelector("#linear-output"),
    unit: "m/s",
  },
  lateral: {
    input: document.querySelector("#lateral-speed"),
    output: document.querySelector("#lateral-output"),
    unit: "m/s",
  },
  angular: {
    input: document.querySelector("#angular-speed"),
    output: document.querySelector("#angular-output"),
    unit: "rad/s",
  },
};

const keyBindings = {
  w: "forward",
  s: "backward",
  a: "left",
  d: "right",
  q: "rotate-left",
  e: "rotate-right",
  " ": "stop",
};

let lastConsoleOpener = null;
let motionInterval = null;
let activeMotion = null;
let motionAbortController = null;
let selectedBagId = null;
let statusPoll = null;
let toastTimer = null;
let playerState = "idle";
let replayMode = null;
let replayHeartbeat = null;
let heartbeatInFlight = false;
let controlToken = null;
let serverMode = "preview";
let commandSequence = 0;
const REPLAY_HEARTBEAT_MS = 200;
const browserSessionId = globalThis.crypto?.randomUUID
  ? globalThis.crypto.randomUUID().replaceAll("-", "_")
  : `session_${Date.now()}_${Math.random().toString(36).slice(2)}`;

function motionEnabled() {
  return serverMode === "motion" || serverMode === "console";
}

function playbackEnabled() {
  return serverMode === "playback" || serverMode === "console";
}

function setMenu(open) {
  menuToggle.setAttribute("aria-expanded", String(open));
  menuToggle.setAttribute("aria-label", open ? "Close navigation menu" : "Open navigation menu");
  mobileMenu.hidden = !open;
  document.body.classList.toggle("menu-open", open);
}

menuToggle.addEventListener("click", () => {
  setMenu(menuToggle.getAttribute("aria-expanded") !== "true");
});

mobileMenu.addEventListener("click", () => setMenu(false));
mobileSheet.addEventListener("click", (event) => event.stopPropagation());
mobileSheet.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => setMenu(false)));

function showToast(message) {
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.add("is-visible");
  toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 3400);
}

async function apiRequest(path, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 2500);
  const headers = { ...(options.headers || {}) };
  const method = (options.method || "GET").toUpperCase();

  if (options.signal) {
    if (options.signal.aborted) controller.abort();
    options.signal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  if (method === "POST") {
    if (!controlToken) throw new Error("Control session is not initialized");
    headers["X-MOF-Control-Token"] = controlToken;
  }

  try {
    const response = await fetch(path, { ...options, method, headers, signal: controller.signal });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : null;

    if (!response.ok) {
      throw new Error(payload?.error || `Request failed (${response.status})`);
    }

    return payload;
  } finally {
    window.clearTimeout(timeout);
  }
}

function setPanelDisabled(panel, disabled) {
  panel.classList.toggle("is-disabled", disabled);
  panel.setAttribute("aria-disabled", String(disabled));
  panel.querySelectorAll("button, input").forEach((control) => {
    if (disabled || panel === motionPanel) control.disabled = disabled;
  });
}

function applyMode(mode) {
  serverMode = mode || "preview";
  const labels = {
    motion: "MOTION ONLY",
    playback: "PLAYBACK ONLY · DOMAIN 97",
    console: "ROBOT + ISOLATED PLAYBACK",
    preview: "PREVIEW · NO CONTROL",
  };
  modeBadge.textContent = labels[serverMode] || labels.preview;
  modeBadge.dataset.mode = serverMode;
  setPanelDisabled(motionPanel, !motionEnabled());
  setPanelDisabled(bagPanel, !playbackEnabled());
  if (!motionEnabled()) stopMotion(false);
  if (playbackEnabled()) {
    refreshBagsButton.disabled = false;
    updatePlayer({ state: playerState });
  }
}

async function initializeSession() {
  const payload = await apiRequest("/api/session");
  controlToken = payload.token;
  applyMode(payload.mode);
  if (motionEnabled()) {
    const seq = ++commandSequence;
    await apiRequest("/api/stop", {
      method: "POST",
      body: JSON.stringify({ session_id: browserSessionId, seq }),
    });
  }
  return payload;
}

function setConnection(state, label) {
  connectionPill.dataset.state = state;
  connectionLabel.textContent = label;
}

async function openConsole(event) {
  event?.preventDefault();
  lastConsoleOpener = event?.currentTarget || document.activeElement;
  setMenu(false);
  consoleElement.hidden = false;
  document.body.classList.add("console-open");
  consoleClose.focus();
  try {
    await initializeSession();
    await refreshStatus();
    if (playbackEnabled()) await loadBags();
  } catch (error) {
    setConnection("offline", "Control offline");
    showToast(error.message || "Unable to initialize control session");
  }
  window.clearInterval(statusPoll);
  statusPoll = window.setInterval(refreshStatus, 1600);
}

function closeConsole() {
  stopMotion();
  window.clearInterval(statusPoll);
  statusPoll = null;
  consoleElement.hidden = true;
  document.body.classList.remove("console-open");
  lastConsoleOpener?.focus?.();
}

consoleOpeners.forEach((opener) => opener.addEventListener("click", openConsole));
consoleClose.addEventListener("click", closeConsole);
consoleElement.addEventListener("click", (event) => {
  if (event.target === consoleElement) closeConsole();
});

function updateSpeedControl(control) {
  const value = Number(control.input.value);
  const minimum = Number(control.input.min);
  const maximum = Number(control.input.max);
  const progress = ((value - minimum) / (maximum - minimum)) * 100;
  control.output.textContent = `${value.toFixed(2)} ${control.unit}`;
  control.input.style.setProperty("--range-progress", `${progress}%`);
}

Object.values(speedControls).forEach((control) => {
  updateSpeedControl(control);
  control.input.addEventListener("input", () => updateSpeedControl(control));
});

function vectorForMotion(motion) {
  const linear = Number(speedControls.linear.input.value);
  const lateral = Number(speedControls.lateral.input.value);
  const angular = Number(speedControls.angular.input.value);

  switch (motion) {
    case "forward":
      return { vx: linear, vy: 0, wz: 0 };
    case "backward":
      return { vx: -linear, vy: 0, wz: 0 };
    case "left":
      return { vx: 0, vy: lateral, wz: 0 };
    case "right":
      return { vx: 0, vy: -lateral, wz: 0 };
    case "rotate-left":
      return { vx: 0, vy: 0, wz: angular };
    case "rotate-right":
      return { vx: 0, vy: 0, wz: -angular };
    default:
      return { vx: 0, vy: 0, wz: 0 };
  }
}

function renderCommand(vector) {
  commandVector.textContent = `vx ${vector.vx.toFixed(2)} · vy ${vector.vy.toFixed(2)} · ω ${vector.wz.toFixed(2)}`;
}

async function sendMotion(motion) {
  if (!motion || !motionEnabled()) return;
  motionAbortController?.abort();
  motionAbortController = new AbortController();
  const vector = vectorForMotion(motion);
  const seq = ++commandSequence;
  renderCommand(vector);

  try {
    await apiRequest("/api/motion", {
      method: "POST",
      body: JSON.stringify({ ...vector, session_id: browserSessionId, seq }),
      signal: motionAbortController.signal,
    });
    setConnection("online", "Robot online");
  } catch (error) {
    if (error.name === "AbortError") return;
    setConnection("offline", "Control offline");
    if (activeMotion) showToast(error.message || "Robot control service is unavailable");
    stopMotion(false);
  }
}

function startMotion(motion) {
  if (!motionEnabled()) return;
  if (motion === "stop") {
    stopMotion();
    return;
  }

  if (activeMotion === motion) return;
  stopMotion(false);
  activeMotion = motion;
  motionKeys.forEach((key) => key.classList.toggle("is-active", key.dataset.motion === motion));
  sendMotion(motion);
  motionInterval = window.setInterval(() => sendMotion(activeMotion), 50);
}

function stopMotion(notifyServer = true) {
  window.clearInterval(motionInterval);
  motionInterval = null;
  activeMotion = null;
  motionKeys.forEach((key) => key.classList.remove("is-active"));
  const zero = { vx: 0, vy: 0, wz: 0 };
  renderCommand(zero);

  if (notifyServer && motionEnabled() && controlToken) {
    motionAbortController?.abort();
    motionAbortController = null;
    const seq = ++commandSequence;
    apiRequest("/api/stop", {
      method: "POST",
      body: JSON.stringify({ session_id: browserSessionId, seq }),
      keepalive: true,
    }).catch(() => {});
  }
}

motionKeys.forEach((key) => {
  key.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    key.setPointerCapture?.(event.pointerId);
    startMotion(key.dataset.motion);
  });
  key.addEventListener("pointerup", () => stopMotion());
  key.addEventListener("pointercancel", () => stopMotion());
  key.addEventListener("lostpointercapture", () => stopMotion());
});

window.addEventListener("pointerup", () => {
  if (activeMotion) stopMotion();
});

document.addEventListener("keydown", (event) => {
  const consoleIsOpen = !consoleElement.hidden;

  if (event.key === "Escape") {
    if (consoleIsOpen) {
      closeConsole();
    } else if (menuToggle.getAttribute("aria-expanded") === "true") {
      setMenu(false);
      menuToggle.focus();
    }
    return;
  }

  if (!consoleIsOpen || event.target.matches("input, textarea, select")) return;
  const motion = keyBindings[event.key.toLowerCase()];
  if (!motion || event.repeat) return;
  event.preventDefault();
  startMotion(motion);
});

document.addEventListener("keyup", (event) => {
  if (!consoleElement.hidden && keyBindings[event.key.toLowerCase()]) {
    event.preventDefault();
    stopMotion();
  }
});

window.addEventListener("blur", () => stopMotion());
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopMotion();
});
window.addEventListener("pagehide", () => stopMotion());

window.addEventListener("resize", () => {
  if (window.innerWidth > 720 && menuToggle.getAttribute("aria-expanded") === "true") {
    setMenu(false);
  }
});

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return minutes ? `${minutes}m ${remainder}s` : `${remainder}s`;
}

function selectBag(bag, button) {
  if (!playbackEnabled()) return;
  selectedBagId = bag.id;
  selectedBagName.textContent = bag.name;
  bagList.querySelectorAll(".bag-item").forEach((item) => item.setAttribute("aria-selected", "false"));
  button.setAttribute("aria-selected", "true");
  bagPlay.disabled = !(bag.has_motion || bag.topic_count > 0);
}

function createBagItem(bag) {
  const button = document.createElement("button");
  const icon = document.createElement("span");
  const copy = document.createElement("span");
  const name = document.createElement("strong");
  const detail = document.createElement("span");
  const meta = document.createElement("span");

  button.type = "button";
  button.className = "bag-item";
  button.setAttribute("role", "option");
  button.setAttribute("aria-selected", String(bag.id === selectedBagId));
  icon.className = "bag-file-icon";
  icon.innerHTML = '<i class="fa-solid fa-wave-square" aria-hidden="true"></i>';
  copy.className = "bag-copy";
  name.textContent = bag.name;
  detail.textContent = bag.has_motion
    ? (bag.topic_count > 0
      ? `motion + ${bag.topic_count} safe topics · ${Number(bag.message_count || 0).toLocaleString()} messages`
      : `motion replay · ${Number(bag.message_count || 0).toLocaleString()} messages`)
    : `${bag.topic_count} safe topics · ${Number(bag.message_count || 0).toLocaleString()} messages`;
  meta.className = "bag-meta";
  meta.textContent = formatDuration(bag.duration_seconds);
  copy.append(name, detail);
  button.append(icon, copy, meta);
  button.addEventListener("click", () => selectBag(bag, button));
  return button;
}

async function loadBags() {
  if (!playbackEnabled()) return;
  bagList.innerHTML = '<div class="bag-empty"><i class="fa-solid fa-circle-notch fa-spin" aria-hidden="true"></i><span>Scanning recordings…</span></div>';
  refreshBagsButton.querySelector("i").classList.add("fa-spin");

  try {
    const payload = await apiRequest("/api/bags");
    bagList.replaceChildren();

    if (!payload.bags.length) {
      bagList.innerHTML = '<div class="bag-empty"><i class="fa-regular fa-folder-open" aria-hidden="true"></i><span>No rosbag recordings found</span></div>';
      return;
    }

    payload.bags.forEach((bag) => bagList.append(createBagItem(bag)));
  } catch (error) {
    bagList.innerHTML = '<div class="bag-empty"><i class="fa-solid fa-link-slash" aria-hidden="true"></i><span>Start Web/server.py to browse rosbags</span></div>';
  } finally {
    refreshBagsButton.querySelector("i").classList.remove("fa-spin");
  }
}

function updatePlayer(player) {
  playerState = player?.state || "idle";
  replayMode = player?.replay_mode || null;
  if (!playbackEnabled()) {
    bagStop.disabled = true;
    bagPause.disabled = true;
    bagPlay.disabled = true;
    refreshBagsButton.disabled = true;
    stopReplayHeartbeat();
    return;
  }
  const active = ["playing", "paused"].includes(playerState);
  const motionActive = active && replayMode === "motion";
  refreshBagsButton.disabled = false;
  bagStop.disabled = !active;
  bagPause.disabled = !active || motionActive;
  bagPlay.disabled = active || !selectedBagId;
  bagPause.innerHTML = playerState === "paused"
    ? '<i class="fa-solid fa-play" aria-hidden="true"></i>'
    : '<i class="fa-solid fa-pause" aria-hidden="true"></i>';
  bagPause.setAttribute(
    "aria-label",
    motionActive
      ? "Pause unavailable for motion replay"
      : (playerState === "paused" ? "Resume replay" : "Pause replay")
  );

  if (active && player.name) {
    selectedBagName.textContent = player.name;
    bagPlayState.textContent = motionActive
      ? "Replaying recorded motion"
      : (playerState === "paused" ? "Replay paused" : "Replaying safe topics");
  } else if (playerState === "error") {
    bagPlayState.textContent = player.error || "Replay failed";
  } else {
    bagPlayState.textContent = "Motion replay: /cmd_vel · allowlist: /chassis/debug · /tf · /wheel/odom";
  }

  if (playerState === "playing" && replayMode === "motion") {
    startReplayHeartbeat();
  } else {
    stopReplayHeartbeat();
  }
}

function startReplayHeartbeat() {
  if (replayHeartbeat || !playbackEnabled()) return;
  replayHeartbeat = window.setInterval(async () => {
    if (heartbeatInFlight) return;
    heartbeatInFlight = true;
    try {
      await apiRequest("/api/bags/heartbeat", { method: "POST", body: "{}" });
    } catch (error) {
      // The server-side dead-man stops the robot if heartbeats stop arriving.
    } finally {
      heartbeatInFlight = false;
    }
  }, REPLAY_HEARTBEAT_MS);
}

function stopReplayHeartbeat() {
  if (replayHeartbeat) {
    window.clearInterval(replayHeartbeat);
    replayHeartbeat = null;
  }
}

async function refreshStatus() {
  try {
    const payload = await apiRequest("/api/status");
    applyMode(payload.mode);
    if (payload.mode === "preview") {
      setConnection("checking", "Preview mode");
    } else if (payload.mode === "playback") {
      setConnection("online", "Isolated playback");
    } else if (payload.mode === "console" && payload.ros_available && payload.safety_subscribers > 0) {
      setConnection("online", "Robot + playback ready");
    } else if (payload.ros_available && payload.safety_subscribers > 0) {
      setConnection("online", "Robot online");
    } else if (payload.ros_available) {
      setConnection("checking", "Safety chain idle");
    } else {
      setConnection("offline", "ROS unavailable");
    }
    updatePlayer(payload.player);
  } catch (error) {
    setConnection("offline", "Control offline");
    updatePlayer({ state: "idle" });
  }
}

refreshBagsButton.addEventListener("click", loadBags);

bagPlay.addEventListener("click", async () => {
  if (!selectedBagId) return;
  try {
    const payload = await apiRequest("/api/bags/play", {
      method: "POST",
      body: JSON.stringify({ id: selectedBagId }),
    });
    updatePlayer(payload.player);
    showToast(
      payload.player?.replay_mode === "motion"
        ? "Live motion replay started — robot follows the recorded path"
        : "Rosbag replay started in isolated Domain 97 with telemetry allowlist"
    );
  } catch (error) {
    showToast(error.message);
  }
});

bagPause.addEventListener("click", async () => {
  const action = playerState === "paused" ? "resume" : "pause";
  try {
    const payload = await apiRequest(`/api/bags/${action}`, { method: "POST", body: "{}" });
    updatePlayer(payload.player);
  } catch (error) {
    showToast(error.message);
  }
});

bagStop.addEventListener("click", async () => {
  try {
    const payload = await apiRequest("/api/bags/stop", { method: "POST", body: "{}" });
    updatePlayer(payload.player);
  } catch (error) {
    showToast(error.message);
  }
});

const statValues = [...document.querySelectorAll(".stat-value")];
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function formatValue(element, value) {
  const decimals = Number(element.dataset.decimals);
  return `${value.toFixed(decimals)}${element.dataset.suffix}`;
}

function animateValue(element, index) {
  const target = Number(element.dataset.target);
  const duration = 1500 + index * 80;
  const startOffset = 480 + index * 90;

  if (reducedMotion) {
    element.textContent = formatValue(element, target);
    return;
  }

  window.setTimeout(() => {
    const startTime = performance.now();

    function update(now) {
      const progress = Math.min((now - startTime) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      element.textContent = formatValue(element, target * eased);
      if (progress < 1) requestAnimationFrame(update);
    }

    requestAnimationFrame(update);
  }, startOffset);
}

if ("IntersectionObserver" in window) {
  const statsObserver = new IntersectionObserver(
    (entries, observer) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      statValues.forEach(animateValue);
      observer.disconnect();
    },
    { threshold: 0.25 },
  );
  statsObserver.observe(document.querySelector(".stats"));
} else {
  statValues.forEach(animateValue);
}

if (window.location.hash === "#product") {
  window.setTimeout(() => openConsole(), 0);
}

initializeSession()
  .then(() => refreshStatus())
  .catch(() => {
    applyMode("preview");
    setConnection("offline", "Control offline");
  });
