"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const clock = t => { t = Math.floor(t || 0); const h = Math.floor(t/3600), m = Math.floor(t%3600/60), s = t%60;
  return h ? `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}` : `${m}:${String(s).padStart(2,"0")}`; };
const SOURCE = {youtube:"YouTube", spotify:"Spotify", soundcloud:"SoundCloud"};

async function api(path, opts = {}) {
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers = {"Content-Type":"application/json"};
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "That didn't work. Try again.");
  return data;
}
let toastTimer;
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.hidden = true, 3200);
}

const state = {status:null, batches:[], flags:{}, route:null, job:null, batch:null, query:"", style:null,
               tab:"transcript", side:"summary", chat:[], asking:false, listHtml:"", detailKey:""};

/* ---------- Links on a transcript ---------- */
function jumpUrl(job, seconds) {
  const t = Math.floor(seconds);
  if (job.source === "youtube") return job.url + (job.url.includes("?") ? "&" : "?") + "t=" + t + "s";
  if (job.source === "soundcloud") return job.url + "#t=" + clock(t);
  return null;
}
function stampHtml(job, seconds, label) {
  const url = jumpUrl(job, seconds), text = esc(label ?? clock(seconds));
  return url ? `<a class="stamp" href="${esc(url)}" target="_blank" rel="noreferrer" title="Open at ${text}">${text}</a>`
             : `<span class="stamp">${text}</span>`;
}

/* ---------- A small, safe Markdown renderer ---------- */
function inline(text, job) {
  let s = esc(text);
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  return s.replace(/\[(\d{1,2}(?::\d{2}){1,2})\]/g, (m, t) => {
    const secs = t.split(":").reduce((a, b) => a * 60 + Number(b), 0);
    return job ? stampHtml(job, secs, t) : `<span class="stamp">${t}</span>`;
  });
}
function markdown(md, job) {
  const out = []; let list = null, para = [];
  const flush = () => { if (para.length) { out.push(`<p>${inline(para.join(" "), job)}</p>`); para = []; } };
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of String(md || "").split(/\r?\n/)) {
    const line = raw.trim(); let m;
    if (!line) { flush(); close(); continue; }
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { flush(); close(); const n = Math.min(m[1].length + (m[1].length < 2 ? 1 : 0), 3);
      out.push(`<h${n}>${inline(m[2], job)}</h${n}>`); continue; }
    if ((m = line.match(/^[-*•]\s+(.*)$/)) || (m = line.match(/^\d+[.)]\s+(.*)$/))) {
      flush(); const want = /^\d/.test(line) ? "ol" : "ul";
      if (list !== want) { close(); out.push(`<${want}>`); list = want; }
      out.push(`<li>${inline(m[1], job)}</li>`); continue; }
    if ((m = line.match(/^>\s?(.*)$/))) { flush(); close(); out.push(`<blockquote>${inline(m[1], job)}</blockquote>`); continue; }
    close(); para.push(line);
  }
  flush(); close();
  return out.join("");
}

/* ---------- Library rail ---------- */
function statusLine(j) {
  if (j.status === "working") return j.stage + (j.progress ? `, ${Math.round(j.progress * 100)}%` : "");
  if (j.status === "queued") return "Waiting";
  if (j.status === "failed") return "Failed";
  if (j.status === "cancelled") return "Stopped";
  return [SOURCE[j.source], j.duration ? clock(j.duration) : null].filter(Boolean).join(", ");
}
function entryHtml(b) {
  const single = !b.is_collection && b.jobs.length === 1;
  if (single) {
    const j = b.jobs[0], cls = j.status === "failed" ? "failed" : j.status === "queued" ? "queued" : "";
    const current = state.route?.type === "job" && state.route.id === j.id ? 'aria-current="page"' : "";
    const title = j.status === "working"
      ? `<b class="swipe" style="--p:${Math.max(j.progress || 0, .03)}">${esc(j.title)}</b>` : `<b>${esc(j.title)}</b>`;
    return `<a class="entry ${cls}" href="#job/${j.id}" ${current}>${title}<small>${esc(statusLine(j))}</small></a>`;
  }
  const done = b.jobs.filter(j => j.status === "done").length, n = b.jobs.length;
  const failed = b.jobs.filter(j => j.status === "failed").length;
  const active = b.jobs.some(j => j.status === "working" || j.status === "queued");
  const current = state.route?.type === "batch" && state.route.id === b.id ? 'aria-current="page"' : "";
  const title = active ? `<b class="swipe" style="--p:${Math.max(done / n, .03)}">${esc(b.title)}</b>` : `<b>${esc(b.title)}</b>`;
  const note = `${SOURCE[b.source] || "Links"}, ${done} of ${n} done` + (failed ? `, ${failed} failed` : "");
  return `<a class="entry" href="#batch/${b.id}" ${current}>${title}<small>${esc(note)}</small></a>`;
}
function renderList() {
  let html;
  if (state.query.length >= 2) html = state.results?.length
    ? state.results.map(r => `<a class="entry" href="#job/${r.id}"><b>${esc(r.title)}</b><span class="snippet">${highlight(esc(r.snippet), state.query)}</span></a>`).join("")
    : `<p class="entry"><small>Nothing matches that search.</small></p>`;
  else html = state.batches.map(entryHtml).join("");
  if (html !== state.listHtml) { $("#list").innerHTML = html; state.listHtml = html; }
}
function highlight(safeHtml, query) {
  if (!query) return safeHtml;
  const q = esc(query).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return safeHtml.replace(new RegExp(`(?![^<]*>)(${q})`, "gi"), "<mark>$1</mark>");
}

/* ---------- Main view ---------- */
function renderEmpty() {
  document.body.classList.remove("reading");
  $("#view").innerHTML = `<div class="empty">
    <h1>Get every word from a link.</h1>
    <p>Paste a video, song, podcast or a whole playlist above. You'll get a timestamped transcript and an AI summary you can question.</p>
    <ul>
      <li><b>YouTube.</b> Videos, Shorts, playlists and channels, using captions when they exist.</li>
      <li><b>Download.</b> Save videos and whole playlists as MP4 or MP3, from YouTube and most other video sites.</li>
      <li><b>SoundCloud.</b> Tracks, sets and profiles, transcribed from the audio.</li>
      <li><b>Spotify.</b> Songs through published lyrics, podcasts through their public feed.</li>
    </ul>
    ${state.status && !state.status.ai_ready ? `<p>Summaries need an AI key. <a href="#" data-do="settings">Add one in Settings</a>.</p>` : ""}
  </div>`;
}
function header(job) {
  const square = job.source !== "youtube" ? "square" : "";
  const thumb = job.thumbnail ? `<img class="${square}" src="${esc(job.thumbnail)}" alt="" referrerpolicy="no-referrer">` : "";
  const facts = [job.author, SOURCE[job.source], job.duration ? clock(job.duration) : null, job.method, job.language ? job.language.toUpperCase() : null].filter(Boolean).join(", ");
  const done = job.status === "done";
  return `<a class="back quiet" href="#">Back to list</a><div class="head">${thumb}<div class="grow">
    <h1>${esc(job.title)}</h1><p>${esc(facts)}</p>
    <div class="actions">
      ${done ? `<button class="quiet" data-do="copy">Copy transcript</button>
      <details class="menu"><summary>Download</summary><div>
        <a href="/api/jobs/${job.id}/export/txt">Plain text (.txt)</a>
        <a href="/api/jobs/${job.id}/export/timed">Text with timestamps (.txt)</a>
        <a href="/api/jobs/${job.id}/export/md">Transcript and summaries (.md)</a>
        <a href="/api/jobs/${job.id}/export/srt">Subtitles (.srt)</a>
        <a href="/api/jobs/${job.id}/export/vtt">Subtitles (.vtt)</a>
        <a href="/api/jobs/${job.id}/export/json">Data (.json)</a>
      </div></details>` : ""}
      <a class="quiet" href="${esc(job.url)}" target="_blank" rel="noreferrer">Open original</a>
      ${job.source !== "spotify" ? `<button class="quiet" data-do="saveMedia">Download ${job.source === "youtube" ? "MP4" : "MP3"}</button>` : ""}
      ${["queued","working"].includes(job.status) ? `<button class="quiet" data-do="cancel">Stop</button>` : ""}
      ${["failed","cancelled"].includes(job.status) ? `<button class="quiet" data-do="retry">Try again</button>` : ""}
      ${done ? `<button class="quiet" data-do="retry">Transcribe again</button>` : ""}
      <button class="quiet danger" data-do="delete">Delete</button>
    </div></div></div>`;
}
function transcriptHtml(job) {
  const q = ($("#tq")?.value || "").trim();
  return job.paragraphs.map(p => `<div class="para${job.line_by_line ? " line" : ""}">${stampHtml(job, p.start)}<p class="words">${highlight(esc(p.text), q)}</p></div>`).join("");
}
function summaryHtml(job) {
  const styles = state.status?.styles || [];
  const have = Object.fromEntries(job.summaries.map(s => [s.style, s]));
  const current = state.style || job.summaries.at(-1)?.style || "overview";
  state.style = current;
  const seg = styles.map(s => `<button data-style="${s.id}" aria-pressed="${s.id === current}">${esc(s.label)}</button>`).join("");
  const s = have[current];
  let body;
  if (!state.status?.ai_ready) body = `<p>Summaries need an AI key. A free Groq key is enough.</p><button class="primary" data-do="settings">Add a key in Settings</button>`;
  else if (!s) body = `<button class="primary" data-do="summarise">Write ${esc(styles.find(x => x.id === current)?.label.toLowerCase() || "summary")}</button>`;
  else if (s.status === "pending" || s.status === "working") body = `<p class="writing">Writing this now. Long recordings take a few minutes.</p>`;
  else if (s.status === "failed") body = `<p class="warn">${esc(s.error)}</p><button class="quiet" data-do="summarise">Try again</button>`;
  else body = markdown(s.content, job) + `<p class="byline">Written by ${esc(s.model)}. <button class="quiet" data-do="summarise">Rewrite</button> <button class="quiet" data-do="copySummary">Copy</button></p>`;
  return `<div class="seg" role="group" aria-label="Summary type">${seg}</div><div class="prose">${body}</div>`;
}
function askHtml(job) {
  const turns = state.chat.map(t => t.role === "user" ? `<p class="turn me">${esc(t.content)}</p>` : `<div class="turn prose">${markdown(t.content, job)}</div>`).join("");
  return `<div class="ask">${turns || `<p class="note">Ask anything about this transcript. Answers point to the moment it was said.</p>`}
    ${state.asking ? `<p class="writing">Thinking.</p>` : ""}
    <form id="askForm"><label class="sr" for="q">Your question</label><input id="q" placeholder="What did they say about..." autocomplete="off"><button class="primary" ${state.asking ? "disabled" : ""}>Ask</button></form></div>`;
}
function sideHtml(job) {
  return `<div class="seg" role="group" aria-label="Panel"><button data-side="summary" aria-pressed="${state.side === "summary"}">Summary</button><button data-side="ask" aria-pressed="${state.side === "ask"}">Ask</button></div>
    <div id="sideBody">${state.side === "summary" ? summaryHtml(job) : askHtml(job)}</div>`;
}
function renderJob() {
  const job = state.job;
  document.body.classList.add("reading");
  if (job.status !== "done") {
    const pct = job.status === "working" ? Math.max(job.progress || 0, .03) : 0;
    const body = job.status === "failed" ? `<h2>This one didn't work</h2><p class="bad">${esc(job.error)}</p>`
      : job.status === "cancelled" ? `<h2>Stopped</h2><p>Choose Try again to restart it.</p>`
      : job.status === "queued" ? `<h2>Waiting its turn</h2><p>Items run one at a time, in order.</p>`
      : `<h2><span class="swipe" style="--p:${pct}">${esc(job.stage)}</span></h2><p>You can keep using the app while this runs.</p>`;
    $("#view").innerHTML = header(job) + `<div class="status">${body}</div>`;
    return;
  }
  $("#view").innerHTML = header(job) + `
    <div class="tabs" role="group" aria-label="View"><button data-tab="transcript" aria-pressed="${state.tab === "transcript"}">Transcript</button><button data-tab="side" aria-pressed="${state.tab === "side"}">Summary and questions</button></div>
    <div class="split" data-tab="${state.tab}">
      <div class="pane main">
        <div class="pane-tools"><label class="sr" for="tq">Find in transcript</label><input id="tq" type="search" placeholder="Find in transcript"><small id="tc"></small></div>
        <div id="words">${transcriptHtml(job)}</div>
      </div>
      <div class="pane side" id="side">${sideHtml(job)}</div>
    </div>`;
}
function refreshSide() { const s = $("#side"); if (s && state.job) s.innerHTML = sideHtml(state.job); }

function renderBatch() {
  const b = state.batch;
  document.body.classList.add("reading");
  const done = b.jobs.filter(j => j.status === "done").length, failed = b.jobs.filter(j => ["failed","cancelled"].includes(j.status)).length;
  const roll = b.summaries.find(s => s.style === "collection");
  let rollup = "";
  if (!state.status?.ai_ready) rollup = "";
  else if (!roll) rollup = done ? `<button class="primary" data-do="rollup">Summarise the whole collection</button>` : "";
  else if (["pending","working"].includes(roll.status)) rollup = `<p class="writing">Writing the collection summary.</p>`;
  else if (roll.status === "failed") rollup = `<p class="warn">${esc(roll.error)}</p><button class="quiet" data-do="rollup">Try again</button>`;
  else rollup = `<div class="prose">${markdown(roll.content)}</div><p class="byline">Written by ${esc(roll.model)}. <button class="quiet" data-do="rollup">Rewrite</button></p>`;
  const rows = b.jobs.map((j, i) => `<a class="row-item ${j.status === "failed" ? "failed" : ""}" href="#job/${j.id}"><span>${i + 1}</span><span>${j.status === "working" ? `<b class="swipe" style="--p:${Math.max(j.progress||0,.03)}">${esc(j.title)}</b>` : `<b>${esc(j.title)}</b>`}<small>${esc(j.status === "failed" ? j.error : statusLine(j))}</small></span></a>`).join("");
  $("#view").innerHTML = `<a class="back quiet" href="#">Back to list</a><div class="head"><div class="grow"><h1>${esc(b.title)}</h1>
    <p>${esc(SOURCE[b.source] || "Links")}, ${done} of ${b.jobs.length} transcribed</p>
    <div class="actions">
      ${done ? `<a class="quiet" href="/api/batches/${b.id}/export">Download all (.zip)</a>` : ""}
      ${failed ? `<button class="quiet" data-do="retryAll">Retry ${failed} that didn't finish</button>` : ""}
      ${b.url ? `<a class="quiet" href="${esc(b.url)}" target="_blank" rel="noreferrer">Open original</a>` : ""}
      <button class="quiet danger" data-do="deleteBatch">Delete</button>
    </div></div></div>
    ${rollup ? `<div class="rollup">${rollup}</div>` : ""}<div class="rows">${rows}</div>`;
}

/* ---------- Routing and refresh ---------- */
async function route() {
  const [type, id] = location.hash.slice(1).split("/");
  state.route = id ? {type, id} : type === "downloads" ? {type} : null;
  $("#navDownloads").toggleAttribute("aria-current", type === "downloads");
  state.listHtml = ""; state.detailKey = "";
  if (type === "job" && id) {
    state.chat = []; state.style = null; state.side = "summary"; state.tab = "transcript";
    try { state.job = await api(`/api/jobs/${id}`); state.detailKey = jobKey(state.job); renderJob(); $("#view").scrollTop = 0; }
    catch (e) { toast(e.message); location.hash = ""; }
  } else if (type === "batch" && id) {
    try { state.batch = await api(`/api/batches/${id}`); renderBatch(); $("#view").scrollTop = 0; }
    catch (e) { toast(e.message); location.hash = ""; }
  } else if (type === "downloads") {
    state.dlHtml = ""; await refreshDownloads(); renderDownloads(); $("#view").scrollTop = 0;
  } else renderEmpty();
  renderList();
}
const jobKey = j => [j.status, j.stage, j.progress, ...j.summaries.map(s => s.style + s.status + s.updated)].join("|");
async function poll() {
  let active = false;
  try {
    const dl = await refreshDownloads();
    active = dl.some(d => ["queued","working"].includes(d.status));
    if (state.route?.type === "downloads") renderDownloads();
    const lib = await api("/api/library");
    state.batches = lib.batches; state.flags = lib.summaries;
    active = active || lib.batches.some(b => b.jobs.some(j => ["queued","working"].includes(j.status))) || Object.values(lib.summaries).some(s => ["pending","working"].includes(s));
    renderList();
    const r = state.route;
    if (r?.type === "job") {
      const fresh = await api(`/api/jobs/${r.id}`), key = jobKey(fresh);
      if (key !== state.detailKey) {
        const wasDone = state.job?.status === "done"; state.detailKey = key; state.job = fresh;
        if (wasDone && fresh.status === "done") refreshSide(); else renderJob();
      }
    } else if (r?.type === "batch") {
      const fresh = await api(`/api/batches/${r.id}`);
      const key = JSON.stringify([fresh.jobs.map(j => [j.status, j.progress, j.stage]), fresh.summaries.map(s => [s.status, s.updated])]);
      if (key !== state.detailKey) { state.detailKey = key; state.batch = fresh; renderBatch(); }
    }
  } catch { /* the next pass tries again */ }
  setTimeout(poll, active ? 1200 : 5000);
}

/* ---------- Paste flow ---------- */
const links = $("#links");
links.addEventListener("input", () => { links.style.height = "auto"; links.style.height = links.scrollHeight + 2 + "px"; });
links.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#paste").requestSubmit(); } });
$("#paste").addEventListener("submit", e => { e.preventDefault(); readLinks("transcribe"); });
$("#dl").addEventListener("click", () => readLinks("download"));
async function readLinks(mode) {
  const text = links.value.trim(), err = $("#pasteError"), btn = mode === "download" ? $("#dl") : $("#go");
  const label = btn.textContent;
  err.hidden = true;
  if (!text) { links.focus(); return; }
  $("#go").disabled = $("#dl").disabled = true; btn.textContent = "Reading link";
  try {
    const res = await api("/api/resolve", {method:"POST", body:{text, mode}});
    if (!res.collections.length) throw new Error(res.errors[0]?.error || "Nothing was found at that link.");
    const hasList = res.collections.some(c => c.is_collection);
    if (mode === "download") openPicker(res, "download");
    else if (!hasList && !res.errors.length) await enqueue(res.collections); else openPicker(res, "transcribe");
  } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  $("#go").disabled = $("#dl").disabled = false; btn.textContent = label;
}
async function enqueue(collections) {
  const {batches} = await api("/api/jobs", {method:"POST", body:{collections}});
  links.value = ""; links.style.height = "auto";
  await refreshLibrary();
  const b = state.batches.find(x => x.id === batches[0]);
  if (b) location.hash = !b.is_collection && b.jobs.length === 1 ? `job/${b.jobs[0].id}` : `batch/${b.id}`;
}
async function refreshLibrary() { const lib = await api("/api/library"); state.batches = lib.batches; state.flags = lib.summaries; renderList(); }

let picking = [], pickMode = "transcribe", pickFmt = "mp4";
function openPicker(res, mode = "transcribe") {
  picking = res.collections; pickMode = mode;
  $("#pickFormat").hidden = mode !== "download";
  if (mode === "download") {
    pickFmt = state.settings?.download_format || "mp4";
    $("#quality").value = state.settings?.download_quality || "1080";
    showFormat();
    $("#saveTo").textContent = `Saves to ${state.settings?.download_folder || "your Downloads folder"}` +
      (picking.some(c => c.is_collection) ? ", in a folder named after the playlist." : ".");
  }
  const total = picking.reduce((n, c) => n + c.items.length, 0);
  $("#pickTitle").textContent = picking.length === 1 ? picking[0].title : `${picking.length} links`;
  $("#pickList").innerHTML = picking.map((c, ci) => (picking.length > 1 ? `<div class="pick-group">${esc(c.title)}</div>` : "") +
    c.items.map((it, i) => `<label class="pick-item"><input type="checkbox" data-c="${ci}" data-i="${i}" checked><span>${esc(it.title)}<small>${esc([it.author, it.duration ? clock(it.duration) : null].filter(Boolean).join(", "))}</small></span></label>`).join("")).join("");
  const errs = $("#pickErrors"); errs.hidden = !res.errors.length;
  errs.textContent = res.errors.map(e => `${e.url}: ${e.error}`).join(" ");
  $("#pickAll").checked = true;
  pickCount(); $("#pick").showModal();
}
function pickCount() {
  const boxes = [...document.querySelectorAll("#pickList input")], n = boxes.filter(b => b.checked).length;
  $("#pickCount").textContent = `${n} of ${boxes.length} selected`;
  const go = $("#pickGo"); go.textContent = `${pickMode === "download" ? "Download" : "Transcribe"} ${n} item${n === 1 ? "" : "s"}`; go.disabled = !n;
}
$("#pickList").addEventListener("change", pickCount);
$("#pickAll").addEventListener("change", e => { document.querySelectorAll("#pickList input").forEach(b => b.checked = e.target.checked); pickCount(); });
$("#pickCancel").addEventListener("click", () => $("#pick").close());
$("#pickForm").addEventListener("submit", async e => {
  e.preventDefault();
  const chosen = picking.map((c, ci) => ({...c, items: c.items.filter((_, i) => document.querySelector(`#pickList input[data-c="${ci}"][data-i="${i}"]`).checked)})).filter(c => c.items.length);
  $("#pick").close();
  try {
    if (pickMode === "download") await enqueueDownloads(chosen, pickFmt, $("#quality").value);
    else await enqueue(chosen);
  } catch (ex) { toast(ex.message); }
});
function showFormat() {
  document.querySelectorAll("[data-fmt]").forEach(b => b.setAttribute("aria-pressed", b.dataset.fmt === pickFmt));
  $("#qualityWrap").hidden = pickFmt === "mp3";
}
document.querySelectorAll("[data-fmt]").forEach(b => b.addEventListener("click", () => { pickFmt = b.dataset.fmt; showFormat(); }));
async function enqueueDownloads(collections, format, quality) {
  await api("/api/downloads", {method:"POST", body:{collections, format, quality}});
  state.settings = {...state.settings, download_format: format, download_quality: quality};
  links.value = ""; links.style.height = "auto";
  if (location.hash === "#downloads") { state.dlHtml = ""; await refreshDownloads(); renderDownloads(); }
  else location.hash = "downloads";
}

/* ---------- Downloads page ---------- */
const size = b => b > 1073741824 ? (b / 1073741824).toFixed(2) + " GB" : (b / 1048576).toFixed(1) + " MB";
async function refreshDownloads() {
  const res = await api("/api/downloads");
  state.downloads = res.downloads;
  const busy = res.downloads.filter(d => ["queued","working"].includes(d.status)).length;
  const c = $("#dlCount"); c.hidden = !busy; c.textContent = busy;
  return res.downloads;
}
function dlLine(d) {
  const kind = d.format === "mp3" ? "MP3" : `MP4${d.quality === "best" ? "" : ", " + d.quality + "p"}`;
  if (d.status === "working") return `${d.stage}${d.progress ? `, ${Math.round(d.progress * 100)}%` : ""}`;
  if (d.status === "queued") return `Waiting, ${kind}`;
  if (d.status === "failed") return d.error || "Failed";
  if (d.status === "cancelled") return "Stopped";
  return [kind, d.size ? size(d.size) : null, d.file ? d.file.split(/[\\/]/).pop() : null].filter(Boolean).join(", ");
}
function dlRow(d) {
  const title = d.status === "working" ? `<b class="swipe" style="--p:${Math.max(d.progress || 0, .03)}">${esc(d.title)}</b>` : `<b>${esc(d.title)}</b>`;
  const thumb = d.thumbnail ? `<img src="${esc(d.thumbnail)}" alt="" referrerpolicy="no-referrer" loading="lazy">` : `<span class="ph"></span>`;
  const b = (act, label, cls = "quiet") => `<button class="${cls}" data-dl="${act}" data-id="${d.id}">${label}</button>`;
  const acts = d.status === "done" ? b("open", "Play") + b("reveal", "Show in folder")
    : ["queued","working"].includes(d.status) ? b("cancel", "Stop") : b("retry", "Try again");
  return `<div class="dl-row ${d.status === "failed" ? "failed" : ""}">${thumb}<div>${title}<small>${esc(dlLine(d))}</small></div>
    <div class="row-actions">${acts}${b("remove", "Remove", "quiet danger")}</div></div>`;
}
function renderDownloads() {
  document.body.classList.add("reading");
  const list = state.downloads || [];
  let html;
  if (!list.length) html = `<p class="note">Paste a video or playlist link above and choose Download. Files are saved as MP4 video or MP3 audio.</p>`;
  else {
    const groups = [], seen = new Map();
    for (const d of list) {
      const key = d.group_id || d.id;
      if (!seen.has(key)) { seen.set(key, []); groups.push([d.group_title, seen.get(key)]); }
      seen.get(key).push(d);
    }
    html = groups.map(([name, items]) => (name ? `<h2 class="dl-group">${esc(name)}</h2>` : "") +
      items.sort((a, b) => a.position - b.position).map(dlRow).join("")).join("");
  }
  if (html === state.dlHtml && $(".dl-page")) return;
  state.dlHtml = html;
  const finished = list.some(d => ["done","failed","cancelled"].includes(d.status));
  $("#view").innerHTML = `<a class="back quiet" href="#">Back to list</a><div class="dl-page"><h1>Downloads</h1>
    <div class="actions"><button class="quiet" data-dl="folder">Open downloads folder</button>
    ${finished ? `<button class="quiet" data-dl="clear">Clear finished from list</button>` : ""}</div>${html}</div>`;
}
document.addEventListener("click", async e => {
  const t = e.target.closest("[data-dl]"); if (!t) return;
  const id = t.dataset.id, act = t.dataset.dl;
  try {
    if (act === "folder") await api("/api/folder", {method:"POST"});
    else if (act === "clear") await api("/api/downloads/clear", {method:"POST"});
    else if (act === "remove") await api(`/api/downloads/${id}`, {method:"DELETE"});
    else await api(`/api/downloads/${id}/${act}`, {method:"POST"});
    if (!["open","reveal","folder"].includes(act)) { state.dlHtml = ""; await refreshDownloads(); renderDownloads(); }
  } catch (ex) { toast(ex.message); }
});

/* ---------- Actions ---------- */
async function reloadJob() { state.job = await api(`/api/jobs/${state.route.id}`); state.detailKey = jobKey(state.job); renderJob(); }
const actions = {
  settings: openSettings,
  async saveMedia() {
    const j = state.job, fmt = j.source === "youtube" ? "mp4" : "mp3";
    await api("/api/downloads", {method:"POST", body:{format: fmt, quality: state.settings?.download_quality || "1080",
      collections:[{title: j.title, is_collection:false, items:[{url:j.url, source:j.source, title:j.title, author:j.author, duration:j.duration, thumbnail:j.thumbnail}]}]}});
    toast("Added to Downloads"); refreshDownloads();
  },
  async copy() { const r = await fetch(`/api/jobs/${state.job.id}/export/txt`); await navigator.clipboard.writeText(await r.text()); toast("Transcript copied"); },
  async copySummary() { const s = state.job.summaries.find(x => x.style === state.style); await navigator.clipboard.writeText(s.content); toast("Summary copied"); },
  async cancel() { await api(`/api/jobs/${state.job.id}/cancel`, {method:"POST"}); await reloadJob(); },
  async retry() { await api(`/api/jobs/${state.job.id}/retry`, {method:"POST"}); await reloadJob(); },
  async delete() { await api(`/api/jobs/${state.job.id}`, {method:"DELETE"}); toast("Deleted"); location.hash = ""; await refreshLibrary(); },
  async summarise() { await api(`/api/jobs/${state.job.id}/summary`, {method:"POST", body:{style:state.style}}); await reloadJob(); },
  async rollup() { await api(`/api/batches/${state.batch.id}/summary`, {method:"POST"}); state.batch = await api(`/api/batches/${state.batch.id}`); renderBatch(); },
  async retryAll() { await api(`/api/batches/${state.batch.id}/retry`, {method:"POST"}); state.batch = await api(`/api/batches/${state.batch.id}`); renderBatch(); },
  async deleteBatch() { await api(`/api/batches/${state.batch.id}`, {method:"DELETE"}); toast("Deleted"); location.hash = ""; await refreshLibrary(); },
};
document.addEventListener("click", async e => {
  const t = e.target.closest("[data-do],[data-style],[data-side],[data-tab]");
  if (!t) return;
  if (t.dataset.do) { e.preventDefault(); try { await actions[t.dataset.do](); } catch (ex) { toast(ex.message); } }
  else if (t.dataset.style) { state.style = t.dataset.style; refreshSide(); }
  else if (t.dataset.side) { state.side = t.dataset.side; refreshSide(); }
  else if (t.tagName === "BUTTON" && t.dataset.tab) {
    state.tab = t.dataset.tab; $(".split").dataset.tab = state.tab;
    document.querySelectorAll(".tabs button").forEach(b => b.setAttribute("aria-pressed", b.dataset.tab === state.tab));
  }
});
document.addEventListener("input", e => {
  if (e.target.id === "tq") {
    const q = e.target.value.trim(); $("#words").innerHTML = transcriptHtml(state.job);
    $("#tc").textContent = q ? `${$("#words").querySelectorAll("mark").length} found` : "";
  }
});
document.addEventListener("submit", async e => {
  if (e.target.id !== "askForm") return;
  e.preventDefault();
  const question = $("#q").value.trim(); if (!question || state.asking) return;
  const history = state.chat.slice();
  state.chat.push({role:"user", content:question}); state.asking = true; refreshSide();
  try {
    const {answer} = await api(`/api/jobs/${state.job.id}/ask`, {method:"POST", body:{question, history}});
    state.chat.push({role:"assistant", content:answer});
  } catch (ex) { state.chat.pop(); toast(ex.message); }
  state.asking = false; refreshSide(); $("#q")?.focus();
});
let findTimer;
$("#find").addEventListener("input", e => {
  state.query = e.target.value.trim(); clearTimeout(findTimer);
  if (state.query.length < 2) { renderList(); return; }
  findTimer = setTimeout(async () => { state.results = (await api(`/api/search?q=${encodeURIComponent(state.query)}`)).results; renderList(); }, 220);
});
window.addEventListener("hashchange", route);

/* ---------- Settings ---------- */
const form = $("#settingsForm");
function showKeyField() {
  const p = $("#aiProvider").value;
  document.querySelectorAll("#keys label").forEach(l => l.hidden = l.dataset.for !== p);
  $("#aiModel").placeholder = state.status.providers[p]?.model || "";
  $("#modelList").innerHTML = "";
}
function fillSettings(s) {
  for (const [k, v] of Object.entries(s)) {
    const el = form.elements[k]; if (!el) continue;
    if (el.type === "checkbox") el.checked = !!v; else el.value = v ?? "";
  }
  showKeyField();
}
function readSettings() {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    out[el.name] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value || 500) : el.value.trim();
  }
  return out;
}
async function openSettings() {
  const s = await api("/api/settings");
  $("#aiResult").textContent = "";
  fillSettings(s); $("#settings").showModal();
}
async function saveSettings() { state.settings = await api("/api/settings", {method:"PUT", body:readSettings()}); state.status = await api("/api/status"); }
$("#openSettings").addEventListener("click", openSettings);
$("#settingsCancel").addEventListener("click", () => $("#settings").close());
$("#aiProvider").addEventListener("change", () => { $("#aiModel").value = ""; showKeyField(); });
form.addEventListener("submit", async e => {
  e.preventDefault();
  try { await saveSettings(); $("#settings").close(); toast("Settings saved"); if (!state.route) renderEmpty(); else if (state.job) refreshSide(); }
  catch (ex) { toast(ex.message); }
});
$("#testAI").addEventListener("click", async () => {
  const out = $("#aiResult"); out.textContent = "Testing.";
  try { await saveSettings(); const r = await api("/api/settings/test", {method:"POST"}); out.textContent = `Working: ${r.model}`; }
  catch (ex) { out.textContent = ex.message; }
});
$("#loadModels").addEventListener("click", async () => {
  const out = $("#aiResult"); out.textContent = "Loading.";
  try { await saveSettings(); const {models} = await api("/api/models");
    $("#modelList").innerHTML = models.map(m => `<option value="${esc(m)}">`).join("");
    out.textContent = `${models.length} models found. Click the Model box to choose.`; }
  catch (ex) { out.textContent = ex.message; }
});

(async function start() {
  if (innerWidth < 520) links.placeholder = "Paste a link or playlist";
  state.status = await api("/api/status");
  $("#aiProvider").innerHTML = Object.entries(state.status.providers).map(([k, p]) => `<option value="${k}">${esc(p.label)}</option>`).join("");
  $("#defaultStyle").innerHTML = state.status.styles.map(s => `<option value="${s.id}">${esc(s.label)}</option>`).join("");
  state.settings = await api("/api/settings");
  await refreshLibrary();
  await route();
  poll();
  if (!state.route) links.focus();
})();
