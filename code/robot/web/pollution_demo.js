(() => {
  const panel = document.querySelector("#pollution-demo-panel");
  if (!panel) return;

  const scenarioSelect = document.querySelector("#pollution-scenario");
  const stepInput = document.querySelector("#pollution-step");
  const stepLabel = document.querySelector("#pollution-step-label");
  const previousButton = document.querySelector("#pollution-prev");
  const nextButton = document.querySelector("#pollution-next");
  const playButton = document.querySelector("#pollution-play");
  const map = document.querySelector("#pollution-map");
  const detail = document.querySelector("#pollution-region-detail");
  const values = Object.fromEntries(
    [...panel.querySelectorAll("[data-pollution-value]")].map((element) => [element.dataset.pollutionValue, element]),
  );
  const metricQuery = "current_risk";
  const metricFormat = {
    pm25: (value) => Number(value).toFixed(1),
    voc: (value) => Number(value).toFixed(3),
    co2: (value) => Number(value).toFixed(0),
    temperature: (value) => Number(value).toFixed(1),
    humidity: (value) => Number(value).toFixed(1),
    current_risk: (value) => Number(value).toFixed(1),
    predicted_risk: (value) => Number(value).toFixed(1),
  };

  const state = {
    enabled: false,
    loaded: false,
    scenarios: [],
    scenarioId: null,
    step: 0,
    snapshot: null,
    selectedRegion: "A",
    timer: null,
    requestId: 0,
    loading: false,
  };

  function riskColor(value) {
    if (value >= 70) return "#ef4444";
    if (value >= 40) return "#facc15";
    return "#22c55e";
  }

  function setDetail(message) {
    detail.textContent = message;
  }

  async function getJson(path) {
    const response = await fetch(path, { method: "GET", cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `污染演示请求失败 (${response.status})`);
    return payload;
  }

  function currentScenario() {
    return state.scenarios.find((scenario) => scenario.scenario_id === state.scenarioId) || null;
  }

  function updateStepControls() {
    const scenario = currentScenario();
    const maximum = Math.max(0, Number(scenario?.steps || 1) - 1);
    stepInput.max = String(maximum);
    stepInput.value = String(Math.min(state.step, maximum));
    stepLabel.textContent = `${state.step} / ${maximum}`;
    previousButton.disabled = state.step <= 0;
    nextButton.disabled = state.step >= maximum;
  }

  function renderMetrics(record) {
    Object.entries(metricFormat).forEach(([metric, formatter]) => {
      if (values[metric] && record[metric] !== undefined) values[metric].textContent = formatter(record[metric]);
    });
  }

  function renderMap(snapshot) {
    map.replaceChildren();
    snapshot.regions.forEach((record) => {
      const region = document.createElement("button");
      const risk = Number(record.current_risk);
      region.type = "button";
      region.className = "pollution-region";
      region.dataset.regionId = record.region_id;
      region.setAttribute("aria-label", `${record.region_id} 区域，当前风险 ${risk.toFixed(1)}`);
      region.setAttribute("aria-pressed", String(record.region_id === state.selectedRegion));
      region.style.setProperty("--region-color", riskColor(risk));
      const name = document.createElement("strong");
      const riskLabel = document.createElement("span");
      name.textContent = record.region_id;
      riskLabel.textContent = `风险 ${risk.toFixed(1)}`;
      region.append(name, riskLabel);
      region.addEventListener("click", () => {
        state.selectedRegion = record.region_id;
        renderMap(snapshot);
        renderSelectedRegion(snapshot);
      });
      map.append(region);
    });
  }

  function renderSelectedRegion(snapshot) {
    const record = snapshot.regions.find((item) => item.region_id === state.selectedRegion) || snapshot.regions[0];
    if (!record) return;
    state.selectedRegion = record.region_id;
    const scenario = currentScenario();
    setDetail(
      `${scenario?.title || snapshot.scenario_id} · ${scenario?.description || ""}  `
      + `当前区域：${record.region_id} · 坐标 (${record.x}, ${record.y}) · `
      + `PM2.5 ${metricFormat.pm25(record.pm25)} · VOC ${metricFormat.voc(record.voc)} · `
      + `当前风险 ${metricFormat.current_risk(record.current_risk)} · 预测风险 ${metricFormat.predicted_risk(record.predicted_risk)}`,
    );
  }

  function renderSnapshot(snapshot) {
    state.snapshot = snapshot;
    updateStepControls();
    renderMap(snapshot);
    const selected = snapshot.regions.find((record) => record.region_id === state.selectedRegion) || snapshot.regions[0];
    if (selected) renderMetrics(selected);
    renderSelectedRegion(snapshot);
  }

  async function loadSnapshot() {
    if (!state.enabled || !state.scenarioId) return;
    const scenario = currentScenario();
    if (!scenario || !Number.isInteger(state.step) || state.step < 0 || state.step >= scenario.steps) return;
    const requestId = ++state.requestId;
    const query = new URLSearchParams({
      scenario_id: state.scenarioId,
      step: String(state.step),
      metric: metricQuery,
    });
    try {
      const snapshot = await getJson(`/api/pollution/snapshot?${query.toString()}`);
      if (requestId === state.requestId) renderSnapshot(snapshot);
    } catch (error) {
      if (requestId === state.requestId) setDetail(error.message || "污染演示数据不可用");
    }
  }

  async function loadCatalog() {
    if (!state.enabled || state.loaded || state.loading) return;
    state.loading = true;
    try {
      const payload = await getJson("/api/pollution/catalog");
      state.scenarios = Array.isArray(payload.scenarios) ? payload.scenarios : [];
      scenarioSelect.replaceChildren();
      state.scenarios.forEach((scenario) => {
        const option = document.createElement("option");
        option.value = scenario.scenario_id;
        option.textContent = scenario.title;
        scenarioSelect.append(option);
      });
      state.scenarioId = state.scenarios[0]?.scenario_id || null;
      state.step = 0;
      state.loaded = true;
      updateStepControls();
      await loadSnapshot();
    } catch (error) {
      setDetail(error.message || "污染演示目录不可用");
    } finally {
      state.loading = false;
    }
  }

  function stopPlayback() {
    if (state.timer) window.clearInterval(state.timer);
    state.timer = null;
    playButton.textContent = "播放";
  }

  function advanceStep() {
    const scenario = currentScenario();
    const maximum = Math.max(0, Number(scenario?.steps || 1) - 1);
    if (state.step >= maximum) {
      stopPlayback();
      return;
    }
    state.step += 1;
    updateStepControls();
    loadSnapshot();
  }

  function startPlayback() {
    if (state.timer) {
      stopPlayback();
      return;
    }
    playButton.textContent = "暂停";
    state.timer = window.setInterval(advanceStep, 1000);
  }

  scenarioSelect.addEventListener("change", () => {
    if (!state.scenarios.some((scenario) => scenario.scenario_id === scenarioSelect.value)) return;
    stopPlayback();
    state.scenarioId = scenarioSelect.value;
    state.step = 0;
    state.selectedRegion = "A";
    updateStepControls();
    loadSnapshot();
  });
  stepInput.addEventListener("input", () => {
    const parsed = Number(stepInput.value);
    if (!Number.isInteger(parsed) || parsed < 0) return;
    state.step = parsed;
    updateStepControls();
    loadSnapshot();
  });
  previousButton.addEventListener("click", () => {
    if (state.step <= 0) return;
    state.step -= 1;
    updateStepControls();
    loadSnapshot();
  });
  nextButton.addEventListener("click", advanceStep);
  playButton.addEventListener("click", startPlayback);

  window.PollutionDemo = {
    setEnabled(enabled) {
      const nextEnabled = Boolean(enabled);
      state.enabled = nextEnabled;
      panel.hidden = !state.enabled;
      if (!state.enabled) stopPlayback();
      if (state.enabled) loadCatalog();
    },
    loadCatalog,
  };
})();
