// ── State ──
let faultTable = null;
let thresholds = {};
let ws = null;
let monitoring = false;

const API = '';  // same origin

// ── Tabs ──
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
  });
});

// ── Upload ──
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => { if (fileInput.files.length) uploadFile(fileInput.files[0]); });

async function uploadFile(file) {
  const status = document.getElementById('parse-status');
  const result = document.getElementById('parse-result');
  const msg = document.getElementById('parse-msg');

  status.classList.remove('hidden');
  result.classList.add('hidden');
  msg.textContent = `Parsing "${file.name}" with Docling + Gemini...`;

  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch(API + '/api/parse', { method: 'POST', body: form });
    if (!res.ok) throw new Error(await res.text());
    faultTable = await res.json();
    thresholds = faultTable.thresholds || {};
    showParseResult(faultTable);
    renderFaultTable(faultTable);
    // switch to monitor tab and load live sensor data
    document.querySelector('[data-tab="monitor"]').click();
    await refreshSensors();
    await refreshScenarios();
  } catch (err) {
    msg.textContent = 'Error: ' + err.message;
  }
}

function showParseResult(ft) {
  document.getElementById('parse-status').classList.add('hidden');
  document.getElementById('parse-result').classList.remove('hidden');
  document.getElementById('doc-title').textContent = ft.document_title;
  document.getElementById('doc-equipment').textContent = ft.equipment_type;
  document.getElementById('doc-model').textContent = ft.model;
  document.getElementById('doc-fault-count').textContent = ft.faults.length;

  // Equipment info in topbar
  const info = document.getElementById('equipment-info');
  info.classList.remove('hidden');
  document.getElementById('equip-type').textContent = ft.equipment_type;
  document.getElementById('equip-model').textContent = ft.model;

  // Thresholds
  const grid = document.getElementById('thresholds-grid');
  grid.innerHTML = '';
  for (const [name, t] of Object.entries(ft.thresholds)) {
    const parts = [];
    if (t.min !== null) parts.push(t.min);
    parts.push('–');
    if (t.max !== null) parts.push(t.max);
    grid.innerHTML += `<div class="threshold-chip">
      <div class="name">${name}</div>
      <div class="range">${t.min ?? '—'} – ${t.max ?? '—'} ${t.unit}</div>
    </div>`;
  }
}

// ── Fault Table ──
function renderFaultTable(ft) {
  const container = document.getElementById('fault-table-container');
  if (!ft || !ft.faults.length) {
    container.innerHTML = '<p class="placeholder-text">No faults extracted.</p>';
    return;
  }

  let html = `<table class="fault-table">
    <thead><tr>
      <th>Symptom</th><th>Possible Cause</th><th>Solution</th>
      <th>Sensor Hints</th><th>Actions</th>
    </tr></thead><tbody>`;

  for (const f of ft.faults) {
    const hints = (f.sensor_hints || []).map(h => `<span class="hint-chip">${h}</span>`).join('');
    const actions = (f.actionable_steps || []).map(a => `<span class="action-chip">${a}</span>`).join('');
    html += `<tr>
      <td>${f.symptom}</td>
      <td>${f.possible_cause}</td>
      <td>${f.solution}</td>
      <td>${hints}</td>
      <td>${actions}</td>
    </tr>`;
  }

  html += '</tbody></table>';
  container.innerHTML = html;
}

// ── Unit normalisation ──
// Convert a sensor value from its unit to the threshold's unit so comparisons work
function normalizeToThresholdUnit(value, sensorUnit, thresholdUnit) {
  const su = sensorUnit.toLowerCase().replace(/[()]/g, '');
  const tu = thresholdUnit.toLowerCase().replace(/[()]/g, '');
  if (su === tu) return value;
  // W ↔ kW
  if (su === 'w' && tu === 'kw') return value / 1000;
  if (su === 'kw' && tu === 'w') return value * 1000;
  // mA ↔ A
  if (su === 'ma' && tu === 'a') return value / 1000;
  if (su === 'a' && tu === 'ma') return value * 1000;
  // mV ↔ V
  if (su === 'mv' && tu === 'v') return value / 1000;
  if (su === 'v' && tu === 'mv') return value * 1000;
  // mm/s ↔ m/s
  if (su === 'mm/s' && tu === 'm/s') return value / 1000;
  if (su === 'm/s' && tu === 'mm/s') return value * 1000;
  // No conversion found — return as-is
  return value;
}

// ── Sensors ──
function renderSensors(snapshot) {
  const grid = document.getElementById('sensor-grid');
  // Only show sensors that have a real threshold (min or max) from the document
  const sensorNames = snapshot
    ? Object.keys(snapshot.readings).filter(name => {
        const t = thresholds[name];
        return t && (t.min !== null || t.max !== null);
      })
    : [];

  let html = '';
  for (const name of sensorNames) {
    const reading = snapshot.readings[name];
    const value = reading.value;
    const unit = reading.unit;
    const t = thresholds[name];

    let status = '';
    let pct = 0;
    let barColor = 'var(--green)';

    if (reading && t) {
      const nv = normalizeToThresholdUnit(reading.value, reading.unit, t.unit);
      if (t.max !== null) {
        pct = (nv / t.max) * 100;
        if (nv > t.max) { status = 'danger'; barColor = 'var(--red)'; }
        else if (pct > 80) { status = 'warning'; barColor = 'var(--yellow)'; }
        pct = Math.min(pct, 100);
      }
      if (t.min !== null && nv < t.min) {
        status = 'danger'; barColor = 'var(--red)'; pct = 0;
      }
    }

    const displayName = name.replace(/_/g, ' ');
    const thresholdText = t ? `Threshold: ${t.min ?? '—'} – ${t.max ?? '—'} ${t.unit}` : '';
    const displayValue = value.toFixed(1);

    html += `<div class="sensor-card ${status}">
      <div class="sensor-name">${displayName}</div>
      <div class="sensor-value">${displayValue} <span class="sensor-unit">${unit}</span></div>
      <div class="sensor-bar"><div class="sensor-bar-fill" style="width:${pct}%;background:${barColor}"></div></div>
      <div class="sensor-threshold">${thresholdText}</div>
    </div>`;
  }
  grid.innerHTML = html;

  // Out-of-bounds alert banner
  const alertContainer = document.getElementById('sensor-alerts') ||
    (() => { const d = document.createElement('div'); d.id = 'sensor-alerts'; grid.parentNode.insertBefore(d, grid); return d; })();

  if (!snapshot) { alertContainer.innerHTML = ''; return; }

  const outOfBounds = [];
  for (const name of sensorNames) {
    const reading = snapshot.readings[name];
    const t = thresholds[name];
    if (!reading || !t) continue;
    const nv = normalizeToThresholdUnit(reading.value, reading.unit, t.unit);
    if (t.max !== null && nv > t.max) outOfBounds.push(`${name.replace(/_/g, ' ')} (${nv.toFixed(1)} > ${t.max} ${t.unit})`);
    else if (t.min !== null && nv < t.min) outOfBounds.push(`${name.replace(/_/g, ' ')} (${nv.toFixed(1)} < ${t.min} ${t.unit})`);
  }

  if (outOfBounds.length) {
    alertContainer.innerHTML = `<div class="alert-banner">Out of bounds: ${outOfBounds.join(' · ')}</div>`;
  } else {
    alertContainer.innerHTML = '';
  }
}

// ── Fault Injection ──
document.getElementById('btn-inject').addEventListener('click', async () => {
  const scenario = document.getElementById('scenario-select').value;
  if (!scenario) return;
  await fetch(API + `/api/sensors/inject?scenario=${scenario}`, { method: 'POST' });
  refreshSensors();
});

document.getElementById('btn-clear').addEventListener('click', async () => {
  await fetch(API + '/api/sensors/inject', { method: 'DELETE' });
  document.getElementById('scenario-select').value = '';
  await refreshSensors();
  await runDiagnosis();
});

async function refreshSensors() {
  const res = await fetch(API + '/api/sensors');
  const snapshot = await res.json();
  renderSensors(snapshot);
  if (monitoring) checkThresholdCrossings(snapshot);
}

function checkThresholdCrossings(snapshot) {
  if (!snapshot || !snapshot.readings) return;

  // Compute which sensors are currently out of bounds
  const currentOOB = new Set();
  for (const [name, reading] of Object.entries(snapshot.readings)) {
    const t = thresholds[name];
    if (!t || (t.min === null && t.max === null)) continue;
    const nv = normalizeToThresholdUnit(reading.value, reading.unit, t.unit);
    if ((t.max !== null && nv > t.max) || (t.min !== null && nv < t.min)) {
      currentOOB.add(name);
    }
  }

  // Check if the set changed (sensor crossed a boundary in either direction)
  const changed = currentOOB.size !== _prevOutOfBounds.size ||
    [...currentOOB].some(s => !_prevOutOfBounds.has(s)) ||
    [..._prevOutOfBounds].some(s => !currentOOB.has(s));

  _prevOutOfBounds = currentOOB;

  if (changed) {
    const now = Date.now();
    if (now - _lastDiagTime >= _DIAG_COOLDOWN_MS) {
      _lastDiagTime = now;
      runDiagnosis();
    }
  }
}

async function refreshScenarios() {
  try {
    const res = await fetch(API + '/api/scenarios');
    if (!res.ok) return;
    const scenarios = await res.json();
    const select = document.getElementById('scenario-select');
    const current = select.value;
    select.innerHTML = '<option value="">-- Normal --</option>';
    for (const key of Object.keys(scenarios)) {
      const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      select.innerHTML += `<option value="${key}">${label}</option>`;
    }
    if (current && scenarios[current]) select.value = current;
  } catch (_) {}
}

// ── Diagnosis ──
document.getElementById('btn-diagnose').addEventListener('click', async () => {
  const panel = document.getElementById('diagnosis-panel');
  panel.innerHTML = '<div class="status-card"><div class="spinner"></div><span>Running diagnosis...</span></div>';

  try {
    const res = await fetch(API + '/api/diagnose', { method: 'POST' });
    const body = await res.json();
    if (!res.ok) {
      throw new Error(body.detail || 'Error');
    }
    const diag = body;
    renderDiagnosis(diag);
    refreshSensors();
  } catch (err) {
    panel.innerHTML = `<p class="placeholder-text">Error: ${err.message}</p>`;
  }
});

function renderDiagnosis(d) {
  const panel = document.getElementById('diagnosis-panel');

  const faultBadge = d.fault_detected
    ? '<span class="badge badge-fault">Fault Detected</span>'
    : '<span class="badge badge-ok">Normal</span>';

  const confBadge = `<span class="badge badge-${d.confidence}">${d.confidence}</span>`;

  let html = `<div class="diag-header">${faultBadge} ${confBadge}</div>`;

  if (d.fault_detected) {
    html += `
      <div class="diag-section">
        <div class="diag-label">Symptom</div>
        <div class="diag-text">${d.matched_symptom || '—'}</div>
      </div>
      <div class="diag-section">
        <div class="diag-label">Possible Cause</div>
        <div class="diag-text">${d.possible_cause || '—'}</div>
      </div>
      <div class="diag-section">
        <div class="diag-label">Solution</div>
        <div class="diag-text">${d.solution || '—'}</div>
      </div>
      <div class="diag-section camera-verification-section">
        <div class="diag-label">Physical Obstruction Check</div>
        <p class="camera-hint">Verify if a physical obstruction is contributing to this fault.</p>
        <button class="btn btn-scan" onclick="startCameraScan()">Scan for Obstructions</button>
        <div id="camera-result"></div>
      </div>`;
  }

  html += `
    <div class="diag-section">
      <div class="diag-label">Reasoning</div>
      <div class="diag-text">${d.reasoning}</div>
    </div>`;

  if (d.recommended_actions && d.recommended_actions.length) {
    const softwareActions = d.recommended_actions.filter(a => a.startsWith('software:'));
    const robotActions = d.recommended_actions.filter(a => a.startsWith('robot:'));
    const humanActions = d.recommended_actions.filter(a => a.startsWith('human:'));
    // Anything without a known prefix goes into software
    const otherActions = d.recommended_actions.filter(a => !a.startsWith('software:') && !a.startsWith('robot:') && !a.startsWith('human:'));

    if (softwareActions.length || otherActions.length) {
      const items = [...softwareActions, ...otherActions].map(a => {
        const label = a.replace(/^software:\s*/, '').replace(/_/g, ' ');
        return `<li class="action-software">${label}</li>`;
      }).join('');
      html += `<div class="diag-section">
        <div class="diag-label">Software Actions</div>
        <ul class="diag-actions">${items}</ul>
      </div>`;
    }

    if (robotActions.length) {
      html += `<div class="diag-section">
        <div class="diag-label">Robot Actions</div>
        <div class="robot-actions-list">
          ${robotActions.map((a, i) => {
            const label = a.replace(/^robot:\s*/, '').replace(/_/g, ' ');
            return `<div class="robot-action" id="robot-action-${i}">
              <span class="robot-action-label">${label}</span>
              <button class="btn btn-robot" onclick="executeRobotAction(${i}, '${label.replace(/'/g, "\\'")}', this)">Execute</button>
            </div>`;
          }).join('')}
        </div>
        <div id="robot-action-status"></div>
      </div>`;
    }

    if (humanActions.length) {
      html += `<div class="diag-section">
        <div class="diag-label">Human Actions Required</div>
        <div class="human-actions-list">
          ${humanActions.map((a, i) => {
            const label = a.replace(/^human:\s*/, '').replace(/_/g, ' ');
            return `<div class="human-action" id="human-action-${i}">
              <span class="human-action-label">${label}</span>
              <button class="btn btn-human" onclick="confirmHumanAction(${i}, this)">Mark Done</button>
            </div>`;
          }).join('')}
        </div>
        <div id="human-actions-status" class="human-actions-status"></div>
      </div>`;
      window._humanActionsTotal = humanActions.length;
      window._humanActionsDone = 0;
    }
  }

  if (d.sensor_evidence && Object.keys(d.sensor_evidence).length) {
    html += `<div class="diag-section">
      <div class="diag-label">Sensor Evidence</div>
      <div class="evidence-grid">
        ${Object.entries(d.sensor_evidence).map(([k, v]) =>
          `<div class="evidence-item"><span class="sensor">${k}</span>: ${v}</div>`
        ).join('')}
      </div>
    </div>`;
  }

  panel.innerHTML = html;
}

// ── Human Action Confirmation ──
async function confirmHumanAction(index, btn) {
  const card = document.getElementById(`human-action-${index}`);
  card.classList.add('done');
  btn.disabled = true;
  btn.textContent = 'Done';

  window._humanActionsDone++;
  const statusEl = document.getElementById('human-actions-status');

  if (window._humanActionsDone >= window._humanActionsTotal) {
    statusEl.innerHTML = '<div class="status-card"><div class="spinner"></div><span>All human actions confirmed. Re-running diagnosis...</span></div>';
    try {
      const res = await fetch(API + '/api/diagnose', { method: 'POST' });
      if (!res.ok) {
        let detail;
        try { detail = (await res.json()).detail; } catch (_) { detail = await res.text(); }
        throw new Error(detail || 'Error');
      }
      const diag = await res.json();
      renderDiagnosis(diag);
      refreshSensors();
    } catch (err) {
      statusEl.innerHTML = `<p class="placeholder-text">Re-diagnosis error: ${err.message}</p>`;
    }
  } else {
    statusEl.textContent = `${window._humanActionsDone}/${window._humanActionsTotal} actions confirmed`;
  }
}

// ── Robot Action Execution ──
let _robotPollId = null;

async function executeRobotAction(index, label, btn) {
  btn.disabled = true;
  btn.textContent = 'Running...';

  const statusEl = document.getElementById('robot-action-status');
  statusEl.innerHTML = '<div class="status-card"><div class="spinner"></div><span>Robot arm executing movement...</span></div>';

  try {
    const res = await fetch(API + '/api/robot-action?action=' + encodeURIComponent(label), { method: 'POST' });
    if (!res.ok) {
      const detail = (await res.json()).detail;
      throw new Error(detail);
    }
    // Poll for completion every 2s
    _robotPollId = setInterval(() => pollRobotStatus(index, btn), 2000);
  } catch (err) {
    statusEl.innerHTML = `<p class="placeholder-text">Error: ${err.message}</p>`;
    btn.disabled = false;
    btn.textContent = 'Execute';
  }
}

async function pollRobotStatus(index, btn) {
  try {
    const res = await fetch(API + '/api/robot-status');
    const status = await res.json();
    const statusEl = document.getElementById('robot-action-status');
    const card = document.getElementById(`robot-action-${index}`);

    if (status.state === 'completed') {
      clearInterval(_robotPollId);
      if (card) card.classList.add('done');
      btn.textContent = 'Done';
      statusEl.innerHTML = '<div class="status-card" style="color:var(--green)">Robot action completed successfully.</div>';
      await fetch(API + '/api/robot-action/reset', { method: 'POST' });
      // Re-run diagnosis after short delay
      setTimeout(runDiagnosis, 1500);
    } else if (status.state === 'error') {
      clearInterval(_robotPollId);
      btn.disabled = false;
      btn.textContent = 'Retry';
      statusEl.innerHTML = `<div class="status-card" style="color:var(--red)">Error: ${status.error_message}</div>`;
      await fetch(API + '/api/robot-action/reset', { method: 'POST' });
    }
    // If still "running", spinner stays
  } catch (_) {}
}

// ── Camera Verification Flow ──
async function startCameraScan() {
  const resultEl = document.getElementById('camera-result');
  resultEl.innerHTML = '<div class="status-card"><div class="spinner"></div><span>Scanning with depth camera...</span></div>';

  try {
    const res = await fetch(API + '/api/camera/scan', { method: 'POST' });
    if (!res.ok) {
      const body = await res.json();
      throw new Error(body.detail || 'Scan failed');
    }
    const scan = await res.json();
    renderCameraResult(scan, false);
  } catch (err) {
    resultEl.innerHTML = `<p class="placeholder-text">Camera error: ${err.message}</p>
      <button class="btn btn-scan" onclick="startCameraScan()" style="margin-top:8px">Retry Scan</button>`;
  }
}

function renderCameraResult(scan, isPostDeploy) {
  const resultEl = document.getElementById('camera-result');
  let html = `<div class="camera-image-container">
    <img src="data:image/jpeg;base64,${scan.image_base64}" alt="Camera scan" class="camera-image" />
  </div>`;

  if (scan.detected) {
    html += `<div class="camera-status camera-status-found">
      <span class="badge badge-fault">${scan.obstacle_count} obstruction${scan.obstacle_count > 1 ? 's' : ''} detected</span>`;
    for (const obs of scan.obstacles) {
      html += `<div class="obstacle-detail">${obs.distance_m.toFixed(2)}m away &middot; ${obs.width}&times;${obs.height}px</div>`;
    }
    if (isPostDeploy) {
      html += `<p class="camera-msg">Obstruction still present after robot action.</p>
        <button class="btn btn-scan" onclick="startCameraScan()">Re-scan</button>
        <button class="btn btn-deploy" onclick="deployWithVerification()">Retry Automated Solution</button>`;
    } else {
      html += `<button class="btn btn-deploy" onclick="deployWithVerification()">Deploy Automated Solution</button>`;
    }
    html += `</div>`;
  } else {
    html += `<div class="camera-status camera-status-clear">
      <span class="badge badge-ok">No obstructions detected</span>`;
    if (isPostDeploy) {
      html += `<p class="camera-msg">Obstruction successfully cleared.</p>`;
      setTimeout(runDiagnosis, 1000);
    } else {
      html += `<p class="camera-msg">Area is clear. No physical obstruction found.</p>`;
    }
    html += `</div>`;
  }

  resultEl.innerHTML = html;
}

async function deployWithVerification() {
  const resultEl = document.getElementById('camera-result');
  resultEl.innerHTML = '<div class="status-card"><div class="spinner"></div><span>Deploying automated solution (~50s)...</span></div>';

  try {
    const res = await fetch(API + '/api/robot-action?action=clear_obstruction', { method: 'POST' });
    if (!res.ok) {
      const body = await res.json();
      throw new Error(body.detail || 'Failed to start');
    }

    const pollId = setInterval(async () => {
      try {
        const statusRes = await fetch(API + '/api/robot-status');
        const status = await statusRes.json();

        if (status.state === 'completed') {
          clearInterval(pollId);
          await fetch(API + '/api/robot-action/reset', { method: 'POST' });
          resultEl.innerHTML = '<div class="status-card"><div class="spinner"></div><span>Action complete. Verifying with camera...</span></div>';
          setTimeout(async () => {
            try {
              const scanRes = await fetch(API + '/api/camera/scan', { method: 'POST' });
              if (!scanRes.ok) throw new Error('Verification scan failed');
              const scan = await scanRes.json();
              renderCameraResult(scan, true);
            } catch (err) {
              resultEl.innerHTML = `<p class="placeholder-text">Verification error: ${err.message}</p>`;
            }
          }, 1500);
        } else if (status.state === 'error') {
          clearInterval(pollId);
          await fetch(API + '/api/robot-action/reset', { method: 'POST' });
          resultEl.innerHTML = `<div class="status-card" style="color:var(--red)">Robot error: ${status.error_message}</div>
            <button class="btn btn-deploy" onclick="deployWithVerification()">Retry</button>`;
        }
      } catch (_) {}
    }, 2000);
  } catch (err) {
    resultEl.innerHTML = `<p class="placeholder-text">Error: ${err.message}</p>`;
  }
}

// ── Monitoring ──
const btnMonitor = document.getElementById('btn-monitor');
let _sensorPollId = null;
let _prevOutOfBounds = new Set();
let _lastDiagTime = 0;
const _DIAG_COOLDOWN_MS = 10000;  // min 10s between threshold-triggered diagnoses

btnMonitor.addEventListener('click', () => {
  if (monitoring) {
    stopMonitoring();
  } else {
    startMonitoring();
  }
});

function startMonitoring() {
  monitoring = true;
  btnMonitor.textContent = 'Stop Monitoring';
  btnMonitor.classList.add('active');
  _prevOutOfBounds = new Set();
  _lastDiagTime = 0;
  // Sensor polling is already running — monitoring just enables threshold-triggered diagnosis
}

async function runDiagnosis() {
  try {
    const res = await fetch(API + '/api/diagnose', { method: 'POST' });
    if (!res.ok) return;
    const diag = await res.json();
    renderDiagnosis(diag);
  } catch (_) {}
}

function stopMonitoring() {
  monitoring = false;
  btnMonitor.textContent = 'Start Monitoring';
  btnMonitor.classList.remove('active');
}

// ── Init: try to load existing fault table, then start live sensor polling ──
(async () => {
  try {
    const res = await fetch(API + '/api/fault-table');
    if (res.ok) {
      faultTable = await res.json();
      thresholds = faultTable.thresholds || {};
      showParseResult(faultTable);
      renderFaultTable(faultTable);
    }
  } catch (_) {}

  // Always try to render sensors and load scenarios
  try { await refreshSensors(); } catch (_) {}
  try { await refreshScenarios(); } catch (_) {}

  // Always poll sensors every 1s for live hardware updates
  _sensorPollId = setInterval(refreshSensors, 1000);
})();
