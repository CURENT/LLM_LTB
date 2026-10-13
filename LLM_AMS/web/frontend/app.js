"use strict";

// ---------------------------------------------------------------- API helpers
const API = {
  routines: () => fetch("api/routines").then(j),
  cases: () => fetch("api/cases").then(j),
  solvers: (r) => fetch(`api/solvers/${encodeURIComponent(r)}`).then(j),
  formulation: (r) => fetch(`api/formulation/${encodeURIComponent(r)}`).then(j),
  case: (r, c) => fetch(`api/case?routine=${encodeURIComponent(r)}&case=${encodeURIComponent(c)}`).then(j),
  solve: (body) => post("api/solve", body),
  weekBuild: (body) => post("api/week/build", body),
  report: (body) => post("api/report", body),
  chat: (body) => post("api/chat", body),
  llm: () => fetch("api/llm").then(j),
  setLlm: (body) => post("api/llm", body),
  parameters: (r, c) => fetch(`api/parameters?routine=${encodeURIComponent(r)}&case=${encodeURIComponent(c)}`).then(j),
  applyParameter: (body) => post("api/parameters/apply", body),
  promptParameter: (body) => post("api/parameters/prompt", body),
  resetParameters: (body) => post("api/parameters/reset", body),
};

async function j(resp) {
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return resp.json();
}

function post(url, body) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  }).then(j);
}

// ---------------------------------------------------------------- state
const state = {
  routine: null,
  case: null,
  solver: null,
  provider: null,
  model: null,
  busy: false,
  solved: false,       // a solve has run for the current routine/case
  reportOpen: false,
  parameters: null,
};

// ---------------------------------------------------------------- DOM utils
const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function setStatus(text, kind) {
  const el = $("status");
  el.textContent = text;
  el.className = "status" + (kind ? " " + kind : "");
}

async function typeset(el) {
  if (!window.MathJax || !window.MathJax.typesetPromise) return;
  try {
    await window.MathJax.typesetPromise([el]);
  } catch (err) {
    console.error("MathJax typeset failed", err);
  }
}

// ---------------------------------------------------------------- sidebar
function renderRoutines(groups) {
  const list = $("routine-list");
  list.innerHTML = "";
  for (const g of groups) {
    const title = document.createElement("div");
    title.className = "routine-group-title";
    title.innerHTML = `<span class="tag">${esc(g.category)}</span> ${esc(g.name)}`;
    list.appendChild(title);

    for (const r of g.routines) {
      const btn = document.createElement("button");
      btn.className = "routine-btn";
      btn.dataset.routine = r.name;
      btn.innerHTML = r.hasFormulation
        ? `<span>${esc(r.name)}</span>`
        : `<span>${esc(r.name)}</span><span class="no-formula">no eqs</span>`;
      btn.addEventListener("click", () => selectRoutine(r.name));
      list.appendChild(btn);
    }
  }
}

function highlightRoutine(name) {
  document.querySelectorAll(".routine-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.routine === name);
  });
}

function renderCases(cases, def) {
  const sel = $("case-select");
  sel.innerHTML = "";
  for (const c of cases) {
    const opt = document.createElement("option");
    opt.value = c.alias;
    opt.textContent = c.label;
    sel.appendChild(opt);
  }
  sel.value = def;
  sel.addEventListener("change", () => {
    state.case = sel.value;
    refresh();
  });
}

// ---------------------------------------------------------------- week profile upload
// Reads the chosen profile.csv in the browser and posts its text; the backend builds an
// N-slot case (generated/week/<id>.xlsx), registers it in the case picker and loads it.
function initWeekUpload() {
  const file = $("profile-file"), btn = $("week-build-btn"), note = $("week-note");
  if (!file || !btn) return;
  const sync = () => { btn.disabled = !file.files.length; };
  file.addEventListener("change", sync);
  sync();                                   // a file may already be selected (page restored)
  btn.addEventListener("click", async () => {
    if (!file.files.length || state.busy) return;
    const f = file.files[0];
    const text = await f.text();
    const stem = f.name.replace(/\.csv$/i, "").replace(/[^A-Za-z0-9_.-]/g, "_");
    state.busy = true; btn.disabled = true;
    setStatus(`building case from ${f.name}…`);
    note.className = "control-note";
    note.textContent = "Building…";
    try {
      const r = await API.weekBuild({ profile_csv: text, base: state.case, case_id: `${state.case}_${stem}` });
      const sel = $("case-select");
      if (![...sel.options].some((o) => o.value === r.case_id)) {
        const opt = document.createElement("option");
        opt.value = r.case_id; opt.textContent = r.label;
        sel.appendChild(opt);
      }
      sel.value = r.case_id;
      state.case = r.case_id;
      note.textContent = `Built ${r.case_id}: ${r.n_slots} slots` +
        (r.curve_sheets.length ? `, per-load sheets ${r.curve_sheets.join("/")}` : ", native tables only") +
        `. Now choose ED or UC and press Run solve.`;
      setStatus(`built ${r.case_id} (${r.n_slots} slots)`, "ok");
      state.busy = false;
      await refresh();
    } catch (err) {
      note.className = "control-note warn";
      note.textContent = `Build failed: ${err.message}`;
      setStatus(`error: ${err.message}`, "err");
      state.busy = false;
    } finally {
      btn.disabled = !file.files.length;
    }
  });
}

// ---------------------------------------------------------------- LLM provider / model
// Mirrors the terminal's provider prompt: pick Ollama (local) or OpenAI (needs
// an API key), then a model. Changing either rebuilds the chat agent.
let llmData = null;

function renderLlm(payload) {
  llmData = payload;
  state.provider = payload.provider;
  state.model = payload.model;

  const provSel = $("provider-select");
  if (!provSel) return;
  provSel.innerHTML = "";
  for (const p of payload.providers || []) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.label + (p.available ? "" : " — unavailable");
    provSel.appendChild(opt);
  }
  provSel.value = payload.provider;
  renderModelOptions();
}

function renderModelOptions() {
  const provSel = $("provider-select");
  const modelSel = $("model-select");
  if (!provSel || !modelSel || !llmData) return;
  const p = (llmData.providers || []).find((x) => x.id === provSel.value);
  const models = (p && p.models) || [];
  modelSel.innerHTML = "";
  if (models.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = p && p.available ? "(no models)" : "(unavailable)";
    modelSel.appendChild(opt);
    modelSel.disabled = true;
  } else {
    modelSel.disabled = false;
    for (const m of models) {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      modelSel.appendChild(opt);
    }
    // keep the active model when still on the same provider, else the default
    const desired = provSel.value === state.provider && state.model
      ? state.model : (p && p.default);
    modelSel.value = models.includes(desired) ? desired : models[0];
  }
  updateLlmNote();
}

function updateLlmNote() {
  const note = $("llm-note");
  const provSel = $("provider-select");
  if (!note || !provSel || !llmData) return;
  const p = (llmData.providers || []).find((x) => x.id === provSel.value);
  if (p && !p.available) {
    note.textContent = p.id === "openai"
      ? "Set OPENAI_API_KEY in .env, then reselect."
      : "Ollama not reachable at localhost:11434.";
    note.className = "control-note warn";
  } else {
    note.textContent = `Chat agent: ${provSel.value} · ${$("model-select").value || "—"}`;
    note.className = "control-note";
  }
}

async function applyLlm() {
  const provider = $("provider-select").value;
  const model = $("model-select").value || null;
  if (provider === state.provider && model === state.model) return;
  setStatus(`switching assistant to ${provider}…`);
  try {
    const payload = await API.setLlm({ provider, model });
    renderLlm(payload);
    setStatus(`assistant: ${payload.provider} · ${payload.model}`, "ok");
  } catch (err) {
    setStatus(`error: ${err.message}`, "err");
  }
}

// ---------------------------------------------------------------- formulation
function renderFormulation(f) {
  $("f-title").textContent = f.title || f.routine;
  $("f-subtitle").innerHTML = f.subtitle ? esc(f.subtitle) : "";

  const body = $("f-body");
  body.innerHTML = "";

  if (!f.sections || f.sections.length === 0) {
    body.innerHTML = `<p class="empty-note">No symbolic formulation is registered for ${esc(f.routine)}.</p>`;
    return;
  }

  for (const section of f.sections) {
    const sec = document.createElement("div");
    sec.className = "f-section";

    const head = document.createElement("div");
    head.className = "f-section-head";
    // section headings may contain inline math (e.g. "solve for $V_j$")
    head.innerHTML = esc(section.heading);
    sec.appendChild(head);

    const isVars = /variable/i.test(section.heading);
    let counter = 0;
    const numbered = /constraint/i.test(section.heading) && section.items.length > 1;

    for (const item of section.items) {
      if (isVars) {
        const row = document.createElement("div");
        row.className = "var-row";
        // symbol as inline math, description (may contain $...$) on the right
        row.innerHTML =
          `<div class="var-sym">\\(${item.latex}\\)</div>` +
          `<div class="var-desc">${esc(item.desc || "")}</div>`;
        sec.appendChild(row);
      } else {
        const row = document.createElement("div");
        row.className = "eq-row" + (numbered ? " numbered" : "");
        counter += 1;
        const eq = `<div class="eq-display">\\[${item.latex}\\]</div>` +
          (item.desc ? `<div class="eq-desc">${esc(item.desc)}</div>` : "");
        row.innerHTML = numbered ? `<div class="eq-num">(${counter})</div><div>${eq}</div>` : eq;
        sec.appendChild(row);
      }
    }
    body.appendChild(sec);
  }

  // typeset the whole card so the subtitle's inline math ($...$) renders too
  typeset($("formulation-card"));
}

// ---------------------------------------------------------------- case data
function renderCase(payload) {
  $("c-label").textContent = payload.snapshot.label || "Case data";

  const info = payload.info || {};
  const meta = $("c-meta");
  meta.innerHTML = "";
  if (info.loaded) {
    const pills = [
      ["case", payload.case],
      ["routine", payload.routine],
      ["buses", info.n_bus],
      ["lines", info.n_line],
      ["loads", info.n_pq],
      ["gens", info.n_staticgen],
    ];
    meta.innerHTML =
      `<div class="meta-pills">` +
      pills.map(([k, v]) => `<span class="pill">${esc(k)} ${esc(v)}</span>`).join("") +
      `</div>`;
  }

  const body = $("c-body");
  body.innerHTML = "";
  const tables = (payload.snapshot && payload.snapshot.tables) || [];
  if (tables.length === 0) {
    body.innerHTML = `<p class="empty-note">No data tables for this routine.</p>`;
    return;
  }
  for (const t of tables) {
    const wrap = document.createElement("div");
    wrap.className = "data-table-wrap";
    const head = `<thead><tr>${t.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>`;
    const rows = t.rows
      .map((r) => `<tr>${r.map((cell) => `<td>${esc(cell)}</td>`).join("")}</tr>`)
      .join("");
    wrap.innerHTML =
      `<p class="data-table-title">${esc(t.title)}</p>` +
      `<table class="data-table">${head}<tbody>${rows}</tbody></table>`;
    body.appendChild(wrap);
  }
}

// ---------------------------------------------------------------- parameters panel
function optionLabel(item, fields) {
  return fields
    .map((f) => item[f] !== undefined && item[f] !== null ? `${f}=${typeof item[f] === "number" ? Number(item[f].toPrecision(7)) : item[f]}` : "")
    .filter(Boolean)
    .join(" · ");
}

function renderParamSelect(id, items, fields) {
  return `<select id="${id}">` + (items || []).map((item) => {
    const idx = item.idx;
    return `<option value="${esc(idx)}">${item.label ? `${esc(item.label)} (${esc(idx)})` : `${esc(idx)} — ${esc(optionLabel(item, fields))}`}</option>`;
  }).join("") + `</select>`;
}

function renderOptionalSelect(id, items, fields, noneLabel = "None") {
  return `<select id="${id}"><option value="">${esc(noneLabel)}</option>` +
    (items || []).map((item) => {
      const idx = item.idx;
      return `<option value="${esc(idx)}">${esc(idx)} — ${esc(optionLabel(item, fields))}</option>`;
    }).join("") + `</select>`;
}

function infoIcon(text) {
  return `<span class="param-info" title="${esc(text)}">i</span>`;
}

const parameterRanges = new Map();
const signedPercent = value => `${Number(value) >= 0 ? "+" : ""}${Number(value)}%`;

function renderParameters(payload) {
  state.parameters = payload;
  const body = $("parameters-body");
  if (!body) return;
  const opts = payload.options || {};
  const controls = payload.available_controls || (payload.config?.controls || []).filter(c => c.enabled !== false &&
    (!c.requires_constraints || (opts.constraints || []).some(r => c.requires_constraints.includes(r.idx))) &&
    (!c.routines || c.routines.includes(payload.routine || state.routine)));
  if (!controls.length) {
    body.innerHTML = `<p class="empty-note">No parameter controls are available for ${esc(payload.routine || state.routine)} on this case.</p>`;
    return;
  }
  body.innerHTML = controls.map(c => {
    const saved = payload.values?.[c.id] || {};
    const emptySource = Object.values(c.parameters).some(s => s.source && !(opts[s.source] || []).length);
    const unavailable = c.available === false || emptySource;
    const fields = Object.entries(c.parameters).map(([name, spec]) => {
      const id = `param-${c.id}-${name}`;
      const value = saved[name] ?? spec.default ?? "";
      let input;
      if (spec.type === "select") {
        const rows = opts[spec.source] || [];
        input = renderParamSelect(id, rows, Object.keys(rows[0] || {}).filter(k => k !== "idx"));
        input += `<div class="parameter-current" id="${id}-current"></div>`;
      } else if (spec.type === "boolean") {
        input = `<input id="${id}" type="checkbox" ${value ? "checked" : ""}>`;
      } else {
        const bounds = parameterRanges.get(id) || { min: spec.slider_min ?? spec.min, max: spec.slider_max ?? spec.max };
        if (spec.type === "range") {
          bounds.min = Math.min(bounds.min, Number(value));
          bounds.max = Math.max(bounds.max, Number(value));
          parameterRanges.set(id, bounds);
        }
        input = `<input id="${id}" type="${spec.type === "range" ? "range" : "number"}" value="${esc(value)}"
          ${(spec.type === "range" ? bounds.min : spec.min) !== undefined ? `min="${spec.type === "range" ? bounds.min : spec.min}"` : ""}
          ${(spec.type === "range" ? bounds.max : spec.max) !== undefined ? `max="${spec.type === "range" ? bounds.max : spec.max}"` : ""}
          step="${spec.step || "any"}">`;
        if (spec.type === "range") {
          input += `<output for="${id}">${signedPercent(value)}</output><div class="parameter-range-bounds">` +
            ["min", "max"].map(bound => `<label for="${id}-${bound}">${bound === "min" ? "Min" : "Max"} (%)
              <input id="${id}-${bound}" type="number" required step="${spec.step || "any"}" value="${bounds[bound]}"
                ${spec.min !== undefined ? `min="${spec.min}"` : ""} ${spec.max !== undefined ? `max="${spec.max}"` : ""}></label>`).join("") +
            `</div><span id="${id}-error" class="parameter-range-error" role="alert"></span>`;
        }
      }
      return `<div class="param-field"><label for="${id}">${esc(spec.label || name.replaceAll("_", " "))} ${esc(spec.unit || "")}</label>${input}</div>`;
    }).join("");
    return `<fieldset data-operation="${c.id}" ${unavailable ? "disabled" : ""}>
      <legend>${esc(c.label)} ${infoIcon(c.description)}</legend>
      ${unavailable ? `<p class="control-note">${esc(c.unavailable_reason || "No eligible devices in this case.")}</p>` : ""}${fields}</fieldset>`;
  }).join("");
  controls.forEach(c => {
    const fieldset = body.querySelector(`[data-operation="${c.id}"]`);
    Object.entries(c.parameters).forEach(([name, spec]) => {
      const input = $(`param-${c.id}-${name}`);
      if (spec.type === "select" && (opts[spec.source] || []).some(r => String(r.idx) === String(payload.values?.[c.id]?.[name]))) input.value = payload.values[c.id][name];
      const syncLive = () => {
        const [key, source] = Object.entries(c.parameters).find(([, s]) => s.type === "select") || [];
        const row = key && (opts[source.source] || []).find(r => r.idx === $(`param-${c.id}-${key}`).value);
        if (!row) return;
        $(`param-${c.id}-${key}-current`).textContent = row.label
          ? `${row.label} (${row.idx}) - ${row.enabled ? "Enabled" : "Disabled"}`
          : optionLabel(row, Object.keys(row).filter(k => k !== "idx"));
        const live = {online: Boolean(row.u), enabled: row.enabled, rate_a: row.rate_a};
        for (const [field, value] of Object.entries(live)) {
          const el = $(`param-${c.id}-${field}`);
          if (el && value !== undefined) { if (el.type === "checkbox") el.checked = value; else el.value = value; }
        }
      };
      if (spec.type === "select") { syncLive(); input.addEventListener("change", syncLive); }
      if (spec.type === "range") {
        const minInput = $(`${input.id}-min`);
        const maxInput = $(`${input.id}-max`);
        const updateRange = () => {
          minInput.setCustomValidity("");
          maxInput.setCustomValidity("");
          const min = minInput.valueAsNumber;
          const max = maxInput.valueAsNumber;
          const valid = minInput.checkValidity() && maxInput.checkValidity() && min < max;
          const error = valid ? "" : `Min must be less than Max; use valid ${spec.step || "numeric"}-step bounds${spec.min !== undefined ? ` at or above ${spec.min}%` : ""}.`;
          $(`${input.id}-error`).textContent = error;
          if (!valid) { minInput.setCustomValidity(error); return; }
          const before = input.value;
          input.min = min;
          input.max = max;
          parameterRanges.set(input.id, { min, max });
          input.nextElementSibling.textContent = signedPercent(input.value);
          if (input.value !== before) fieldset.dataset.dirty = "true";
        };
        minInput.addEventListener("input", updateRange);
        maxInput.addEventListener("input", updateRange);
      }
      input.addEventListener("input", () => {
        fieldset.dataset.dirty = "true";
        if (spec.type === "range") input.nextElementSibling.textContent = signedPercent(input.value);
      });
      input.addEventListener("change", () => { fieldset.dataset.dirty = "true"; });
    });
  });
}

async function applyParameterForm() {
  if (state.busy || !state.parameters) return;
  for (const input of $("parameters-body").querySelectorAll(".parameter-range-bounds input")) {
    if (!input.reportValidity()) return;
  }
  state.busy = true;
  setStatus("applying parameter changes...");
  const actions = [];
  for (const c of state.parameters.available_controls || state.parameters.config.controls) {
    const form = document.querySelector(`[data-operation="${c.id}"]`);
    if (!form || form.disabled || form.dataset.dirty !== "true") continue;
    const parameters = {};
    for (const [name, spec] of Object.entries(c.parameters)) {
      const el = $(`param-${c.id}-${name}`);
      parameters[name] = spec.type === "boolean" ? el.checked :
        ["range", "number"].includes(spec.type) ? (el.value === "" ? null : Number(el.value)) : el.value;
    }
    actions.push({ operation: c.id, parameters });
  }
  if (!actions.length) {
    setStatus("no parameter changes selected", "ok");
    releaseBusy();
    return;
  }
  try {
    const payload = await post("api/parameters/batch", { actions, routine: state.routine, case: state.case });
    if (payload.result.ok) renderParameters(payload.parameters);
    await refreshAfterParameter(payload.result);
  } catch (err) {
    setStatus(err.message, "err");
  } finally {
    releaseBusy();
  }
}

async function resetParameters() {
  if (state.busy) return;
  state.busy = true;
  setStatus("resetting parameters…");
  try {
    const payload = await API.resetParameters({ routine: state.routine, case: state.case });
    renderParameters(payload.parameters);
    await refreshAfterParameter(payload.result);
  } catch (err) {
    setStatus(`error: ${err.message}`, "err");
  } finally {
    releaseBusy();
  }
}

async function refreshAfterParameter(result) {
  if (!result || !result.ok) {
    setStatus(result ? result.message : "parameter change failed", "err");
    return;
  }
  const [f, c] = await Promise.all([
    API.formulation(state.routine),
    API.case(state.routine, state.case),
  ]);
  renderFormulation(f);
  renderCase(c);
  resetResults();
  setStatus(result.message, "ok");
}

// ---------------------------------------------------------------- orchestration
async function selectRoutine(name) {
  state.routine = name;
  highlightRoutine(name);
  await refresh();
}

// Sync the sidebar to whatever case / routine / solver the chat agent switched
// to, so the correct routine is highlighted and its equations + case data load.
async function applyAgentSession(session) {
  if (!session) return;
  let changed = false;

  if (session.routine && session.routine !== state.routine) {
    state.routine = session.routine;
    highlightRoutine(state.routine);
    changed = true;
  }
  if (session.case && session.case !== state.case) {
    ensureCaseOption(session.case);
    state.case = session.case;
    const sel = $("case-select");
    if (sel) sel.value = session.case;
    changed = true;
  }
  if (changed) {
    await refresh();          // loads the new routine's formulation + case data
  }
  // Apply the agent's solver after refresh re-rendered the compatible list.
  if (session.solver) {
    const sel = $("solver-select");
    if (sel && [...sel.options].some((o) => o.value === session.solver)) {
      state.solver = session.solver;
      sel.value = session.solver;
    }
  }
}

// The agent may switch to a case not in the curated picker — make sure the
// dropdown has an option for it so it can be selected.
function ensureCaseOption(alias) {
  const sel = $("case-select");
  if (!sel || !alias) return;
  if ([...sel.options].some((o) => o.value === alias)) return;
  const opt = document.createElement("option");
  opt.value = alias;
  opt.textContent = alias;
  sel.appendChild(opt);
}

async function refresh() {
  // If a refresh is requested while busy (e.g. the user changes the grid case
  // mid-solve), don't silently drop it — queue it so the sidebar can't end up
  // showing a case/routine the backend hasn't actually loaded.
  if (state.busy) { _refreshPending = true; return; }
  state.busy = true;
  _refreshPending = false;
  setStatus(`loading ${state.routine}…`);
  try {
    const [f, c, s, p] = await Promise.all([
      API.formulation(state.routine),
      API.case(state.routine, state.case),
      API.solvers(state.routine).catch(() => ({ solvers: [], default: "CLARABEL" })),
      API.parameters(state.routine, state.case),
    ]);
    renderFormulation(f);
    renderCase(c);
    renderSolvers(s);
    renderParameters(p);
    resetResults();             // prior solve is stale for the new routine/case
    setStatus(`${state.routine} · ${state.case}`, "ok");
  } catch (err) {
    setStatus(`error: ${err.message}`, "err");
    console.error(err);
  } finally {
    releaseBusy();
  }
}

// Release the busy lock and run any refresh that was requested while busy, so
// the sidebar selection always ends up reflected in the loaded case/routine.
let _refreshPending = false;
function releaseBusy() {
  state.busy = false;
  if (_refreshPending) { _refreshPending = false; refresh(); }
}

// ---------------------------------------------------------------- solver picker
function renderSolvers(payload) {
  const sel = $("solver-select");
  if (!sel) return;
  const solvers = (payload && payload.solvers) || [];
  const def = (payload && payload.default) || solvers[0] || "CLARABEL";
  sel.innerHTML = "";
  const list = solvers.length ? solvers : [def];
  for (const name of list) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    sel.appendChild(opt);
  }
  // keep the user's choice if still valid, else fall back to the default
  state.solver = list.includes(state.solver) ? state.solver : def;
  sel.value = state.solver;
}

// ---------------------------------------------------------------- results
function resetResults() {
  state.solved = false;
  const body = $("results-body");
  if (body) {
    body.innerHTML =
      `<p class="empty-note">No solve yet. Press <strong>Run solve</strong> ` +
      `to compute results for the selected routine and case.</p>`;
  }
}

function metricPill(label, value) {
  return `<div class="metric"><span class="metric-k">${esc(label)}</span>` +
         `<span class="metric-v">${esc(value)}</span></div>`;
}

function renderResults(payload) {
  const body = $("results-body");
  if (!body) return;
  const converged = payload.converged;
  const statusCls = converged ? "ok" : "warn";
  const statusTxt = converged ? "converged" : "did not converge";

  const viol = (payload.violations || []).filter((v) => v[2] === "VIOLATION");
  const warn = (payload.violations || []).filter((v) => v[2] === "WARN");

  const metrics = [
    metricPill("routine", payload.routine),
    metricPill("solver", payload.solver),
    metricPill("status", statusTxt),
  ];
  if (payload.objective !== null && payload.objective !== undefined) {
    metrics.unshift(metricPill("objective", Number(payload.objective).toFixed(4)));
  }
  if (payload.pibr) metrics.push(metricPill("IBR dispatch (pu)", Number(payload.pibr[0]).toFixed(6)));
  if (payload.pcurt) metrics.push(metricPill("curtailment (pu)", Number(payload.pcurt[0]).toFixed(6)));
  if (payload.verification) {
    metrics.push(metricPill("independent checks", payload.verification.passed ? "PASS" : "FAIL"));
    metrics.push(metricPill("nodal residual (pu)", Number(payload.verification.residuals.nodal_balance_pu).toExponential(2)));
  }
  if (viol.length) metrics.push(metricPill("violations", String(viol.length)));
  if (payload.horizon_slots && payload.horizon_slots > 1) {
    metrics.push(metricPill("slots", String(payload.horizon_slots)));
  }
  if (payload.pq_curves && payload.pq_curves.length) {
    metrics.push(metricPill("load curves", payload.pq_curves.join(", ")));
  }
  if (!viol.length && warn.length) metrics.push(metricPill("warnings", String(warn.length)));
  else if (!viol.length) metrics.push(metricPill("limits", "all respected"));

  // plot thumbnails
  const plots = payload.plots || {};
  const figs = ["pg", "plf", "pd"]
    .filter((k) => plots[k])
    .map(
      (k) =>
        `<figure class="result-fig">` +
        `<a href="${esc(plots[k])}" target="_blank" rel="noopener" title="Open ${esc(k)} full size in a new tab">` +
        `<img src="${esc(plots[k])}" alt="${esc(k)}" loading="lazy" /></a>` +
        `<figcaption>${esc(k)} <span class="fig-open">⤢ open</span></figcaption></figure>`
    )
    .join("");

  // violation list (compact)
  let violHtml = "";
  const flagged = (payload.violations || []).filter((v) => v[2] === "VIOLATION" || v[2] === "WARN");
  if (flagged.length) {
    const items = flagged
      .map((v) => `<li class="sev-${esc((v[2] || "").toLowerCase())}"><strong>${esc(v[0])}</strong> — ${esc(v[1])}</li>`)
      .join("");
    violHtml = `<div class="result-block"><h3>Flagged limits</h3><ul class="viol-list">${items}</ul></div>`;
  }

  body.innerHTML =
    `<div class="result-status ${statusCls}">Solve ${esc(statusTxt)} ` +
    `(exit code ${esc(payload.exit_code)})</div>` +
    `<div class="metric-row">${metrics.join("")}</div>` +
    (figs ? `<div class="result-figs">${figs}</div>` : "") +
    violHtml +
    `<p class="result-hint">Open the <strong>analysis report</strong> on the right for the full ` +
    `engineering write-up of these numbers.</p>`;
}

async function runSolve() {
  if (state.busy) return;
  state.busy = true;
  setBusyButtons(true);
  setStatus(`solving ${state.routine}…`);
  try {
    const payload = await API.solve({
      routine: state.routine,
      case: state.case,
      solver: state.solver,
    });
    renderResults(payload);
    state.solved = true;
    setStatus(`solved ${state.routine} · ${state.case}`, "ok");
    if (state.reportOpen) await loadReport(false);   // keep report in sync
  } catch (err) {
    const body = $("results-body");
    if (body) body.innerHTML = `<p class="result-error">Solve failed: ${esc(err.message)}</p>`;
    setStatus(`error: ${err.message}`, "err");
  } finally {
    setBusyButtons(false);
    releaseBusy();
  }
}

function setBusyButtons(busy) {
  for (const id of ["run-btn", "report-btn"]) {
    const b = $(id);
    if (b) b.disabled = busy;
  }
}

// ---------------------------------------------------------------- report panel
function openReport() {
  state.reportOpen = true;
  $("layout").classList.add("report-open");
  const panel = $("report-panel");
  panel.classList.add("open");
  panel.setAttribute("aria-hidden", "false");
}

function closeReport() {
  state.reportOpen = false;
  $("layout").classList.remove("report-open");
  const panel = $("report-panel");
  panel.classList.remove("open");
  panel.setAttribute("aria-hidden", "true");
}

async function loadReport(openIfClosed = true) {
  if (openIfClosed) openReport();
  const doc = $("report-doc");
  doc.innerHTML = `<p class="report-loading">Compiling analysis report…</p>`;
  setBusyButtons(true);
  try {
    const payload = await API.report({
      routine: state.routine,
      case: state.case,
      solver: state.solver,
    });
    state.solved = true;
    renderReport(payload.markdown);
  } catch (err) {
    doc.innerHTML = `<p class="report-error">Could not build report: ${esc(err.message)}</p>`;
  } finally {
    setBusyButtons(false);
  }
}

function renderReport(markdown) {
  const doc = $("report-doc");
  let html;
  try {
    html = window.marked ? window.marked.parse(markdown) : `<pre>${esc(markdown)}</pre>`;
  } catch (_) {
    html = `<pre>${esc(markdown)}</pre>`;
  }
  doc.innerHTML = html;
  decorateReport(doc);
  doc.scrollTop = 0;
}

// Post-process the rendered report: number figures, colour-code status cells.
function decorateReport(root) {
  // wrap <img> into a numbered <figure> using its alt as the caption
  let figN = 0;
  root.querySelectorAll("p > img, img").forEach((img) => {
    if (img.closest("figure")) return;
    figN += 1;
    const fig = document.createElement("figure");
    fig.className = "report-figure";
    const cap = document.createElement("figcaption");
    cap.textContent = `Figure ${figN}. ${(img.getAttribute("alt") || "").replace(/`/g, "")}`;
    const parent = img.parentElement;
    img.replaceWith(fig);
    fig.appendChild(img);
    fig.appendChild(cap);
    // if the image sat alone in a <p>, drop the now-empty paragraph wrapper
    if (parent && parent.tagName === "P" && !parent.textContent.trim()) {
      parent.replaceWith(fig);
    }
  });

  // colour-code assessment / severity table cells by their text
  const tone = {
    "violation": "sev-violation", "overloaded": "sev-violation", "at pmax": "sev-violation",
    "warning": "sev-warn", "congested": "sev-warn", "near pmax": "sev-warn",
    "within limits": "sev-ok", "marginal": "sev-ok", "ok": "sev-ok",
    "at pmin": "sev-muted", "low": "sev-warn",
  };
  root.querySelectorAll("td").forEach((td) => {
    const key = td.textContent.trim().toLowerCase();
    if (tone[key]) td.classList.add(tone[key]);
  });

  // typeset any math the report may contain (kept minimal; symbols use <code>)
  if (window.MathJax && window.MathJax.typesetPromise) {
    window.MathJax.typesetPromise([root]).catch(() => {});
  }
}

// ---------------------------------------------------------------- chat
function appendChat(role, contentHtml, opts = {}) {
  const log = $("chat-log");
  const row = document.createElement("div");
  row.className = `chat-msg chat-${role}` + (opts.pending ? " pending" : "");
  row.innerHTML =
    `<div class="chat-role">${role === "user" ? "You" : "Agent"}</div>` +
    `<div class="chat-body">${contentHtml}</div>`;
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
  return row;
}

function mdInline(text) {
  try {
    return window.marked ? window.marked.parse(text) : esc(text);
  } catch (_) {
    return esc(text);
  }
}

async function sendChat() {
  const ta = $("chat-text");
  const msg = ta.value.trim();
  if (!msg || state.busy) return;
  ta.value = "";
  appendChat("user", esc(msg));
  const pending = appendChat("agent", `<span class="dots">working…</span>`, { pending: true });
  state.busy = true;
  let res = null;
  try {
    res = await API.chat({
      message: msg,
      routine: state.routine,
      case: state.case,
      solver: state.solver,
    });
    pending.classList.remove("pending");
    pending.querySelector(".chat-body").innerHTML = mdInline(res.reply || "_(no reply)_");
    if (window.MathJax && window.MathJax.typesetPromise) {
      window.MathJax.typesetPromise([pending]).catch(() => {});
    }
  } catch (err) {
    pending.classList.remove("pending");
    pending.querySelector(".chat-body").innerHTML =
      `<span class="chat-err">${esc(err.message)}</span>`;
  } finally {
    state.busy = false;
  }

  // After the turn: sync the sidebar to whatever the agent switched to (routine
  // selection loads its equations; case selection loads its data tables).
  if (res && res.session) {
    try { await applyAgentSession(res.session); } catch (e) { console.error(e); }
  }
  if (res && res.ok) {
    renderFormulation(await API.formulation(state.routine));
    renderCase(await API.case(state.routine, state.case));
    renderParameters(await API.parameters(state.routine, state.case));
    if (!res.results) resetResults();
  }
  // If the agent produced a solve, refresh the results card to match.
  if (res && res.results) {
    try { await runSolve(); } catch (_) {}
  }
}

// ---------------------------------------------------------------- boot
async function boot() {
  setStatus("loading…");
  wireEvents();
  try {
    const [routines, cases, llm] = await Promise.all([
      API.routines(), API.cases(), API.llm().catch(() => null),
    ]);
    renderRoutines(routines.groups);
    renderCases(cases.cases, cases.default);
    initWeekUpload();
    if (llm) renderLlm(llm);
    state.case = cases.default;
    state.routine = routines.default;
    highlightRoutine(state.routine);
    await refresh();
  } catch (err) {
    setStatus(`error: ${err.message}`, "err");
    console.error(err);
  }
}

function wireEvents() {
  $("solver-select").addEventListener("change", (e) => { state.solver = e.target.value; });
  $("provider-select").addEventListener("change", () => { renderModelOptions(); applyLlm(); });
  $("model-select").addEventListener("change", applyLlm);
  $("run-btn").addEventListener("click", runSolve);
  $("report-btn").addEventListener("click", () => loadReport(true));
  $("report-close").addEventListener("click", closeReport);
  $("report-refresh").addEventListener("click", () => loadReport(true));
  $("report-print").addEventListener("click", () => {
    // ensure the document starts at the very top so the title is never clipped
    const doc = $("report-doc");
    if (doc) doc.scrollTop = 0;
    window.print();
  });
  initResizer();
  initVResizer();

  const form = $("chat-form");
  form.addEventListener("submit", (e) => { e.preventDefault(); sendChat(); });
  $("chat-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
  });
  $("param-apply").addEventListener("click", applyParameterForm);
  $("param-reset").addEventListener("click", resetParameters);
}

// Drag the divider between the workspace and the report panel to resize it.
function initResizer() {
  const resizer = $("report-resizer");
  const layout = $("layout");
  const panel = $("report-panel");
  if (!resizer || !layout || !panel) return;
  let dragging = false;

  const onMove = (e) => {
    if (!dragging) return;
    // panel is pinned to the right; width = (panel right edge) - cursor x
    const right = panel.getBoundingClientRect().right;
    const desired = right - e.clientX;
    // keep at least ~600px of the layout for the sidebar + workspace columns
    const layoutW = layout.getBoundingClientRect().width;
    const upper = Math.max(360, layoutW - 600);
    const w = Math.max(340, Math.min(desired, upper));
    layout.style.setProperty("--report-w", w + "px");
  };
  const stop = () => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove("report-resizing");
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", stop);
  };
  resizer.addEventListener("mousedown", (e) => {
    e.preventDefault();
    dragging = true;
    document.body.classList.add("report-resizing");
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", stop);
  });
  // double-click restores the default width
  resizer.addEventListener("dblclick", () => layout.style.removeProperty("--report-w"));
}

// Drag the horizontal divider to split the vertical space between the Assistant
// pane (top) and the workspace of three columns (bottom).
function initVResizer() {
  const resizer = $("v-resizer");
  const shell = document.querySelector(".shell");
  if (!resizer || !shell) return;
  let dragging = false;

  const onMove = (e) => {
    if (!dragging) return;
    const rect = shell.getBoundingClientRect();
    let h = e.clientY - rect.top;                 // desired Assistant-pane height
    const max = Math.max(150, rect.height - 200); // leave room for the columns
    h = Math.max(150, Math.min(h, max));
    shell.style.setProperty("--chat-h", h + "px");
  };
  const stop = () => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove("v-resizing");
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", stop);
  };
  resizer.addEventListener("mousedown", (e) => {
    e.preventDefault();
    dragging = true;
    document.body.classList.add("v-resizing");
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", stop);
  });
  // double-click restores the default split
  resizer.addEventListener("dblclick", () => shell.style.removeProperty("--chat-h"));
}

boot();
