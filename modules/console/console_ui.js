// console_ui.js — DOM state, render, binders (loads after schema + api)
const S = {
  projects: [],
  schema: [],
  base: {},
  values: {},
  preview: null,
  logOffset: 0,
  poll: null,
  outcomeTab: "",
  pairTab: "",
  ioTab: false,
};


const $ = (id) => document.getElementById(id);


function eq(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function syncLeftIntoValues() {
  if (!S.values) return;
  const data = ($("data") && $("data").value || "").trim();
  if (data) S.values.INPUT = data.split(/[/\\]/).pop() || data;
  const md = ($("modulesDir") && $("modulesDir").value || "").trim();
  if (md) S.values.MODULES_DIR = md;
}

function collectOverlay() {
  syncLeftIntoValues();
  const ov = {};
  for (const [k, v] of Object.entries(S.values)) {
    if (!(k in S.base) || !eq(v, S.base[k])) ov[k] = v;
  }
  return ov;
}

function payload() {
  const overlay = Object.assign({}, S.values);
  const tab = typeof outcomeTab === "function" ? outcomeTab() : "";
  if (tab) overlay.GROUP_SOURCE_COL = tab;
  return {
    project: $("project").value,
    data: $("data").value.trim(),
    settings: $("settings").value.trim(),
    overlay,
    run_outcome: tab || "",
  };
}

function setDot(cls, text) {
  $("statusDot").className = cls || "";
  $("srv").textContent = text;
}

function fillProjects(list) {
  S.projects = list;
  const sel = $("project");
  sel.innerHTML = list.map(p => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join("");
  const p = list.find(x => x.name === (sel && sel.value));
  fillDatalist("xlsxList", (p && p.xlsx) || []);
}

function showPane(name) {
  const flow = name === "flow";
  const preview = name === "preview";
  $("paneConfig").hidden = flow || preview;
  $("paneFlow").hidden = !flow;
  const prevPane = $("panePreview");
  if (prevPane) prevPane.hidden = !preview;
  $("tabBtnConfig").classList.toggle("on", !flow && !preview);
  $("tabBtnFlow").classList.toggle("on", flow);
  const tabPrev = $("tabBtnPreview");
  if (tabPrev) tabPrev.classList.toggle("on", preview);
  const url = new URL(location.href);
  if (flow) url.searchParams.set("tab", "flow");
  else if (preview) url.searchParams.set("tab", "preview");
  else url.searchParams.delete("tab");
  history.replaceState(null, "", url);
  if (flow) loadFlowFrame();
}

function loadFlowFrame() {
  const proj = $("project").value;
  const f = $("flowFrame");
  const src = "/flow_chart.html?project=" + encodeURIComponent(proj);
  const askFit = () => {
    requestAnimationFrame(() => {
      try { f.contentWindow.postMessage("fit-flow", "*"); } catch (e) {}
      postFlowSteps(f);
    });
  };
  if (f.dataset.proj !== proj) {
    f.onload = askFit;
    f.src = src;
    f.dataset.proj = proj;
  } else {
    askFit();
  }
}

function extLockRows() {
  const prev = (S.preview && Array.isArray(S.preview.external)) ? S.preview.external : [];
  if (prev.length) return prev;
  const tests = (S.values && Array.isArray(S.values.EXTERNAL_TESTS)) ? S.values.EXTERNAL_TESTS : [];
  return tests.filter((row) => row && String(row.file || row.data || "").trim());
}
function extLockLabel() {
  const names = [];
  extLockRows().forEach((row) => {
    const file = String((row && (row.file || row.data)) || "").split(/[/\\]/).pop();
    const name = file || (row && row.name) || "";
    if (name && names.indexOf(name) < 0) names.push(name);
  });
  return names.join("；") || "—";
}
function fillLockStamp() {
  const name = (el) => {
    const s = (el && el.value || "").trim();
    if (!s) return "—";
    return s.split(/[/\\]/).pop() || s;
  };
  if ($("lockProject")) $("lockProject").textContent = ($("project") && $("project").value) || "—";
  if ($("lockData")) $("lockData").textContent = name($("data"));
  if ($("lockExt")) $("lockExt").textContent = extLockLabel();
  if ($("lockSettings")) $("lockSettings").textContent = name($("settings")) || "settings.ini";
  if ($("lockModules")) $("lockModules").textContent = ($("modulesDir") && $("modulesDir").value) || "—";
}

function resolveLockedData(p, raw) {
  const s = String(raw || "").trim();
  if (!s) return (p && (p.default_xlsx || (p.xlsx || [])[0])) || "";
  if (/[/\\]/.test(s) || (p && (p.xlsx || []).includes(s))) return s;
  if (p && p.dir) {
    const hit = (p.xlsx || []).find(x => String(x).split(/[/\\]/).pop() === s);
    if (hit) return hit;
    return String(p.dir).replace(/[/\\]+$/, "") + "/" + s;
  }
  return s;
}

function applyUrlLock() {
  const qs = new URLSearchParams(location.search);
  const proj = qs.get("project") || ($("project") && $("project").value);
  if (proj && $("project")) $("project").value = proj;
  const p = S.projects.find(x => x.name === ($("project") && $("project").value));
  if (p && $("settings") && !$("settings").value) $("settings").value = p.settings;
  const dataQ = qs.get("data");
  if ($("data")) $("data").value = resolveLockedData(p, dataQ);
  if (p && $("settings") && (!qs.get("settings"))) $("settings").value = p.settings;
  if (qs.get("settings") && $("settings")) $("settings").value = qs.get("settings");
  if (qs.get("modules") && $("modulesDir")) $("modulesDir").value = qs.get("modules");
  S.ioLock = {};
  try {
    const proj = ($("project") && $("project").value) || "";
    const raw = localStorage.getItem("rad-io-lock:" + proj);
    const obj = raw ? JSON.parse(raw) : null;
    if (obj && typeof obj === "object") S.ioLock = obj;
  } catch (e) {}
  for (const [key, q] of Object.entries(START_PAGE_QS)) {
    if (qs.has(q)) {
      const raw = qs.get(q);
      if (key === "USE_INTERNAL_SPLIT_WHEN_EXTERNAL") {
        S.ioLock[key] = (raw === "1" || raw === "true");
      } else if (key === "EXTERNAL_TESTS" || key === "RAD_ID_COLS") {
        try {
          const parsed = raw ? JSON.parse(raw) : [];
          S.ioLock[key] = Array.isArray(parsed) ? parsed : [];
        } catch (e) { S.ioLock[key] = []; }
      } else if (key === "LABEL_COL") {
        S.ioLock[key] = raw == null ? "" : raw;
      } else {
        S.ioLock[key] = raw || "";
      }
    }
  }
  fillLockStamp();
}

function onProjectChange() {
  $("runCol").hidden = true;
  const f = $("flowFrame");
  if (f && f.dataset.proj && f.dataset.proj !== $("project").value) {
    f.dataset.proj = "";
    if (!$("paneFlow").hidden) loadFlowFrame();
  }
  const p = S.projects.find(x => x.name === $("project").value);
  if (!p) return;
  if ($("settings") && !$("settings").value) $("settings").value = p.settings;
  if ($("data") && !$("data").value) {
    $("data").value = p.default_xlsx || (p.xlsx && p.xlsx[0]) || "";
  }
  fillDatalist("xlsxList", p.xlsx || []);
  fillLockStamp();
}

function placeGrpCard() {
  const card = $("grpCard");
  if (!card) return;
  const park = $("grpCardPark");
  const multi = outcomeCols().length > 1;
  if (S.ioTab) {
    if (park) park.appendChild(card);
    card.hidden = true;
    return;
  }
  if (multi && !outcomeTab()) {
    if (park) park.appendChild(card);
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const slot = $("ioGrpSlot");
  const io = document.querySelector('[data-sec="io"] .sec-body');
  const root = $("formRoot");
  if (slot) slot.appendChild(card);
  else if (io) io.insertBefore(card, io.firstChild);
  else if (root) root.insertBefore(card, root.firstChild);
  else if (park) park.appendChild(card);
  renderGroupCard();
}

function placePalCard() {
  const card = $("palCard");
  if (!card) return;
  card.hidden = false;
  const col = $("palCol");
  if (col && !col.contains(card)) {
    const inner = col.querySelector(".col-inner") || col;
    inner.appendChild(card);
  }
  buildPalList();
  renderPalCard();
}

function ioLockKey() {
  return "rad-io-lock:" + (($("project") && $("project").value) || "");
}


function workbookSheets() {
  return ioSheetOpts.length ? ioSheetOpts : (((S.preview && S.preview.sheets) || []));
}
function applyIoDefaults(obj) {
  const dst = obj || {};
  const sheets = workbookSheets();
  const sheetKeys = ["RAD_SHEET", "CLIN_SHEET", "QC_SHEET", "ICC_SHEET", "EXTERNAL_CLIN_SHEET", "EXTERNAL_RAD_SHEET"];
  const keys = [
    "RAD_SHEET", "CLIN_SHEET", "QC_SHEET", "ICC_SHEET",
    "RAD_ID_COL", "CLIN_ID_COL",
    "EXTERNAL_CLIN_SHEET", "EXTERNAL_RAD_SHEET",
  ];
  keys.forEach((k) => {
    if (String(dst[k] == null ? "" : dst[k]).trim()) return;
    const fb = IO_DEFAULTS[k];
    if (sheetKeys.indexOf(k) >= 0) {
      dst[k] = pickExistingSheet(fb, sheets, "");
    } else {
      dst[k] = fb;
    }
  });
  if (Array.isArray(dst.EXTERNAL_TESTS)) {
    dst.EXTERNAL_TESTS = dst.EXTERNAL_TESTS.map((r) => {
      const row = r && typeof r === "object" ? Object.assign({}, r) : {};
      if (!(String(row.clin_sheet || "").trim())) row.clin_sheet = IO_DEFAULTS.CLIN_SHEET;
      if (!(String(row.rad_sheet || "").trim())) row.rad_sheet = IO_DEFAULTS.RAD_SHEET;
      if (!(String(row.rad_id_col || "").trim())) row.rad_id_col = IO_DEFAULTS.RAD_ID_COL;
      return row;
    });
  }
  return dst;
}

function applyIoLock() {
  if (!S.values) return;
  if (S.ioLock) {
    for (const [k, v] of Object.entries(S.ioLock)) {
      const keepEmpty = k === "LABEL_COL" || k === "RAD_ID_COLS";
      if (START_PAGE_KEYS.indexOf(k) < 0 && SHEET_KEYS.indexOf(k) < 0
          && ID_KEYS.indexOf(k) < 0 && !keepEmpty) continue;
      if (!keepEmpty) {
        if (v == null || v === "") continue;
        if (Array.isArray(v) && !v.length) continue;
      } else if (v == null) continue;
      S.values[k] = v;
    }
  }
  applyIoDefaults(S.values);
}

function persistIoLock() {
  if (!S.values) return;
  const obj = {};
  START_PAGE_KEYS.forEach(k => { obj[k] = S.values[k]; });
  SHEET_KEYS.forEach(k => { obj[k] = S.values[k]; });
  ID_KEYS.forEach(k => { obj[k] = S.values[k]; });
  ["LABEL_COL", "RAD_ID_COLS"].forEach(k => {
    if (Object.prototype.hasOwnProperty.call(S.values, k)) obj[k] = S.values[k];
  });
  S.ioLock = obj;
  try { localStorage.setItem(ioLockKey(), JSON.stringify(obj)); } catch (e) {}
  const proj = ($("project") && $("project").value) || "";
  const data = ($("data") && $("data").value || "").trim();
  if (proj && data) {
    try { localStorage.setItem("rad-data-file:" + proj, data.split(/[/\\]/).pop() || data); } catch (e) {}
  }
}

function applySchema(schema, values) {
  S.schema = schema || [];
  if (values) {
    const clean = JSON.parse(JSON.stringify(values));
    ["FORCE_REMOVE_PREFIXES", "OUTCOME_POSITIVE_LABELS", "PRESERVE_GROUP_AS"].forEach(k => {
      delete clean[k];
    });
    S.base = JSON.parse(JSON.stringify(clean));
    S.values = JSON.parse(JSON.stringify(clean));
  }
  applyIoLock();
  fillPairwiseKeep();
  renderForm();
  updateDiff();
  renderGroupCard();
  renderPalCard();
  if (typeof syncRunScope === "function") syncRunScope();
}

function hexNorm(c, fb) {
  const s = String(c || "").trim();
  if (/^#[0-9a-fA-F]{6}$/.test(s)) return s.toUpperCase();
  if (/^#[0-9a-fA-F]{3}$/.test(s)) {
    return ("#" + s[1] + s[1] + s[2] + s[2] + s[3] + s[3]).toUpperCase();
  }
  return (fb || "#888888").toUpperCase();
}

function palLists() {
  const pal = (typeof fieldValue === "function" ? fieldValue("FIG_PALETTE") : null) || (S.values && S.values.FIG_PALETTE) || "npg";
  const preset = PALETTES[pal] || PALETTES.npg;
  const rawMain = typeof fieldValue === "function" ? fieldValue("FIG_COLORS_MAIN") : S.values.FIG_COLORS_MAIN;
  const rawAux = typeof fieldValue === "function" ? fieldValue("FIG_COLORS_AUX") : S.values.FIG_COLORS_AUX;
  const main = (Array.isArray(rawMain) ? rawMain : []).map((c, i) => hexNorm(c, preset.main[i]));
  const aux = (Array.isArray(rawAux) ? rawAux : []).map((c, i) => hexNorm(c, preset.aux[i]));
  while (main.length < 5) main.push(preset.main[main.length]);
  while (aux.length < 5) aux.push(preset.aux[aux.length]);
  return { main: main.slice(0, 5), aux: aux.slice(0, 5), preset, pal };
}

function matchPalette(main, aux) {
  const key = (a) => a.map((c) => hexNorm(c)).join(",");
  const mk = key(main), ak = key(aux);
  for (const [id, p] of Object.entries(PALETTES)) {
    if (key(p.main) === mk && key(p.aux) === ak) return id;
  }
  return "custom";
}

function renderSwatches(el, colors, labels, which) {
  if (!el) return;
  const tips = which === "aux" ? PAL_AUX_TIPS : PAL_MAIN_TIPS;
  el.innerHTML = colors.map((c, i) =>
    `<label class="swatch" data-tip="${esc((tips[i] || labels[i]) + " " + hexNorm(c))}">
      <input type="color" value="${hexNorm(c).toLowerCase()}" data-pal="${which}" data-i="${i}"/>
      <span>${esc(labels[i])}</span>
    </label>`
  ).join("");
}

function palDots(colors, cls) {
  const chips = (colors || []).map((c) =>
    `<i style="background:${hexNorm(c)}" title="${esc(c)}"></i>`
  ).join("");
  return `<div class="pal-mini ${cls || ""}">${chips}</div>`;
}

function palOptionHtml(id, p, extra) {
  const main = extra && extra.main ? extra.main : p.main;
  const aux = extra && extra.aux ? extra.aux : p.aux;
  const tip = (p.blurb || p.tag || p.label || "") +
    " 主色 " + (main || []).join(" ") + "；辅色 " + (aux || []).join(" ");
  return `<label class="pal-opt" data-pal-id="${esc(id)}" data-tip="${esc(tip)}">
    <input type="radio" name="figPalette" value="${esc(id)}"/>
    <div>
      <div class="pal-opt-title"><b>${esc(p.label)}</b><span>${esc(p.tag)}</span></div>
      ${palDots(main, "")}
      ${palDots(aux, "aux")}
    </div>
  </label>`;
}

function buildPalList() {
  const box = $("palList");
  if (!box || box.dataset.ready) return;
  let html = PAL_ORDER.map((id) => palOptionHtml(id, PALETTES[id])).join("");
  html += palOptionHtml("custom", {
    label: "自定义",
    tag: "改下面色块后",
    blurb: "已手改色块。再点上面某一套可恢复预设。",
    main: ["#CCCCCC", "#BBBBBB", "#AAAAAA", "#999999", "#888888"],
    aux: ["#EEEEEE", "#DDDDDD", "#CCCCCC", "#BBBBBB", "#AAAAAA"],
  });
  box.innerHTML = html;
  box.dataset.ready = "1";
}

function selectedPalId() {
  const on = document.querySelector('input[name="figPalette"]:checked');
  return on ? on.value : "npg";
}

function syncPalChrome() {
  if (!$("palList")) return;
  buildPalList();
  const id = (typeof fieldValue === "function" ? fieldValue("FIG_PALETTE") : null) || (S.values && S.values.FIG_PALETTE) || "npg";
  const customRow = $("palList").querySelector('.pal-opt[data-pal-id="custom"]');
  if (customRow) {
    customRow.hidden = id !== "custom";
    if (id === "custom") {
      const { main, aux } = palLists();
      const body = customRow.querySelector("div");
      if (body) {
        const title = body.querySelector(".pal-opt-title");
        body.innerHTML = (title ? title.outerHTML : "")
          + palDots(main, "") + palDots(aux, "aux");
      }
    }
  }
  $("palList").querySelectorAll(".pal-opt").forEach((lab) => {
    const v = lab.getAttribute("data-pal-id");
    const radio = lab.querySelector("input");
    const on = v === id;
    if (radio) radio.checked = on;
    lab.classList.toggle("on", on);
  });
  const meta = PALETTES[id];
  if ($("palHint")) {
    $("palHint").textContent = meta
      ? (meta.tag + "。 " + meta.blurb)
      : "已改色块，按自定义写入。再点上面某一套可恢复。";
  }
}

function palOverridden() {
  return ["FIG_PALETTE", "FIG_COLORS_MAIN", "FIG_COLORS_AUX"].some(k => fieldHasOverride(k));
}

function renderPalCard() {
  if (!$("palList") || !S.values || !Object.keys(S.values).length) return;
  if (!outcomeTab() && !S.values.FIG_PALETTE) S.values.FIG_PALETTE = "npg";
  const { main, aux } = palLists();
  if (!outcomeTab()) {
    S.values.FIG_COLORS_MAIN = main;
    S.values.FIG_COLORS_AUX = aux;
  }
  renderSwatches($("palMain"), main, PAL_MAIN_LABELS, "main");
  renderSwatches($("palAux"), aux, PAL_AUX_LABELS, "aux");
  syncPalChrome();
  if ($("palInherit")) $("palInherit").hidden = !(outcomeTab() && !palOverridden());
  if ($("palUnov")) $("palUnov").hidden = !(outcomeTab() && palOverridden());
}

function applyPalPick(id) {
  if (!S.values) return;
  const pick = id || selectedPalId();
  setFieldValue("FIG_PALETTE", pick);
  if (pick !== "custom" && PALETTES[pick]) {
    setFieldValue("FIG_COLORS_MAIN", PALETTES[pick].main.slice());
    setFieldValue("FIG_COLORS_AUX", PALETTES[pick].aux.slice());
  }
  markDirty();
  renderPalCard();
}

function bindPalCard() {
  if (!$("palList")) return;
  buildPalList();
  $("palList").addEventListener("change", (e) => {
    const t = e.target;
    if (!t || t.name !== "figPalette") return;
    applyPalPick(t.value);
  });
  const onColor = (e) => {
    const t = e.target;
    if (!t || !t.dataset || !t.dataset.pal) return;
    const which = t.dataset.pal;
    const i = parseInt(t.dataset.i, 10);
    const { main, aux } = palLists();
    const arr = which === "aux" ? aux : main;
    arr[i] = hexNorm(t.value);
    setFieldValue("FIG_COLORS_MAIN", main);
    setFieldValue("FIG_COLORS_AUX", aux);
    setFieldValue("FIG_PALETTE", matchPalette(main, aux));
    updateDiff();
    syncPalChrome();
    if ($("palInherit")) $("palInherit").hidden = !(outcomeTab() && !palOverridden());
    if ($("palUnov")) $("palUnov").hidden = !(outcomeTab() && palOverridden());
  };
  if ($("palMain")) $("palMain").addEventListener("input", onColor);
  if ($("palAux")) $("palAux").addEventListener("input", onColor);
  if ($("palUnov")) {
    $("palUnov").addEventListener("click", (e) => {
      e.preventDefault();
      ["FIG_PALETTE", "FIG_COLORS_MAIN", "FIG_COLORS_AUX"].forEach(clearFieldOverride);
      renderPalCard();
      markDirty();
    });
  }
}

function defaultSubgroupCol() {
  const tab = typeof outcomeTab === "function" ? outcomeTab() : "";
  if (tab) return tab;
  const cols = outcomeCols();
  return cols.length === 1 ? cols[0] : "";
}

function subgroupCol() {
  const raw = String(fieldValue("SUBGROUP_COL") || "").trim();
  return raw || defaultSubgroupCol();
}

function subgroupIsOutcome() {
  const sub = subgroupCol();
  const tab = defaultSubgroupCol();
  return !sub || !tab || sub === tab;
}

function columnLevels(col) {
  const name = String(col || "").trim();
  const clinDist = (S.preview && S.preview.clin_dist) || {};
  const outDist = (S.preview && S.preview.outcome_dist) || {};
  const skip = new Set(["", "nan", "none", "nat", "<na>"]);
  const keysOf = (d) => Object.keys(d || {}).filter(k => !skip.has(String(k).trim().toLowerCase()));
  if (name && clinDist[name] && typeof clinDist[name] === "object") return keysOf(clinDist[name]);
  if (name && outDist[name] && typeof outDist[name] === "object") return keysOf(outDist[name]);
  return [];
}

function groupLevels() {
  const col = subgroupCol();
  let fromPrev = columnLevels(col);
  if (!fromPrev.length && col && S.preview && S.preview.group_levels
      && col === String(S.preview.group_source || col)) {
    fromPrev = (S.preview.group_levels || []).map(x => String(x == null ? "" : x).trim()).filter(Boolean);
  } else if (!fromPrev.length && !col && outcomeCols().length > 1 && !outcomeTab()) {
    fromPrev = [];
  } else if (!fromPrev.length && !col) {
    fromPrev = (S.preview && S.preview.group_levels) || [];
  }
  const allowed = new Set(fromPrev.map(String));
  const keep = Array.isArray(fieldValue("GROUP_KEEP")) ? fieldValue("GROUP_KEEP") : [];
  const extra = [];
  for (const p of (fieldValue("PAIRWISE_GROUPS") || [])) {
    if (Array.isArray(p)) extra.push(...p);
  }
  const seen = new Set();
  const out = [];
  for (const x of fromPrev.concat(keep).concat(extra)) {
    const s = String(x == null ? "" : x).trim();
    if (!s || seen.has(s)) continue;
    if (allowed.size && !allowed.has(s)) continue;
    seen.add(s);
    out.push(s);
  }
  return out;
}

function hasOutcomeCols() {
  const cols = S.values && S.values.OUTCOME_COLS;
  return Array.isArray(cols) && cols.some(x => String(x || "").trim());
}

function inferGroupMode() {
  const pairs = Array.isArray(fieldValue("PAIRWISE_GROUPS")) ? fieldValue("PAIRWISE_GROUPS") : [];
  const keep = Array.isArray(fieldValue("GROUP_KEEP")) ? fieldValue("GROUP_KEEP").map(String) : [];
  // 多结局公共页：不要把残留 PAIRWISE_ALL 收成两两比较。
  if (!outcomeTab() && outcomeCols().length > 1 && pairs.length === 0) return "all";
  if (fieldValue("PAIRWISE_ALL") || pairs.length > 1) return "allpairs";
  if (pairs.length === 1 || keep.length === 2) return "pair";
  return "all";
}

function fillPairwiseKeep() {
  if (!S.values || inferGroupMode() !== "allpairs") return;
  if (outcomeCols().length > 1 && !outcomeTab()) return;
  if (pairTab()) return;
  S.values.PAIRWISE_ALL = true;
  const levels = groupLevels();
  const keep = Array.isArray(S.values.GROUP_KEEP) ? S.values.GROUP_KEEP : [];
  if (keep.length < 2 && levels.length >= 2) S.values.GROUP_KEEP = levels.slice();
  if (S.base) {
    S.base.PAIRWISE_ALL = true;
    if (Array.isArray(S.values.GROUP_KEEP) && S.values.GROUP_KEEP.length >= 2
        && (!Array.isArray(S.base.GROUP_KEEP) || S.base.GROUP_KEEP.length < 2)) {
      S.base.GROUP_KEEP = S.values.GROUP_KEEP.slice();
    }
  }
}

function uniqKeep(arr) {
  const seen = new Set();
  const out = [];
  for (const x of arr || []) {
    const s = String(x == null ? "" : x).trim();
    if (!s || seen.has(s)) continue;
    seen.add(s);
    out.push(s);
  }
  return out;
}

function clinPool() {
  return uniqKeep((S.preview && S.preview.clin_cols) || []);
}

function implicitSkip() {
  const v = S.values || {};
  const cols = [];
  for (const k of ["CLIN_ID_COL", "LABEL_COL"]) {
    if (v[k]) cols.push(String(v[k]));
  }
  cols.push("Patient_ID", "Group");
  for (const c of (v.OUTCOME_COLS || [])) cols.push(String(c));
  return uniqKeep(cols);
}

function syncClinRoles(write) {
  if (!S.values) return { skip: [], rm: [], locked: new Set() };
  const locked = implicitSkip();
  const skip = uniqKeep((fieldValue("SKIP_CLIN_FEATURES") || []).concat(locked));
  const skipSet = new Set(skip);
  const rm = uniqKeep(fieldValue("FORCE_REMOVE_FEATURES") || []).filter(x => !skipSet.has(x));
  if (write) {
    if (outcomeTab()) {
      if (fieldHasOverride("SKIP_CLIN_FEATURES")) setFieldValue("SKIP_CLIN_FEATURES", skip);
      if (fieldHasOverride("FORCE_REMOVE_FEATURES")) setFieldValue("FORCE_REMOVE_FEATURES", rm);
    } else {
      S.values.SKIP_CLIN_FEATURES = skip;
      S.values.FORCE_REMOVE_FEATURES = rm;
    }
  }
  return { skip, rm, locked: new Set(locked) };
}

function clinAddselectKeys() {
  return ["OUTCOME_COLS", "SKIP_CLIN_FEATURES", "FORCE_REMOVE_FEATURES",
          "FORCE_INTER_FEATURES"];
}
function radPool() {
  return uniqKeep((S.preview && S.preview.rad_cols) || []);
}
function radIdNames() {
  return new Set(["Name", "Patient_ID", "patient_id", "PatientID", "Group", "ID"]
    .concat((S.preview && S.preview.rad_id_cols) || []).map(String));
}
function markerAnchor(stored) {
  const raw = String(stored || "");
  if (raw.indexOf("suffix:") === 0) return { anchor: "end", part: raw.slice(7) };
  if (raw.indexOf("prefix:") === 0) return { anchor: "start", part: raw.slice(7) };
  return { anchor: "start", part: raw };
}
function columnMatchesPrefix(name, prefix) {
  // 与 data_io._column_matches_prefix 同规则。suffix: 才认结尾。
  const s = String(name);
  const spec = markerAnchor(prefix);
  const p = spec.part;
  if (!p) return false;
  if (spec.anchor === "end") return s.slice(-p.length) === p;
  if (s.indexOf(p) === 0) return true;
  if (p.slice(-2) === "__") {
    const broad = p.slice(0, -1);
    if (broad && s.indexOf(broad) === 0) return true;
  }
  return false;
}
function excludedRadName(name) {
  const prefs = asList(fieldValue("RAD_FEATURE_PREFIX_EXCLUDE")).filter(Boolean);
  if (!prefs.length) return false;
  const s = String(name);
  if (radIdNames().has(s) || clinPool().indexOf(s) >= 0) return false;
  return prefs.some((p) => columnMatchesPrefix(s, p));
}
function radPoolForAnalysis() {
  return radPool().filter((c) => !excludedRadName(c));
}
function nameToken(s) {
  return String(s == null ? "" : s).trim().toLowerCase().replace(/[\s_]/g, "");
}
function rawPool(f) {
  if (!f) return [];
  const previewOn = !!(S.preview && (clinPool().length || radPool().length));
  if (previewOn && f.pool_from === "clin") return clinPool();
  if (previewOn && f.pool_from === "rad") return radPoolForAnalysis();
  if (previewOn && f.pool_from === "both") {
    const models = (f.model_options && f.model_options.length)
      ? f.model_options
      : ["Clinical", "RadScore", "Combined"];
    return uniqKeep([].concat(models, clinPool(), radPoolForAnalysis()));
  }
  const src = (f.option_pool && f.option_pool.length) ? f.option_pool : (f.add_options || []);
  return uniqKeep(src);
}

function selectedNames(v) {
  if (Array.isArray(v)) return v.map(x => String(x == null ? "" : x).trim()).filter(Boolean);
  if (typeof v === "string") {
    const s = v.trim();
    return s ? [s] : [];
  }
  return [];
}
function dropdownOptions(f, have) {
  const haveS = selectedNames(have);
  const haveTok = new Set(haveS.map(nameToken));
  const modelTok = new Set(["clinical", "radscore", "combined"]);
  const key = f && f.key;
  let addOpts = rawPool(f).filter(c => {
    const s = String(c);
    if (!s || haveS.indexOf(s) >= 0) return false;
    const t = nameToken(s);
    if (modelTok.has(t) && haveTok.has(t)) return false;
    return true;
  });
  if (key === "FORCE_REMOVE_FEATURES" || key === "FORCE_INTER_FEATURES"
      || key === "FORCE_MODEL_FEATURES") {
    const skipSet = new Set((fieldValue("SKIP_CLIN_FEATURES") || []).map(String));
    addOpts = addOpts.filter(c => !skipSet.has(String(c)));
  }
  if (key === "FORCE_INTER_FEATURES" || key === "FORCE_MODEL_FEATURES") {
    const rmSet = new Set((fieldValue("FORCE_REMOVE_FEATURES") || []).map(String));
    addOpts = addOpts.filter(c => !rmSet.has(String(c)));
  }
  return addOpts;
}
function filterAddOptions(opts, query) {
  const q = String(query || "").trim().toLowerCase();
  const list = opts || [];
  if (!q) {
    if (list.length <= 80) return { rows: list.slice(), more: 0 };
    const pinned = list.filter(o => {
      const t = nameToken(o);
      return t === "clinical" || t === "radscore" || t === "combined";
    });
    return { rows: pinned, more: list.length - pinned.length };
  }
  const hit = list.filter(o => String(o).toLowerCase().indexOf(q) >= 0);
  return { rows: hit.slice(0, 30), more: Math.max(0, hit.length - 30) };
}
function matchingOptions(opts, query) {
  const q = String(query || "").trim().toLowerCase();
  const list = opts || [];
  if (!q) return list.slice();
  return list.filter(o => String(o).toLowerCase().indexOf(q) >= 0);
}
function removableValues(f, key) {
  const cur = selectedNames(fieldValue(key));
  if (f && key === "SKIP_CLIN_FEATURES") {
    const locked = new Set(((f.locked_options || implicitSkip()) || []).map(String));
    return cur.filter(x => !locked.has(String(x)));
  }
  return cur;
}
function featureIdNames() {
  const ids = radIdNames();
  implicitSkip().forEach((s) => ids.add(String(s)));
  asList(fieldValue("RAD_ID_COLS")).forEach((s) => {
    const name = String(s || "").trim();
    if (name) ids.add(name);
  });
  const radId = String(fieldValue("RAD_ID_COL") || "").trim();
  if (radId) ids.add(radId);
  return ids;
}
function sheetFeatureColumns(which) {
  const ids = featureIdNames();
  const src = which === "rad" ? radPoolForAnalysis() : clinPool();
  return src.filter((c) => c && !ids.has(String(c)));
}
function sheetBulkSides(f, query) {
  if (!f || (f.pool_from !== "clin" && f.pool_from !== "rad" && f.pool_from !== "both")) return [];
  const q = String(query || "").trim().toLowerCase();
  const wantCln = !q || q === "cln";
  const wantRad = !q || q === "rad";
  if (!wantCln && !wantRad) return [];
  const opts = new Set((dropdownOptions(f, fieldValue(f.key) || []) || []).map(String));
  const sides = [];
  if (wantCln) {
    const cols = sheetFeatureColumns("cln").filter((c) => opts.has(String(c)));
    if (cols.length) sides.push({ which: "cln", cols });
  }
  if (wantRad) {
    const cols = sheetFeatureColumns("rad").filter((c) => opts.has(String(c)));
    if (cols.length) sides.push({ which: "rad", cols });
  }
  return sides;
}
function addSheetFeatures(key, which) {
  if (!key || (which !== "cln" && which !== "rad")) return;
  const f = (S.schema || []).find((x) => x.key === key);
  const side = sheetBulkSides(f, "").find((s) => s.which === which);
  if (!side) return;
  const raw = fieldValue(key);
  const cur = Array.isArray(raw) ? raw.map(String) : (raw ? [String(raw)] : []);
  const have = new Set(cur);
  side.cols.forEach((name) => {
    const s = String(name || "").trim();
    if (!s || have.has(s)) return;
    have.add(s);
    cur.push(coerce(s, S.base[key]));
  });
  setFieldValue(key, cur);
  markDirty();
  refreshField(key);
}
function renderAdder(f) {
  const label = (f && f.add_label) || "+ 搜索列名";
  const key = (f && f.key) || "";
  const n = dropdownOptions(f, fieldValue(key) || []).length;
  const canClear = removableValues(f, key).length > 0;
  return `<div class="addfind" data-addfind="${esc(key)}">
    <div class="addfind-row">
      <input type="text" data-add-q="${esc(key)}" placeholder="${esc(label)}" autocomplete="off" spellcheck="false"/>
      <button type="button" class="addfind-op" data-add-all="${esc(key)}" title="全部添加"${n ? "" : " disabled"}>+</button>
      <button type="button" class="addfind-op" data-add-clear="${esc(key)}" title="清除已选"${canClear ? "" : " disabled"}>−</button>
    </div>
    <div class="addfind-menu" hidden></div>
  </div>`;
}
function clearAddedValues(key) {
  if (!key) return;
  const f = (S.schema || []).find(x => x.key === key);
  const cur = selectedNames(fieldValue(key));
  const drop = new Set(removableValues(f, key));
  setFieldValue(key, cur.filter(x => !drop.has(String(x))));
  markDirty();
  refreshField(key);
}
function addAllValues(key, query) {
  if (!key) return;
  const f = (S.schema || []).find(x => x.key === key);
  const raw = fieldValue(key);
  const cur = Array.isArray(raw) ? raw.map(String) : (raw ? [String(raw)] : []);
  const have = new Set(cur);
  matchingOptions(dropdownOptions(f, cur), query).forEach(v => {
    const s = String(v || "").trim();
    if (!s || have.has(s)) return;
    have.add(s);
    cur.push(coerce(s, S.base[key]));
  });
  setFieldValue(key, cur);
  markDirty();
  refreshField(key);
}
function addListValue(key, v) {
  v = String(v || "").trim();
  if (!key || !v) return;
  const raw = fieldValue(key);
  const cur = Array.isArray(raw) ? raw.map(String) : (raw ? [String(raw)] : []);
  if (cur.indexOf(v) < 0) cur.push(coerce(v, S.base[key]));
  setFieldValue(key, cur);
  markDirty();
  refreshField(key);
}
function paintAddMenu(input) {
  const key = input.getAttribute("data-add-q");
  const wrap = input.closest(".addfind");
  const menu = wrap && wrap.querySelector(".addfind-menu");
  if (!menu) return;
  const f = (S.schema || []).find(x => x.key === key);
  const sides = sheetBulkSides(f, input.value);
  const view = filterAddOptions(dropdownOptions(f, fieldValue(key) || []), input.value);
  const sheetBtn = sides.length
    ? `<div class="addfind-sheets">${sides.map((s) =>
        `<button type="button" class="addfind-item addfind-sheet" data-add-sheet="${esc(s.which)}" data-add-pick-key="${esc(key)}" title="加入 ${esc(s.which)} 工作表中尚未选中的全部特征">${esc(s.which)}（${s.cols.length}）</button>`
      ).join("")}</div>`
    : "";
  const rows = view.rows.map(o =>
    `<button type="button" class="addfind-item" data-add-pick="${esc(o)}" data-add-pick-key="${esc(key)}">${esc(o)}</button>`
  ).join("");
  let note = "";
  if (!view.rows.length && !sheetBtn) {
    note = `<div class="addfind-note">${String(input.value || "").trim() ? "无匹配" : "无可选列"}</div>`;
  } else if (view.more > 0) {
    note = `<div class="addfind-note">${String(input.value || "").trim()
      ? "还有 " + view.more + " 条，请继续输入"
      : "共 " + (view.rows.length + view.more) + " 列，输入关键字筛选"}</div>`;
  }
  menu.innerHTML = sheetBtn + rows + note;
  menu.hidden = false;
}

function refreshClinRoleDropdowns() {
  if (!S.values || !S.schema) return;
  syncClinRoles(true);
  for (const key of clinAddselectKeys()) {
    const f = S.schema.find(x => x.key === key);
    if (!f) continue;
    const have = fieldValue(key) || [];
    f.value = have;
    f.add_options = dropdownOptions(f, have);
    if (key === "SKIP_CLIN_FEATURES") f.locked_options = implicitSkip();
  }
}

function grpKeys() {
  return [
    "GROUP_KEEP", "PAIRWISE_GROUPS", "PAIRWISE_ALL",
    "SUBGROUP_COL",
    "GROUP_FROM_ID", "GROUP_FROM_ID_SEP", "POSITIVE_GROUP_LABEL",
    "GROUP_DISPLAY_POS", "GROUP_DISPLAY_NEG", "GROUP_LEVEL_DISPLAY",
  ];
}

function levelDisplayMap() {
  const raw = fieldValue("GROUP_LEVEL_DISPLAY");
  return (raw && typeof raw === "object" && !Array.isArray(raw)) ? raw : {};
}

function levelDisplay(raw) {
  const s = String(raw == null ? "" : raw).trim();
  const d = levelDisplayMap()[s];
  const out = d == null ? "" : String(d).trim();
  return out || s;
}

function setLevelDisplay(raw, name) {
  const s = String(raw == null ? "" : raw).trim();
  if (!s) return;
  const map = Object.assign({}, levelDisplayMap());
  const d = String(name == null ? "" : name).trim();
  if (!d || d === s) delete map[s];
  else map[s] = d;
  setFieldValue("GROUP_LEVEL_DISPLAY", map);
}

function grpOverridden() {
  return grpKeys().some(k => fieldHasOverride(k));
}

function generateAllPairs(levels) {
  const labs = uniqKeep(levels || []);
  const out = [];
  for (let i = 0; i < labs.length; i++) {
    for (let j = i + 1; j < labs.length; j++) {
      out.push([labs[i], labs[j]]);
    }
  }
  return out;
}

function pairDisplayNames(pos, neg) {
  const oc = pairOwnerOutcome();
  const k = pairKey(pos, neg);
  const o = (oc && pairOverridesOf(oc, k)) || {};
  return {
    pos: String(o.GROUP_DISPLAY_POS || levelDisplay(pos) || "").trim() || pos,
    neg: String(o.GROUP_DISPLAY_NEG || levelDisplay(neg) || "").trim() || neg,
  };
}

function pairTabLabel(pos, neg) {
  const d = pairDisplayNames(pos, neg);
  return d.pos + " vs " + d.neg;
}

function replaceResolvedPair(oldPos, oldNeg, newPos, newNeg) {
  const next = resolvedPairs().map(p => (
    p[0] === oldPos && p[1] === oldNeg ? [newPos, newNeg] : p
  ));
  setFieldValue("PAIRWISE_GROUPS", next);
  setFieldValue("PAIRWISE_ALL", true);
  const oc = pairOwnerOutcome();
  const oldK = pairKey(oldPos, oldNeg);
  const newK = pairKey(newPos, newNeg);
  if (oc && oldK !== newK && S.values.PAIRWISE_OVERRIDES
      && S.values.PAIRWISE_OVERRIDES[oc]
      && S.values.PAIRWISE_OVERRIDES[oc][oldK]) {
    const ov = S.values.PAIRWISE_OVERRIDES[oc][oldK];
    if (newPos === oldNeg && newNeg === oldPos) {
      const dp = ov.GROUP_DISPLAY_POS;
      const dn = ov.GROUP_DISPLAY_NEG;
      if (dp != null) ov.GROUP_DISPLAY_NEG = dp;
      if (dn != null) ov.GROUP_DISPLAY_POS = dn;
    }
    S.values.PAIRWISE_OVERRIDES[oc][newK] = ov;
    delete S.values.PAIRWISE_OVERRIDES[oc][oldK];
  }
  S.pairTab = newK;
}

function fillSubgroupColSelect() {
  const sel = $("grpSubCol");
  if (!sel) return;
  const clin = uniqKeep((S.preview && S.preview.clin_cols) || []);
  const outcomes = outcomeCols();
  const def = defaultSubgroupCol();
  const stored = String(fieldValue("SUBGROUP_COL") || "").trim();
  const seen = new Set();
  const opts = ['<option value="">（空白）</option>'];
  const push = (c, tag) => {
    const s = String(c || "").trim();
    if (!s || seen.has(s)) return;
    seen.add(s);
    opts.push(`<option value="${esc(s)}">${esc(s)}${tag || ""}</option>`);
  };
  if (def) push(def, "（本结局）");
  for (const c of outcomes) push(c, c === def ? "" : "（结局）");
  for (const c of clin) push(c, "");
  sel.innerHTML = opts.join("");
  if (stored && seen.has(stored)) sel.value = stored;
  else sel.value = "";
}

function groupLevelOptions(levels, selected) {
  return levels.map(o =>
    `<option value="${esc(o)}"${o === selected ? " selected" : ""}>${esc(o)}</option>`
  ).join("");
}

function groupLevelRow(g, levels) {
  const d = levelDisplay(g);
  return `<div class="grp-disp-row">
    <select class="grp-lv" data-glv="${esc(g)}" data-tip="自动识别的组（列值）。">${groupLevelOptions(levels, g)}</select>
    <input class="grp-disp-in" data-gdisp="${esc(g)}" value="${esc(d)}" placeholder="展示名" data-tip="表头、图例和结果目录用这个名字。"/>
    ${levelPolaritySelect(g)}
  </div>`;
}

function renderGroupCard() {
  if (!$("grpChips") || !S.values || !Object.keys(S.values).length) return;
  const allLevels = groupLevels();
  const onPair = !!pairTab();
  if ($("grpSubBlock")) $("grpSubBlock").hidden = onPair;
  fillSubgroupColSelect();
  if ($("grpLevelsBlock")) $("grpLevelsBlock").hidden = false;
  const levelLab = $("grpLevelsBlock") && $("grpLevelsBlock").querySelector("label.k");
  if (levelLab) {
    levelLab.textContent = "分组";
    levelLab.setAttribute("data-tip", "自动识别的组。下拉是列值，接着填展示名，再选阴性、阳性或不选。表头用展示名。");
  }
  if (!allLevels.length) {
    const multiPub = outcomeCols().length > 1 && !outcomeTab();
    $("grpChips").innerHTML = multiPub
      ? '<span class="hint">多结局：进入各结局页后，再看各组。</span>'
      : '<span class="hint">尚未检出。载入数据后从结局列读取。</span>';
  } else {
    $("grpChips").innerHTML = allLevels.map(g => groupLevelRow(g, allLevels)).join("");
  }
  const hint = [];
  if (allLevels.length) {
    hint.push("检出 " + allLevels.length + " 组: " + allLevels.map(g => g + "→" + levelDisplay(g)).join("、") + "。");
    hint.push("表头用展示名。阳性=1，阴性=0，不选仍留在总表。");
  }
  const hintText = hint.join(" ");
  if ($("grpHint")) {
    $("grpHint").textContent = "";
    $("grpHint").hidden = true;
    if (hintText) $("grpHint").setAttribute("data-tip", hintText);
    else $("grpHint").removeAttribute("data-tip");
  }
  const grpH2 = document.querySelector("#grpCard > h2");
  if (grpH2) {
    const base = "自动识别全部组。每一行：组下拉、展示名、阴性 / 阳性 / 不选。表头用展示名。";
    grpH2.setAttribute("data-tip", hintText ? base + " " + hintText : base);
  }
  if ($("grpInherit")) $("grpInherit").hidden = !(outcomeTab() && !grpOverridden());
  if ($("grpUnov")) $("grpUnov").hidden = !(outcomeTab() && grpOverridden());
}

function readGroupRows() {
  const box = $("grpChips");
  if (!box) return [];
  const out = [];
  const seen = new Set();
  box.querySelectorAll(".grp-disp-row").forEach((row) => {
    const lv = row.querySelector(".grp-lv");
    const disp = row.querySelector("[data-gdisp]");
    const pol = row.querySelector("[data-gpol]");
    const level = String((lv && lv.value) || "").trim();
    if (!level || seen.has(level)) return;
    seen.add(level);
    out.push({
      level: level,
      display: String((disp && disp.value) || "").trim(),
      pol: pol ? String(pol.value || "") : "",
    });
  });
  return out;
}

function applyGroupRows(changed) {
  if (!$("grpChips") || !S.values) return;
  if (changed && changed.matches && changed.matches("[data-gpol]")) {
    const val = String(changed.value || "");
    if (val === "0" || val === "1") {
      $("grpChips").querySelectorAll("[data-gpol]").forEach((el) => {
        if (el !== changed && el.value === val) el.value = "";
      });
    }
  }
  const rows = readGroupRows();
  let pos = "";
  let neg = "";
  rows.forEach((r) => {
    setLevelDisplay(r.level, r.display);
    writeLevelCode(r.level, r.pol);
    if (r.pol === "1") pos = r.level;
    else if (r.pol === "0") neg = r.level;
  });
  if (pos && neg && pos === neg) neg = "";
  if (pos) {
    const pr = rows.find((r) => r.level === pos);
    setFieldValue("POSITIVE_GROUP_LABEL", pos);
    setFieldValue("GROUP_DISPLAY_POS", (pr && pr.display) || levelDisplay(pos) || pos);
  } else {
    setFieldValue("POSITIVE_GROUP_LABEL", "");
    setFieldValue("GROUP_DISPLAY_POS", "");
  }
  if (neg) {
    const nr = rows.find((r) => r.level === neg);
    setFieldValue("GROUP_DISPLAY_NEG", (nr && nr.display) || levelDisplay(neg) || neg);
  } else {
    setFieldValue("GROUP_DISPLAY_NEG", "");
  }
  const unset = rows.filter((r) => r.pol !== "0" && r.pol !== "1");
  if (unset.length && pos && neg) setFieldValue("GROUP_KEEP", [pos, neg]);
  else setFieldValue("GROUP_KEEP", []);
  setFieldValue("PAIRWISE_GROUPS", []);
  setFieldValue("PAIRWISE_ALL", false);
  markDirty();
  renderForm();
  renderPairTabs();
  renderGroupCard();
}

function applyGroupCard() {
  applyGroupRows();
}

function applySubgroupCol() {
  if (!$("grpSubCol") || !S.values) return;
  const v = String($("grpSubCol").value || "").trim();
  const def = defaultSubgroupCol();
  setFieldValue("SUBGROUP_COL", v && v !== def ? v : "");
  setFieldValue("GROUP_KEEP", []);
  setFieldValue("PAIRWISE_GROUPS", []);
  setFieldValue("PAIRWISE_ALL", false);
  const lv = columnLevels(v || def);
  const oldPos = String(fieldValue("POSITIVE_GROUP_LABEL") || "");
  if (subgroupIsOutcome() && oldPos && !lv.includes(oldPos)) {
    setFieldValue("POSITIVE_GROUP_LABEL", "");
  }
  markDirty();
  renderPairTabs();
  renderGroupCard();
}

function bindGroupCard() {
  if (!$("grpChips")) return;
  if ($("grpSubCol")) $("grpSubCol").addEventListener("change", applySubgroupCol);
  if ($("grpUnov")) {
    $("grpUnov").addEventListener("click", (e) => {
      e.preventDefault();
      grpKeys().forEach(clearFieldOverride);
      renderForm();
      markDirty();
    });
  }
  if ($("grpChips").dataset.grpBound) return;
  $("grpChips").dataset.grpBound = "1";
  $("grpChips").addEventListener("change", (e) => {
    const lv = e.target.closest(".grp-lv");
    if (lv) {
      const prev = lv.getAttribute("data-glv") || "";
      const next = String(lv.value || "");
      if (prev && next && prev !== next) {
        const other = [...$("grpChips").querySelectorAll(".grp-lv")].find(
          (el) => el !== lv && (el.value === next || el.getAttribute("data-glv") === next)
        );
        if (other) {
          other.value = prev;
          other.setAttribute("data-glv", prev);
        }
        lv.setAttribute("data-glv", next);
        const row = lv.closest(".grp-disp-row");
        const disp = row && row.querySelector("[data-gdisp]");
        const pol = row && row.querySelector("[data-gpol]");
        if (disp) disp.setAttribute("data-gdisp", next);
        if (pol) pol.setAttribute("data-gpol", next);
        if (other) {
          const orow = other.closest(".grp-disp-row");
          const od = orow && orow.querySelector("[data-gdisp]");
          const op = orow && orow.querySelector("[data-gpol]");
          if (od) od.setAttribute("data-gdisp", prev);
          if (op) op.setAttribute("data-gpol", prev);
        }
      }
      applyGroupRows(lv);
      return;
    }
    const pol = e.target.closest("[data-gpol]");
    if (pol) {
      applyGroupRows(pol);
      return;
    }
    const inp = e.target.closest("[data-gdisp]");
    if (!inp) return;
    applyGroupRows(inp);
  });
}

function outcomeCols() {
  return uniqKeep((S.values && S.values.OUTCOME_COLS) || []);
}

function outcomeTab() {
  return S.outcomeTab || "";
}

function pairTab() {
  return S.pairTab || "";
}

function pairKey(pos, neg) {
  return String(pos || "").trim() + "_vs_" + String(neg || "").trim();
}

function parsePairKey(key) {
  const s = String(key || "");
  const i = s.indexOf("_vs_");
  if (i <= 0) return null;
  const a = s.slice(0, i).trim();
  const b = s.slice(i + 4).trim();
  return (a && b && a !== b) ? [a, b] : null;
}

function resolvedPairs() {
  const raw = Array.isArray(fieldValue("PAIRWISE_GROUPS")) ? fieldValue("PAIRWISE_GROUPS") : [];
  const parsed = [];
  const seen = new Set();
  for (const p of raw) {
    if (!Array.isArray(p) || p.length < 2) continue;
    const a = String(p[0] == null ? "" : p[0]).trim();
    const b = String(p[1] == null ? "" : p[1]).trim();
    if (!a || !b || a === b) continue;
    const k = pairKey(a, b);
    if (seen.has(k)) continue;
    seen.add(k);
    parsed.push([a, b]);
  }
  if (parsed.length) return parsed;
  if (!fieldValue("PAIRWISE_ALL")) return [];
  const keep = Array.isArray(fieldValue("GROUP_KEEP"))
    ? fieldValue("GROUP_KEEP").map(x => String(x).trim()).filter(Boolean)
    : [];
  const labels = keep.length >= 2 ? uniqKeep(keep) : groupLevels();
  const out = [];
  for (let i = 0; i < labels.length; i++) {
    for (let j = i + 1; j < labels.length; j++) {
      out.push([labels[i], labels[j]]);
    }
  }
  return out;
}

function pairTabsEnabled() {
  return inferGroupMode() === "allpairs" && resolvedPairs().length >= 3;
}

function showPairTabs() {
  if (S.ioTab) return false;
  if (!pairTabsEnabled()) return false;
  return outcomeCols().length < 2 || !!outcomeTab();
}

function pairOwnerOutcome() {
  const t = outcomeTab();
  if (t) return t;
  const cols = outcomeCols();
  return cols.length === 1 ? cols[0] : "";
}


function overridesOf(outcome) {
  const all = (S.values && S.values.OUTCOME_OVERRIDES) || {};
  const o = all[outcome];
  return o && typeof o === "object" ? o : null;
}

function pairOverridesOf(outcome, pair) {
  const all = (S.values && S.values.PAIRWISE_OVERRIDES) || {};
  const byOut = all[outcome];
  if (!byOut || typeof byOut !== "object") return null;
  const o = byOut[pair];
  return o && typeof o === "object" ? o : null;
}

function fieldHasOverride(key) {
  const pair = pairTab();
  const oc = pairOwnerOutcome();
  if (pair && oc && PAIR_OVERRIDE_KEYS.has(key)) {
    const o = pairOverridesOf(oc, pair);
    return !!(o && Object.prototype.hasOwnProperty.call(o, key));
  }
  const t = outcomeTab();
  if (!t || !OUTCOME_OVERRIDE_KEYS.has(key)) return false;
  const o = overridesOf(t);
  return !!(o && Object.prototype.hasOwnProperty.call(o, key));
}

function fieldValue(key) {
  const pair = pairTab();
  const oc = pairOwnerOutcome();
  if (pair && oc && PAIR_OVERRIDE_KEYS.has(key)) {
    const o = pairOverridesOf(oc, pair);
    if (o && Object.prototype.hasOwnProperty.call(o, key)) return o[key];
    if (key === "SUBGROUP_COL") return "";
    const parsed = parsePairKey(pair);
    if (parsed) {
      if (key === "GROUP_DISPLAY_POS") return levelDisplay(parsed[0]);
      if (key === "GROUP_DISPLAY_NEG") return levelDisplay(parsed[1]);
    }
  }
  const t = outcomeTab();
  if (t && OUTCOME_OVERRIDE_KEYS.has(key)) {
    const o = overridesOf(t);
    if (o && Object.prototype.hasOwnProperty.call(o, key)) return o[key];
    if (key === "SUBGROUP_COL") return "";
    // 结局页没有手填展示名时用分组卡。不要改成结局列名 / non_结局列名。
  }
  return S.values[key];
}

/** Shared continuous vars: UI edits CORR_* and mirrors PARTIAL_* (settings.ini keeps both). */
const SHARED_CORR_PARTIAL = {
  CORR_PREDICTORS: "PARTIAL_X",
  CORR_REFS: "PARTIAL_Y",
  PARTIAL_X: "CORR_PREDICTORS",
  PARTIAL_Y: "CORR_REFS",
};
function mirrorSharedCorrPartial(key, val) {
  const other = SHARED_CORR_PARTIAL[key];
  if (!other || !S.values) return;
  const copy = Array.isArray(val) ? val.slice() : val;
  // write twin without re-entering mirror
  const pair = pairTab();
  const oc = pairOwnerOutcome();
  if (pair && oc && PAIR_OVERRIDE_KEYS.has(other)) {
    if (!S.values.PAIRWISE_OVERRIDES || typeof S.values.PAIRWISE_OVERRIDES !== "object") S.values.PAIRWISE_OVERRIDES = {};
    if (!S.values.PAIRWISE_OVERRIDES[oc] || typeof S.values.PAIRWISE_OVERRIDES[oc] !== "object") S.values.PAIRWISE_OVERRIDES[oc] = {};
    if (!S.values.PAIRWISE_OVERRIDES[oc][pair] || typeof S.values.PAIRWISE_OVERRIDES[oc][pair] !== "object") S.values.PAIRWISE_OVERRIDES[oc][pair] = {};
    S.values.PAIRWISE_OVERRIDES[oc][pair][other] = copy;
    return;
  }
  const t = outcomeTab();
  if (t && OUTCOME_OVERRIDE_KEYS.has(other)) {
    if (!S.values.OUTCOME_OVERRIDES || typeof S.values.OUTCOME_OVERRIDES !== "object") S.values.OUTCOME_OVERRIDES = {};
    if (!S.values.OUTCOME_OVERRIDES[t] || typeof S.values.OUTCOME_OVERRIDES[t] !== "object") S.values.OUTCOME_OVERRIDES[t] = {};
    S.values.OUTCOME_OVERRIDES[t][other] = copy;
    return;
  }
  S.values[other] = copy;
}
function setFieldValue(key, val) {
  const pair = pairTab();
  const oc = pairOwnerOutcome();
  if (pair && oc && PAIR_OVERRIDE_KEYS.has(key)) {
    if (!S.values.PAIRWISE_OVERRIDES || typeof S.values.PAIRWISE_OVERRIDES !== "object") {
      S.values.PAIRWISE_OVERRIDES = {};
    }
    if (!S.values.PAIRWISE_OVERRIDES[oc] || typeof S.values.PAIRWISE_OVERRIDES[oc] !== "object") {
      S.values.PAIRWISE_OVERRIDES[oc] = {};
    }
    if (!S.values.PAIRWISE_OVERRIDES[oc][pair] || typeof S.values.PAIRWISE_OVERRIDES[oc][pair] !== "object") {
      S.values.PAIRWISE_OVERRIDES[oc][pair] = {};
    }
    S.values.PAIRWISE_OVERRIDES[oc][pair][key] = val;
    mirrorSharedCorrPartial(key, val);
    return;
  }
  const t = outcomeTab();
  if (t && OUTCOME_OVERRIDE_KEYS.has(key)) {
    if (!S.values.OUTCOME_OVERRIDES || typeof S.values.OUTCOME_OVERRIDES !== "object") {
      S.values.OUTCOME_OVERRIDES = {};
    }
    if (!S.values.OUTCOME_OVERRIDES[t] || typeof S.values.OUTCOME_OVERRIDES[t] !== "object") {
      S.values.OUTCOME_OVERRIDES[t] = {};
    }
    S.values.OUTCOME_OVERRIDES[t][key] = val;
    mirrorSharedCorrPartial(key, val);
    return;
  }
  S.values[key] = val;
  mirrorSharedCorrPartial(key, val);
}

function clearFieldOverride(key) {
  const pair = pairTab();
  const oc = pairOwnerOutcome();
  if (pair && oc) {
    const o = pairOverridesOf(oc, pair);
    if (o && Object.prototype.hasOwnProperty.call(o, key)) {
      delete o[key];
      if (!Object.keys(o).length) {
        delete S.values.PAIRWISE_OVERRIDES[oc][pair];
        if (!Object.keys(S.values.PAIRWISE_OVERRIDES[oc]).length) {
          delete S.values.PAIRWISE_OVERRIDES[oc];
        }
      }
    }
    return;
  }
  const t = outcomeTab();
  const o = t && overridesOf(t);
  if (o && Object.prototype.hasOwnProperty.call(o, key)) {
    delete o[key];
    if (!Object.keys(o).length) delete S.values.OUTCOME_OVERRIDES[t];
  }
}

function runScopeText() {
  if (S.ioTab) return "请先在左侧选择项目和数据，再到公共页运行";
  const tab = outcomeTab();
  const cols = outcomeCols();
  if (tab) return "子结局页：只跑「" + tab + "」";
  if (cols.length > 1) return "公共页：依次跑全部 " + cols.length + " 个结局（" + cols.join("、") + "）";
  if (cols.length === 1) return "跑结局「" + cols[0] + "」";
  return "跑当前配置";
}

function syncRunScope() {
  const tab = outcomeTab();
  const cols = outcomeCols();
  const scope = runScopeText();
  const hint = $("runScopeHint");
  if (hint) {
    hint.textContent = "配置载入后才可运行。" + scope + "。开关只用 settings.ini。";
  }
  const btn = $("btnRun");
  if (btn) {
    btn.setAttribute("data-tip", S.ioTab
      ? "请先在左侧选择项目和数据。"
      : (tab
        ? ("只跑当前结局 " + tab + "。用本页配置覆盖 ini，不改源文件。")
        : (cols.length > 1
          ? ("公共页依次跑全部 " + cols.length + " 个结局。用本页配置覆盖 ini，不改源文件。")
          : "用本页完整配置跑统计（覆盖 ini，不改源文件）。")));
  }
}

function renderOutcomeTabs() {
  const bar = $("outcomeTabBar");
  if (!bar) return;
  const cols = outcomeCols();
  if (S.outcomeTab && cols.indexOf(S.outcomeTab) < 0) S.outcomeTab = "";
  bar.hidden = false;
  const items = [{key: "", lab: "公共"}];
  if (cols.length >= 2) cols.forEach(t => items.push({key: t, lab: t}));
  const cur = outcomeTab() || "";
  bar.innerHTML = items.map(it => {
    const on = (it.key || "") === cur;
    const cls = "otab" + (on ? " on" : "");
    return `<button type="button" class="${cls}" data-otab="${esc(it.key)}">${esc(it.lab)}</button>`;
  }).join("");
  syncRunScope();
}

function bindOutcomeTabs() {
  const bar = $("outcomeTabBar");
  if (!bar || bar.dataset.bound) return;
  bar.dataset.bound = "1";
  bar.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-otab]");
    if (!btn) return;
    const t = btn.getAttribute("data-otab") || "";
    S.ioTab = false;
    S.outcomeTab = t;
    S.pairTab = "";
    renderOutcomeTabs();
    renderForm();
    updateDiff();
    syncRunScope();
  });
}

function renderPairTabs() {
  const bar = $("pairTabBar");
  if (!bar) return;
  if (!showPairTabs()) {
    bar.hidden = true;
    if (!pairTabsEnabled()) S.pairTab = "";
    return;
  }
  const pairs = resolvedPairs();
  const keys = pairs.map(p => pairKey(p[0], p[1]));
  if (S.pairTab && keys.indexOf(S.pairTab) < 0) S.pairTab = "";
  bar.hidden = false;
  const cur = pairTab();
  const items = [{key: "", lab: "公共"}].concat(pairs.map(p => ({
    key: pairKey(p[0], p[1]),
    lab: pairTabLabel(p[0], p[1]),
  })));
  bar.innerHTML = items.map(it => {
    const on = (it.key || "") === cur;
    return `<button type="button" class="otab${on ? " on" : ""}" data-ptab="${esc(it.key)}">${esc(it.lab)}</button>`;
  }).join("");
}

function bindPairTabs() {
  const bar = $("pairTabBar");
  if (!bar || bar.dataset.bound) return;
  bar.dataset.bound = "1";
  bar.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-ptab]");
    if (!btn) return;
    S.pairTab = btn.getAttribute("data-ptab") || "";
    renderPairTabs();
    renderForm();
    updateDiff();
  });
}

function syncQcHtmlToggle() {
  const el = $("doQcHtml");
  if (!el) return;
  const on = !!(typeof fieldValue === "function" ? fieldValue("DO_QC_REPORT") : S.values && S.values.DO_QC_REPORT);
  el.checked = on;
  if (el.dataset.bound) return;
  el.dataset.bound = "1";
  el.addEventListener("change", () => {
    if (typeof setFieldValue === "function") setFieldValue("DO_QC_REPORT", el.checked);
    else if (S.values) S.values.DO_QC_REPORT = el.checked;
    if (typeof markDirty === "function") markDirty();
  });
}

function syncBaselineToggles() {
  [
    ["doBaselineAll", "DO_BASELINE_ALL"],
    ["doBaselineTt", "DO_BASELINE_TRAIN_TEST"],
  ].forEach(([id, key]) => {
    const el = $(id);
    if (!el) return;
    el.checked = switchOn(key);
    if (el.dataset.bound) return;
    el.dataset.bound = "1";
    el.addEventListener("change", () => {
      setFieldValue(key, el.checked);
      markDirty();
    });
  });
}

function syncPartialGates() {
  const on = switchOn("DO_PARTIAL");
  document.querySelectorAll("[data-partial-gate]").forEach((el) => {
    el.classList.toggle("partial-off", !on);
  });
}

function renderPartialField(f) {
  const gate = f.key === "PARTIAL_METHOD" || f.key === "PARTIAL_CONTROLS";
  const html = renderField(f);
  if (!gate) return html;
  const off = switchOn("DO_PARTIAL") ? "" : " partial-off";
  return `<div class="partial-gate${off}" data-partial-gate="${esc(f.key)}">${html}</div>`;
}

function isHiddenField(f) {
  if (ALWAYS_SHOW_KEYS.has(f.key)) return false;
  if (HIDDEN_FORM_KEYS.has(f.key)) return true;
  if (CARD_ONLY_KEYS.has(f.key)) return true;
  const multi = outcomeCols().length > 1;
  const tab = outcomeTab();
  const pairsOn = showPairTabs();
  const ptab = pairTab();
  if (multi && !tab && PUBLIC_HIDE_WHEN_MULTI.has(f.key)) return true;
  if (pairsOn) {
    if (ptab) {
      if (!PUBLIC_HIDE_WHEN_MULTI.has(f.key)) return true;
    } else if (multi) {
      if (!PUBLIC_HIDE_WHEN_MULTI.has(f.key)) return true;
    } else if (PUBLIC_HIDE_WHEN_MULTI.has(f.key)) {
      return true;
    }
    return false;
  }
  if (tab) {
    if (!PUBLIC_HIDE_WHEN_MULTI.has(f.key)) return true;
  }
  return false;
}

function ensureConsoleFields() {
  if (!S.values) S.values = {};
  if (!Array.isArray(S.values.DCA_CURVES) || !S.values.DCA_CURVES.length) {
    S.values.DCA_CURVES = ["Clinical", "RadScore", "Combined"];
  }
  if (S.schema && !S.schema.some(f => f.key === "DCA_CURVES")) {
    const proto = S.schema.find(f => f.key === "DO_DCA") || {
      section: "switch", section_title: "开关",
    };
    S.schema.push({
      key: "DCA_CURVES",
      label: "DCA 曲线指标",
      help: "画进 DCA 的曲线。可多选 Clinical / RadScore / Combined 或临床列。",
      widget: "checklist",
      hot: false,
      section: proto.section || "dca",
      section_title: proto.section_title || "DCA",
      add_options: ["Clinical", "RadScore", "Combined"],
    });
  }
}

let ioSheetOpts = [];
let ioRadIdOpts = [];
let ioClinIdOpts = [];

function uniqOpts(items) {
  const have = [];
  const seen = new Set();
  for (const x of items || []) {
    const s = String(x == null ? "" : x).trim();
    if (!s || seen.has(s)) continue;
    seen.add(s);
    have.push(s);
  }
  return have;
}
const SHEET_ALIASES = {
  cln: ["clin", "clinical"],
  clin: ["cln", "clinical"],
  rad: ["radiomics"],
};
function pickExistingSheet(cur, items, fallback) {
  const have = uniqOpts(items);
  const want = String(cur == null ? "" : cur).trim() || String(fallback || "").trim();
  if (!want) return "";
  if (have.indexOf(want) >= 0) return want;
  const low = want.toLowerCase();
  const ci = have.find((s) => String(s).toLowerCase() === low);
  if (ci) return ci;
  for (const a of (SHEET_ALIASES[low] || [])) {
    const hit = have.find((s) => String(s).toLowerCase() === String(a).toLowerCase());
    if (hit) return hit;
  }
  return "";
}
function fillSelect(el, items, keep, fallback) {
  if (typeof el === "string") el = $(el);
  if (!el) return;
  let cur = keep != null ? String(keep) : String(el.value || "");
  if (!cur && fallback) cur = String(fallback);
  const have = uniqOpts(items);
  if (cur && have.indexOf(cur) < 0) cur = pickExistingSheet(cur, have, fallback);
  let html = "<option value=\"\">（空）</option>";
  for (const s of have) html += "<option value=\"" + esc(s) + "\">" + esc(s) + "</option>";
  if (cur && have.indexOf(cur) < 0) html += "<option value=\"" + esc(cur) + "\">" + esc(cur) + "</option>";
  el.innerHTML = html;
  el.value = cur || "";
}
function fillDatalist(id, items) {
  const el = $(id);
  if (!el) return;
  el.innerHTML = uniqOpts(items).map(s => "<option value=\"" + esc(s) + "\"></option>").join("");
}
const EXCLUDE_EMPTY_LABELS = {
  EXCLUDE_FILE: "（空＝默认 false_classification.xlsx）",
  EXCLUDE_SHEET: "（空＝默认 exc）",
  EXCLUDE_COL: "（空＝默认 exc）",
  EXCLUDE_ROW: "（空＝整列当 ID）",
};
function growItems(v) {
  if (Array.isArray(v)) {
    const out = [];
    const seen = new Set();
    v.forEach((x) => {
      const s = String(x == null ? "" : x).trim();
      if (!s || seen.has(s)) return;
      seen.add(s);
      out.push(s);
    });
    return out;
  }
  const s = String(v == null ? "" : v).trim();
  return s ? [s] : [];
}
function packGrow(items) {
  const out = growItems(items);
  if (!out.length) return "";
  if (out.length === 1) return out[0];
  return out;
}
function excludeXlsxOptions() {
  const p = S.projects.find(x => x.name === ($("project") && $("project").value));
  const names = ((p && p.xlsx) || []).map(x => String(x).split(/[/\\]/).pop());
  growItems(fieldValue("EXCLUDE_FILE")).forEach((cur) => {
    const base = String(cur).split(/[/\\]/).pop();
    if (base && names.indexOf(base) < 0) names.unshift(base);
  });
  return uniqOpts(names);
}
function fillGrowOptions(key, items, emptyLabel, moreLabel) {
  const box = document.querySelector('.grow-rows[data-key="' + key + '"]');
  if (!box) return;
  const selects = [...box.querySelectorAll("[data-grow]")];
  const chosen = selects.map((el) => String(el.value || ""));
  const any = chosen.some(Boolean);
  selects.forEach((el, i) => {
    const cur = chosen[i];
    const blank = (!cur && any) ? (moreLabel || emptyLabel) : emptyLabel;
    const taken = new Set(chosen.filter((v, j) => j !== i && v));
    let html = "<option value=\"\">" + esc(blank || "（不设）") + "</option>";
    uniqOpts(items).forEach((s) => {
      if (!s || (taken.has(s) && s !== cur)) return;
      html += "<option value=\"" + esc(s) + "\">" + esc(s) + "</option>";
    });
    if (cur && uniqOpts(items).indexOf(cur) < 0) {
      html += "<option value=\"" + esc(cur) + "\">" + esc(cur) + "</option>";
    }
    el.innerHTML = html;
    el.value = cur || "";
  });
}
function fillExcludeSelect(key, items, emptyLabel) {
  if (key === "EXCLUDE_FILE") {
    const f = (S.schema || []).find((x) => x.key === key) || {};
    fillGrowOptions(key, items, emptyLabel, f.more_label || "（再选一个文件）");
    return;
  }
  const el = document.querySelector('.field-group.exclude [data-key="' + key + '"]');
  if (!el || el.tagName !== "SELECT") return;
  const cur = String(fieldValue(key) ?? el.value ?? "");
  const have = uniqOpts(items);
  let html = "<option value=\"\">" + esc(emptyLabel || EXCLUDE_EMPTY_LABELS[key] || "（空）") + "</option>";
  for (const s of have) html += "<option value=\"" + esc(s) + "\">" + esc(s) + "</option>";
  if (cur && have.indexOf(cur) < 0) html += "<option value=\"" + esc(cur) + "\">" + esc(cur) + "</option>";
  el.innerHTML = html;
  el.value = cur || "";
}
async function refreshExcludeDownstream(fromKey, fileOverride) {
  const files = growItems(fieldValue("EXCLUDE_FILE"));
  const file = fileOverride != null ? String(fileOverride) : (files[0] || "");
  let sheet = String(fieldValue("EXCLUDE_SHEET") || "");
  let col = String(fieldValue("EXCLUDE_COL") || "");
  if (fromKey === "EXCLUDE_FILE") { sheet = ""; col = ""; }
  else if (fromKey === "EXCLUDE_SHEET") { col = ""; }
  if (fromKey === "EXCLUDE_FILE_ADD") {
    if (!file) return;
    try {
      const j = await fetchXlsxMeta(file, "", "");
      const el = document.querySelector('.field-group.exclude [data-key="EXCLUDE_SHEET"]');
      const have = [];
      if (el) [...el.options].forEach((o) => { if (o.value) have.push(o.value); });
      (j.sheets || []).forEach((s) => { if (have.indexOf(s) < 0) have.push(s); });
      fillExcludeSelect("EXCLUDE_SHEET", have, EXCLUDE_EMPTY_LABELS.EXCLUDE_SHEET);
    } catch (e) {}
    return;
  }
  try {
    const j = await fetchXlsxMeta(file, sheet, col);
    if (fromKey === "EXCLUDE_FILE") {
      fillExcludeSelect("EXCLUDE_SHEET", j.sheets || [], EXCLUDE_EMPTY_LABELS.EXCLUDE_SHEET);
      sheet = String(fieldValue("EXCLUDE_SHEET") || "");
    }
    if (fromKey === "EXCLUDE_FILE" || fromKey === "EXCLUDE_SHEET") {
      const j2 = (j.columns && j.columns.length && fromKey !== "EXCLUDE_FILE")
        ? j
        : await fetchXlsxMeta(file, sheet, "");
      fillExcludeSelect("EXCLUDE_COL", j2.columns || [], EXCLUDE_EMPTY_LABELS.EXCLUDE_COL);
      col = String(fieldValue("EXCLUDE_COL") || "");
    }
    const j3 = (j.values && j.values.length && fromKey === "EXCLUDE_COL")
      ? j
      : await fetchXlsxMeta(file, sheet, col);
    fillExcludeSelect("EXCLUDE_ROW", j3.values || [], EXCLUDE_EMPTY_LABELS.EXCLUDE_ROW);
  } catch (e) {}
}
function bindExcludeCascade() {
  const box = document.querySelector(".field-group.exclude");
  if (!box) return;
  fillExcludeSelect("EXCLUDE_FILE", excludeXlsxOptions(), EXCLUDE_EMPTY_LABELS.EXCLUDE_FILE);
  const prev = (S.preview && S.preview.exclude) || {};
  if (prev.sheets && prev.sheets.length) fillExcludeSelect("EXCLUDE_SHEET", prev.sheets, EXCLUDE_EMPTY_LABELS.EXCLUDE_SHEET);
  if (prev.columns && prev.columns.length) fillExcludeSelect("EXCLUDE_COL", prev.columns, EXCLUDE_EMPTY_LABELS.EXCLUDE_COL);
  if (prev.values && prev.values.length) fillExcludeSelect("EXCLUDE_ROW", prev.values, EXCLUDE_EMPTY_LABELS.EXCLUDE_ROW);
  ["EXCLUDE_SHEET", "EXCLUDE_COL"].forEach((key) => {
    const el = box.querySelector('[data-key="' + key + '"]');
    if (!el || el.dataset.excBound) return;
    el.dataset.excBound = "1";
    el.addEventListener("change", () => {
      if (key === "EXCLUDE_FILE") {
        setFieldValue("EXCLUDE_SHEET", "");
        setFieldValue("EXCLUDE_COL", "");
        setFieldValue("EXCLUDE_ROW", "");
      } else if (key === "EXCLUDE_SHEET") {
        setFieldValue("EXCLUDE_COL", "");
        setFieldValue("EXCLUDE_ROW", "");
      } else if (key === "EXCLUDE_COL") {
        setFieldValue("EXCLUDE_ROW", "");
      }
      refreshExcludeDownstream(key);
    });
  });
}
function applyPreviewLists(prev) {
  prev = prev || S.preview || {};
  ioSheetOpts = prev.sheets || [];
  ioRadIdOpts = prev.rad_id_cols || prev.id_options || [];
  ioClinIdOpts = prev.clin_cols || prev.id_options || [];
}
function fillAllIoSelects(keep) {
  const k = keep || (S.values || {});
  const setSheet = (id, key, fallback) => {
    const resolved = pickExistingSheet(k[key], ioSheetOpts, fallback);
    if (S.values) S.values[key] = resolved;
    fillSelect(id, ioSheetOpts, resolved, "");
    fillSelect(document.querySelector('[data-key="' + key + '"]'), ioSheetOpts, resolved, "");
  };
  setSheet("ioRadSheet", "RAD_SHEET", IO_DEFAULTS.RAD_SHEET);
  setSheet("ioClinSheet", "CLIN_SHEET", IO_DEFAULTS.CLIN_SHEET);
  setSheet("ioQcSheet", "QC_SHEET", IO_DEFAULTS.QC_SHEET);
  setSheet("ioIccSheet", "ICC_SHEET", IO_DEFAULTS.ICC_SHEET);
  fillSelect(document.querySelector('[data-key="RAD_ID_COL"]'), ioRadIdOpts, k.RAD_ID_COL, IO_DEFAULTS.RAD_ID_COL);
  fillSelect(document.querySelector('[data-key="CLIN_ID_COL"]'), ioClinIdOpts.length ? ioClinIdOpts : ioRadIdOpts, k.CLIN_ID_COL, IO_DEFAULTS.CLIN_ID_COL);
  const box = $("extRows");
  if (!box) return;
  box.querySelectorAll("[data-ext-row]").forEach((row) => {
    fillSelect(row.querySelector("[data-ext=clin]"), ioSheetOpts, null, IO_DEFAULTS.CLIN_SHEET);
    fillSelect(row.querySelector("[data-ext=rad]"), ioSheetOpts, null, IO_DEFAULTS.RAD_SHEET);
    fillSelect(row.querySelector("[data-ext=radid]"), ioRadIdOpts, null, IO_DEFAULTS.RAD_ID_COL);
  });
}
function emptyExtRow() {
  return {
    name: "", file: "",
    clin_sheet: IO_DEFAULTS.CLIN_SHEET,
    rad_sheet: IO_DEFAULTS.RAD_SHEET,
    rad_id_col: IO_DEFAULTS.RAD_ID_COL,
  };
}
function readExtRows() {
  const box = $("extRows");
  const out = [];
  if (!box) return out;
  box.querySelectorAll("[data-ext-row]").forEach((row) => {
    const val = (sel) => ((row.querySelector(sel) || {}).value || "").trim();
    const file = val("[data-ext=file]");
    const cs = val("[data-ext=clin]");
    const rs = val("[data-ext=rad]");
    const rid = val("[data-ext=radid]");
    const name = val("[data-ext=name]");
    if (!file) return;
    out.push({
      name: name, file: file,
      clin_sheet: cs || IO_DEFAULTS.CLIN_SHEET,
      rad_sheet: rs || IO_DEFAULTS.RAD_SHEET,
      rad_id_col: rid || IO_DEFAULTS.RAD_ID_COL,
    });
  });
  return out.filter(r => r.clin_sheet && r.rad_sheet);
}
function bindExtRowEvents() {
  const box = $("extRows");
  if (!box) return;
  box.querySelectorAll("input,select").forEach((el) => {
    el.addEventListener("change", collectIoModule);
  });
  box.querySelectorAll("[data-ext-pick]").forEach((btn) => {
    btn.onclick = () => {
      const row = btn.closest("[data-ext-row]");
      const inp = row && row.querySelector("[data-ext-filepick]");
      if (inp) inp.click();
    };
  });
  box.querySelectorAll("[data-ext-filepick]").forEach((inp) => {
    inp.onchange = async () => {
      const f = inp.files && inp.files[0];
      const row = inp.closest("[data-ext-row]");
      const dest = row && row.querySelector("[data-ext=file]");
      if (f && dest) dest.value = f.name;
      collectIoModule();
      if (f) await previewIntoExtRow(row, f.name);
    };
  });
  box.querySelectorAll("[data-ext-del]").forEach((btn) => {
    btn.onclick = () => {
      const row = btn.closest("[data-ext-row]");
      if (row) row.remove();
      const box2 = $("extRows");
      if (box2 && !box2.querySelector("[data-ext-row]")) renderExtRows([emptyExtRow()]);
      collectIoModule();
    };
  });
}
function renderExtRows(list) {
  const box = $("extRows");
  if (!box) return;
  const rows = (Array.isArray(list) && list.length) ? list : [emptyExtRow()];
  box.innerHTML = rows.map((r, i) => {
    const name = (r && r.name) || "";
    const file = (r && (r.file || r.EXTERNAL_FILE)) || "";
    const cs = (r && (r.clin_sheet || r.EXTERNAL_CLIN_SHEET)) || IO_DEFAULTS.CLIN_SHEET;
    const rs = (r && (r.rad_sheet || r.EXTERNAL_RAD_SHEET)) || IO_DEFAULTS.RAD_SHEET;
    const rid = (r && (r.rad_id_col || r.EXTERNAL_RAD_ID_COL)) || IO_DEFAULTS.RAD_ID_COL;
    return `<div class="ext-row" data-ext-row="${i}">
      <input data-ext="name" type="text" placeholder="名称，可空" value="${esc(name)}"/>
      <input data-ext="file" type="text" list="xlsxList" placeholder="文件，可空" autocomplete="off" spellcheck="false" value="${esc(file)}"/>
      <input data-ext-filepick type="file" accept=".xlsx,.xls" hidden/>
      <button type="button" class="btn sec" data-ext-pick>选择</button>
      <select data-ext="clin"></select>
      <select data-ext="rad"></select>
      <select data-ext="radid"></select>
      <button type="button" class="btn sec" data-ext-del title="删这一条">删</button>
    </div>`;
  }).join("");
  box.querySelectorAll("[data-ext-row]").forEach((row, i) => {
    const r = rows[i] || emptyExtRow();
    fillSelect(row.querySelector("[data-ext=clin]"), ioSheetOpts, r.clin_sheet || r.EXTERNAL_CLIN_SHEET, IO_DEFAULTS.CLIN_SHEET);
    fillSelect(row.querySelector("[data-ext=rad]"), ioSheetOpts, r.rad_sheet || r.EXTERNAL_RAD_SHEET, IO_DEFAULTS.RAD_SHEET);
    fillSelect(row.querySelector("[data-ext=radid]"), ioRadIdOpts, r.rad_id_col || r.EXTERNAL_RAD_ID_COL, IO_DEFAULTS.RAD_ID_COL);
  });
  bindExtRowEvents();
}
function ioDataName() {
  const v = (($("ioData") && $("ioData").value) || ($("data") && $("data").value) || "").trim();
  if (v) return v.split(/[/\\]/).pop() || v;
  const proj = ($("project") && $("project").value) || "";
  return proj ? proj + ".xlsx" : "";
}
function fillIoModule() {
  if ($("ioProject")) $("ioProject").textContent = ($("project") && $("project").value) || "—";
  if ($("ioData") && !$("ioData").dataset.dirty) {
    const raw = ($("data") && $("data").value) || "";
    $("ioData").value = raw.split(/[/\\]/).pop() || raw;
  }
  const v = applyIoDefaults(S.values || {});
  applyPreviewLists(S.preview);
  const rawTests = Array.isArray(v.EXTERNAL_TESTS) ? v.EXTERNAL_TESTS : [];
  const tests = rawTests.filter((r) => r && String(r.file || r.EXTERNAL_FILE || "").trim());
  if (!tests.length && String(v.EXTERNAL_FILE || "").trim()) {
    tests.push({
      name: v.EXTERNAL_NAME || "ext",
      file: v.EXTERNAL_FILE,
      clin_sheet: v.EXTERNAL_CLIN_SHEET || IO_DEFAULTS.CLIN_SHEET,
      rad_sheet: v.EXTERNAL_RAD_SHEET || IO_DEFAULTS.RAD_SHEET,
      rad_id_col: v.EXTERNAL_RAD_ID_COL || IO_DEFAULTS.RAD_ID_COL,
    });
  }
  renderExtRows(tests.length ? tests : [emptyExtRow()]);
  fillAllIoSelects(v);
  if ($("ioExtSplit") && v.USE_INTERNAL_SPLIT_WHEN_EXTERNAL != null) {
    $("ioExtSplit").checked = !!v.USE_INTERNAL_SPLIT_WHEN_EXTERNAL;
  }
  const p = S.projects.find(x => x.name === ($("project") && $("project").value));
  fillDatalist("xlsxList", (p && p.xlsx) || []);
}
function collectIoModule() {
  if (!S.values) S.values = {};
  const data = ioDataName();
  if ($("data") && data) $("data").value = resolveLockedData(
    S.projects.find(x => x.name === ($("project") && $("project").value)),
    data
  );
  SHEET_KEYS.concat(ID_KEYS).forEach((key) => {
    const el = document.querySelector('[data-key="' + key + '"]');
    if (el) S.values[key] = (el.value || "").trim();
  });
  applyIoDefaults(S.values);
  const tests = readExtRows();
  const first = tests[0] || emptyExtRow();
  S.values.EXTERNAL_FILE = first.file || "";
  S.values.EXTERNAL_CLIN_SHEET = first.clin_sheet || "";
  S.values.EXTERNAL_RAD_SHEET = first.rad_sheet || "";
  S.values.EXTERNAL_TESTS = tests;
  if ($("ioExtSplit")) S.values.USE_INTERNAL_SPLIT_WHEN_EXTERNAL = !!$("ioExtSplit").checked;
  persistIoLock();
  fillLockStamp();
}
let ioReloadBusy = false;
function bindIoModule() {
  if ($("ioCard") && $("ioCard").dataset.bound) return;
  if ($("ioCard")) $("ioCard").dataset.bound = "1";
  if ($("ioExtSplit")) $("ioExtSplit").addEventListener("change", collectIoModule);
  if ($("btnExtAdd")) {
    $("btnExtAdd").onclick = () => {
      const box = $("extRows");
      const cur = [];
      (box ? box.querySelectorAll("[data-ext-row]") : []).forEach((row) => {
        const val = (sel) => ((row.querySelector(sel) || {}).value || "").trim();
        cur.push({
          name: val("[data-ext=name]"),
          file: val("[data-ext=file]"),
          clin_sheet: val("[data-ext=clin]"),
          rad_sheet: val("[data-ext=rad]"),
          rad_id_col: val("[data-ext=radid]"),
        });
      });
      cur.push(emptyExtRow());
      renderExtRows(cur);
      collectIoModule();
    };
  }
}

function postFlowSteps(frame) {
  if (!frame || !frame.contentWindow) return;
  const on = (key) => { try { return !!switchOn(key); } catch (e) { return false; } };
  const msg = {
    type: "console-flow-steps",
    lasso: on("DO_LASSO"),
    clinical: on("DO_CLIN"),
    radiomics: on("DO_LASSO") || on("DO_RAD_LOGISTIC"),
    nomogram: on("DO_NOMOGRAM"),
    roc: on("DO_ROC"),
    calibration: on("DO_CALIBRATION"),
    dca: on("DO_DCA"),
    nri: on("DO_NRI"),
    idi: on("DO_NRI"),
    dag: on("DO_DAG"),
    mediation: on("DO_MEDIATION"),
  };
  try { frame.contentWindow.postMessage(msg, "*"); } catch (e) {}
}
function stitchSections(parts) {
  const out = [];
  const buf = [];
  const want = new Set(["radprep", "rad", "radlog", "fig"]);
  const idOf = (html) => {
    const m = String(html || "").match(/data-sec="([^"]+)"/);
    return m ? m[1] : "";
  };
  const flush = () => {
    if (!buf.length) return;
    out.push(`<div class="sec-quad">${buf.join("")}</div>`);
    buf.length = 0;
  };
  for (const html of parts) {
    if (!html) continue;
    if (want.has(idOf(html))) buf.push(html);
    else { flush(); out.push(html); }
  }
  flush();
  return out.join("");
}
function alignedItems(v) {
  if (Array.isArray(v)) return v.map((x) => String(x == null ? "" : x).trim());
  const s = String(v == null ? "" : v).trim();
  return s ? [s] : [];
}
function packPairSide(items) {
  const out = items.map((x) => String(x == null ? "" : x).trim());
  if (!out.some((x) => x)) return "";
  if (out.length === 1) return out[0];
  return out;
}
function tdSelectHtml(key, cur, taken, blankLabel) {
  const f = (S.schema || []).find((x) => x.key === key) || {};
  let opts = (f.options || []).map((o) => String(o)).filter(Boolean);
  if (!opts.length) opts = clinPool();
  let html = `<option value="">${esc(blankLabel)}</option>`;
  opts.forEach((s) => {
    if (taken.has(s)) return;
    html += `<option value="${esc(s)}"${s === cur ? " selected" : ""}>${esc(s)}</option>`;
  });
  if (cur && opts.indexOf(cur) < 0) {
    html += `<option value="${esc(cur)}" selected>${esc(cur)}</option>`;
  }
  return html;
}
function saveTdPair(times, stats) {
  setFieldValue("TD_ROC_TIMES", packPairSide(times));
  setFieldValue("TD_ROC_STATUS_COLS", packPairSide(stats));
  markDirty();
  renderForm();
}
function renderTdRocPair() {
  let times = alignedItems(fieldValue("TD_ROC_TIMES"));
  let stats = alignedItems(fieldValue("TD_ROC_STATUS_COLS"));
  while (times.length && !times[times.length - 1]) times.pop();
  while (stats.length && !stats[stats.length - 1]) stats.pop();
  const n = Math.max(times.length, stats.length);
  const rows = [];
  for (let i = 0; i < n; i++) rows.push({ t: times[i] || "", s: stats[i] || "" });
  rows.push({ t: "", s: "" });
  const body = rows.map((row, i) => {
    const last = i === rows.length - 1;
    const takenT = new Set(rows.map((r) => r.t).filter((v) => v && v !== row.t));
    const takenS = new Set(rows.map((r) => r.s).filter((v) => v && v !== row.s));
    const blankT = last && n ? "（再选一列）" : "（不设）";
    const blankS = last && n ? "（再选一列）" : "（不设）";
    const del = (!last && (row.t || row.s))
      ? `<button type="button" class="kv-del" data-td-del="${i}" title="删这一行">×</button>`
      : "<span></span>";
    return `<div class="td-pair-row">
      <select data-td-time="${i}">${tdSelectHtml("TD_ROC_TIMES", row.t, takenT, blankT)}</select>
      <select data-td-status="${i}">${tdSelectHtml("TD_ROC_STATUS_COLS", row.s, takenS, blankS)}</select>
      ${del}
    </div>`;
  }).join("");
  return `<div class="td-pair" data-td-pair="1">
    <div class="td-pair-head"><span>tdROC时间列</span><span>tdROC状态列</span><span></span></div>
    ${body}
  </div>`;
}
function bindTdRocPair(box) {
  if (!box || box.dataset.tdBound) return;
  box.dataset.tdBound = "1";
  const read = () => ({
    times: [...box.querySelectorAll("[data-td-time]")].map((el) => el.value),
    stats: [...box.querySelectorAll("[data-td-status]")].map((el) => el.value),
  });
  box.addEventListener("change", (e) => {
    if (!e.target.matches("[data-td-time],[data-td-status]")) return;
    const cur = read();
    while (cur.times.length && !cur.times[cur.times.length - 1] && !cur.stats[cur.stats.length - 1]) {
      cur.times.pop();
      cur.stats.pop();
    }
    saveTdPair(cur.times, cur.stats);
  });
  box.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-td-del]");
    if (!btn) return;
    const i = Number(btn.getAttribute("data-td-del"));
    const cur = read();
    cur.times.splice(i, 1);
    cur.stats.splice(i, 1);
    while (cur.times.length && !cur.times[cur.times.length - 1] && !cur.stats[cur.stats.length - 1]) {
      cur.times.pop();
      cur.stats.pop();
    }
    saveTdPair(cur.times, cur.stats);
  });
}
function moduleSwitchKey(id) {
  return {
    repro: "DO_REPRO",
    io: "DO_IO",
    exclude: "DO_EXCLUDE_SAMPLES",
    clin: "DO_CLIN",
    ancova: "DO_ANCOVA",
    mlr: "DO_MLR",
    nomo: "DO_NOMOGRAM",
    dca: "DO_DCA",
    nri: "DO_NRI",
    corr: "DO_HEATMAP",
    advanced: "DO_ADVANCED",
    rad: "DO_LASSO",
    radlog: "DO_RAD_LOGISTIC",
    fig: "DO_FIGURE_PANELS",
  }[id] || "";
}
function switchOn(key) {
  if (!key) return true;
  if (S.values && Object.prototype.hasOwnProperty.call(S.values, key)) return !!S.values[key];
  return !!fieldValue(key);
}
function groupSelectHtml(key) {
  if (!key || !S.schema) return "";
  const f = S.schema.find(x => x.key === key);
  if (!f) return "";
  const v = fieldValue(key);
  const labels = f.option_labels || {};
  const opts = (f.options || []).map(o => {
    let lab = (o === "" || o == null) ? "全部" : o;
    if (labels && labels[o] != null) lab = labels[o];
    else if (labels && labels[String(o)] != null) lab = labels[String(o)];
    return `<option value="${esc(o)}" ${String(o) === String(v ?? "") ? "selected" : ""}>${esc(lab)}</option>`;
  }).join("");
  const tipBits = [];
  if (f.help) tipBits.push(String(f.help));
  if (f.key) tipBits.push(String(f.key));
  const tip = tipBits.length ? ` title="${esc(tipBits.join("\n"))}"` : "";
  return `<label class="sec-group"${tip} onclick="event.stopPropagation()"><span>${esc(f.label || "分析组别")}</span><select data-key="${esc(key)}">${opts}</select></label>`;
}
function titleSwitchHtml(keys) {
  if (!keys || !keys.length || !S.schema) return "";
  return keys.map((key) => {
    const f = S.schema.find((x) => x.key === key);
    const lab = (f && f.label) ? f.label : key;
    const tip = (f && f.help) ? String(f.help) : lab;
    return `<label class="title-sw" title="${esc(tip)}" onclick="event.stopPropagation()"><input type="checkbox" data-key="${esc(key)}" ${switchOn(key) ? "checked" : ""}/><span>${esc(lab)}</span></label>`;
  }).join("");
}
function sectionHead(id, title, extra, hint, titleKeys) {
  const key = moduleSwitchKey(id);
  const dim = key && !switchOn(key) ? " mod-off" : "";
  let tip = "启用";
  if (key && S.schema) {
    const f = S.schema.find(x => x.key === key);
    if (f && f.help) tip = String(f.help);
  }
  const sw = key
    ? `<label class="mod-sw" title="${esc(tip)}" onclick="event.stopPropagation()"><input type="checkbox" data-key="${esc(key)}" ${switchOn(key) ? "checked" : ""}/></label>`
    : "";
  const count = typeof extra === "number" || (typeof extra === "string" && /^\d+$/.test(extra));
  const n = extra == null || extra === "" || count ? "" : ` · ${esc(String(extra))}`;
  const groupKey = id === "corr" ? "CORR_GROUP" : (id === "pcorr" ? "PARTIAL_GROUP" : "");
  const group = groupSelectHtml(groupKey);
  const headTip = hint ? ` data-tip="${esc(String(hint))}"` : "";
  const peers = titleSwitchHtml(titleKeys);
  const peerGap = peers || group ? `<span class="title-gap" aria-hidden="true"></span>` : "";
  return {
    dim,
    html: `<h3${headTip} onclick="this.parentNode.classList.toggle('off')">${sw}<span class="sec-title">${esc(title)}</span>${n}${peerGap}${peers}${group}</h3>`,
  };
}
function dropSwitch(id, fields) {
  const key = moduleSwitchKey(id);
  return key ? fields.filter(f => f.key !== key) : fields;
}
function renderForm() {
  const mid = document.querySelector(".col.mid");
  const y = mid ? mid.scrollTop : 0;
  const card = $("grpCard");
  const park = $("grpCardPark");
  if (card && park) park.appendChild(card);
  ensureConsoleFields();
  renderOutcomeTabs();
  renderPairTabs();
  const root = $("formRoot");
  const ioCard = $("ioCard");
  if (ioCard) ioCard.hidden = true;
  if (root) root.hidden = false;
  fillIoModule();
  S.ioTab = false;
  const groups = [];
  const map = new Map();
  const bundleOf = {};
  for (const b of FIELD_BUNDLES) {
    for (const k of b.keys) bundleOf[k] = b;
  }
  const markKeys = new Set([
    "RAD_MARKER_SIDE", "RAD_MARKER_MIN_N",
    "RAD_FEATURE_PREFIX_EXCLUDE", "RAD_FEATURE_DISPLAY_STRIP",
    "RAD_FEATURE_PREFIX_GROUP",
  ]);
  for (const f of S.schema) {
    if (markKeys.has(f.key)) {
      f.section = "radmark";
      f.section_title = "前缀 / 筛选 / 分类 / 剥离";
    }
  }
  for (const f of S.schema) {
    if (isHiddenField(f)) continue;
    if (!map.has(f.section)) {
      const g = {id: f.section, title: f.section_title, fields: []};
      map.set(f.section, g);
      groups.push(g);
    }
    map.get(f.section).fields.push(f);
  }
  const markGroup = groups.find((g) => g.id === "radmark");
  if (markGroup) {
    const rest = groups.filter((g) => g.id !== "radmark");
    const figAt = rest.findIndex((g) => g.id === "fig");
    rest.splice(figAt >= 0 ? figAt + 1 : rest.length, 0, markGroup);
    groups.length = 0;
    rest.forEach((g) => groups.push(g));
  }
  root.innerHTML = stitchSections(groups.map(g => {
    const fields = dropSwitch(g.id, g.fields);
    const used = new Set();
    const chunks = [];
    for (const f of fields) {
      if (used.has(f.key)) continue;
      const b = bundleOf[f.key];
      if (b) {
        const members = b.keys.map(k => fields.find(x => x.key === k)).filter(Boolean);
        members.forEach(m => used.add(m.key));
        if (members.length) chunks.push({ id: b.id, html: renderBundle(b, members) });
      } else {
        used.add(f.key);
        chunks.push({ id: "", html: renderField(f) });
      }
    }
    const parts = chunks.map(c => c.html);
    if (g.id === "io") {
      if (!parts.length) return "";
      const pull = (id) => chunks.filter(c => c.id === id).map(c => c.html).join("");
      const rest = chunks.filter(c => c.id !== "subproj" && c.id !== "maps").map(c => c.html).join("");
      const head = sectionHead("io", "工作表", "");
      return `<div class="sec-block${head.dim}" data-sec="io">
      ${head.html}
      <div class="sec-body io-sheets">${pull("subproj")}<div id="ioGrpSlot"></div>${pull("maps")}${rest}</div>
    </div>`;
    }
    if (!parts.length && g.id !== "switch" && !moduleSwitchKey(g.id)) return "";
    if (g.id === "switch") {
      const titleKeys = ["DO_FALSE_CLASS", "DO_FDR", "DO_FWER"];
      const titleSet = new Set(titleKeys);
      const flat = fields.filter(f => !isHiddenField(f) && !titleSet.has(f.key));
      const bools = flat.filter(f => f.widget === "bool");
      const rest = flat.filter(f => f.widget !== "bool");
      if (!bools.length && !rest.length && !titleKeys.length) return "";
      const hint = "全局：错分表、多重校正、VIF 警告阈值与 Bootstrap 次数。VIF 高阈值不再用于剔除候选。";
      const head = sectionHead("switch", g.title, bools.length + rest.length, hint, titleKeys);
      return `<div class="sec-block${head.dim}" data-sec="switch">
      ${head.html}
      <div class="sec-body switch-body">
        ${bools.length ? `<div class="sec-bools">${bools.map(renderField).join("")}</div>` : ""}
        ${rest.length ? `<div class="switch-inputs">${rest.map(renderField).join("")}</div>` : ""}
      </div>
    </div>`;
    }
    if (g.id === "clinprep") {
      const order = [
        "ORDINAL_MAX_LEVELS", "CLIN_FORCE_CONTINUOUS", "CLIN_FORCE_CATEGORICAL", "SKIP_CLIN_FEATURES",
      ];
      const vis = fields.filter(f => !isHiddenField(f));
      const byKey = new Map(vis.map(f => [f.key, f]));
      const left = order.map(k => byKey.get(k)).filter(Boolean);
      for (const f of vis) {
        if (order.indexOf(f.key) >= 0) continue;
        left.push(f);
      }
      if (!left.length) return "";
      const hint = "计数/计量等级与强制计数在 body。都不在临床筛选模块。";
      const head = sectionHead("clinprep", g.title, left.length, hint);
      return `<div class="sec-block${head.dim}" data-sec="clinprep">
      ${head.html}
      <div class="sec-body clinprep-body">${left.map(renderField).join("")}</div>
    </div>`;
    }
    if (g.id === "clin") {
      const titleKeys = ["DO_UNI_LOGISTIC_TABLE", "DO_MV_LOGISTIC_TABLE", "DO_FIRTH"];
      const titleSet = new Set(titleKeys);
      const order = [
        "TABLE1_P_THRESHOLD",
        "FORCE_REMOVE_FEATURES", "FORCE_INTER_FEATURES",
      ];
      const radOnly = new Set([
        "DO_ICC_FILTER", "ICC_REQUIRE_ROW", "ENABLE_CORR_FILTER", "RAD_COR_CUTOFF",
        "ICC_THRESHOLD", "ICC_FEATURE_COL", "ICC_INTRA_COL", "ICC_INTER_COL",
        "ORDINAL_MAX_LEVELS", "CLIN_FORCE_CATEGORICAL",
      ]);
      const byKey = new Map(fields.map(f => [f.key, f]));
      const vis = order.map(k => byKey.get(k)).filter(f => f && !isHiddenField(f) && !titleSet.has(f.key));
      for (const f of fields) {
        if (isHiddenField(f) || radOnly.has(f.key) || titleSet.has(f.key) || vis.indexOf(f) >= 0) continue;
        vis.push(f);
      }
      if (!vis.length && !titleKeys.length) return "";
      const html = vis.map(renderField).join("");
      const hint = "单因素 / 多因素表与 Firth 在标题行。Table1 p 在 body。计数/计量等级与强制计数在临床预处理。";
      const head = sectionHead("clin", g.title, vis.length, hint, titleKeys);
      return `<div class="sec-block${head.dim}" data-sec="clin">
      ${head.html}
      <div class="sec-body clin-body">${html}</div>
    </div>`;
    }
    if (g.id === "radprep") {
      const titleKeys = ["DO_ICC_FILTER", "ICC_REQUIRE_ROW", "ENABLE_CORR_FILTER", "LASSO_CACHE"];
      const titleSet = new Set(titleKeys);
      const hasRad = !!pickExistingSheet(
        (S.values && S.values.RAD_SHEET) || "", workbookSheets(), ""
      );
      if (!hasRad) {
        const head = sectionHead("radprep", g.title, "未用", "未指定组学 sheet，跳过预处理。要做组学请回起始页选择组学表。", titleKeys);
        return `<div class="sec-block off${head.dim}" data-sec="radprep">
      ${head.html}
      <div class="sec-body radprep-body"></div>
    </div>`;
      }
      const order = [
        "RAD_COR_CUTOFF",
        "ICC_THRESHOLD", "ICC_FEATURE_COL", "ICC_INTRA_COL", "ICC_INTER_COL",
      ];
      const vis = fields.filter(f => !isHiddenField(f) && !titleSet.has(f.key));
      const byKey = new Map(vis.map(f => [f.key, f]));
      const left = order.map(k => byKey.get(k)).filter(Boolean);
      for (const f of vis) {
        if (left.indexOf(f) < 0) left.push(f);
      }
      if (!left.length && !titleKeys.length) return "";
      const hint = "相关过滤界值，以及 ICC 阈值和三列。LASSO 缓存在标题行，默认开。在训练集拟合，验证层套用同一套插补和标准化。标记的筛选、分类、剥离在单独模块。";
      const head = sectionHead("radprep", g.title, left.length, hint, titleKeys);
      return `<div class="sec-block${head.dim}" data-sec="radprep">
      ${head.html}
      <div class="sec-body radprep-body">${left.map(renderField).join("")}</div>
    </div>`;
    }
    if (g.id === "radmark") {
      const hasRad = !!pickExistingSheet(
        (S.values && S.values.RAD_SHEET) || "", workbookSheets(), ""
      );
      const prefixF = fields.find(f => f.key === "RAD_FEATURE_PREFIX_EXCLUDE");
      const stripF = fields.find(f => f.key === "RAD_FEATURE_DISPLAY_STRIP");
      const hint = "先按标记侧和最少列数识别，再勾选筛选、分类、剥离。勾选排除的不进任何分析。手填分类后，未排除的标记可分进各类，每一类单独做 Logistic。都不填则仍是一份 RadScore。剥离只改显示名。";
      if (!hasRad) {
        const head = sectionHead("radmark", g.title, "未用", "未指定组学 sheet，跳过标记。要做组学请回起始页选择组学表。");
        return `<div class="sec-block off${head.dim}" data-sec="radmark">
      ${head.html}
      <div class="sec-body radmark-body"></div>
    </div>`;
      }
      const head = sectionHead("radmark", g.title, "", hint);
      return `<div class="sec-block${head.dim}" data-sec="radmark">
      ${head.html}
      <div class="sec-body radmark-body">${radPrefixStripCombo(prefixF, stripF, true)}</div>
    </div>`;
    }
    if (g.id === "rad") {
      const titleKeys = ["LASSO_PLOT_PATH"];
      const titleSet = new Set(titleKeys);
      const hasRad = !!pickExistingSheet(
        (S.values && S.values.RAD_SHEET) || "", workbookSheets(), ""
      );
      const order = [
        "RAD_FEATURE_SOURCE", "ALPHA", "LASSO_NFOLDS", "LASSO_N_JOBS",
        "LASSO_N_LAMBDAS", "LASSO_C_MAX", "LASSO_C_MIN", "LASSO_PREFILTER_TOPK",
      ];
      const vis = fields.filter(f => !isHiddenField(f) && !titleSet.has(f.key));
      const byKey = new Map(vis.map(f => [f.key, f]));
      const left = order.map(k => byKey.get(k)).filter(Boolean);
      for (const f of vis) {
        if (order.indexOf(f.key) >= 0) continue;
        left.push(f);
      }
      if (!hasRad) {
        const head = sectionHead("rad", g.title, "未用", "未指定组学 sheet，跳过 LASSO。列线图用临床变量。要做组学请回起始页选择组学表。", titleKeys);
        return `<div class="sec-block off${head.dim}" data-sec="rad">
      ${head.html}
      <div class="sec-body rad-body"></div>
    </div>`;
      }
      if (!left.length && !titleKeys.length) return "";
      const head = sectionHead("rad", g.title, left.length, "", titleKeys);
      return `<div class="sec-block${head.dim}" data-sec="rad">
      ${head.html}
      <div class="sec-body rad-body">
        <div class="rad-inputs">${left.map(renderField).join("")}</div>
      </div>
    </div>`;
    }
    if (g.id === "radlog") {
      const titleKeys = ["DO_RAD_FIRTH"];
      const titleSet = new Set(titleKeys);
      const hasRad = !!pickExistingSheet(
        (S.values && S.values.RAD_SHEET) || "", workbookSheets(), ""
      );
      const order = ["RAD_LOGIT_UNI_P", "RAD_LOGIT_MV_P"];
      const vis = fields.filter(f => !isHiddenField(f) && !titleSet.has(f.key));
      const byKey = new Map(vis.map(f => [f.key, f]));
      const left = order.map(k => byKey.get(k)).filter(Boolean);
      for (const f of vis) {
        if (order.indexOf(f.key) >= 0) continue;
        left.push(f);
      }
      const hint = hasRad
        ? "单因素 / 多因素 Logistic p 筛组学并生成 RadScore。Firth 在标题行。打开则本趟不跑 LASSO。VIF 仅警告（见「开关」）。不重筛；验证模式跟随三个下拉。"
        : "未指定组学 sheet，跳过。要做组学请回起始页选择组学表。";
      const head = sectionHead("radlog", g.title, hasRad ? left.length : "未用", hint, titleKeys);
      return `<div class="sec-block${hasRad ? "" : " off"}${head.dim}" data-sec="radlog">
      ${head.html}
      <div class="sec-body radlog-body">
        ${hasRad && left.length ? `<div class="rad-inputs">${left.map(renderField).join("")}</div>` : ""}
      </div>
    </div>`;
    }
    if (g.id === "fig") {
      const titleKeys = ["DO_PLOT_TIFF", "DO_PLOT_PDF"];
      const titleSet = new Set(titleKeys);
      const vis = fields.filter(f => !isHiddenField(f) && !titleSet.has(f.key));
      if (!vis.length && !titleKeys.length) return "";
      const bools = vis.filter(f => f.widget === "bool");
      const rest = vis.filter(f => f.widget !== "bool");
      const head = sectionHead("fig", g.title, vis.length, "", titleKeys);
      return `<div class="sec-block${head.dim}" data-sec="fig">
      ${head.html}
      <div class="sec-body fig-body">
        ${bools.length ? `<div class="sec-bools">${bools.map(renderField).join("")}</div>` : ""}
        ${rest.length ? `<div class="fig-fields">${rest.map(renderField).join("")}</div>` : ""}
      </div>
    </div>`;
    }
    if (g.id === "ancova" || g.id === "mlr") {
      const vis = fields.filter(f => !isHiddenField(f));
      if (!vis.length) return "";
      const byKey = new Map(vis.map(f => [f.key, f]));
      const order = g.id === "ancova"
        ? ["ANCOVA_OUTCOMES", "ANCOVA_GROUP", "ANCOVA_COVARIATES"]
        : ["MLR_OUTCOMES", "MLR_GROUP", "MLR_COVARIATES"];
      const rest = [];
      for (const k of order) {
        const f = byKey.get(k);
        if (f) rest.push(f);
      }
      for (const f of vis) {
        if (rest.indexOf(f) < 0) rest.push(f);
      }
      const html = rest.map(renderField).join("");
      const head = sectionHead(g.id, g.title, vis.length);
      return `<div class="sec-block${head.dim}" data-sec="${esc(g.id)}">
      ${head.html}
      <div class="sec-body ${esc(g.id)}-body">${html}</div>
    </div>`;
    }
    if (g.id === "pcorr") {
      const titleKeys = ["DO_CORR", "DO_PARTIAL"];
      const vis = fields.filter(f => !isHiddenField(f) && f.key !== "PARTIAL_GROUP" && titleKeys.indexOf(f.key) < 0);
      if (!vis.length) return "";
      const byKey = new Map(vis.map(f => [f.key, f]));
      // 预测因子和参考变量两个开关共用。控制变量与方法只在偏相关开时生效。
      const order = [
        "CORR_PREDICTORS", "CORR_REFS", "PARTIAL_METHOD", "PARTIAL_CONTROLS",
      ];
      const rest = [];
      for (const k of order) {
        const f = byKey.get(k);
        if (f) rest.push(f);
      }
      for (const f of vis) {
        if (rest.indexOf(f) < 0) rest.push(f);
      }
      const html = rest.map(renderPartialField).join("");
      const hint = "预测因子/参考变量共用相关表与偏相关；控制变量与方法仅偏相关。热图在「相关热图」。";
      const head = sectionHead(g.id, g.title, vis.length, hint, titleKeys);
      return `<div class="sec-block${head.dim}" data-sec="${esc(g.id)}">
      ${head.html}
      <div class="sec-body ${esc(g.id)}-body">${html}</div>
    </div>`;
    }
    if (g.id === "roc") {
      const vis = fields.filter(f => !isHiddenField(f));
      if (!vis.length) return "";
      const byKey = new Map(vis.map(f => [f.key, f]));
      const titleKeys = ["DO_ROC"];
      const row1 = ["ROC_MV_P_THRESHOLD", "FORCE_CLIN_FOR_ROC", "ROC_PLOT_EXCLUDE"]
        .map(k => byKey.get(k)).filter(Boolean);
      const used = new Set(titleKeys.concat(row1.map(f => f.key)));
      const extra = vis.filter(f => !used.has(f.key));
      const hint = "当前结局的 ROC。KM、IPCW 与 Landmark 在下面同一行。";
      const head = sectionHead(g.id, g.title, vis.length, hint, titleKeys);
      return `<div class="sec-block${head.dim}" data-sec="roc">
      ${head.html}
      <div class="sec-body roc-body">
        <div class="roc-row roc-row-1">${row1.map(renderField).join("")}</div>
        ${extra.map(renderField).join("")}
      </div>
    </div>`;
    }
    if (g.id === "surv") {
      const vis = fields.filter(f => !isHiddenField(f));
      if (!vis.length) return "";
      const byKey = new Map(vis.map(f => [f.key, f]));
      const sharedTime = fieldValue("SURVIVAL_TIME_COL");
      if (String(fieldValue("TD_IPCW_TIME_COL") || "") !== String(sharedTime || "")) {
        setFieldValue("TD_IPCW_TIME_COL", sharedTime);
      }
      const titleKeys = ["DO_SURVIVAL", "DO_TIME_DEPENDENT_ROC", "DO_SURVIVAL_BY_RISK", "DO_COX", "DO_LANDMARK_ROC"];
      const shareKeys = ["SURVIVAL_TIME_COL", "SURVIVAL_TIME_UNIT", "TD_IPCW_STATUS_COL"];
      const share = shareKeys.map(k => byKey.get(k)).filter(Boolean);
      const horizons = byKey.get("TD_IPCW_HORIZONS");
      const lmStatus = byKey.get("TD_ROC_STATUS_COLS");
      const used = new Set(titleKeys.concat(shareKeys, [
        "TD_IPCW_HORIZONS", "TD_ROC_STATUS_COLS",
        "TD_IPCW_TIME_COL", "TD_ROC_TIMES",
      ]));
      const extra = vis.filter(f => !used.has(f.key));
      const off = (key) => switchOn(key) ? "" : " partial-off";
      const hint = "开关在标题行。同一行是时间、单位、状态、评估月和 Landmark 状态列。时间依赖性 ROC（IPCW）状态 1=事件，0=删失。Landmark 状态列复选，窗口月从列名读。";
      const head = sectionHead(g.id, g.title, vis.length, hint, titleKeys);
      return `<div class="sec-block${head.dim}" data-sec="surv">
      ${head.html}
      <div class="sec-body surv-body">
        <div class="surv-row surv-line">
          ${share.map(renderField).join("")}
          ${horizons ? `<div class="surv-cell${off("DO_TIME_DEPENDENT_ROC")}" data-gate="DO_TIME_DEPENDENT_ROC">${renderField(horizons)}</div>` : ""}
          ${lmStatus ? `<div class="surv-cell surv-lm${off("DO_LANDMARK_ROC")}" data-gate="DO_LANDMARK_ROC">${renderField(lmStatus)}</div>` : ""}
        </div>
        ${extra.map(renderField).join("")}
      </div>
    </div>`;
    }
    if (g.id === "dca" || g.id === "nri") {
      const order = g.id === "dca"
        ? ["DCA_CURVES"]
        : ["NRI_CUTOFF", "NRI_MODEL_OLD", "NRI_MODEL_NEW"];
      const sw = moduleSwitchKey(g.id);
      const vis = fields.filter(f => !isHiddenField(f) && f.key !== sw);
      const byKey = new Map(vis.map(f => [f.key, f]));
      const left = order.map(k => byKey.get(k)).filter(Boolean);
      for (const f of vis) {
        if (left.indexOf(f) < 0) left.push(f);
      }
      const hint = g.id === "dca"
        ? "开=画决策曲线。勾选要画的曲线：Clinical / RadScore / Combined，或从数据加临床列、阅片列。"
        : "相对旧模型的 NRI/IDI。切点、旧模型、新模型在同一行。";
      const head = sectionHead(g.id, g.title, left.length, hint);
      const bodyCls = g.id === "dca" ? "dca-body" : "nri-body";
      return `<div class="sec-block${head.dim}" data-sec="${esc(g.id)}">
      ${head.html}
      <div class="sec-body ${bodyCls}">${left.map(renderField).join("")}</div>
    </div>`;
    }
    if (g.id === "advanced") {
      // C：与 ROC 同构 — 布尔一行 + 分块；pathlist / checklist 全宽（E）
      const vis = fields.filter(f => !isHiddenField(f));
      if (!vis.length) return "";
      const byKey = new Map(vis.map(f => [f.key, f]));
      const boolOrder = ["DO_DAG", "DO_SHAP", "DO_MEDIATION"];
      const bools = [];
      for (const k of boolOrder) {
        const f = byKey.get(k);
        if (f && f.widget === "bool") bools.push(f);
      }
      for (const f of vis) {
        if (f.widget !== "bool" || bools.indexOf(f) >= 0) continue;
        bools.push(f);
      }
      const take = (keys) => keys.map(k => byKey.get(k)).filter(f => f && f.widget !== "bool");
      const blocks = [
        { title: "SHAP", fields: take(["SHAP_MAX_SAMPLES"]) },
        { title: "DAG", fields: take(["DAG_NODES"]) },
        { title: "中介路径", fields: take(["MEDIATION_PATHS"]) },
      ];
      const used = new Set(bools.map(f => f.key));
      blocks.forEach(b => b.fields.forEach(f => used.add(f.key)));
      const extra = vis.filter(f => !used.has(f.key) && f.widget !== "bool");
      if (extra.length) blocks.push({ title: "其他", fields: extra });
      const blockHtml = blocks.filter(b => b.fields.length).map(b =>
        `<div class="mod-block"><div class="mod-block-lab">${esc(b.title)}</div>${b.fields.map(renderField).join("")}</div>`
      ).join("");
      const hint = "DAG、SHAP、中介分析。VIF 阈值与 Bootstrap 次数在「开关」。";
      const titleKeys = ["DO_DAG", "DO_SHAP", "DO_MEDIATION"];
      const head = sectionHead(g.id, g.title, vis.length, hint, titleKeys);
      return `<div class="sec-block${head.dim}" data-sec="advanced">
      ${head.html}
      <div class="sec-body adv-body">
        ${blockHtml ? `<div class="mod-blocks">${blockHtml}</div>` : ""}
      </div>
    </div>`;
    }
    if (g.id === "corr") {
      const vis = fields.filter(f => !isHiddenField(f) && f.key !== "CORR_GROUP");
      if (!vis.length) return "";
      const byKey = new Map(vis.map(f => [f.key, f]));
      const order = ["HEATMAP_COLS"];
      const bools = vis.filter(f => f.widget === "bool");
      const rest = [];
      for (const k of order) {
        const f = byKey.get(k);
        if (f && f.widget !== "bool") rest.push(f);
      }
      for (const f of vis) {
        if (f.widget === "bool") continue;
        if (rest.indexOf(f) < 0) rest.push(f);
      }
      const html = rest.map(renderField).join("");
      const hint = "只画 Spearman 相关热图。列与组别在本块；相关表在「相关/偏相关分析」。";
      const head = sectionHead(g.id, g.title, vis.length, hint);
      return `<div class="sec-block${head.dim}" data-sec="corr">
      ${head.html}
      <div class="sec-body corr-body">
        ${bools.length ? `<div class="sec-bools">${bools.map(renderField).join("")}</div>` : ""}
        ${html ? `<div class="sec-rest">${html}</div>` : ""}
      </div>
    </div>`;
    }
    if (g.id === "nomo") {
      const titleKeys = ["DO_CALIBRATION"];
      const titleSet = new Set(titleKeys);
      const sw = moduleSwitchKey(g.id);
      const vis = fields.filter(f => !isHiddenField(f) && !titleSet.has(f.key) && f.key !== sw);
      if (!vis.length && !titleKeys.length) return "";
      const bools = vis.filter(f => f.widget === "bool");
      const rest = vis.filter(f => f.widget !== "bool");
      const restHtml = rest.map(renderField).join("");
      const hint = "列线图与校准曲线。";
      const head = sectionHead(g.id, g.title, vis.length + titleKeys.length, hint, titleKeys);
      return `<div class="sec-block${head.dim}" data-sec="nomo">
      ${head.html}
      <div class="sec-body nomo-body">
        ${bools.length ? `<div class="sec-bools">${bools.map(renderField).join("")}</div>` : ""}
        ${restHtml ? `<div class="sec-rest">${restHtml}</div>` : ""}
      </div>
    </div>`;
    }
    if (g.id === "exclude" || g.id === "valmode") {
      if (!parts.length) return "";
      const head = sectionHead(g.id, g.title, "");
      return `<div class="sec-block${head.dim}" data-sec="${esc(g.id)}">
      ${head.html}
      <div class="sec-body ${esc(g.id)}-body">${parts.join("")}</div>
    </div>`;
    }
    const head = sectionHead(g.id, g.title, parts.length);
    return `<div class="sec-block${head.dim}" data-sec="${esc(g.id)}">
      ${head.html}
      <div class="sec-body ${["nomo","roc","dca","nri","corr","advanced","rad","surv"].includes(g.id) ? "bools-wrap" : ""}">${parts.join("")}</div>
    </div>`;
  }).join(""));
  root.querySelectorAll("[data-key]").forEach(bindField);
  root.querySelectorAll(".path-list").forEach(bindPathList);
  root.querySelectorAll(".grow-rows").forEach(bindGrowRows);
  root.querySelectorAll("[data-td-pair]").forEach(bindTdRocPair);
  bindExcludeCascade();
  syncQcHtmlToggle();
  placeGrpCard();
  syncBaselineToggles();
  syncPartialGates();
  placePalCard();
  if (mid) mid.scrollTop = y;
}

function asMap(v) {
  return (v && typeof v === "object" && !Array.isArray(v)) ? v : {};
}
function asList(v) {
  return Array.isArray(v) ? v.map(x => String(x == null ? "" : x).trim()).filter(Boolean) : [];
}
function extraKvKeys(key) {
  if (!S.kvExtra) S.kvExtra = {};
  return S.kvExtra[key] || [];
}
function addExtraKvKey(key, left) {
  if (!S.kvExtra) S.kvExtra = {};
  S.kvExtra[key] = uniqKeep((S.kvExtra[key] || []).concat([String(left || "").trim()]));
}
function kvRow(key, left, val, kind, nest) {
  const nestAttr = nest ? ` data-kvn="${esc(nest)}"` : "";
  const chip = `<span class="grp-chip" title="${esc(left)}">${esc(left)}</span>`;
  let right = "";
  if (kind === "bin") {
    const s = (val === 0 || val === "0") ? "0" : ((val === 1 || val === "1") ? "1" : "");
    right = `<select data-kv="${esc(key)}" data-kvl="${esc(left)}"${nestAttr}>
      <option value="">（不编码）</option>
      <option value="1"${s === "1" ? " selected" : ""}>1 阳性</option>
      <option value="0"${s === "0" ? " selected" : ""}>0 阴性</option>
    </select>`;
  } else if (kind === "strip") {
    right = `<input data-kv="${esc(key)}" data-kvl="${esc(left)}" value="${esc(val == null ? "" : val)}" placeholder="空=不剥离" spellcheck="false"/>`;
  } else if (kind === "pexcl") {
    const on = val === true || val === "1" || val === 1;
    right = `<label class="pexcl"><input type="checkbox" data-kv="${esc(key)}" data-kvl="${esc(left)}"${on ? " checked" : ""}/> 排除</label>`;
  } else {
    right = `<input data-kv="${esc(key)}" data-kvl="${esc(left)}" value="${esc(val == null ? "" : val)}" placeholder="子工程名，空=用结局名" spellcheck="false"/>`;
  }
  let delTitle = "";
  if (key === "READER_VALUE_MAP") delTitle = "去掉这条，不再编码";
  else if (key === "RAD_FEATURE_PREFIX_EXCLUDE" || key === "RAD_FEATURE_DISPLAY_STRIP") {
    delTitle = "去掉这一行（需要时可下方手写加回）";
  }
  const del = delTitle
    ? `<button type="button" class="kv-del" data-kv-del="${esc(key)}" data-kvl="${esc(left)}" title="${esc(delTitle)}">−</button>`
    : "";
  return `<div class="kv-row">${chip}${right}${del}</div>`;
}
function hiddenKv(key) {
  if (!S.kvHide) S.kvHide = {};
  return S.kvHide[key] || [];
}
function hideKvToken(key, left) {
  const s = String(left || "").trim();
  if (!s) return;
  if (!S.kvHide) S.kvHide = {};
  S.kvHide[key] = uniqKeep((S.kvHide[key] || []).concat([s]));
}
function unhideKvToken(key, left) {
  if (!S.kvHide || !S.kvHide[key]) return;
  const s = String(left || "").trim();
  S.kvHide[key] = S.kvHide[key].filter((x) => x !== s);
}
function isDefaultBinLabel(s) {
  const t = String(s == null ? "" : s).trim().toLowerCase();
  return t === "positive" || t === "negative";
}
function groupPosNegControlled() {
  if (!subgroupIsOutcome()) return [];
  return groupLevels().map((x) => String(x == null ? "" : x).trim()).filter(Boolean);
}
function encodeColumns() {
  const dist = (S.preview && S.preview.clin_dist) || {};
  const outDist = (S.preview && S.preview.outcome_dist) || {};
  const clin = (S.preview && S.preview.clin_cols) || [];
  const skip = new Set([
    String(fieldValue("CLIN_ID_COL") || "").trim(),
    String(fieldValue("RAD_ID_COL") || "").trim(),
    String(fieldValue("LABEL_COL") || "").trim(),
    "Patient_ID", "Name",
  ].filter(Boolean));
  const ordered = [];
  const seen = new Set();
  const push = (c) => {
    const s = String(c || "").trim();
    if (!s || seen.has(s) || skip.has(s)) return;
    if (!dist[s] && !outDist[s]) return;
    seen.add(s);
    ordered.push(s);
  };
  clin.forEach(push);
  Object.keys(dist).forEach(push);
  Object.keys(outDist).forEach(push);
  return ordered;
}
function isRecistToken(t) {
  return ["cr", "pr", "sd", "pd"].indexOf(String(t || "").trim().toLowerCase()) >= 0;
}
function columnStrings(col) {
  const blocked = new Set(groupPosNegControlled());
  const skipVal = new Set(["nan", "none", "nat", "<na>", "null"]);
  return uniqKeep((columnLevels(col) || []).map((x) => String(x == null ? "" : x).trim()).filter((t) => {
    return t && !isDefaultBinLabel(t) && !isRecistToken(t) && !blocked.has(t) && !skipVal.has(t.toLowerCase());
  }));
}
function tokenIsEncoded(tok) {
  const hit = (v) => v === 0 || v === 1 || v === "0" || v === "1";
  const rmap = asMap(fieldValue("READER_VALUE_MAP"));
  if (Object.prototype.hasOwnProperty.call(rmap, tok) && hit(rmap[tok])) return true;
  const bmap = asMap(fieldValue("OUTCOME_BINARIZE"));
  for (const oc of Object.keys(bmap)) {
    const inner = asMap(bmap[oc]);
    if (Object.prototype.hasOwnProperty.call(inner, tok) && hit(inner[tok])) return true;
  }
  return false;
}
function encodeShownColumns() {
  if (!Array.isArray(S.encodeCols)) S.encodeCols = [];
  if (!Array.isArray(S.encodeHide)) S.encodeHide = [];
  const eligible = encodeColumns();
  const eligibleSet = new Set(eligible);
  const hide = new Set(S.encodeHide);
  const picked = S.encodeCols.filter((c) => eligibleSet.has(c) && !hide.has(c));
  const auto = [];
  eligible.forEach((col) => {
    if (hide.has(col) || picked.indexOf(col) >= 0) return;
    if (columnStrings(col).some(tokenIsEncoded)) auto.push(col);
  });
  return uniqKeep(picked.concat(auto));
}
function encodeValueRow(token) {
  const val = encodeValueOf(token);
  const s = (val === 0 || val === "0") ? "0" : ((val === 1 || val === "1") ? "1" : "");
  return `<div class="kv-row encode-val">
    <span class="grp-chip" title="${esc(token)}">${esc(token)}</span>
    <select data-kv="READER_VALUE_MAP" data-kvl="${esc(token)}">
      <option value="">（不编码）</option>
      <option value="1"${s === "1" ? " selected" : ""}>1 阳性</option>
      <option value="0"${s === "0" ? " selected" : ""}>0 阴性</option>
    </select>
  </div>`;
}
function encodeColumnBlock(col) {
  const strings = columnStrings(col);
  let body;
  if (!strings.length) {
    body = '<span class="kv-empty">这一列的阴阳性已在分组里设定，或没有可编码的文字。</span>';
  } else if (strings.length > 40) {
    const encoded = strings.filter(tokenIsEncoded);
    body = encoded.length
      ? `<span class="kv-empty">这一列有 ${strings.length} 个不同取值，只列出已编码的。</span>${encoded.map(encodeValueRow).join("")}`
      : `<span class="kv-empty">这一列有 ${strings.length} 个不同取值，不逐条列出。</span>`;
  } else {
    body = strings.map(encodeValueRow).join("");
  }
  return `<div class="encode-col">
    <div class="encode-head">
      <span class="grp-chip" title="${esc(col)}">${esc(col)}</span>
      <button type="button" class="kv-del" data-encode-col-del="${esc(col)}" title="收起这一列，已有编码保留">−</button>
    </div>
    ${body}
  </div>`;
}
function orphanEncodedTokens() {
  const blocked = new Set(groupPosNegControlled());
  const covered = new Set();
  encodeColumns().forEach((col) => columnStrings(col).forEach((t) => covered.add(t)));
  const out = [];
  const push = (k) => {
    const s = String(k == null ? "" : k).trim();
    if (!s || isDefaultBinLabel(s) || isRecistToken(s) || blocked.has(s) || covered.has(s) || !tokenIsEncoded(s)) return;
    out.push(s);
  };
  Object.keys(asMap(fieldValue("READER_VALUE_MAP"))).forEach(push);
  const bmap = asMap(fieldValue("OUTCOME_BINARIZE"));
  Object.keys(bmap).forEach((oc) => Object.keys(asMap(bmap[oc])).forEach(push));
  return uniqKeep(out);
}
function writeLevelCode(level, raw) {
  if (!subgroupIsOutcome()) return;
  const s = String(level || "").trim();
  if (!s) return;
  setEncodeValue(s, raw);
}
function levelPolaritySelect(level) {
  const v = encodeValueOf(level);
  const s = (v === 0 || v === "0") ? "0" : ((v === 1 || v === "1") ? "1" : "");
  return `<select class="grp-pol" data-gpol="${esc(level)}" data-tip="阴性=0，阳性=1，不选=不单独编码。含该文字的列一起写。">
    <option value="0"${s === "0" ? " selected" : ""}>阴性</option>
    <option value="1"${s === "1" ? " selected" : ""}>阳性</option>
    <option value=""${!s ? " selected" : ""}>不选</option>
  </select>`;
}
function columnsForToken(token) {
  const tok = String(token || "").trim();
  const out = [];
  const seen = new Set();
  const push = (c) => {
    const s = String(c || "").trim();
    if (!s || seen.has(s)) return;
    seen.add(s);
    out.push(s);
  };
  const bmap = asMap(fieldValue("OUTCOME_BINARIZE"));
  Object.keys(bmap).forEach((oc) => {
    if (Object.prototype.hasOwnProperty.call(asMap(bmap[oc]), tok)) push(oc);
  });
  encodeColumns().forEach((col) => {
    const levels = new Set((columnLevels(col) || []).map((x) => String(x)));
    if (levels.has(tok)) push(col);
  });
  return out;
}
function encodeValueOf(token) {
  const rmap = asMap(fieldValue("READER_VALUE_MAP"));
  if (Object.prototype.hasOwnProperty.call(rmap, token)) return rmap[token];
  const bmap = asMap(fieldValue("OUTCOME_BINARIZE"));
  const order = uniqKeep(outcomeCols().concat(Object.keys(bmap)));
  for (const oc of order) {
    const inner = asMap(bmap[oc]);
    if (Object.prototype.hasOwnProperty.call(inner, token)) return inner[token];
  }
  return "";
}
function setEncodeValue(token, raw) {
  const s = String(raw == null ? "" : raw).trim();
  const rmap = Object.assign({}, asMap(fieldValue("READER_VALUE_MAP")));
  if (!s) delete rmap[token];
  else rmap[token] = Number(s);
  setFieldValue("READER_VALUE_MAP", rmap);
  const bmap = Object.assign({}, asMap(fieldValue("OUTCOME_BINARIZE")));
  let touched = false;
  columnsForToken(token).forEach((oc) => {
    const inner = Object.assign({}, asMap(bmap[oc]));
    if (!s) {
      if (!Object.prototype.hasOwnProperty.call(inner, token)) return;
      delete inner[token];
    } else inner[token] = Number(s);
    if (!Object.keys(inner).length) delete bmap[oc];
    else bmap[oc] = inner;
    touched = true;
  });
  if (touched) setFieldValue("OUTCOME_BINARIZE", bmap);
}
function removeEncodeToken(token) {
  const s = String(token || "").trim();
  if (!s) return;
  hideKvToken("READER_VALUE_MAP", s);
  const rmap = Object.assign({}, asMap(fieldValue("READER_VALUE_MAP")));
  delete rmap[s];
  setFieldValue("READER_VALUE_MAP", rmap);
  const bmap = Object.assign({}, asMap(fieldValue("OUTCOME_BINARIZE")));
  let touched = false;
  Object.keys(bmap).forEach((oc) => {
    const inner = Object.assign({}, asMap(bmap[oc]));
    if (!Object.prototype.hasOwnProperty.call(inner, s)) return;
    delete inner[s];
    if (!Object.keys(inner).length) delete bmap[oc];
    else bmap[oc] = inner;
    touched = true;
  });
  if (touched) setFieldValue("OUTCOME_BINARIZE", bmap);
  if (S.kvExtra && Array.isArray(S.kvExtra.READER_VALUE_MAP)) {
    S.kvExtra.READER_VALUE_MAP = S.kvExtra.READER_VALUE_MAP.filter((x) => x !== s);
  }
}
function coarseRadPrefix(name) {
  // Known image types stay one token: original_firstorder_Mean → original_.
  // Other names keep the sequence/metric and its parameters, and stop at the ROI.
  // ep2d_diff_mddw_25_p2_adc_Frontal_L → ep2d_diff_mddw_25_p2_adc_
  // ADC_b1000_Frontal_L → ADC_b1000_
  // MD_basal_ganglia_L → MD_
  // perfusion_weighted_basal_ganglia_L → perfusion_weighted_
  // relcbf__basal_ganglia_L → relcbf_
  const s = String(name || "");
  if (!s) return "";
  const known = ["standard_fbn_32_", "standard_fbs_16_", "diagnostics_", "original_", "wavelet_", "wavelet-", "log-", "shape_"];
  for (let i = 0; i < known.length; i++) {
    if (s.indexOf(known[i]) === 0) return known[i];
  }
  const cut = s.indexOf("__");
  if (cut > 0) return s.slice(0, cut + 1); // foo__… → foo_
  const parts = s.split("_");
  if (parts.length < 2 || !parts[0]) return "";
  const roi = {
    frontal: 1, parietal: 1, temporal: 1, occipital: 1, insula: 1, cingulum: 1,
    thalamus: 1, cerebellum: 1, brainstem: 1, basal: 1, ganglia: 1, hippocampus: 1, acc: 1,
    l: 1, r: 1, bilat: 1, left: 1, right: 1, bilateral: 1
  };
  const kept = [parts[0]];
  for (let i = 1; i < parts.length; i++) {
    const tok = parts[i];
    if (!tok) break;
    const low = tok.toLowerCase();
    if (roi[low]) break;
    // TitleCase anatomy (Frontal). All-caps parameters (FA, P2) stay in the prefix.
    if (/^[A-Z][a-z]/.test(tok)) break;
    kept.push(tok);
  }
  return kept.join("_") + "_";
}
function coarseRadSuffix(name) {
  const s = String(name || "");
  const i = s.lastIndexOf("_");
  if (i <= 0 || i >= s.length - 1) return "";
  return s.slice(i);
}
function markerSide() {
  const raw = fieldValue("RAD_MARKER_SIDE");
  const s = String(raw == null ? "" : raw).trim();
  if (!s) return "both";
  return s;
}
function markerMinN() {
  const raw = fieldValue("RAD_MARKER_MIN_N");
  if (raw == null || raw === "") return 5;
  const n = Number(raw);
  if (!Number.isFinite(n)) return 5;
  return Math.floor(n);
}
function markerDetectPhrase() {
  const side = markerSide();
  const minN = markerMinN();
  const sideLab = side === "prefix" ? "前缀"
    : (side === "suffix" ? "后缀"
      : (side === "both" ? "前缀或后缀" : ("侧 " + side)));
  return sideLab + "，至少 " + minN + " 列";
}
function markerChoicesFromNames(names) {
  // 识别阈值只在界面。已保存标记仍按原记号匹配，不受最少列数限制。
  // 未识别的标记侧不改成「二者」，两侧都不自动列出。
  const side = markerSide();
  const minN = markerMinN();
  const wantPre = side === "prefix" || side === "both";
  const wantSuf = side === "suffix" || side === "both";
  const pre = new Map();
  const suf = new Map();
  (names || []).forEach((raw) => {
    const name = String(raw || "");
    if (!name) return;
    const p = coarseRadPrefix(name);
    if (p) pre.set(p, (pre.get(p) || 0) + 1);
    const sx = coarseRadSuffix(name);
    if (sx) suf.set(sx, (suf.get(sx) || 0) + 1);
  });
  const counts = new Map();
  if (wantPre) pre.forEach((n, p) => { if (n >= minN) counts.set(p, n); });
  if (wantSuf) suf.forEach((n, sx) => { if (n >= minN) counts.set("suffix:" + sx, n); });
  const kept = [...counts.keys()].sort((a, b) =>
    (counts.get(b) - counts.get(a)) || (a < b ? -1 : (a > b ? 1 : 0))
  );
  return { counts: counts, kept: kept };
}
function radFeatureNames() {
  const ids = radIdNames();
  const out = [];
  radPool().forEach((c) => {
    const name = String(c);
    if (!name || ids.has(name)) return;
    out.push(name);
  });
  return out;
}
function storedMarkerForTyped(tok) {
  const s = String(tok || "").trim();
  if (!s || s.indexOf("suffix:") === 0 || s.indexOf("prefix:") === 0) return s;
  let startN = 0;
  let endN = 0;
  radFeatureNames().forEach((name) => {
    if (name.indexOf(s) === 0) startN += 1;
    else if (s.slice(-2) === "__") {
      const broad = s.slice(0, -1);
      if (broad && name.indexOf(broad) === 0) startN += 1;
    }
    if (name.slice(-s.length) === s) endN += 1;
  });
  if (endN > startN) return "suffix:" + s;
  return s;
}
function radPrefixChoices() {
  const { counts, kept } = radPrefixDetectedCounts();
  const have = asList(fieldValue("RAD_FEATURE_PREFIX_EXCLUDE"));
  const hidden = new Set(hiddenKv("RAD_FEATURE_PREFIX_EXCLUDE"));
  const lefts = uniqKeep(have.concat(kept).concat(extraKvKeys("RAD_FEATURE_PREFIX_EXCLUDE")))
    .filter((p) => !hidden.has(p));
  return lefts.map((p) => ({
    prefix: p,
    n: radPrefixCoverCount(p, counts),
    detected: counts.has(p),
  }));
}
async function rediscoverRadPrefixes() {
  const btn = document.querySelector("[data-act=\"rad-prefix-rescan\"]");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "识别中…";
  }
  try {
    if (typeof reloadPreview === "function") await reloadPreview();
    const cols = radPool();
    const have = asList(fieldValue("RAD_FEATURE_PREFIX_EXCLUDE"));
    const kept = have.filter((p) => cols.some((c) => columnMatchesPrefix(c, p)));
    const dropped = have.filter((p) => kept.indexOf(p) < 0);
    setFieldValue("RAD_FEATURE_PREFIX_EXCLUDE", kept);
    if (S.kvExtra && Array.isArray(S.kvExtra.RAD_FEATURE_PREFIX_EXCLUDE)) {
      S.kvExtra.RAD_FEATURE_PREFIX_EXCLUDE = S.kvExtra.RAD_FEATURE_PREFIX_EXCLUDE.filter((x) => kept.indexOf(x) >= 0);
    }
    if (!S.kvFoldOpen) S.kvFoldOpen = {};
    S.kvFoldOpen.RAD_PREFIX_STRIP_COMBO = true;
    renderForm();
    markDirty();
    const n = radPrefixChoices().filter((r) => r.detected).length;
    const msg = "已按当前 rad 表头识别：" + n + " 个标记（" + markerDetectPhrase() + "）"
      + (dropped.length ? ("；去掉失效勾选 " + dropped.join("、")) : "");
    if ($("runHint")) $("runHint").textContent = msg;
    else alert(msg);
  } catch (e) {
    alert((e && e.message) || String(e));
  } finally {
    const b2 = document.querySelector("[data-act=\"rad-prefix-rescan\"]");
    if (b2) {
      b2.disabled = false;
      b2.textContent = "重新识别";
    }
  }
}
function radPrefixDetectedCounts() {
  return markerChoicesFromNames(radFeatureNames());
}
function radPrefixCoverCount(prefix, counts) {
  if (counts && counts.has(prefix)) return counts.get(prefix) || 0;
  const ids = radIdNames();
  let n = 0;
  radPool().forEach((c) => {
    const name = String(c);
    if (!name || ids.has(name)) return;
    if (columnMatchesPrefix(name, prefix)) n += 1;
  });
  return n;
}
function radPrefixStripChoices() {
  const { counts, kept } = radPrefixDetectedCounts();
  const haveEx = asList(fieldValue("RAD_FEATURE_PREFIX_EXCLUDE"));
  const haveSt = asList(fieldValue("RAD_FEATURE_DISPLAY_STRIP"));
  const extras = uniqKeep(
    extraKvKeys("RAD_FEATURE_PREFIX_EXCLUDE").concat(extraKvKeys("RAD_FEATURE_DISPLAY_STRIP"))
  );
  const hidden = new Set(
    hiddenKv("RAD_FEATURE_PREFIX_EXCLUDE").concat(hiddenKv("RAD_FEATURE_DISPLAY_STRIP"))
  );
  const want = uniqKeep(haveEx.concat(haveSt).concat(kept).concat(extras))
    .filter((p) => !hidden.has(p));
  // Checking a box used to rebuild with excludes first, so that row jumped
  // up under the ep2d block. Keep the order already on screen.
  if (!Array.isArray(S.prefixRowOrder)) S.prefixRowOrder = [];
  let ordered;
  if (!S.prefixRowOrder.length) {
    ordered = want.slice();
  } else {
    const wantSet = new Set(want);
    const stayed = S.prefixRowOrder.filter((p) => wantSet.has(p));
    const seen = new Set(stayed);
    ordered = stayed.concat(want.filter((p) => !seen.has(p)));
  }
  S.prefixRowOrder = ordered.slice();
  return ordered.map((p) => ({ prefix: p, n: radPrefixCoverCount(p, counts) }));
}
function prefixGroupSpec() {
  let raw = fieldValue("RAD_FEATURE_PREFIX_GROUP");
  if (typeof raw === "string") {
    try { raw = JSON.parse(raw); } catch (e) { raw = {}; }
  }
  const spec = (raw && typeof raw === "object" && !Array.isArray(raw)) ? raw : {};
  const names = [];
  const seen = new Set();
  (Array.isArray(spec.names) ? spec.names : []).forEach((x) => {
    const s = String(x || "").trim();
    if (!s || seen.has(s)) return;
    seen.add(s);
    names.push(s);
  });
  const assign = {};
  const src = (spec.assign && typeof spec.assign === "object") ? spec.assign : {};
  Object.keys(src).forEach((k) => {
    const p = String(k || "").trim();
    const g = String(src[k] == null ? "" : src[k]).trim();
    if (p && g && seen.has(g)) assign[p] = g;
  });
  return { names, assign };
}
function setPrefixGroup(spec) {
  setFieldValue("RAD_FEATURE_PREFIX_GROUP", {
    names: (spec.names || []).slice(),
    assign: Object.assign({}, spec.assign || {}),
  });
}
function radPrefixGroupSelect(prefix, exclOn, names, assigned) {
  if (exclOn) {
    return `<select class="pgrp" data-kv="RAD_FEATURE_PREFIX_GROUP" data-kvl="${esc(prefix)}" disabled title="已排除的标记不进入分类"><option>不分类</option></select>`;
  }
  const cur = assigned || "";
  const opts = ['<option value="">不分类</option>'].concat(names.map((n) =>
    `<option value="${esc(n)}"${n === cur ? " selected" : ""}>${esc(n)}</option>`
  ));
  return `<select class="pgrp" data-kv="RAD_FEATURE_PREFIX_GROUP" data-kvl="${esc(prefix)}" title="这一标记进入的分类">${opts.join("")}</select>`;
}
function radPrefixGroupBar(names) {
  const chips = names.map((n) =>
    `<span class="grp-chip pgrp-chip">${esc(n)}<button type="button" class="kv-del" data-grp-del="${esc(n)}" title="去掉这个分类">−</button></span>`
  ).join("");
  return `<div class="rad-prefix-groups">${chips}<input class="kv-add" data-kv-add-group="1" placeholder="+ 分类名，回车" spellcheck="false"/></div>`;
}
function radPrefixStripComboRow(prefix, n, exclOn, stripVal, names, assigned) {
  const spec = markerAnchor(prefix);
  const side = spec.anchor === "end" ? "后缀" : "前缀";
  const shown = side + " " + spec.part;
  const label = n ? `${shown} · ${n}` : shown;
  const chip = `<span class="grp-chip" title="${esc(prefix)}">${esc(label)}</span>`;
  const pexcl = `<label class="pexcl"><input type="checkbox" data-kv="RAD_FEATURE_PREFIX_EXCLUDE" data-kvl="${esc(prefix)}"${exclOn ? " checked" : ""}/> 排除</label>`;
  const grp = radPrefixGroupSelect(prefix, exclOn, names, assigned);
  const strip = `<input data-kv="RAD_FEATURE_DISPLAY_STRIP" data-kvl="${esc(prefix)}" value="${esc(stripVal == null ? "" : stripVal)}" placeholder="空=不剥离" spellcheck="false"/>`;
  const del = `<button type="button" class="kv-del" data-kv-del="RAD_PREFIX_STRIP_COMBO" data-kvl="${esc(prefix)}" title="去掉这一行（需要时可下方手写加回）">−</button>`;
  return `<div class="kv-row">${chip}${pexcl}${grp}${strip}${del}</div>`;
}
function radPrefixStripCombo(prefixF, stripF, asSection) {
  if (!prefixF && !stripF) return "";
  const open = S.kvFoldOpen && S.kvFoldOpen.RAD_PREFIX_STRIP_COMBO ? " open" : "";
  const sideNow = markerSide();
  const sideOpts = [["prefix", "前缀"], ["suffix", "后缀"], ["both", "二者"]];
  if (sideNow && !sideOpts.some((pair) => pair[0] === sideNow)) sideOpts.push([sideNow, sideNow]);
  const sideHtml = sideOpts.map((pair) =>
    `<option value="${esc(pair[0])}"${pair[0] === sideNow ? " selected" : ""}>${esc(pair[1])}</option>`
  ).join("");
  const ctl = `<span class="rad-marker-ctl" data-marker-ctl="1"><label>标记侧 <select data-key="RAD_MARKER_SIDE">${sideHtml}</select></label><label>最少列数 <input type="number" step="1" data-key="RAD_MARKER_MIN_N" value="${esc(markerMinN())}"/></label></span>`;
  const rescan = `<button type="button" class="rad-prefix-rescan" data-act="rad-prefix-rescan" title="按当前 rad 表头、标记侧和最少列数重新识别">重新识别</button>`;
  const excl = new Set(asList(fieldValue("RAD_FEATURE_PREFIX_EXCLUDE")));
  const stripHave = new Set(asList(fieldValue("RAD_FEATURE_DISPLAY_STRIP")));
  const spec = prefixGroupSpec();
  const choices = radPrefixStripChoices();
  const body = choices.length
    ? choices.map((row) => radPrefixStripComboRow(
        row.prefix,
        row.n,
        excl.has(row.prefix),
        stripHave.has(row.prefix) ? markerAnchor(row.prefix).part : "",
        spec.names,
        spec.assign[row.prefix] || ""
      )).join("")
    : `<span class="kv-empty">载入数据后按${esc(markerDetectPhrase())}识别；或下方回车添加。</span>`;
  const head = `<div class="kv-row rad-prefix-head"><span>标记</span><span>筛选</span><span>分类</span><span>剥离</span><span></span></div>`;
  const rows = `<div class="kv-rows rad-prefix-combo" data-key="RAD_PREFIX_STRIP_COMBO">
      ${radPrefixGroupBar(spec.names)}
      ${head}
      ${body}
      <input class="kv-add" data-kv-add-combo="1" placeholder="+ 标记，回车" spellcheck="false"/>
    </div>`;
  if (asSection) {
    return `<div class="rad-prefix-tools">${ctl}${rescan}</div>${rows}`;
  }
  return `<details class="kv-fold rad-strip" data-fold="RAD_PREFIX_STRIP_COMBO"${open}>
    <summary><span class="rad-prefix-sum">${esc("前缀 /筛选/ 分类 / 剥离")}${ctl}${rescan}</span></summary>
    ${rows}
  </details>`;
}
function stripPrefixes() {
  const have = asList(fieldValue("RAD_FEATURE_DISPLAY_STRIP"));
  const detected = radPrefixChoices().filter((row) => row.detected).map((row) => row.prefix);
  const extra = extraKvKeys("RAD_FEATURE_DISPLAY_STRIP");
  const cols = radPool();
  const kept = have.filter((p) => {
    if (extra.indexOf(p) >= 0) return true;
    if (!cols.length) return true;
    return cols.some((c) => columnMatchesPrefix(String(c), String(p)));
  });
  const hidden = new Set(hiddenKv("RAD_FEATURE_DISPLAY_STRIP"));
  return uniqKeep(detected.concat(kept).concat(extra)).filter((p) => !hidden.has(p));
}
function binarizeSummary(inner, levels) {
  const ones = [];
  const zeros = [];
  const unset = [];
  (levels || []).forEach((lv) => {
    if (isDefaultBinLabel(lv)) return;
    const x = inner[lv];
    if (x === 1 || x === "1") ones.push(lv);
    else if (x === 0 || x === "0") zeros.push(lv);
    else unset.push(lv);
  });
  const bits = [];
  if (ones.length) bits.push(ones.join("/") + "→1");
  if (zeros.length) bits.push(zeros.join("/") + "→0");
  if (unset.length) bits.push(unset.join("/") + "未编");
  return bits.length ? bits.join(" · ") : "未编码";
}
function renderKvField(f) {
  const key = f.key;
  const v = fieldValue(key);
  let rows = "";
  if (key === "SUBPROJECT_BY_OUTCOME") {
    const map = asMap(v);
    const lefts = uniqKeep(outcomeCols().concat(Object.keys(map)));
    const body = lefts.length
      ? lefts.map((oc) => kvRow(key, oc, map[oc] == null ? "" : map[oc], "text")).join("")
      : '<span class="kv-empty">先在左侧选出结局列。</span>';
    rows = body;
  } else if (key === "READER_VALUE_MAP") {
    const cols = encodeColumns();
    const shown = encodeShownColumns();
    const rest = cols.filter((c) => shown.indexOf(c) < 0);
    const parts = [];
    if (!cols.length) {
      parts.push('<span class="kv-empty">载入数据后从下拉选择列。</span>');
    } else if (rest.length) {
      parts.push(`<select class="kv-add" data-encode-col><option value="">+ 选择列…</option>${
        rest.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("")
      }</select>`);
    }
    shown.forEach((col) => parts.push(encodeColumnBlock(col)));
    const orphans = orphanEncodedTokens();
    if (orphans.length) {
      parts.push(`<div class="encode-col"><div class="encode-head"><span class="grp-chip">已有编码</span></div>${
        orphans.map(encodeValueRow).join("")
      }</div>`);
    }
    rows = parts.join("");
  } else if (key === "OUTCOME_BINARIZE") {
    const map = asMap(v);
    const outcomes = uniqKeep(outcomeCols().concat(Object.keys(map)));
    if (!outcomes.length) {
      rows = '<span class="kv-empty">先在上方选出结局列。</span>';
    } else {
      const openMap = S.kvFoldOpen || {};
      rows = outcomes.map((oc) => {
        const inner = asMap(map[oc]);
        const levels = uniqKeep(columnLevels(oc).concat(Object.keys(inner)))
          .filter((lv) => !isDefaultBinLabel(lv) && !isRecistToken(lv));
        if (!levels.length) return "";
        const body = levels.map((lv) => kvRow(key, lv, inner[lv], "bin", oc)).join("");
        const sum = binarizeSummary(inner, levels);
        const opened = openMap[oc] ? " open" : "";
        return `<details class="kv-fold" data-fold="${esc(oc)}"${opened}>
          <summary>${esc(oc)} <span class="kv-sum">${esc(sum)}</span></summary>
          ${body}
        </details>`;
      }).filter(Boolean).join("");
      if (!rows) {
        rows = '<span class="kv-empty">默认 Positive→1、Negative→0，不列出。</span>';
      }
    }
  } else if (key === "RAD_FEATURE_DISPLAY_STRIP") {
    const have = asList(v);
    const inList = new Set(have);
    const lefts = stripPrefixes();
    rows = lefts.length
      ? lefts.map((p) => kvRow(key, p, inList.has(p) ? p : "", "strip")).join("")
      : `<span class="kv-empty">载入数据后按${esc(markerDetectPhrase())}识别；或下方回车添加。</span>`;
    rows += `<input class="kv-add" data-kv-add="${esc(key)}" placeholder="+ 标记，回车" spellcheck="false"/>`;
  } else if (key === "RAD_FEATURE_PREFIX_EXCLUDE") {
    const have = new Set(asList(v));
    const choices = radPrefixChoices();
    rows = choices.length
      ? choices.map((row) => {
          const label = row.n ? `${row.prefix} · ${row.n}` : row.prefix;
          return kvRow(key, row.prefix, have.has(row.prefix), "pexcl").replace(
            `>${esc(row.prefix)}<`, `>${esc(label)}<`
          );
        }).join("")
      : `<span class="kv-empty">载入数据后按${esc(markerDetectPhrase())}识别；或下方回车添加。</span>`;
    rows += `<input class="kv-add" data-kv-add="${esc(key)}" placeholder="+ 标记，回车后排除" spellcheck="false"/>`;
  }
  const encodeCls = key === "READER_VALUE_MAP" ? " encode" : "";
  return `<div class="kv-rows${encodeCls}" data-key="${esc(key)}">${rows}</div>`;
}
function applyKvInput(el) {
  const key = el.dataset.kv;
  const left = el.dataset.kvl;
  const nest = el.dataset.kvn;
  const raw = el.value;
  if (!key || !left) return;
  if (key === "RAD_FEATURE_DISPLAY_STRIP") {
    const cur = asList(fieldValue(key)).filter((x) => x !== left);
    const typed = String(raw || "").trim();
    const save = typed && typed === markerAnchor(left).part ? left : typed;
    if (save && cur.indexOf(save) < 0) cur.push(save);
    setFieldValue(key, cur);
    if (save && save !== left) el.dataset.kvl = save;
    return;
  }
  if (key === "RAD_FEATURE_PREFIX_EXCLUDE") {
    const cur = asList(fieldValue(key)).filter((x) => x !== left);
    if (el.checked) cur.push(left);
    setFieldValue(key, cur);
    return;
  }
  if (key === "RAD_FEATURE_PREFIX_GROUP") {
    const spec = prefixGroupSpec();
    const g = String(raw || "").trim();
    if (!g || spec.names.indexOf(g) < 0) delete spec.assign[left];
    else spec.assign[left] = g;
    setPrefixGroup(spec);
    return;
  }
  if (nest) {
    const all = Object.assign({}, asMap(fieldValue(key)));
    const inner = Object.assign({}, asMap(all[nest]));
    const s = String(raw == null ? "" : raw).trim();
    if (!s) delete inner[left];
    else inner[left] = (key === "OUTCOME_BINARIZE" || key === "READER_VALUE_MAP") ? Number(s) : s;
    if (!Object.keys(inner).length) delete all[nest];
    else all[nest] = inner;
    setFieldValue(key, all);
    return;
  }
  if (key === "READER_VALUE_MAP") {
    setEncodeValue(left, raw);
    return;
  }
  const all = Object.assign({}, asMap(fieldValue(key)));
  const s = String(raw == null ? "" : raw).trim();
  if (!s) delete all[left];
  else all[left] = s;
  setFieldValue(key, all);
}
function addKvToken(key, token) {
  const s = String(token || "").trim();
  if (!s) return;
  unhideKvToken(key, s);
  addExtraKvKey(key, s);
  if (key === "RAD_FEATURE_DISPLAY_STRIP" || key === "RAD_FEATURE_PREFIX_EXCLUDE") {
    const cur = asList(fieldValue(key));
    if (cur.indexOf(s) < 0) {
      cur.push(s);
      setFieldValue(key, cur);
    }
  }
  refreshField(key);
}

function renderBundle(b, fields) {
  const hintHtml = b.hint
    ? `<span class="grp-hint" title="${esc(b.hint)}">${esc(b.hint)}</span>`
    : "";
  const bools = fields.filter(f => f.widget === "bool");
  const rest = fields.filter(f => f.widget !== "bool");
  const subLabs = b.cls === "sub3"
    ? `<div class="sub3-labs"><span>结局列</span><span>结局名</span><span>子工程文件夹</span></div>`
    : "";
  const labHtml = b.title
    ? `<div class="grp-lab"><span class="grp-title">${esc(b.title)}</span>${hintHtml}</div>`
    : "";
  const inner = `<div class="field-group ${esc(b.cls)}">
    ${labHtml}
    ${bools.length ? `<div class="grp-bools">${bools.map(renderField).join("")}</div>` : ""}
    ${rest.length ? `<div class="grp-rest">${subLabs}${rest.map(renderField).join("")}</div>` : ""}
  </div>`;
  if (b.id !== "maps") return inner;
  // 文本编码默认收起。打开状态记在 kvFoldOpen.maps，不删 READER_VALUE_MAP。
  const open = S.kvFoldOpen && S.kvFoldOpen.maps ? " open" : "";
  return `<details class="kv-fold encode-fold" data-fold="maps"${open}><summary>${esc(b.title)}</summary>${inner}</details>`;
}

function renderGrowSelect(f) {
  const picked = growItems(fieldValue(f.key));
  const rows = picked.concat([""]);
  const emptyLab = (f.option_labels && f.option_labels[""] != null) ? f.option_labels[""] : "（不设）";
  const moreLab = f.more_label || "（再选）";
  const opts = (f.options || []).map((o) => String(o)).filter((s) => s);
  const html = rows.map((cur, i) => {
    const blank = (i === rows.length - 1 && picked.length) ? moreLab : emptyLab;
    const taken = new Set(picked.filter((v) => v !== cur));
    let options = `<option value="">${esc(blank)}</option>`;
    opts.forEach((s) => {
      if (taken.has(s)) return;
      const lab = (f.option_labels && f.option_labels[s] != null) ? f.option_labels[s] : s;
      options += `<option value="${esc(s)}"${s === cur ? " selected" : ""}>${esc(lab)}</option>`;
    });
    if (cur && opts.indexOf(cur) < 0) {
      options += `<option value="${esc(cur)}" selected>${esc(cur)}</option>`;
    }
    const del = cur
      ? `<button type="button" class="kv-del" data-grow-del="${esc(f.key)}" title="删这一条">×</button>`
      : "";
    return `<div class="grow-row"><select data-grow="${esc(f.key)}">${options}</select>${del}</div>`;
  }).join("");
  return `<div class="grow-rows" data-key="${esc(f.key)}">${html}</div>`;
}
function commitGrow(box, changedEl) {
  const key = box && box.getAttribute("data-key");
  if (!key) return;
  const prevFirst = growItems(fieldValue(key))[0] || "";
  const nextPacked = packGrow([...box.querySelectorAll("[data-grow]")].map((el) => el.value));
  const nextItems = growItems(nextPacked);
  const nextFirst = nextItems[0] || "";
  setFieldValue(key, nextPacked);
  const firstChanged = prevFirst !== nextFirst;
  if (key === "EXCLUDE_FILE" && firstChanged) {
    setFieldValue("EXCLUDE_SHEET", "");
    setFieldValue("EXCLUDE_COL", "");
    setFieldValue("EXCLUDE_ROW", "");
  }
  refreshField(key);
  markDirty();
  if (key !== "EXCLUDE_FILE") return;
  const src = firstChanged ? nextFirst : String((changedEl && changedEl.value) || "");
  if (firstChanged || src) {
    refreshExcludeDownstream(firstChanged ? "EXCLUDE_FILE" : "EXCLUDE_FILE_ADD", src);
  }
}
function bindGrowRows(box) {
  if (!box || box.dataset.growBound) return;
  box.dataset.growBound = "1";
  box.querySelectorAll("[data-grow]").forEach((el) => {
    el.addEventListener("change", () => commitGrow(box, el));
  });
}
function renderField(f) {
  const v = fieldValue(f.key);
  const onPair = !!(pairTab() && PAIR_OVERRIDE_KEYS.has(f.key));
  const onOut = !!(!pairTab() && outcomeTab() && OUTCOME_OVERRIDE_KEYS.has(f.key));
  const isolated = f.key === "SUBGROUP_COL";
  const inherited = !isolated && !!((onPair || onOut) && !fieldHasOverride(f.key));
  const dirty = (onPair || onOut)
    ? fieldHasOverride(f.key)
    : !eq(S.values[f.key], S.base[f.key]);
  const bundled = FIELD_BUNDLES.some(b => b.keys.includes(f.key));
  const wide = !bundled && (
    f.widget === "json" || (f.widget === "text" && String(v).length > 40)
  );
  const span2 = !bundled && f.section !== "clin"
    && !["ancova", "mlr", "pcorr", "clinprep", "nomo", "roc", "corr", "advanced", "fig", "rad", "radlog", "surv"].includes(f.section)
    && (f.widget === "checklist" || f.widget === "numlist" || f.widget === "addselect");
  let inner = "";
  const tipBits = [];
  if (f.help) tipBits.push(String(f.help));
  if (f.key) tipBits.push(String(f.key));
  const tip = tipBits.length ? ` data-tip="${esc(tipBits.join("\n"))}"` : "";
  const star = "";
  const hotCls = "";
  const inhLab = pairTab()
    ? (outcomeCols().length > 1 ? "继承结局" : "用默认")
    : (PUBLIC_HIDE_WHEN_MULTI.has(f.key) ? "用默认" : "继承公共");
  const inh = inherited ? `<span class="inh">${inhLab}</span>` : "";
  const unovLab = pairTab()
    ? (outcomeCols().length > 1 ? "恢复结局" : "恢复默认")
    : (PUBLIC_HIDE_WHEN_MULTI.has(f.key) ? "恢复默认" : "恢复公共");
  const unov = fieldHasOverride(f.key)
    ? `<button type="button" class="unov" data-unov="${esc(f.key)}">${unovLab}</button>`
    : "";
  if (f.widget === "bool") {
    inner = `<label${tip}>${star}<input type="checkbox" data-key="${esc(f.key)}" ${v ? "checked":""}/> ${esc(f.label)}${inh}${unov}</label>`;
    return `<div class="field bool${hotCls} ${dirty?"dirty":""} ${inherited?"inherit":""}">${inner}</div>`;
  }
  const lab = `<div class="lab"${tip}>${star}${esc(f.label)}${inh}${unov}</div>`;
  if (["SUBPROJECT_BY_OUTCOME", "READER_VALUE_MAP", "OUTCOME_BINARIZE", "RAD_FEATURE_DISPLAY_STRIP", "RAD_FEATURE_PREFIX_EXCLUDE"].indexOf(f.key) >= 0) {
    inner = renderKvField(f);
  } else if (f.widget === "number") {
    const ph = f.placeholder ? ` placeholder="${esc(f.placeholder)}"` : "";
    let shown = v;
    if ((shown === null || shown === undefined || shown === "") && f.key === "ORDINAL_MAX_LEVELS") {
      shown = 5;
    }
    const step = f.key === "ORDINAL_MAX_LEVELS" ? "1" : "any";
    const minAttr = f.key === "ORDINAL_MAX_LEVELS" ? ` min="1"` : "";
    inner = `<input type="number" step="${step}"${minAttr} data-key="${esc(f.key)}" value="${shown ?? ""}"${ph}/>`;
  } else if (f.widget === "growselect") {
    inner = renderGrowSelect(f);
  } else if (f.widget === "select") {
    const labels = f.option_labels || {};
    const opts = (f.options || []).map(o => {
      let lab = (o === "" || o == null) ? "（不选）" : o;
      if (labels && labels[o] != null) lab = labels[o];
      else if (labels && labels[String(o)] != null) lab = labels[String(o)];
      return `<option value="${esc(o)}" ${String(o)===String(v ?? "")?"selected":""}>${esc(lab)}</option>`;
    }).join("");
    inner = `<select data-key="${esc(f.key)}">${opts}</select>`;
  } else if (f.widget === "checklist") {
    const have = selectedNames(v);
    const boxes = have.map(x =>
      `<label><input type="checkbox" data-list="${esc(f.key)}" value="${esc(x)}" checked/> ${esc(x)}</label>`
    ).join("");
    inner = `<div class="checks" data-key="${esc(f.key)}"><div class="chip-row">${boxes}</div>
      ${renderAdder(f)}</div>`;
  } else if (f.widget === "addselect") {
    const have = selectedNames(v);
    const locked = new Set((f.locked_options || implicitSkip()).map(String));
    const chips = have.map(x => {
      const lock = f.key === "SKIP_CLIN_FEATURES" && locked.has(String(x));
      return `<label class="${lock ? "lock" : ""}"><input type="checkbox" data-list="${esc(f.key)}" value="${esc(x)}" checked ${lock ? "disabled" : ""}/> ${esc(x)}</label>`;
    }).join("");
    inner = `<div class="checks" data-key="${esc(f.key)}"><div class="chip-row">${chips || ""}</div>
      ${renderAdder(f)}</div>`;
  } else if (f.widget === "timeselect") {
    const have = (Array.isArray(v) ? v : []).map(x => Number(x)).filter(n => !Number.isNaN(n));
    const common = (Array.isArray(f.options) && f.options.length) ? f.options.map(Number) : [3, 6, 12, 24];
    const chips = have.map(x =>
      `<label><input type="checkbox" data-list="${esc(f.key)}" value="${esc(x)}" checked/> ${esc(x)}</label>`
    ).join("");
    const haveSet = new Set(have.map(String));
    const remain = common.filter(n => !Number.isNaN(n) && !haveSet.has(String(n)));
    const opts = [`<option value="">+ 常用月</option>`].concat(
      remain.map(n => `<option value="${esc(n)}">${esc(n)}</option>`)
    ).join("");
    inner = `<div class="checks td-times" data-key="${esc(f.key)}"><div class="chip-row">${chips}</div>
      <div class="td-time-add"><select data-addtime="${esc(f.key)}">${opts}</select>
      <input type="text" data-addnum="${esc(f.key)}" placeholder="手写月数，回车" inputmode="decimal"/></div></div>`;
  } else if (f.widget === "numlist") {
    const have = Array.isArray(v) ? v : [];
    const boxes = have.map(x =>
      `<label><input type="checkbox" data-list="${esc(f.key)}" value="${esc(x)}" checked/> ${esc(x)}</label>`
    ).join("");
    inner = `<div class="checks" data-key="${esc(f.key)}"><div class="chip-row">${boxes}</div>
      <input type="text" data-addnum="${esc(f.key)}" placeholder="回车添加切点"/></div>`;
  } else if (f.widget === "json") {
    inner = `<textarea data-key="${esc(f.key)}">${esc(JSON.stringify(v, null, 2))}</textarea>`;
  } else if (f.widget === "pathlist") {
    inner = renderPathList(f, v);
  } else if (f.widget === "textlist") {
    const text = Array.isArray(v) ? v.join(", ") : String(v || "");
    const ph = f.placeholder ? ` placeholder="${esc(f.placeholder)}"` : ' placeholder="列名，逗号分隔"';
    inner = `<input type="text" data-key="${esc(f.key)}" data-textlist="1" value="${esc(text)}"${ph}/>`;
  } else {
    const ph = f.placeholder ? ` placeholder="${esc(f.placeholder)}"` : "";
    inner = `<input type="text" data-key="${esc(f.key)}" value="${esc(v ?? "")}"${ph}/>`;
  }
  return `<div class="field${hotCls} ${wide?"wide":""} ${span2?"span2":""} ${f.widget==="pathlist"?"pathlist":""} ${dirty?"dirty":""} ${inherited?"inherit":""}">${lab}${inner}</div>`;
}

function mediationNodeOptions(f) {
  const pool = (f && f.option_pool) || [];
  const nodes = fieldValue("DAG_NODES") || [];
  return uniqKeep(["RadScore", "Group", "Combined"].concat(nodes).concat(clinPool()).concat(pool).filter(Boolean));
}
function pathSelectHtml(opts, cur, emptyLab) {
  const v = String(cur || "");
  let html = `<option value="">${esc(emptyLab || "（不选）")}</option>`;
  opts.forEach((o) => {
    html += `<option value="${esc(o)}" ${String(o) === v ? "selected" : ""}>${esc(o)}</option>`;
  });
  return html;
}
function renderPathList(f, v) {
  const stored = Array.isArray(v) ? v.filter((p) => p && typeof p === "object") : [];
  const rows = stored.length ? stored : [{ x: "RadScore", m: "", y: "Group" }];
  const opts = mediationNodeOptions(f);
  const rowHtml = rows.map((p, i) => `
    <div class="path-row" data-path="${i}">
      <select data-path-field="x">${pathSelectHtml(opts, p.x, "（X）")}</select>
      <span class="path-arrow">→</span>
      <select data-path-field="m">${pathSelectHtml(opts, p.m, "（中介）")}</select>
      <span class="path-arrow">→</span>
      <select data-path-field="y">${pathSelectHtml(opts, p.y, "（结局）")}</select>
      <button type="button" class="btn sec" data-path-del="${i}" title="删这一条">删</button>
    </div>`).join("");
  return `<div class="path-list" data-key="${esc(f.key)}">${rowHtml}
    <button type="button" class="btn sec path-add" data-path-add="${esc(f.key)}">+ 路径</button></div>`;
}
function readPathRows(box) {
  return [...box.querySelectorAll(".path-row")].map((row, i) => {
    const val = (k) => ((row.querySelector(`[data-path-field="${k}"]`) || {}).value || "").trim();
    const x = val("x"), m = val("m"), y = val("y");
    if (!x || !m || !y) return null;
    return { id: `M${i + 1}`, x, m, y, label: `${x} → ${m} → ${y}` };
  }).filter(Boolean);
}
function bindPathList(box) {
  const key = box.getAttribute("data-key");
  if (!key) return;
  const commit = () => {
    setFieldValue(key, readPathRows(box));
    markDirty();
  };
  box.querySelectorAll("select").forEach((sel) => {
    sel.addEventListener("change", commit);
  });
  const add = box.querySelector("[data-path-add]");
  if (add) {
    add.onclick = () => {
      const cur = Array.isArray(fieldValue(key)) ? fieldValue(key).slice() : [];
      cur.push({ x: "RadScore", m: "", y: "Group" });
      setFieldValue(key, cur);
      refreshField(key);
    };
  }
  box.querySelectorAll("[data-path-del]").forEach((btn) => {
    btn.onclick = () => {
      const i = Number(btn.getAttribute("data-path-del"));
      const cur = (Array.isArray(fieldValue(key)) ? fieldValue(key) : []).slice();
      if (i >= 0 && i < cur.length) cur.splice(i, 1);
      else setFieldValue(key, readPathRows(box));
      setFieldValue(key, cur);
      refreshField(key);
    };
  });
}

function bindField(el) {
  if (el.matches("select[data-add]")) return;
  const key = el.getAttribute("data-key");
  if (!key || el.classList.contains("checks") || el.classList.contains("kv-rows") || el.classList.contains("path-list") || el.classList.contains("grow-rows")) return;
  if (el.matches("input[type=checkbox]") && key && !el.hasAttribute("data-list")) {
    el.addEventListener("change", () => {
      setFieldValue(key, el.checked);
      markDirty();
      const block = el.closest(".sec-block");
      if (block && el.closest(".mod-sw")) block.classList.toggle("mod-off", !el.checked);
      const gate = block && block.querySelector(`[data-gate="${key}"]`);
      if (gate) gate.classList.toggle("partial-off", !el.checked);
      if (key === "DO_PARTIAL") syncPartialGates();
    });
    return;
  }
  if (el.matches("select") && key && !el.dataset.add) {
    el.addEventListener("change", () => {
      const f = S.schema.find(x => x.key === key);
      let val = el.value;
      const sample = (f && Array.isArray(f.options) && f.options.length) ? f.options[0] : S.base[key];
      if (typeof sample === "number") {
        const n = Number(val);
        if (!Number.isNaN(n)) val = n;
      }
      setFieldValue(key, val);
      if (key === "SURVIVAL_TIME_COL") setFieldValue("TD_IPCW_TIME_COL", val);
      markDirty();
      if (key === "RAD_MARKER_SIDE") renderForm();
      if (SHEET_KEYS.indexOf(key) >= 0 || ID_KEYS.indexOf(key) >= 0) {
        loadDataModule().catch(e => alert(e.message));
      }
    });
    return;
  }
  if (el.matches("input[type=number]")) {
    el.addEventListener("change", () => {
      if (key === "ORDINAL_MAX_LEVELS" && el.value === "") {
        setFieldValue(key, 5);
        el.value = "5";
        markDirty();
        return;
      }
      const n = el.value === "" ? null : Number(el.value);
      setFieldValue(key, Number.isInteger(n) && !String(el.value).includes(".") ? parseInt(el.value,10) : n);
      markDirty();
      if (key === "RAD_MARKER_MIN_N") renderForm();
    });
    return;
  }
  if (el.matches("textarea")) {
    el.addEventListener("change", () => {
      try { setFieldValue(key, JSON.parse(el.value)); el.style.borderColor = ""; }
      catch { el.style.borderColor = "#9b2c2c"; }
      markDirty();
    });
    return;
  }
  if (el.matches("input[type=text]") && key) {
    el.addEventListener("change", () => {
      if (el.getAttribute("data-textlist") === "1") {
        const parts = String(el.value || "").split(/[,，;；]/).map(s => s.trim()).filter(Boolean);
        setFieldValue(key, parts);
      } else {
        setFieldValue(key, el.value);
      }
      markDirty();
    });
  }
}

function bindDelegates() {
  document.body.addEventListener("mousedown", (e) => {
    if (e.target.closest && e.target.closest("[data-marker-ctl]")) e.stopPropagation();
    const sheet = e.target.closest && e.target.closest("[data-add-sheet]");
    if (sheet) {
      e.preventDefault();
      addSheetFeatures(sheet.getAttribute("data-add-pick-key"), sheet.getAttribute("data-add-sheet"));
      return;
    }
    const pick = e.target.closest && e.target.closest("[data-add-pick]");
    if (!pick) return;
    e.preventDefault();
    addListValue(pick.getAttribute("data-add-pick-key"), pick.getAttribute("data-add-pick"));
  });
  document.body.addEventListener("click", (e) => {
    if (e.target.closest && e.target.closest("[data-marker-ctl]")) e.stopPropagation();
    const rescan = e.target.closest && e.target.closest("[data-act=\"rad-prefix-rescan\"]");
    if (rescan) {
      e.preventDefault();
      e.stopPropagation();
      rediscoverRadPrefixes();
      return;
    }
    const all = e.target.closest && e.target.closest("[data-add-all]");
    if (!all || all.disabled) return;
    e.preventDefault();
    const wrap = all.closest(".addfind");
    const input = wrap && wrap.querySelector("[data-add-q]");
    addAllValues(all.getAttribute("data-add-all"), input ? input.value : "");
  });
  document.body.addEventListener("click", (e) => {
    const clear = e.target.closest && e.target.closest("[data-add-clear]");
    if (!clear || clear.disabled) return;
    e.preventDefault();
    clearAddedValues(clear.getAttribute("data-add-clear"));
  });
  document.body.addEventListener("focusin", (e) => {
    const t = e.target;
    if (t && t.dataset && t.dataset.addQ) paintAddMenu(t);
  });
  document.body.addEventListener("input", (e) => {
    const t = e.target;
    if (t && t.dataset && t.dataset.addQ) paintAddMenu(t);
  });
  document.body.addEventListener("focusout", (e) => {
    const t = e.target;
    if (!t || !t.dataset || !t.dataset.addQ) return;
    const wrap = t.closest(".addfind");
    setTimeout(() => {
      if (!wrap || wrap.contains(document.activeElement)) return;
      const menu = wrap.querySelector(".addfind-menu");
      if (menu) {
        menu.hidden = true;
        menu.innerHTML = "";
      }
    }, 150);
  });
  document.body.addEventListener("click", (e) => {
    const growDel = e.target.closest("[data-grow-del]");
    if (growDel) {
      e.preventDefault();
      const row = growDel.closest(".grow-row");
      const sel = row && row.querySelector("[data-grow]");
      const box = growDel.closest(".grow-rows");
      if (sel) sel.value = "";
      if (box) commitGrow(box, sel);
      return;
    }
    const colDel = e.target.closest("[data-encode-col-del]");
    if (colDel) {
      e.preventDefault();
      const col = colDel.getAttribute("data-encode-col-del") || "";
      if (!col) return;
      if (!Array.isArray(S.encodeHide)) S.encodeHide = [];
      if (S.encodeHide.indexOf(col) < 0) S.encodeHide.push(col);
      if (Array.isArray(S.encodeCols)) S.encodeCols = S.encodeCols.filter((x) => x !== col);
      refreshField("READER_VALUE_MAP");
      return;
    }
    const grpDel = e.target.closest("[data-grp-del]");
    if (grpDel) {
      e.preventDefault();
      const name = grpDel.getAttribute("data-grp-del") || "";
      const spec = prefixGroupSpec();
      spec.names = spec.names.filter((n) => n !== name);
      Object.keys(spec.assign).forEach((p) => {
        if (spec.assign[p] === name) delete spec.assign[p];
      });
      setPrefixGroup(spec);
      if (!S.kvFoldOpen) S.kvFoldOpen = {};
      S.kvFoldOpen.RAD_PREFIX_STRIP_COMBO = true;
      renderForm();
      markDirty();
      return;
    }
    const kvDel = e.target.closest("[data-kv-del]");
    if (kvDel) {
      e.preventDefault();
      const key = kvDel.getAttribute("data-kv-del");
      const left = kvDel.getAttribute("data-kvl") || "";
      if (key === "READER_VALUE_MAP") {
        removeEncodeToken(left);
      } else if (key === "RAD_PREFIX_STRIP_COMBO"
          || key === "RAD_FEATURE_PREFIX_EXCLUDE"
          || key === "RAD_FEATURE_DISPLAY_STRIP") {
        const keys = key === "RAD_PREFIX_STRIP_COMBO"
          ? ["RAD_FEATURE_PREFIX_EXCLUDE", "RAD_FEATURE_DISPLAY_STRIP"]
          : [key];
        keys.forEach((k) => {
          hideKvToken(k, left);
          setFieldValue(k, asList(fieldValue(k)).filter((x) => x !== left));
          if (S.kvExtra && Array.isArray(S.kvExtra[k])) {
            S.kvExtra[k] = S.kvExtra[k].filter((x) => x !== left);
          }
        });
        if (key === "RAD_PREFIX_STRIP_COMBO") {
          const spec = prefixGroupSpec();
          if (spec.assign[left]) {
            delete spec.assign[left];
            setPrefixGroup(spec);
          }
        }
        if (!S.kvFoldOpen) S.kvFoldOpen = {};
        S.kvFoldOpen.RAD_PREFIX_STRIP_COMBO = true;
        renderForm();
      } else {
        refreshField(key);
      }
      markDirty();
      return;
    }
    const btn = e.target.closest("[data-unov]");
    if (!btn) return;
    e.preventDefault();
    clearFieldOverride(btn.getAttribute("data-unov"));
    renderForm();
    markDirty();
  });
  document.body.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.list) {
      const key = t.dataset.list;
      const wrap = t.closest(".checks");
      const rawVals = [...wrap.querySelectorAll(`input[data-list="${cssEscape(key)}"]:checked`)]
        .map(x => x.value);
      if (key === "TD_IPCW_HORIZONS") {
        // 空的 base 不会把勾选值转成数字。时间点必须保持数字列表。
        const nums = rawVals.map(x => Number(x)).filter(n => !Number.isNaN(n));
        nums.sort((a, b) => a - b);
        setFieldValue(key, nums);
        refreshField(key);
      } else {
        setFieldValue(key, rawVals.map(x => coerce(x, S.base[key])));
        if (clinAddselectKeys().indexOf(key) >= 0) {
          refreshClinRoleDropdowns();
          renderForm();
        }
      }
      markDirty();
    }
    if (t.dataset.addtime) {
      const key = t.dataset.addtime;
      const n = Number(t.value);
      if (t.value !== "" && !Number.isNaN(n)) {
        const cur = (Array.isArray(fieldValue(key)) ? fieldValue(key) : [])
          .map(x => Number(x)).filter(x => !Number.isNaN(x));
        if (!cur.includes(n)) cur.push(n);
        cur.sort((a, b) => a - b);
        setFieldValue(key, cur);
        refreshField(key);
        markDirty();
      }
    }
    if (t.dataset.add) {
      const key = t.dataset.add;
      const v = t.value;
      if (!v) return;
      e.stopPropagation();
      const raw = fieldValue(key);
      const cur = Array.isArray(raw) ? raw.map(String) : (raw ? [String(raw)] : []);
      if (cur.indexOf(v) < 0) cur.push(v);
      setFieldValue(key, cur);
      t.value = "";
      refreshField(key);
    }
    if (t.hasAttribute("data-encode-col")) {
      const v = String(t.value || "").trim();
      if (!v) return;
      if (!Array.isArray(S.encodeCols)) S.encodeCols = [];
      if (!Array.isArray(S.encodeHide)) S.encodeHide = [];
      S.encodeHide = S.encodeHide.filter((x) => x !== v);
      if (S.encodeCols.indexOf(v) < 0) S.encodeCols.push(v);
      refreshField("READER_VALUE_MAP");
      return;
    }
    if (t.dataset.kv) {
      applyKvInput(t);
      markDirty();
      if (t.dataset.kv === "RAD_FEATURE_PREFIX_EXCLUDE") {
        // Replace the form after this click finishes. A sync rebuild detaches
        // the label while it is still activating, and the row that slides
        // under the pointer takes the check.
        setTimeout(() => renderForm(), 0);
      }
      if (t.dataset.kv === "OUTCOME_BINARIZE") {
        const fold = t.closest("details.kv-fold");
        const oc = fold && fold.getAttribute("data-fold");
        const sumEl = fold && fold.querySelector("summary .kv-sum");
        if (oc && sumEl) {
          const inner = asMap(asMap(fieldValue("OUTCOME_BINARIZE"))[oc]);
          const levels = [...fold.querySelectorAll("[data-kvl]")].map((el) => el.getAttribute("data-kvl"));
          sumEl.textContent = binarizeSummary(inner, uniqKeep(levels));
        }
      }
    }
  });
  document.body.addEventListener("toggle", (e) => {
    const d = e.target.closest && e.target.closest("details.kv-fold");
    if (!d || e.target !== d) return;
    if (!S.kvFoldOpen) S.kvFoldOpen = {};
    S.kvFoldOpen[d.getAttribute("data-fold") || ""] = !!d.open;
  }, true);
  document.body.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t.dataset.kvAddGroup && e.key === "Enter") {
      e.preventDefault();
      const tok = (t.value || "").trim();
      if (!tok) return;
      const spec = prefixGroupSpec();
      if (spec.names.indexOf(tok) < 0) spec.names.push(tok);
      setPrefixGroup(spec);
      t.value = "";
      if (!S.kvFoldOpen) S.kvFoldOpen = {};
      S.kvFoldOpen.RAD_PREFIX_STRIP_COMBO = true;
      renderForm();
      markDirty();
      return;
    }
    if (t.dataset.kvAddCombo && e.key === "Enter") {
      e.preventDefault();
      const tok = storedMarkerForTyped((t.value || "").trim());
      if (!tok) return;
      unhideKvToken("RAD_FEATURE_PREFIX_EXCLUDE", tok);
      unhideKvToken("RAD_FEATURE_DISPLAY_STRIP", tok);
      addExtraKvKey("RAD_FEATURE_PREFIX_EXCLUDE", tok);
      addExtraKvKey("RAD_FEATURE_DISPLAY_STRIP", tok);
      t.value = "";
      if (!S.kvFoldOpen) S.kvFoldOpen = {};
      S.kvFoldOpen.RAD_PREFIX_STRIP_COMBO = true;
      renderForm();
      markDirty();
      return;
    }
    if (t.dataset.kvAdd && e.key === "Enter") {
      e.preventDefault();
      const raw = (t.value || "").trim();
      if (!raw) return;
      const key = t.dataset.kvAdd;
      const tok = (key === "RAD_FEATURE_PREFIX_EXCLUDE" || key === "RAD_FEATURE_DISPLAY_STRIP")
        ? storedMarkerForTyped(raw) : raw;
      addKvToken(key, tok);
      t.value = "";
      markDirty();
      return;
    }
    if (t.dataset.addQ && (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === "Escape")) {
      const wrap = t.closest(".addfind");
      const menu = wrap && wrap.querySelector(".addfind-menu");
      if (e.key === "Escape") {
        if (menu) {
          menu.hidden = true;
          menu.innerHTML = "";
        }
        return;
      }
      const items = menu ? [...menu.querySelectorAll(".addfind-item")] : [];
      if (e.key === "Enter") {
        e.preventDefault();
        const on = items.find(el => el.classList.contains("on"))
          || items.find(el => !el.hasAttribute("data-add-sheet"));
        if (!on) return;
        if (on.hasAttribute("data-add-sheet")) addSheetFeatures(on.getAttribute("data-add-pick-key"), on.getAttribute("data-add-sheet"));
        else addListValue(on.getAttribute("data-add-pick-key"), on.getAttribute("data-add-pick"));
        return;
      }
      if (!items.length) return;
      e.preventDefault();
      let i = items.findIndex(el => el.classList.contains("on"));
      if (e.key === "ArrowDown") i = i < 0 ? 0 : Math.min(items.length - 1, i + 1);
      else i = i < 0 ? items.length - 1 : Math.max(0, i - 1);
      items.forEach(el => el.classList.remove("on"));
      items[i].classList.add("on");
      return;
    }
    if (t.dataset.addnum && e.key === "Enter") {
      e.preventDefault();
      const key = t.dataset.addnum;
      const n = Number(t.value);
      if (Number.isNaN(n) || (key === "TD_IPCW_HORIZONS" && n <= 0)) return;
      const cur = Array.isArray(fieldValue(key)) ? fieldValue(key).slice() : [];
      if (!cur.includes(n)) cur.push(n);
      cur.sort((a,b)=>a-b);
      setFieldValue(key, cur);
      t.value = "";
      refreshField(key);
    }
  });
}

function refreshField(key) {
  const f = S.schema.find(x => x.key === key);
  if (!f) return;
  if (f.widget === "growselect") f.value = fieldValue(key);
  if (f.widget === "checklist" || f.widget === "addselect" || f.widget === "pathlist") {
    const have = fieldValue(key) || [];
    f.value = have;
    f.add_options = dropdownOptions(f, have);
  }
  if (clinAddselectKeys().indexOf(key) >= 0) {
    refreshClinRoleDropdowns();
  }
  renderForm();
}

function coerce(s, sampleArr) {
  if (Array.isArray(sampleArr) && sampleArr.length && typeof sampleArr[0] === "number") {
    const n = Number(s);
    return Number.isNaN(n) ? s : n;
  }
  return s;
}

function markDirty() {
  updateDiff();
  if (typeof renderGroupCard === "function") renderGroupCard();
  document.querySelectorAll(".field").forEach(el => {
    const keyEl = el.querySelector("[data-key], [data-list], [data-add], textarea, select, input");
    let key = null;
    if (keyEl) key = keyEl.dataset.key || keyEl.dataset.list || keyEl.dataset.add;
    if (!key) return;
    const dirty = ((pairTab() && PAIR_OVERRIDE_KEYS.has(key))
      || (!pairTab() && outcomeTab() && OUTCOME_OVERRIDE_KEYS.has(key)))
      ? fieldHasOverride(key)
      : !eq(S.values[key], S.base[key]);
    if (dirty) el.classList.add("dirty");
    else el.classList.remove("dirty");
  });
}

function updateDiff() {
  const ov = collectOverlay();
  const n = Object.keys(ov).length;
  $("diffBanner").textContent = n
    ? `相对 settings.ini 已改 ${n} 项（高亮底）。运行统计时这 ${n} 项 + 页面其余值一并覆盖 settings。`
    : "当前值与 settings.ini 一致。运行时仍以本页完整配置为准（与 py 相同则效果相同）。";
}

function bindTips() {
  const pop = $("tipPop");
  if (!pop) return;
  document.body.addEventListener("mouseover", (e) => {
    const el = e.target.closest("[data-tip]");
    if (!el || !el.getAttribute("data-tip")) { pop.style.display = "none"; return; }
    pop.textContent = el.getAttribute("data-tip");
    pop.style.display = "block";
    const r = el.getBoundingClientRect();
    let x = r.left, y = r.bottom + 6;
    if (x + 350 > window.innerWidth) x = window.innerWidth - 360;
    if (y + 160 > window.innerHeight) y = r.top - 10 - pop.offsetHeight;
    pop.style.left = Math.max(8, x) + "px";
    pop.style.top = Math.max(8, y) + "px";
  });
  document.body.addEventListener("mouseout", (e) => {
    const el = e.target.closest("[data-tip]");
    if (!el) return;
    if (e.relatedTarget && el.contains(e.relatedTarget)) return;
    pop.style.display = "none";
  });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
  }[c]));
}
function cssEscape(s){ return s.replace(/"/g,'\\"'); }

function previewBlockHtml(p, heading, extraRows) {
  if (p && p.error) {
    const title = heading ? `<div class="pv-h">${esc(heading)}</div>` : "";
    return `${title}<div class="kv"><b>错误</b><span class="err">${esc(p.error)}</span></div>`;
  }
  const idc = (p && p.id_check) || {};
  const exc = (p && p.exclude) || {};
  const icc = (p && p.icc) || {};
  const qc = (p && p.qc) || {};
  let html = heading ? `<div class="pv-h">${esc(heading)}</div>` : "";
  html += `<div class="kv">
    <b>文件</b><span class="mono">${esc(p.data || p.file || "")}</span>
    <b>sheets</b><span>${(p.sheets||[]).map(esc).join(" · ") || "（无）"}</span>`;
  if (p.sheet_rule) html += `<b>规则</b><span class="err">${esc(p.sheet_rule)}</span>`;
  const tab = typeof outcomeTab === "function" ? outcomeTab() : "";
  if (tab && p.outcome_dist && p.outcome_dist[tab]) {
    html += `<b>分组</b><span>${esc(tab)}：${Object.keys(p.outcome_dist[tab]).map(esc).join(" · ")}</span>`;
  } else if ((p.group_levels || []).length && !(p.outcome_dist && Object.keys(p.outcome_dist).length > 1)) {
    html += `<b>分组</b><span>${p.group_levels.map(esc).join(" · ")}</span>`;
  }
  html += `
    <b>ID</b><span>clin ${idc.clin_n??"—"} / rad ${idc.rad_n??"—"} / 重叠 ${idc.overlap??"—"}
      （仅临床 ${idc.clin_only??0} · 仅组学 ${idc.rad_only??0}）</span>
    <b>排除</b><span>n=${exc.n||0}${exc.file? " · "+esc(String(exc.file).split(/[/\\\\]/).pop()):""}${exc.n? " · "+(exc.ids||[]).slice(0,8).map(esc).join(", "):""}</span>
    <b>估计 n</b><span>${p.n_after_exclude_est ?? "—"}（${
      (idc.clin_n && idc.rad_n) ? "重叠" : (idc.clin_n ? "临床" : "组学")
    } − 排除）</span>`;
  if (icc.n) html += `<b>ICC</b><span>n=${icc.n} · 双侧≥${icc.threshold} → ${icc.both_ge_thr??"—"}</span>`;
  if (qc.n) html += `<b>影像 QC</b><span>sheet n=${qc.n}${qc.QC_Status ? " · Status " + Object.keys(qc.QC_Status).map(esc).join("/") : ""}</span>`;
  if (extraRows) html += extraRows;
  html += `</div>`;
  if (p.outcome_dist && Object.keys(p.outcome_dist).length) {
    html += `<div style="margin-top:8px"><b>结局分布</b>`;
    for (const [col, dist] of Object.entries(p.outcome_dist)) {
      html += `<div class="dist"><code>${esc(col)}</code> `;
      for (const [k,v] of Object.entries(dist)) html += `<span>${esc(k)} = ${v}</span>`;
      html += `</div>`;
    }
    html += `</div>`;
  }
  html += previewTablesHtml(p);
  return html;
}
function previewTablesHtml(p) {
  const info = (p && p.sheet_info) || {};
  const resolved = (p && p.resolved_sheets) || {};
  const order = [];
  ["CLIN_SHEET", "QC_SHEET", "ICC_SHEET", "RAD_SHEET"].forEach((key) => {
    const name = resolved[key];
    if (name && info[name] && order.indexOf(name) < 0) order.push(name);
  });
  Object.keys(info).forEach((name) => {
    if (order.indexOf(name) < 0) order.push(name);
  });
  let html = "";
  order.forEach((name) => {
    const show = info[name] || {};
    const head = Array.isArray(show.head) ? show.head : [];
    if (head.length) {
      const cols = Object.keys(head[0]);
      html += `<div class="preview-table-wrap"><table class="mini"><tr>${
        cols.map((c) => `<th>${esc(c)}</th>`).join("")
      }</tr>${
        head.map((r) => `<tr>${cols.map((c) => `<td>${esc(r[c])}</td>`).join("")}</tr>`).join("")
      }</table><div class="hint">${esc(show.name || name)} 前 ${head.length} 行 · 全部 ${cols.length} 列 · ${show.n_rows}×${show.n_cols}</div></div>`;
      return;
    }
    if (Number(show.n_cols) > 400) {
      html += `<div class="hint">${esc(show.name || name)} ${show.n_rows}×${show.n_cols}。列数过多，预览不展开这一张表。</div>`;
    }
  });
  return html;
}
function extPreviewRows(p) {
  const raw = (p && Array.isArray(p.external)) ? p.external : [];
  return raw.filter((ext) => {
    if (!ext || typeof ext !== "object") return false;
    if (ext.error) return true;
    const file = String(ext.file || ext.data || "").trim();
    return !!file;
  });
}
function renderExtPreview(p) {
  const box = $("extPreviewBox");
  if (!box) return;
  const exts = extPreviewRows(p);
  if (!exts.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.innerHTML = exts.map((ext) => {
    const label = "外部验证预览 · " + (ext.name || fileBase(ext.file || ext.data) || "ext");
    let extra = "";
    if (ext.qc_html) {
      extra = `<b>QC HTML</b><span class="mono">${esc(ext.qc_html)}</span>`
        + (ext.has_qc_sheet === false ? `<b>影像 QC</b><span class="hint">该外验表无 qc sheet</span>` : "");
    }
    return `<div class="pv-block">${previewBlockHtml(ext, label, extra)}</div>`;
  }).join("");
}
function fileBase(p) {
  const s = String(p || "");
  const i = Math.max(s.lastIndexOf("/"), s.lastIndexOf("\\"));
  return i >= 0 ? s.slice(i + 1) : s;
}
function renderPreview(p) {
  if (!p) {
    $("previewBox").innerHTML = '<span class="hint">无预览</span>';
    renderExtPreview(null);
    return;
  }
  S.preview = p;
  fillPairwiseKeep();
  if (S.schema && S.schema.length) renderForm();
  else if (typeof renderGroupCard === "function") renderGroupCard();
  $("previewBox").innerHTML = previewBlockHtml(p, "");
  renderExtPreview(p);
}

function startPageQs() {
  return new URLSearchParams(location.search);
}
function applyUrlSheetsToValues() {
  if (!S.values) return;
  const qs = startPageQs();
  for (const [key, q] of Object.entries(START_PAGE_QS)) {
    if (!qs.has(q)) continue;
    const raw = qs.get(q);
    if (key === "EXTERNAL_TESTS" || key === "RAD_ID_COLS") {
      try {
        const parsed = raw ? JSON.parse(raw) : [];
        if (Array.isArray(parsed)) S.values[key] = parsed;
      } catch (e) {}
      continue;
    }
    if (key === "LABEL_COL") {
      S.values[key] = raw == null ? "" : raw;
      continue;
    }
    if (raw == null || raw === "") continue;
    if (key === "USE_INTERNAL_SPLIT_WHEN_EXTERNAL") {
      S.values[key] = raw === "1" || raw === "true";
      continue;
    }
    S.values[key] = raw;
  }
  const dataQ = (qs.get("data") || "").trim();
  if (dataQ) S.values.INPUT = dataQ.split(/[/\\]/).pop() || dataQ;
}

function startPoll() {
  if (S.poll) clearInterval(S.poll);
  S.poll = setInterval(tickLog, 800);
  tickLog();
}


function selectedResultRel() {
  const on = document.querySelector('input[name="resultPick"]:checked');
  return on ? on.value : "";
}

function syncResultPick() {
  const cur = selectedResultRel();
  document.querySelectorAll("#resultsBox .res-opt").forEach((lab) => {
    const radio = lab.querySelector("input");
    lab.classList.toggle("on", !!(radio && radio.value === cur));
  });
}

function openSelectedResult() {
  const proj = $("project").value;
  const rel = selectedResultRel();
  if (!rel) { alert("先在结果列表里选一项"); return; }
  window.open("/files/" + encodeURI(proj + "/" + rel), "_blank", "noopener");
}

function setPortTip(text) {
  const tip = String(text || "");
  ["portLab", "port"].forEach((id) => {
    const el = $(id);
    if (el) el.setAttribute("data-tip", tip);
  });
}

async function boot() {
  bindDelegates();
  bindGroupCard();
  bindPalCard();
  bindOutcomeTabs();
  bindPairTabs();
  bindIoModule();
  bindTips();
  try {
    const h = await api("/api/health");
    const p = h.port || location.port || 8765;
    setDot("on", "本机服务已连接 · " + p);
  } catch {
    setDot("err", "请先运行 python console.py");
    $("diffBanner").className = "banner bad";
    $("diffBanner").textContent = "未连上后端。打开工程文件夹里的 <工程名>.html 拨开关，或在 modules/console 执行 python console.py";
    return;
  }
  const pr = await api("/api/projects");
  fillProjects(pr.projects || []);
  const ses = (await api("/api/session")).session;
  const qs = new URLSearchParams(location.search);
  const urlProj = qs.get("project");
  const urlDir = (qs.get("dir") || "").trim();
  if (urlProj && ![...$("project").options].some(o => o.value === urlProj)) {
    const opt = document.createElement("option");
    opt.value = urlProj;
    opt.textContent = urlProj;
    $("project").appendChild(opt);
    S.projects.push({
      name: urlProj,
      dir: urlDir,
      settings: (qs.get("settings") || ""),
      xlsx: qs.get("data") ? [qs.get("data")] : [],
      default_xlsx: qs.get("data") || "",
    });
  }
  if (urlProj && [...$("project").options].some(o => o.value === urlProj)) {
    $("project").value = urlProj;
  } else if (ses && ses.project) {
    $("project").value = ses.project;
  } else {
    $("project").value = pr.default_project || "";
  }
  onProjectChange();
  applyUrlLock();
  const sameSes = ses && ses.project === $("project").value;
  if (!qs.get("data") && sameSes && ses.data) $("data").value = ses.data;
  if (!qs.get("settings") && sameSes && ses.settings) $("settings").value = ses.settings;
  fillLockStamp();
  $("btnDry").onclick = () => dryRun().catch(e => alert(e.message));
  $("btnRun").onclick = () => runStats().catch(e => alert(e.message));
  if ($("runBat")) {
    $("runBat").onchange = () => {
      if ($("runBat").checked) runBat().catch(e => { if ($("runBat")) $("runBat").checked = false; alert(e.message); });
      else stopRun().catch(e => alert(e.message));
    };
  }
  $("btnRestart").onclick = () => restartServer().catch(e => alert(e.message));
  if ($("btnDataQc")) $("btnDataQc").onclick = () => runDataQc().catch(e => alert(e.message));
  $("btnWrite").onclick = () => writeback().catch(e => alert(e.message));
  $("btnResults").onclick = () => refreshResults().catch(e => alert(e.message));
  $("btnOpenResult").onclick = () => openSelectedResult();
  if ($("btnCopyLog")) {
    $("btnCopyLog").onclick = () => copyRunLog().catch(e => alert(e.message));
  }
  $("tabBtnConfig").onclick = () => showPane("config");
  if ($("tabBtnPreview")) $("tabBtnPreview").onclick = () => showPane("preview");
  $("tabBtnFlow").onclick = () => showPane("flow");
  const wantTab = new URLSearchParams(location.search).get("tab");
  if (wantTab === "flow") showPane("flow");
  else if (wantTab === "preview") showPane("preview");
  try {
    await loadAll();
    if (wantTab === "public" || wantTab === "config") {
      S.ioTab = false;
      renderOutcomeTabs();
      renderForm();
    }
    if (sameSes && ses && ses.restore_page && ses.overlay) {
      Object.assign(S.values, ses.overlay);
      renderForm();
      updateDiff();
    }
  } catch (e) {
    $("loadHint").innerHTML = `<span class="err">${esc(e.message)}</span>`;
    S.ioTab = false;
    if ($("ioCard")) $("ioCard").hidden = true;
    if ($("formRoot")) $("formRoot").hidden = false;
    fillIoModule();
    renderOutcomeTabs();
    renderForm();
  }
}

document.addEventListener("focusin", (e) => {
  const t = e.target;
  if (!t || t.tagName !== "SELECT") return;
  const col = t.closest(".col");
  if (!col) return;
  const pad = 12;
  const tr = t.getBoundingClientRect();
  const cr = col.getBoundingClientRect();
  if (tr.bottom > cr.bottom - pad) col.scrollTop += (tr.bottom - cr.bottom + pad);
  else if (tr.top < cr.top + pad) col.scrollTop -= (cr.top + pad - tr.top);
});

boot();
