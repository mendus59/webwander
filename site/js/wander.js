import {
  collection, query, where, orderBy, startAt, limit, getDocs, doc, getDoc, setDoc, deleteDoc,
  addDoc, serverTimestamp, arrayUnion, increment,
} from "https://www.gstatic.com/firebasejs/11.0.2/firebase-firestore.js";
import {
  configured, db, signIn, logOut, watchUser, isAdmin, loadCategories, faviconFor, el, safeStorage,
} from "./firebase.js";

const $ = (id) => document.getElementById(id);
const local = {
  seen: safeStorage("ww.seen", []),
  cats: safeStorage("ww.cats", []),
  affinity: safeStorage("ww.affinity", {}),
};

const state = {
  user: null,
  categories: [],
  catById: {},
  selected: new Set(local.cats.get()),
  seen: new Set(local.seen.get()),
  affinity: local.affinity.get(),
  saved: new Set(),
  buffer: [],
  current: null,
  loading: false,
};

// ------------------------------------------------------------------ setup

async function init() {
  state.categories = await loadCategories();
  state.catById = Object.fromEntries(state.categories.map((c) => [c.id, c]));
  renderChips();
  if (!configured) {
    toast("Firebase isn't configured yet. See SETUP.md in the repo.", 8000);
    $("auth-btn").hidden = true;
    return;
  }
  loadStats();
  watchUser(onUser);
}

async function loadStats() {
  try {
    const s = (await getDoc(doc(db, "meta", "stats"))).data();
    if (s?.approved) $("stats-line").textContent = `${s.approved.toLocaleString()} sites to discover so far.`;
  } catch { /* stats are optional */ }
}

async function onUser(user) {
  state.user = user;
  $("auth-btn").textContent = user ? "Sign out" : "Sign in";
  $("saved-btn").hidden = !user;
  $("admin-link").hidden = !(await isAdmin(user));
  if (!user) return;
  // Merge this browser's anonymous history into the account.
  const userRef = doc(db, "users", user.uid);
  const seenRef = doc(db, "users", user.uid, "private", "seen");
  const [u, s] = await Promise.all([getDoc(userRef), getDoc(seenRef)]);
  const data = u.data() || {};
  for (const id of s.data()?.ids || []) state.seen.add(id);
  if (data.categories && !local.cats.get().length) state.selected = new Set(data.categories);
  for (const [k, v] of Object.entries(data.affinity || {})) state.affinity[k] = (state.affinity[k] || 0) + v;
  const savedSnap = await getDocs(collection(db, "users", user.uid, "saved"));
  state.saved = new Set(savedSnap.docs.map((d) => d.id));
  await Promise.all([
    setDoc(seenRef, { ids: [...state.seen] }, { merge: true }),
    setDoc(userRef, {
      categories: [...state.selected], affinity: state.affinity,
      lastSeenAt: serverTimestamp(), ...(u.exists() ? {} : { createdAt: serverTimestamp() }),
    }, { merge: true }),
  ]);
  local.affinity.set({});  // now lives in the account
  renderChips();
  updateSaveBtn();
}

// ------------------------------------------------------------------ picking sites

async function fetchBatch() {
  const cats = [...state.selected].slice(0, 30);
  const base = [where("status", "==", "approved")];
  if (cats.length) base.push(where("categories", "array-contains-any", cats));
  const r = Math.random();
  const run = (from) => getDocs(query(collection(db, "sites"), ...base, orderBy("rand"), startAt(from), limit(40)));
  let snap = await run(r);
  let docs = snap.docs;
  if (docs.length < 40) docs = docs.concat((await run(0)).docs);  // wrap around
  const ids = new Set();
  return docs
    .filter((d) => !ids.has(d.id) && ids.add(d.id) && !state.seen.has(d.id))
    .map((d) => ({ id: d.id, ...d.data() }));
}

function weightOf(site) {
  const aff = (site.categories || []).reduce((sum, c) => sum + (state.affinity[c] || 0), 0);
  return Math.exp(Math.max(-3, Math.min(3, aff * 0.35))) * (0.5 + (site.score || 50) / 100);
}

function pickWeighted(list) {
  const weights = list.map(weightOf);
  let r = Math.random() * weights.reduce((a, b) => a + b, 0);
  for (let i = 0; i < list.length; i++) {
    r -= weights[i];
    if (r <= 0) return i;
  }
  return list.length - 1;
}

async function next() {
  if (!configured || state.loading) return;
  state.loading = true;
  $("wander").classList.add("spinning");
  try {
    state.buffer = state.buffer.filter((s) => !state.seen.has(s.id) && matchesFilter(s));
    for (let attempt = 0; state.buffer.length < 3 && attempt < 3; attempt++) {
      const have = new Set(state.buffer.map((s) => s.id));
      state.buffer.push(...(await fetchBatch()).filter((s) => !have.has(s.id)));
    }
    if (!state.buffer.length) return showEmpty();
    const [site] = state.buffer.splice(pickWeighted(state.buffer), 1);
    show(site);
    markSeen(site);
  } catch (e) {
    console.error(e);
    toast("Couldn't load a site. Check your connection and try again.");
  } finally {
    state.loading = false;
    $("wander").classList.remove("spinning");
  }
}

function matchesFilter(site) {
  return !state.selected.size || (site.categories || []).some((c) => state.selected.has(c));
}

function markSeen(site) {
  state.seen.add(site.id);
  local.seen.set([...state.seen].slice(-5000));
  if (state.user) {
    setDoc(doc(db, "users", state.user.uid, "private", "seen"), { ids: arrayUnion(site.id) }, { merge: true })
      .catch(console.error);
  }
}

// ------------------------------------------------------------------ rendering

function show(site) {
  state.current = site;
  document.body.classList.add("wandering");
  for (const id of ["welcome", "empty", "card", "frame"]) $(id).hidden = true;
  $("current").hidden = false;
  $("cur-icon").style.visibility = "visible";
  $("cur-icon").src = faviconFor(site.url);
  $("cur-title").textContent = site.title || site.domain;
  $("cur-title").href = site.url;
  $("cur-title").title = site.description || site.url;
  $("open").href = site.url;
  $("cur-cats").replaceChildren(...(site.categories || []).map((c) => {
    const cat = state.catById[c];
    return cat ? el("span", { class: "tag" }, `${cat.emoji} ${cat.label}`) : null;
  }));
  updateSaveBtn();
  if (site.frameable !== false) {
    const frame = $("frame");
    frame.src = site.url;
    frame.hidden = false;
  } else {
    $("card-title").textContent = site.title || site.domain;
    $("card-desc").textContent = site.description || site.url;
    $("card-img").src = site.image || "";
    $("card-img").hidden = !site.image;
    $("card-open").href = site.url;
    $("card").hidden = false;
  }
  history.replaceState(null, "", `#${site.id}`);
}

function showEmpty() {
  for (const id of ["welcome", "card", "frame"]) $(id).hidden = true;
  $("current").hidden = true;
  $("empty").hidden = false;
}

function updateSaveBtn() {
  const saved = state.current && state.saved.has(state.current.id);
  $("save").textContent = saved ? "★" : "☆";
  $("save").classList.toggle("on", !!saved);
}

function renderChips() {
  $("chips").replaceChildren(...state.categories.map((c) => el("button", {
    class: `chip ${state.selected.has(c.id) ? "on" : ""}`,
    onclick: (e) => {
      state.selected.has(c.id) ? state.selected.delete(c.id) : state.selected.add(c.id);
      e.currentTarget.classList.toggle("on");
    },
  }, `${c.emoji} ${c.label}`)));
  $("filter-btn").textContent = state.selected.size ? `Topics (${state.selected.size})` : "Topics";
}

function saveFilter() {
  local.cats.set([...state.selected]);
  state.buffer = [];
  renderChips();
  if (state.user) setDoc(doc(db, "users", state.user.uid), { categories: [...state.selected] }, { merge: true });
}

let toastTimer;
function toast(msg, ms = 3000) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

// ------------------------------------------------------------------ actions

function vote(delta) {
  const site = state.current;
  if (!site) return;
  const updates = {};
  for (const c of site.categories || []) {
    state.affinity[c] = (state.affinity[c] || 0) + delta;
    updates[c] = increment(delta);
  }
  if (state.user) {
    setDoc(doc(db, "users", state.user.uid), { affinity: updates }, { merge: true }).catch(console.error);
    setDoc(doc(db, "users", state.user.uid, "votes", site.id), { vote: delta, at: serverTimestamp() }).catch(console.error);
  } else {
    local.affinity.set(state.affinity);
  }
  toast(delta > 0 ? "You'll see more like this." : "You'll see less like this.");
  if (delta < 0) next();
}

async function toggleSave() {
  const site = state.current;
  if (!site) return;
  if (!state.user) return toast("Sign in to save sites.");
  const ref = doc(db, "users", state.user.uid, "saved", site.id);
  if (state.saved.has(site.id)) {
    state.saved.delete(site.id);
    await deleteDoc(ref);
  } else {
    state.saved.add(site.id);
    await setDoc(ref, {
      url: site.url, title: site.title || site.domain, categories: site.categories || [], savedAt: serverTimestamp(),
    });
    toast("Saved.");
  }
  updateSaveBtn();
}

async function openSaved() {
  const snap = await getDocs(query(collection(db, "users", state.user.uid, "saved"), orderBy("savedAt", "desc")));
  $("saved-list").replaceChildren(...(snap.empty
    ? [el("li", { class: "muted" }, "Nothing saved yet. Hit ☆ on a site you love.")]
    : snap.docs.map((d) => {
      const s = d.data();
      return el("li", {},
        el("img", { src: faviconFor(s.url), width: 16, height: 16, alt: "" }),
        el("a", { href: s.url, target: "_blank", rel: "noopener" }, s.title),
        el("button", {
          class: "icon-btn small", title: "Remove",
          onclick: async (e) => {
            await deleteDoc(d.ref);
            state.saved.delete(d.id);
            e.currentTarget.closest("li").remove();
            updateSaveBtn();
          },
        }, "✕"));
    })));
  $("suggest-msg").textContent = "";
  $("saved-dlg").showModal();
}

async function suggest(e) {
  e.preventDefault();
  const url = $("suggest-url").value.trim();
  try {
    await addDoc(collection(db, "submissions"), { url, uid: state.user.uid, createdAt: serverTimestamp() });
    $("suggest-msg").textContent = "Thanks! It'll be checked on the next crawl and reviewed by an admin.";
    $("suggest-url").value = "";
  } catch (err) {
    console.error(err);
    $("suggest-msg").textContent = "Couldn't submit that. Is it a full URL starting with https://?";
  }
}

// ------------------------------------------------------------------ wiring

$("wander").onclick = next;
$("wander-big").onclick = next;
$("like").onclick = () => vote(1);
$("dislike").onclick = () => vote(-1);
$("save").onclick = toggleSave;
$("card-open").onclick = () => setTimeout(next, 400);
$("auth-btn").onclick = () => (state.user ? logOut() : signIn().catch((e) => toast(e.message)));
$("filter-btn").onclick = () => $("filter-dlg").showModal();
$("chips-clear").onclick = () => { state.selected.clear(); renderChips(); };
$("chips-done").onclick = () => { saveFilter(); $("filter-dlg").close(); };
$("filter-dlg").addEventListener("close", saveFilter);
$("saved-btn").onclick = openSaved;
$("saved-close").onclick = () => $("saved-dlg").close();
$("suggest-form").onsubmit = suggest;
$("reset-seen").onclick = () => {
  state.seen.clear();
  local.seen.set([]);
  if (state.user) setDoc(doc(db, "users", state.user.uid, "private", "seen"), { ids: [] });
  next();
};

document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, dialog")) return;
  if (e.key === " " || e.key === "ArrowRight") { e.preventDefault(); next(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); vote(1); }
  else if (e.key === "ArrowDown") { e.preventDefault(); vote(-1); }
  else if (e.key === "s") toggleSave();
});

init();
