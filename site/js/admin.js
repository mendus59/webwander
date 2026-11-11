import {
  collection, query, where, orderBy, limit, startAfter, getDocs, getDoc, doc, writeBatch,
  updateDoc, addDoc, deleteDoc, serverTimestamp, getCountFromServer,
} from "https://www.gstatic.com/firebasejs/11.0.2/firebase-firestore.js";
import {
  configured, db, signIn, logOut, watchUser, isAdmin, loadCategories, faviconFor, el,
} from "./firebase.js";
import { repoUrl } from "./firebase-config.js";

const $ = (id) => document.getElementById(id);
const PAGE = 50;
const state = { user: null, status: "proposed", cursor: null, cats: [], catById: {}, rows: new Map() };

$("crawl-link").href = `${repoUrl}/actions/workflows/crawl.yml`;

// ------------------------------------------------------------------ run the crawler
// GitHub Pages is static, so the browser calls the GitHub API itself with a narrowly
// scoped token the admin pastes once (Actions read/write on this repo only). It is kept
// in this browser's localStorage and only ever sent to api.github.com.

const REPO = repoUrl.replace("https://github.com/", "");
const API = `https://api.github.com/repos/${REPO}/actions/workflows/crawl.yml`;
const TOKEN_KEY = "webwander.githubToken";
const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } };
const setToken = (t) => { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch {} };

async function gh(url, opts = {}, token = getToken()) {
  const r = await fetch(url, {
    ...opts,
    headers: { Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
      ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers || {}) },
  });
  if (!r.ok) {
    const msg = (await r.json().catch(() => ({}))).message || `HTTP ${r.status}`;
    throw Object.assign(new Error(msg), { status: r.status });
  }
  return r.status === 204 ? null : r.json();
}

function openCrawlDialog() {
  const has = !!getToken();
  $("token-setup").hidden = has;
  $("token-forget").hidden = !has;
  $("crawl-error").hidden = true;
  $("token-repo").textContent = REPO;
  $("token-new").href = "https://github.com/settings/personal-access-tokens/new";
  $("crawl-dlg").showModal();
}

async function startCrawl(e) {
  e.preventDefault();
  const token = getToken() || $("crawl-token").value.trim();
  if (!token) { $("crawl-error").textContent = "Paste a token first."; $("crawl-error").hidden = false; return; }
  $("crawl-go").disabled = true;
  try {
    await gh(`${API}/dispatches`, { method: "POST", body: JSON.stringify({
      ref: "main", inputs: { min_new: String($("crawl-min").value || 10), max_fetch: String($("crawl-max").value || 300) },
    }) }, token);
    setToken(token);
    $("crawl-token").value = "";
    $("crawl-dlg").close();
    toast("Crawl started. New sites appear in Proposed when it finishes (usually 10-40 minutes).");
    watchCrawl(true);
  } catch (err) {
    const hint = err.status === 401 ? " The token is invalid or expired; paste a new one."
      : err.status === 403 || err.status === 404 ? " The token needs Actions: Read and write on this repository." : "";
    if (err.status === 401 || err.status === 403 || err.status === 404) { setToken(""); $("token-setup").hidden = false; $("token-forget").hidden = true; }
    $("crawl-error").textContent = `GitHub said: ${err.message}.${hint}`;
    $("crawl-error").hidden = false;
  } finally {
    $("crawl-go").disabled = false;
  }
}

// Shows the latest run's state next to the button, polling while one is queued or running.
let crawlTimer;
async function watchCrawl(justStarted = false) {
  clearTimeout(crawlTimer);
  try {
    const { workflow_runs: [run] = [] } = await gh(`${API}/runs?per_page=1`);
    const fresh = run && Date.now() - new Date(run.created_at) < 2 * 60_000;
    if (justStarted && !fresh) { crawlTimer = setTimeout(() => watchCrawl(true), 4000); return; }
    if (!run) { $("crawl-status").textContent = ""; return; }
    const active = run.status !== "completed";
    const when = new Date(run.updated_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
    $("crawl-status").replaceChildren(el("a", { href: run.html_url, target: "_blank", rel: "noopener" },
      active ? `⏳ crawl ${run.status.replace("_", " ")}…`
        : `${run.conclusion === "success" ? "✓" : "⚠"} last crawl ${run.conclusion}, ${when}`));
    $("crawl-btn").disabled = active;
    if (active) crawlTimer = setTimeout(watchCrawl, 15_000);
    else if (justStarted === false && $("crawl-status").dataset.wasActive) { loadStats(); reload(); }
    $("crawl-status").dataset.wasActive = active ? "1" : "";
  } catch (err) {
    console.warn("crawl status:", err.message);
  }
}

async function init() {
  state.cats = await loadCategories();
  state.catById = Object.fromEntries(state.cats.map((c) => [c.id, c]));
  if (!configured) {
    $("gate-msg").textContent = "Firebase isn't configured yet. Fill in site/js/firebase-config.js (see SETUP.md).";
    $("auth-btn").hidden = true;
    return;
  }
  watchUser(onUser);
}

async function onUser(user) {
  state.user = user;
  $("auth-btn").textContent = user ? "Sign out" : "Sign in";
  const admin = await isAdmin(user);
  $("panel").hidden = !admin;
  $("gate").hidden = admin;
  if (!user) {
    $("gate-msg").textContent = "Sign in with an admin account to review proposed sites.";
  } else if (!admin) {
    $("gate-msg").replaceChildren(
      "You're signed in but not an admin yet. In the Firebase console, create a Firestore document at ",
      el("code", {}, `admins/${user.uid}`),
      " (any fields, e.g. email), then reload this page.",
    );
  } else {
    loadStats();
    reload();
    $("crawl-btn").hidden = false;
    watchCrawl();
  }
}

async function loadStats() {
  try { await loadStatsInner(); } catch (e) { console.error(e); }
}

async function loadStatsInner() {
  const counts = await Promise.all(["proposed", "approved", "rejected", "backlog"].map(async (s) =>
    (await getCountFromServer(query(collection(db, "sites"), where("status", "==", s)))).data().count));
  const meta = (await getDoc(doc(db, "meta", "stats"))).data() || {};
  const subs = (await getCountFromServer(collection(db, "submissions"))).data().count;
  $("stats").replaceChildren(
    stat("Waiting for review", counts[0]),
    stat("Approved", counts[1]),
    stat("Rejected", counts[2]),
    stat("Backlog", counts[3]),
    ...(subs ? [stat("Being scored", subs)] : []),
    stat("Last crawl", meta.lastRun ? `${new Date(meta.lastRun).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}, +${meta.lastRunNew} new` : "never"),
    stat("Crawler version", meta.crawlerVersion ? `v${meta.crawlerVersion}` : "unknown",
      meta.crawlerBuild ? `${repoUrl}/commit/${meta.crawlerBuild}` : null),
  );
}

const stat = (label, value, href = null) => el("div", { class: "stat" },
  el("strong", { class: String(value).length > 8 ? "long" : "" },
    href ? el("a", { href, target: "_blank", rel: "noopener", title: "the code that ran" }, String(value)) : String(value)),
  el("span", {}, label));

// ------------------------------------------------------------------ list

async function reload() {
  state.cursor = null;
  state.rows.clear();
  $("list").replaceChildren();
  $("select-all").checked = false;
  await loadMore();
}

function showError(e) {
  console.error(e);
  const link = String(e?.message || "").match(/https:\/\/console\.firebase\.google\.com\S+/)?.[0];
  $("list").append(el("p", { class: "warn center" },
    "⚠ Couldn't load this list: ", e?.message || String(e),
    link ? el("span", {}, " ", el("a", { href: link, target: "_blank", rel: "noopener" }, "Create the missing index ↗")) : null));
}

async function loadMore() {
  try {
    if (!state.cursor && state.status === "proposed") await loadPending();
    if (!state.cursor && state.status === "backlog") await backlogHeader();
    await loadSites();
  } catch (e) {
    $("more").hidden = true;
    showError(e);
  }
}

async function loadSites() {
  const parts = [where("status", "==", state.status), orderBy("score", "desc"), limit(PAGE)];
  if (state.cursor) parts.push(startAfter(state.cursor));
  const snap = await getDocs(query(collection(db, "sites"), ...parts));
  state.cursor = snap.docs.at(-1) || state.cursor;
  for (const d of snap.docs) {
    const row = renderRow(d.id, d.data());
    state.rows.set(d.id, { data: d.data(), node: row });
    $("list").append(row);
  }
  if (!state.rows.size && !$("list").querySelector(".review")) $("list").append(el("p", { class: "muted center" }, "Nothing here. 🎉"));
  $("more").hidden = snap.docs.length < PAGE;
  updateSelection();
}

// Sites added by hand or suggested by visitors that haven't been scored yet. They are shown
// at the top of Proposed (where they will land) and are scored right away when the
// "Run crawler" token is set up, otherwise by the next daily crawl.
async function loadPending() {
  const snap = await getDocs(collection(db, "submissions"));
  if (snap.empty) return;
  const docs = snap.docs.map((d) => ({ id: d.id, ...d.data() }));
  const token = !!getToken();
  $("list").append(el("div", { class: "row pending-head" },
    el("strong", { class: "small" }, `⏳ ${docs.length} site${docs.length === 1 ? "" : "s"} being scored`),
    el("span", { class: "muted small" }, token ? "They move into this list when scoring finishes (a minute or two)."
      : "They're scored by the next daily crawl, or now if you set up Run crawler."),
    el("div", { class: "spacer" }),
    el("button", { class: "ghost-btn small", onclick: () => scoreNow(true) }, "Score now")));
  for (const sub of docs) {
    const who = sub.uid === state.user?.uid ? "you" : "a visitor";
    const node = el("article", { class: "review queued" },
      el("input", { type: "checkbox", style: "visibility:hidden" }),
      el("div", { class: "score" }, "…"),
      el("div", { class: "body" },
        el("div", { class: "title-row" },
          el("a", { href: sub.url, target: "_blank", rel: "noopener", class: "title" }, sub.url)),
        el("p", { class: "muted small" }, `Added by ${who} · being scored`,
          sub.autoApprove ? " · will be approved once scored" : "")),
      el("div", { class: "actions" },
        el("button", { class: "ghost-btn danger", onclick: async () => {
          await deleteDoc(doc(db, "submissions", sub.id));
          node.remove();
          toast("Removed.");
          loadStats();
        } }, "Remove")));
    $("list").append(node);
  }
  watchPending(docs.length);
}

// Start the "Score submissions" workflow (needs the Run crawler token).
async function scoreNow(ask = false) {
  if (!getToken()) {
    if (ask) { openCrawlDialog(); toast("Set up the token once, then Score now works instantly."); }
    return false;
  }
  try {
    await gh(`https://api.github.com/repos/${REPO}/actions/workflows/score.yml/dispatches`,
      { method: "POST", body: JSON.stringify({ ref: "main" }) });
    if (ask) toast("Scoring started. The sites move into Proposed in a minute or two.");
    return true;
  } catch (err) {
    toast(`Couldn't start scoring: ${err.message}`);
    return false;
  }
}

// While submissions are pending, check every 20 s and refresh Proposed when they're done.
let pendingTimer;
function watchPending(n) {
  clearTimeout(pendingTimer);
  pendingTimer = setTimeout(async () => {
    try {
      const left = (await getCountFromServer(collection(db, "submissions"))).data().count;
      if (left < n && state.status === "proposed") { loadStats(); reload(); }
      else if (left) watchPending(n);
    } catch (e) { console.warn(e); }
  }, 20_000);
}

// Good sites the crawler scored but held back to keep categories balanced. They are
// ordinary sites with status "backlog", so each site is only ever in one tab.
async function backlogHeader() {
  const n = (await getCountFromServer(query(collection(db, "sites"), where("status", "==", "backlog")))).data().count;
  $("list").append(el("p", { class: "muted small backlog-count" },
    `${n} scored site${n === 1 ? "" : "s"} held back to keep each run's categories balanced. ` +
    "The crawler offers these first in later runs, or propose some now."));
  if (!n) return;
  const count = el("input", { type: "number", value: "10", min: "1", max: "100", class: "num", "aria-label": "how many" });
  $("list").append(el("div", { class: "row backlog-batch" },
    el("button", { class: "wander-btn", onclick: (e) => proposeFromBacklog(Number(count.value) || 10, e.currentTarget) },
      "Propose a balanced batch"),
    count,
    el("span", { class: "muted small" }, "sites move to Proposed, already scored. No crawl needed.")));
}

// ---- propose a batch straight from the backlog (same idea as crawler/wander/balance.py)

const IGNORED_TOPICS = new Set(["mature"]);
const topicsOf = (d) => {
  const t = (d.categories || []).filter((c) => !IGNORED_TOPICS.has(c));
  return t.length ? t : ["_uncategorised"];
};

function simhashSimilarity(a, b) {
  if (!a || !b) return 0;
  let x = BigInt("0x" + a) ^ BigInt("0x" + b), bits = 0;
  while (x) { bits += Number(x & 1n); x >>= 1n; }
  return 1 - bits / 64;
}

// Greedy: always take the candidate whose least-covered category has the fewest picks so
// far, nudged toward categories the database holds fewest of, then by score.
export function pickBalanced(pool, k, dbCounts = {}) {
  const dbTotal = Object.values(dbCounts).reduce((a, b) => a + b, 0) || 1;
  const run = {}, domains = new Set(), chosen = [];
  const need = (d) => {
    const t = topicsOf(d);
    return [Math.min(...t.map((c) => run[c] || 0)), Math.min(...t.map((c) => dbCounts[c] || 0)) / dbTotal, -(d.score || 0)];
  };
  const cmp = (a, b) => { const x = need(a), y = need(b); return x[0] - y[0] || x[1] - y[1] || x[2] - y[2]; };
  const remaining = [...pool];
  while (remaining.length && chosen.length < k) {
    remaining.sort(cmp);
    const d = remaining.shift();
    if (domains.has(d.registrableDomain || d.domain)) continue;
    if (chosen.some((c) => simhashSimilarity(c.simhash, d.simhash) >= 0.9)) continue;
    chosen.push(d);
    domains.add(d.registrableDomain || d.domain);
    for (const c of topicsOf(d)) run[c] = (run[c] || 0) + 1;
  }
  return chosen;
}

async function proposeFromBacklog(k, btn) {
  btn.disabled = true;
  try {
    const meta = (await getDoc(doc(db, "meta", "stats"))).data() || {};
    const snap = await getDocs(query(collection(db, "sites"), where("status", "==", "backlog")));
    const pool = snap.docs.map((d) => ({ id: d.id, ...d.data() }));
    const batch = pickBalanced(pool, k, meta.categories || {});
    if (!batch.length) return toast("The backlog is empty.");
    for (const d of batch) if (!state.rows.has(d.id)) state.rows.set(d.id, { data: d, node: { remove() {} } });
    await setStatus(batch.map((d) => d.id), "proposed", `${batch.length} site${batch.length === 1 ? "" : "s"} moved from the backlog to Proposed.`);
    document.querySelector('#tabs button[data-status="proposed"]').click();
  } catch (e) {
    toast(`Couldn't propose from the backlog: ${e.message}`);
    console.error(e);
  } finally {
    btn.disabled = false;
  }
}

function scoreClass(s) {
  return s >= 70 ? "good" : s >= 45 ? "ok" : "low";
}

function renderRow(id, s, actions = null) {
  const b = s.scoreBreakdown || {};
  const chosen = new Set(s.categories || []);
  const editable = true;
  // Show tags the site has even if categories.json no longer lists them.
  const cats = [...state.cats, ...[...chosen].filter((c) => !state.catById[c]).map((c) => ({ id: c, emoji: "❔", label: c }))];
  const catChips = cats.map((c) => el("button", {
    disabled: !editable,
    class: `chip small ${chosen.has(c.id) ? "on" : ""}`,
    title: s.categoryScores?.[c.id] ? `tagger score ${s.categoryScores[c.id]}` : "",
    onclick: (e) => {
      if (!editable) return;
      chosen.has(c.id) ? chosen.delete(c.id) : chosen.add(c.id);
      e.currentTarget.classList.toggle("on");
      state.rows.get(id).data.categories = [...chosen];
      if (s.status !== "proposed") updateDoc(doc(db, "sites", id), { categories: [...chosen] });
    },
  }, `${c.emoji} ${c.label}`));

  const warnings = [];
  if (s.belowThreshold) warnings.push("below threshold (filled to reach daily minimum)");
  if (s.unscanned) warnings.push(`couldn't scan: ${s.scanBlocked || "a"} bot check and no archived copy, so no score or tags. Open it to judge it yourself`);
  else if (s.scoredFrom === "archive") warnings.push(`${s.scanBlocked || "a"} bot check: scored from the Wayback Machine copy of ${s.archivedAt || "an earlier date"}`);
  if (s.autoReject) warnings.push(`auto-check flagged: ${s.autoReject}`);
  if (s.resubmitted) warnings.push("rejected before, re-added by an admin");
  if (s.status === "rejected" && s.previousStatus) warnings.push(`was ${s.previousStatus} before the weekly recheck`);
  if (s.nearestSimilarity >= 0.85) warnings.push(`very similar to an existing site (${Math.round(s.nearestSimilarity * 100)}%)`);

  return el("article", { class: "review", "data-id": id },
    el("input", { type: "checkbox", class: "pick", onchange: updateSelection, style: editable ? null : "visibility:hidden" }),
    s.unscanned
      ? el("div", { class: "score unscanned", title: "not scanned: behind a bot check" }, "?")
      : el("div", { class: `score ${scoreClass(s.score)}`, title: JSON.stringify(b) }, String(s.score ?? "?")),
    el("div", { class: "body" },
      el("div", { class: "title-row" },
        el("img", { src: faviconFor(s.url), width: 16, height: 16, alt: "" }),
        el("a", { href: s.url, target: "_blank", rel: "noopener", class: "title" }, s.title || s.domain),
        el("span", { class: "muted small" }, s.domain),
        s.source ? el("span", { class: "tag" }, s.source) : null,
        chosen.has("mature") ? el("span", { class: "tag mature" }, "🔞 Mature") : null,
        s.frameable === false ? el("span", { class: "tag" }, "opens in new tab") : null,
        s.scanBlocked ? el("span", { class: "tag", title: "this site answers crawlers with a bot check" }, `🛡 ${s.scanBlocked}`) : null,
        s.scoredFrom === "archive" ? el("a", { class: "tag", target: "_blank", rel: "noopener",
          href: `https://web.archive.org/web/${(s.archivedAt || "").replaceAll("-", "")}/${s.url}` }, "archived copy ↗") : null,
        s.crawlerVersion ? el("span", { class: "tag", title: "crawler version that scored this site" }, `v${s.crawlerVersion}`) : null),
      s.description ? el("p", { class: "desc" }, s.description) : null,
      s.unscanned ? null : el("div", { class: "bars" }, ...["interactivity", "media", "independence", "craft", "novelty", "security", "penalties"].filter((k) => k !== "media" || k in b).map((k) =>
        el("span", { class: `sbar ${k}`, title: k }, `${k} ${b[k] ?? 0}`))),
      !s.unscanned && (s.reasons || []).length ? el("p", { class: "muted small" }, s.reasons.join(" · ")) : null,
      warnings.length ? el("p", { class: "warn small" }, "⚠ " + warnings.join(" · ")) : null,
      el("div", { class: "chips tag-editor collapsed" }, ...catChips,
        editable && el("button", {
          class: "chip small edit-tags",
          onclick: (e) => e.currentTarget.parentElement.classList.toggle("collapsed"),
        }, "✎ edit tags"))),
    el("div", { class: "actions" }, ...(actions || [
      el("button", { class: "ghost-btn", onclick: () => preview(s) }, "Preview"),
      s.status !== "approved" ? el("button", { class: "wander-btn", onclick: () => setStatus([id], "approved") }, "Approve") : null,
      s.status !== "rejected" ? el("button", { class: "ghost-btn danger", onclick: () => setStatus([id], "rejected") }, "Reject") : null,
      s.status !== "proposed" ? el("button", { class: "ghost-btn", onclick: () => setStatus([id], "proposed") },
        s.status === "backlog" ? "Propose" : "Back to queue") : null])));
}

async function setStatus(ids, status, message = null) {
  for (let i = 0; i < ids.length; i += 400) {
    const batch = writeBatch(db);
    for (const id of ids.slice(i, i + 400)) {
      const row = state.rows.get(id);
      const update = { status, reviewedAt: serverTimestamp() };
      if (row?.data.categories) update.categories = row.data.categories;
      if (status === "approved") Object.assign(update, { approvedAt: serverTimestamp(), approvedBy: state.user.uid });
      batch.update(doc(db, "sites", id), update);
    }
    await batch.commit();
  }
  for (const id of ids) {
    state.rows.get(id)?.node.remove();
    state.rows.delete(id);
  }
  toast(message || `${ids.length} site${ids.length === 1 ? "" : "s"} ${status === "proposed" ? "moved to Proposed" : status}.`);
  updateSelection();
  loadStats();
}

function selectedIds() {
  return [...document.querySelectorAll(".review .pick:checked")].map((c) => c.closest(".review").dataset.id);
}

function updateSelection() {
  const n = selectedIds().length;
  $("selected-count").textContent = n ? `${n} selected` : "";
  $("bulk-approve").disabled = !n || state.status === "approved";
  $("bulk-reject").disabled = !n || state.status === "rejected";
}

function preview(s) {
  $("preview-title").textContent = s.title || s.domain;
  $("preview-open").href = s.url;
  $("preview-frame").src = s.frameable === false ? "about:blank" : s.url;
  $("preview-note").hidden = s.frameable !== false;
  $("preview-dlg").showModal();
}

let toastTimer;
function toast(msg) {
  $("toast").textContent = msg;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3000);
}

// ------------------------------------------------------------------ wiring

$("auth-btn").onclick = () => (state.user ? logOut() : signIn().catch((e) => toast(e.message)));
$("more").onclick = loadMore;
$("tabs").onclick = (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b === btn));
  state.status = btn.dataset.status;
  reload();
};
$("select-all").onchange = (e) => {
  document.querySelectorAll(".review .pick").forEach((c) => { c.checked = e.target.checked; });
  updateSelection();
};
$("bulk-approve").onclick = () => setStatus(selectedIds(), "approved");
$("bulk-reject").onclick = () => setStatus(selectedIds(), "rejected");
$("bulk-score").onclick = async () => {
  const min = Number($("bulk-min").value);
  const snap = await getDocs(query(collection(db, "sites"), where("status", "==", "proposed"), orderBy("score", "desc")));
  const ids = snap.docs.filter((d) => (d.data().score || 0) >= min).map((d) => d.id);
  if (!ids.length) return toast(`No proposed sites score ${min} or more.`);
  if (!confirm(`Approve ${ids.length} proposed site(s) scoring ${min}+ with their auto-detected categories?`)) return;
  for (const d of snap.docs) if (!state.rows.has(d.id)) state.rows.set(d.id, { data: d.data(), node: { remove() {} } });
  await setStatus(ids, "approved");
};
$("add-form").onsubmit = async (e) => {
  e.preventDefault();
  await addDoc(collection(db, "submissions"), {
    url: $("add-url").value.trim(), uid: state.user.uid, createdAt: serverTimestamp(),
    autoApprove: $("add-approve").checked,
  });
  $("add-url").value = "";
  const now = await scoreNow();
  toast(now ? "Scoring now. It'll be in Proposed in a minute or two."
    : "Added. It's scored by the next daily crawl (set up Run crawler to score instantly).");
  loadStats();
  if (state.status === "proposed") reload();
};
$("crawl-btn").onclick = openCrawlDialog;
$("crawl-form").onsubmit = startCrawl;
$("crawl-cancel").onclick = () => $("crawl-dlg").close();
$("token-forget").onclick = () => { setToken(""); $("token-setup").hidden = false; $("token-forget").hidden = true; toast("Token removed from this browser."); };
$("preview-close").onclick = () => $("preview-dlg").close();
$("preview-dlg").addEventListener("close", () => { $("preview-frame").src = "about:blank"; });

init();
