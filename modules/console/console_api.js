// console_api.js — token + fetch wrapper + endpoint calls
// Depends on: console_schema.js (optional), console_ui.js provides $, S, helpers used inside.

const CONSOLE_TOKEN = (document.querySelector('meta[name="console-token"]') || {}).content || "__CONSOLE_TOKEN__";

async function api(path, opt) {
  opt = opt || {};
  const headers = Object.assign({"X-Console-Token": CONSOLE_TOKEN}, opt.headers || {});
  opt = Object.assign({}, opt, {headers});
  const r = await fetch(path, opt);
  const t = await r.text();
  let j;
  try { j = JSON.parse(t); } catch { throw new Error(t.slice(0, 200)); }
  if (!r.ok || j.ok === false) throw new Error(j.error || t.slice(0, 200));
  return j;
}

async function fetchXlsxMeta(file, sheet, col) {
  const q = new URLSearchParams({
    project: ($("project") && $("project").value) || "",
    settings: ($("settings") && $("settings").value) || "",
    data: ($("data") && $("data").value) || "",
    file: file || "",
    sheet: sheet || "",
    col: col || "",
  });
  return api("/api/xlsx-meta?" + q.toString());
}

async function fetchPreview(file) {
  const q = new URLSearchParams({
    project: $("project").value,
    settings: ($("settings") && $("settings").value.trim()) || "",
    data: file || ioDataName(),
  });
  const j = await api("/api/settings?" + q.toString());
  return j;
}

async function previewIntoExtRow(row, file) {
  if (!row || !file) return;
  try {
    const j = await fetchPreview(file);
    const prev = (j && j.preview) || {};
    const sheets = prev.sheets || ioSheetOpts;
    const ids = prev.rad_id_cols || prev.id_options || ioRadIdOpts;
    fillSelect(row.querySelector("[data-ext=clin]"), sheets, null, IO_DEFAULTS.CLIN_SHEET);
    fillSelect(row.querySelector("[data-ext=rad]"), sheets, null, IO_DEFAULTS.RAD_SHEET);
    fillSelect(row.querySelector("[data-ext=radid]"), ids, null, IO_DEFAULTS.RAD_ID_COL);
  } catch (e) {}
}

async function refreshIoLists() {
  const msg = $("ioMsg");
  try {
    const j = await fetchPreview(ioDataName());
    if (!j) return;
    applyPreviewLists(j.preview);
    fillAllIoSelects(S.values || {});
    const p = S.projects.find(x => x.name === ($("project") && $("project").value));
    fillDatalist("xlsxList", (p && p.xlsx) || []);
    if (msg) msg.textContent = "已读取工作表。";
  } catch (e) {
    if (msg) msg.textContent = "读取工作表失败：" + (e && e.message ? e.message : e);
  }
}

async function loadDataModule() {
  if (ioReloadBusy) return;
  const msg = $("ioMsg");
  if (msg) msg.textContent = "正在载入…";
  collectIoModule();
  ioReloadBusy = true;
  try {
    await loadAll();
    fillIoModule();
    if (msg) msg.textContent = "已载入。工作表和 ID 列在起始页。";
  } catch (e) {
    if (msg) msg.innerHTML = `<span class="err">${esc(e && e.message ? e.message : e)}</span>`;
    throw e;
  } finally {
    ioReloadBusy = false;
  }
}

async function loadAll() {
  $("loadHint").textContent = "载入中…";
  collectIoModule();
  const qs = startPageQs();
  const q = new URLSearchParams({
    project: $("project").value,
    settings: $("settings").value.trim(),
    data: $("data").value.trim() || ioDataName(),
  });
  const dir = (qs.get("dir") || "").trim();
  if (dir) q.set("dir", dir);
  const extQ = (qs.get("ext_tests") || "").trim();
  if (extQ) q.set("ext_tests", extQ);
  else if (S.values && Array.isArray(S.values.EXTERNAL_TESTS) && S.values.EXTERNAL_TESTS.length) {
    q.set("ext_tests", JSON.stringify(S.values.EXTERNAL_TESTS));
  }
  const fromVal = {
    RAD_SHEET: "rad_sheet",
    CLIN_SHEET: "clin_sheet",
    QC_SHEET: "qc_sheet",
    ICC_SHEET: "icc_sheet",
    RAD_ID_COL: "rad_id",
    CLIN_ID_COL: "clin_id",
    LABEL_COL: "label_col",
    RAD_ID_COLS: "rad_id_cols",
  };
  for (const [key, qn] of Object.entries(fromVal)) {
    if (key === "RAD_ID_COLS") {
      const cur = S.values && S.values[key];
      if (Array.isArray(cur)) q.set(qn, JSON.stringify(cur));
      else if (qs.has(qn)) q.set(qn, qs.get(qn) || "[]");
      continue;
    }
    if (key === "LABEL_COL") {
      if (S.values && Object.prototype.hasOwnProperty.call(S.values, key)) {
        q.set(qn, String(S.values[key] == null ? "" : S.values[key]));
      } else if (qs.has(qn)) q.set(qn, qs.get(qn) || "");
      continue;
    }
    const v = (S.values && S.values[key] != null) ? String(S.values[key]).trim() : "";
    if (v) q.set(qn, v);
    else if (qs.has(qn)) q.set(qn, qs.get(qn) || "");
  }
  const j = await api("/api/settings?" + q.toString());
  if (j.preview) {
    S.preview = j.preview;
    applyPreviewLists(j.preview);
  }
  applySchema(j.schema, j.values);
  if (j.preview && j.preview.resolved_sheets) {
    Object.assign(S.values, j.preview.resolved_sheets);
  }
  applyUrlSheetsToValues();
  if (j.preview) renderPreview(j.preview);
  if ($("modulesDir") && !($("modulesDir").value || "").trim()) {
    const md = String(S.values.MODULES_DIR || "").trim();
    if (!md) {
      const err = "MODULES_DIR 未设置：请在 settings.ini 填写 MODULES_DIR（算法库 modules 文件夹路径）。";
      $("loadHint").innerHTML = `<span class="err">${esc(err)}</span>`;
      throw new Error(err);
    }
    $("modulesDir").value = md;
  }
  fillLockStamp();
  persistIoLock();
  fillIoModule();
  $("loadHint").innerHTML = `<span class="ok">已载入</span> ${esc(j.settings_path)}`;
  if ($("ioMsg")) $("ioMsg").textContent = "已载入。改项目或数据文件会自动刷新。";
  $("runCol").hidden = false;
  await refreshResults();
}

async function reloadPreview() {
  const j = await api("/api/preview", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload()),
  });
  if (j.schema) {
    S.schema = j.schema;
    renderForm();
  }
  renderPreview(j.preview);
}

async function dryRun() {
  $("runHint").textContent = "干跑中…";
  const j = await api("/api/dryrun", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload()),
  });
  $("log").textContent = j.text || "";
  $("log").scrollTop = 0;
  if (j.preview) renderPreview(j.preview);
  $("runHint").innerHTML = '<span class="ok">干跑完成（未建模）</span>';
}

async function runStats() {
  const scope = runScopeText();
  if (!confirm("将用本页配置" + scope + "（覆盖 settings.ini，不改源文件）。可能需数分钟。继续？")) return;
  S.logOffset = 0;
  $("log").textContent = "";
  const j = await api("/api/run", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload()),
  });
  $("runHint").textContent = "已启动 pid=" + j.pid + " · " + runScopeText();
  setDot("run", "运行中");
  startPoll();
}

async function runBat() {
  const proj = $("project").value;
  if (!proj) { alert("先选项目"); if ($("runBat")) $("runBat").checked = false; return; }
  S.logOffset = 0;
  $("log").textContent = "";
  const j = await api("/api/run/bat", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({project: proj}),
  });
  $("runHint").textContent = "运行统计已启动 pid=" + j.pid;
  setDot("run", "运行中");
  startPoll();
}

async function tickLog() {
  try {
    const j = await api("/api/run/log?offset=" + S.logOffset);
    if (j.text) {
      $("log").textContent += j.text;
      $("log").scrollTop = $("log").scrollHeight;
    }
    S.logOffset = j.n || S.logOffset;
    if (j.status === "running") setDot("run", "运行中");
    else if (j.status === "ok") {
      setDot("on", "完成");
      $("runHint").innerHTML = '<span class="ok">运行完成</span>';
      if ($("runBat")) $("runBat").checked = false;
      clearInterval(S.poll); S.poll = null;
      await refreshResults();
    } else if (j.status === "error" || j.status === "stopped") {
      setDot("err", j.status);
      $("runHint").innerHTML = `<span class="err">${esc(j.status)}</span>`;
      if ($("runBat")) $("runBat").checked = false;
      clearInterval(S.poll); S.poll = null;
    }
  } catch (e) {
    $("runHint").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

async function stopRun() {
  await api("/api/run/stop", {method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"});
}

async function restartServer() {
  const hint = $("runHint");
  if (hint) hint.textContent = "正在重启服务…";
  setDot("run", "重启中");
  const proj = ($("project") && $("project").value) || "";
  try {
    await api("/api/restart", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({project: proj}),
    });
  } catch (e) {
    // 旧进程关掉时 fetch 可能失败，继续等新进程
  }
  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setTimeout(r, 400));
    try {
      const r = await fetch("/api/health", {cache: "no-store"});
      if (r.ok) {
        location.reload();
        return;
      }
    } catch (e) {}
  }
  setDot("err", "未连上");
  if (hint) hint.textContent = "服务未在时限内起来。请手动运行 python console.py";
  alert("服务未在时限内起来。请手动运行 python console.py");
}

async function runDataQc() {
  const hint = $("qcHint");
  if (hint) hint.textContent = "正在对整体数据做 QC…";
  const btn = $("btnDataQc");
  if (btn) btn.disabled = true;
  try {
    const j = await api("/api/qc", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload()),
    });
    const rel = j.rel || ("ref/" + String(j.html || "qc.html").split(/[/\\\\]/).pop());
    const extBits = (Array.isArray(j.external) ? j.external : []).map((e) => {
      const n = esc(String((e && (e.qc_html || e.html)) || "").split(/[/\\\\]/).pop());
      const lab = esc((e && e.name) || "ext");
      if (e && e.error) return `${lab} <span class="err">${esc(e.error)}</span>`;
      return `${lab}→<code>${n}</code>`;
    });
    if (hint) {
      hint.innerHTML = `<span class="ok">已写入工程根</span> <code>${esc(rel)}</code>`
        + ` · clin ${j.n_clin ?? "—"} / rad ${j.n_rad ?? "—"} / 重叠 ${j.n_overlap ?? "—"}`
        + (extBits.length ? `<br>外验 QC：${extBits.join(" · ")}` : "");
    }
    await refreshResults();
  } catch (e) {
    if (hint) hint.innerHTML = `<span class="err">${esc(e.message)}</span>`;
    throw e;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function writeback() {
  if (!confirm("保存到 settings.ini？现有 ini 会备份为 settings.ini.bak（再次保存会覆盖这份备份）。")) return;
  const body = payload();
  const j = await api("/api/writeback", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(body),
  });
  $("runHint").innerHTML = `<span class="ok">已保存</span> 更新 ${j.updated.length} · 追加 ${j.appended.length} · 备份 ${esc(String(j.backup || "").split(/[/\\\\]/).pop())}`;
  S.base = JSON.parse(JSON.stringify(S.values));
  updateDiff();
  renderForm();
  renderGroupCard();
  renderPalCard();
}

async function refreshResults() {
  const proj = $("project").value;
  const j = await api("/api/results?project=" + encodeURIComponent(proj));
  const box = $("resultsBox");
  const prev = selectedResultRel();
  if (!j.results || !j.results.length) {
    box.innerHTML = '<span class="hint">还没有 HTML 结果</span>';
    return;
  }
  const pick = (prev && j.results.some(r => r.rel === prev)) ? prev : j.results[0].rel;
  box.innerHTML = '<div class="res-list">' + j.results.map(r =>
    `<div class="res-row">
      <label class="res-opt${r.rel === pick ? " on" : ""}" data-tip="${esc(
      r.rel + "。点这一行后再点「打开所选」。"
    )}">
      <input type="radio" name="resultPick" value="${esc(r.rel)}" ${r.rel === pick ? "checked" : ""}/>
      <span>${esc(r.rel)}</span>
    </label>
    </div>`
  ).join("") + "</div>";
  box.querySelectorAll('input[name="resultPick"]').forEach((el) => {
    el.addEventListener("change", syncResultPick);
  });
}

async function openResultFolder(rel) {
  const proj = $("project").value;
  if (!rel) throw new Error("未指定结果路径");
  const j = await api("/api/open-folder", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ project: proj, rel }),
  });
  if (!j.ok) throw new Error(j.error || "无法打开文件夹");
}

async function copyRunLog() {
  const el = $("log");
  const text = el ? String(el.textContent || "") : "";
  if (!navigator.clipboard || !navigator.clipboard.writeText) {
    throw new Error("当前环境不支持剪贴板写入");
  }
  await navigator.clipboard.writeText(text);
  const hint = $("copyLogHint");
  if (hint) {
    hint.hidden = false;
    setTimeout(() => { hint.hidden = true; }, 1500);
  }
}

async function loadPort() {
  if (!$("port")) return;
  try {
    const j = await api("/api/port");
    const cur = j.current || j.preferred || 8765;
    $("port").value = j.preferred || cur;
    setPortTip(
      "当前服务 " + (j.current || "—") +
      " · 下次启动 " + (j.preferred || cur) +
      " · 可选 " + (j.lo || 8765) + "–" + (j.hi || 8776) +
      "。被占可改端口后点「记住」。"
    );
  } catch {
    const here = parseInt(location.port || "8765", 10);
    $("port").value = here || 8765;
    setPortTip("未能读取端口状态。可手填 8765–8776，点「记住」。");
  }
}

async function savePort() {
  const n = parseInt($("port").value, 10);
  if (!(n >= 1024 && n <= 65535)) { alert("端口须在 1024–65535"); return; }
  const j = await api("/api/port", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({port: n}),
  });
  setPortTip(j.note || ("已记住端口 " + n + "。下次启动用此端口；当前进程仍在 " + (j.current || location.port) + "。"));
  $("runHint").textContent = "下次启动用端口 " + n + "（当前进程仍在 " + (j.current || location.port) + "）";
}

