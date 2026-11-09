import {
  collection, query, where, orderBy, limit, startAfter, getDocs, getDoc, doc, writeBatch,
  updateDoc, addDoc, serverTimestamp, getCountFromServer,
} from "https://www.gstatic.com/firebasejs/11.0.2/firebase-firestore.js";
import {
  configured, db, signIn, logOut, watchUser, isAdmin, loadCategories, faviconFor, el,
} from "./firebase.js";
import { repoUrl } from "./firebase-config.js";

const $ = (id) => document.getElementById(id);
const PAGE = 50;
const state = { user: null, status: "proposed", cursor: null, cats: [], catById: {}, rows: new Map() };

$("crawl-link").href = `${repoUrl}/actions/workflows/crawl.yml`;

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
  }
}

async function loadStats() {
  const counts = await Promise.all(["proposed", "approved", "rejected"].map(async (s) =>
    (await getCountFromServer(query(collection(db, "sites"), where("status", "==", s)))).data().count));
  const meta = (await getDoc(doc(db, "meta", "stats"))).data() || {};
  const subs = (await getCountFromServer(collection(db, "submissions"))).data().count;
  $("stats").replaceChildren(
    stat("Waiting for review", counts[0]),
    stat("Approved", counts[1]),
    stat("Rejected", counts[2]),
    stat("Queued for scoring", subs),
    stat("Last crawl", meta.lastRun ? `${new Date(meta.lastRun).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}, +${meta.lastRunNew} new` : "never"),
  );
}

const stat = (label, value) => el("div", { class: "stat" },
  el("strong", { class: String(value).length > 8 ? "long" : "" }, String(value)), el("span", {}, label));

// ------------------------------------------------------------------ list

async function reload() {
  state.cursor = null;
  state.rows.clear();
  $("list").replaceChildren();
  $("select-all").checked = false;
  await loadMore();
}

async function loadMore() {
  const parts = [where("status", "==", state.status), orderBy("score", "desc"), limit(PAGE)];
  if (state.cursor) parts.push(startAfter(state.cursor));
  const snap = await getDocs(query(collection(db, "sites"), ...parts));
  state.cursor = snap.docs.at(-1) || state.cursor;
  for (const d of snap.docs) {
    const row = renderRow(d.id, d.data());
    state.rows.set(d.id, { data: d.data(), node: row });
    $("list").append(row);
  }
  if (!state.rows.size) $("list").append(el("p", { class: "muted center" }, "Nothing here. 🎉"));
  $("more").hidden = snap.docs.length < PAGE;
  updateSelection();
}

function scoreClass(s) {
  return s >= 70 ? "good" : s >= 45 ? "ok" : "low";
}

function renderRow(id, s) {
  const b = s.scoreBreakdown || {};
  const chosen = new Set(s.categories || []);
  const catChips = state.cats.map((c) => el("button", {
    class: `chip small ${chosen.has(c.id) ? "on" : ""}`,
    title: s.categoryScores?.[c.id] ? `tagger score ${s.categoryScores[c.id]}` : "",
    onclick: (e) => {
      chosen.has(c.id) ? chosen.delete(c.id) : chosen.add(c.id);
      e.currentTarget.classList.toggle("on");
      state.rows.get(id).data.categories = [...chosen];
      if (s.status !== "proposed") updateDoc(doc(db, "sites", id), { categories: [...chosen] });
    },
  }, `${c.emoji} ${c.label}`));

  const warnings = [];
  if (s.belowThreshold) warnings.push("below threshold (filled to reach daily minimum)");
  if (s.autoReject) warnings.push(`auto-check flagged: ${s.autoReject}`);
  if (s.nearestSimilarity >= 0.85) warnings.push(`very similar to an existing site (${Math.round(s.nearestSimilarity * 100)}%)`);

  return el("article", { class: "review", "data-id": id },
    el("input", { type: "checkbox", class: "pick", onchange: updateSelection }),
    el("div", { class: `score ${scoreClass(s.score)}`, title: JSON.stringify(b) }, String(s.score ?? "?")),
    el("div", { class: "body" },
      el("div", { class: "title-row" },
        el("img", { src: faviconFor(s.url), width: 16, height: 16, alt: "" }),
        el("a", { href: s.url, target: "_blank", rel: "noopener", class: "title" }, s.title || s.domain),
        el("span", { class: "muted small" }, s.domain),
        s.source ? el("span", { class: "tag" }, s.source) : null,
        s.frameable === false ? el("span", { class: "tag" }, "opens in new tab") : null),
      s.description ? el("p", { class: "desc" }, s.description) : null,
      el("div", { class: "bars" }, ...["interactivity", "independence", "craft", "novelty", "penalties"].map((k) =>
        el("span", { class: `sbar ${k}`, title: k }, `${k} ${b[k] ?? 0}`))),
      (s.reasons || []).length ? el("p", { class: "muted small" }, s.reasons.join(" · ")) : null,
      warnings.length ? el("p", { class: "warn small" }, "⚠ " + warnings.join(" · ")) : null,
      el("div", { class: "chips tag-editor collapsed" }, ...catChips,
        el("button", {
          class: "chip small edit-tags",
          onclick: (e) => e.currentTarget.parentElement.classList.toggle("collapsed"),
        }, "✎ edit tags"))),
    el("div", { class: "actions" },
      el("button", { class: "ghost-btn", onclick: () => preview(s) }, "Preview"),
      s.status !== "approved" ? el("button", { class: "wander-btn", onclick: () => setStatus([id], "approved") }, "Approve") : null,
      s.status !== "rejected" ? el("button", { class: "ghost-btn danger", onclick: () => setStatus([id], "rejected") }, "Reject") : null,
      s.status !== "proposed" ? el("button", { class: "ghost-btn", onclick: () => setStatus([id], "proposed") }, "Back to queue") : null));
}

async function setStatus(ids, status) {
  for (let i = 0; i < ids.length; i += 400) {
    const batch = writeBatch(db);
    for (const id of ids.slice(i, i + 400)) {
      const row = state.rows.get(id);
      const update = { status, reviewedAt: serverTimestamp(), categories: row?.data.categories || [] };
      if (status === "approved") Object.assign(update, { approvedAt: serverTimestamp(), approvedBy: state.user.uid });
      batch.update(doc(db, "sites", id), update);
    }
    await batch.commit();
  }
  for (const id of ids) {
    state.rows.get(id)?.node.remove();
    state.rows.delete(id);
  }
  toast(`${ids.length} site${ids.length === 1 ? "" : "s"} ${status === "proposed" ? "sent back to the queue" : status}.`);
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
  toast("Queued. It'll be scored on the next crawler run.");
  loadStats();
};
$("preview-close").onclick = () => $("preview-dlg").close();
$("preview-dlg").addEventListener("close", () => { $("preview-frame").src = "about:blank"; });

init();
