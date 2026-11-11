// Saved sites panel: list or card view, filtered by tag and by date added.
import {
  collection, query, orderBy, getDocs, getDoc, doc, deleteDoc,
} from "https://www.gstatic.com/firebasejs/11.0.2/firebase-firestore.js";
import { db, faviconFor, el, safeStorage } from "./firebase.js";

const $ = (id) => document.getElementById(id);
const DAY = 86400000;
const MATURE = "mature";
const viewPref = safeStorage("ww.savedView", "list");

let manifestPromise;
function shotsManifest() {
  // Written by the Screenshots workflow; missing until it has run once.
  manifestPromise ??= fetch("shots/manifest.json", { cache: "no-cache" })
    .then((r) => (r.ok ? r.json() : {}))
    .catch(() => ({}));
  return manifestPromise;
}

const siteInfo = new Map();  // id -> Promise<{image, description}> for saves made before we stored them
function lookupSite(id) {
  if (!siteInfo.has(id)) {
    siteInfo.set(id, getDoc(doc(db, "sites", id))
      .then((d) => (d.exists() ? d.data() : {}))
      .catch(() => ({})));
  }
  return siteInfo.get(id);
}

function domainOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}

function hue(text) {
  let h = 0;
  for (const ch of text) h = (h * 31 + ch.codePointAt(0)) % 360;
  return h;
}

const fmtDate = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" });

function startOfDay(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
function parseDateInput(v) {
  if (!v) return null;
  const [y, m, d] = v.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function createSavedPanel(ctx) {
  // ctx: { user(), settings(), catById(), forget(id) }
  const f = { tags: new Set(), date: "any", from: "", to: "", sort: "new" };
  let items = [];
  let view = viewPref.get() === "cards" ? "cards" : "list";

  function catLabel(c) {
    const cat = ctx.catById()[c];
    return cat ? `${cat.emoji} ${cat.label}` : c;
  }

  function visibleItems() {
    // Mature sites follow the off-by-default setting here too.
    return ctx.settings().mature ? items : items.filter((s) => !s.categories.includes(MATURE));
  }

  function dateWindow() {
    const now = new Date();
    if (f.date === "today") return [startOfDay(now), null];
    if (["7", "30", "365"].includes(f.date)) return [new Date(now - Number(f.date) * DAY), null];
    if (f.date === "custom") {
      const from = parseDateInput(f.from);
      const to = parseDateInput(f.to);
      return [from, to && new Date(+to + DAY)];  // "to" includes that whole day
    }
    return [null, null];
  }

  function filtered() {
    const [from, to] = dateWindow();
    const list = visibleItems().filter((s) => {
      if (f.tags.size && !s.categories.some((c) => f.tags.has(c))) return false;
      if (from && s.savedAt < from) return false;
      if (to && s.savedAt >= to) return false;
      return true;
    });
    list.sort((a, b) => (f.sort === "old" ? a.savedAt - b.savedAt : b.savedAt - a.savedAt));
    return list;
  }

  function filtersActive() {
    return f.tags.size > 0 || f.date !== "any";
  }

  function toggleTag(c) {
    f.tags.has(c) ? f.tags.delete(c) : f.tags.add(c);
    render();
  }

  // ---------------------------------------------------------------- rendering

  function renderTags(pool) {
    const counts = {};
    for (const s of pool) for (const c of s.categories) counts[c] = (counts[c] || 0) + 1;
    for (const c of f.tags) if (!counts[c]) f.tags.delete(c);  // e.g. mature after turning it off
    const tags = Object.keys(counts).sort((a, b) => counts[b] - counts[a] || catLabel(a).localeCompare(catLabel(b)));
    $("saved-tags").replaceChildren(...tags.map((c) => el("button", {
      type: "button",
      class: `chip small ${f.tags.has(c) ? "on" : ""} ${c === MATURE ? "mature" : ""}`,
      "aria-pressed": String(f.tags.has(c)),
      onclick: () => toggleTag(c),
    }, `${catLabel(c)} `, el("span", { class: "count" }, counts[c]))));
  }

  function tagEls(s) {
    return s.categories.map((c) => el("button", {
      type: "button", class: `tag ${c === MATURE ? "mature" : ""} ${f.tags.has(c) ? "on" : ""}`,
      title: f.tags.has(c) ? "Remove this tag filter" : "Show only sites with this tag",
      onclick: () => toggleTag(c),
    }, catLabel(c)));
  }

  function removeBtn(s) {
    return el("button", {
      type: "button", class: "icon-btn small", title: "Remove from saved", "aria-label": `Remove ${s.title}`,
      onclick: async () => {
        await deleteDoc(doc(db, "users", ctx.user().uid, "saved", s.id));
        items = items.filter((x) => x.id !== s.id);
        ctx.forget(s.id);
        render();
      },
    }, "✕");
  }

  function favicon(s, size = 16) {
    return el("img", {
      class: "fav", src: faviconFor(s.url), width: size, height: size, alt: "", loading: "lazy",
      onerror: (e) => { e.currentTarget.style.visibility = "hidden"; },
    });
  }

  function listItem(s) {
    return el("li", { class: "saved-row" },
      favicon(s),
      el("div", { class: "saved-main" },
        el("a", { href: s.url, target: "_blank", rel: "noopener", class: "saved-title" }, s.title),
        el("div", { class: "saved-sub" },
          el("span", { class: "muted small" }, `${domainOf(s.url)} · Added ${fmtDate.format(s.savedAt)}`),
          ...tagEls(s))),
      removeBtn(s));
  }

  // A preview that is never blank: the page's screenshot, else its own preview image,
  // else a generated card with its icon, name and topics.
  function placeholder(s) {
    const h = hue(domainOf(s.url));
    const emoji = s.categories.map((c) => ctx.catById()[c]?.emoji).filter(Boolean).slice(0, 3).join(" ");
    return el("div", {
      class: "thumb-fallback",
      style: `--h1:${h};--h2:${(h + 50) % 360}`,
    },
    el("span", { class: "fb-emoji", "aria-hidden": "true" }, emoji || "🧭"),
    el("span", { class: "fb-title" }, s.title),
    el("span", { class: "fb-domain" }, favicon(s, 14), domainOf(s.url)));
  }

  function fillThumb(box, s, manifest) {
    const sources = [];
    const shot = manifest[s.id];
    if (shot?.f) sources.push(`shots/${shot.f}?v=${shot.t || 0}`);
    if (s.image) sources.push(s.image);
    let looked = s.image !== undefined;
    const tryNext = () => {
      const src = sources.shift();
      if (!src) {
        box.replaceChildren(placeholder(s));
        if (!looked) {  // saved before we stored the preview image: ask the site record
          looked = true;
          lookupSite(s.id).then((d) => {
            s.image = d.image || null;
            if (s.image) { sources.push(s.image); tryNext(); }
          });
        }
        return;
      }
      const img = el("img", { src, alt: "", loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
      img.onerror = tryNext;
      img.onload = () => {
        // Some preview images are 1x1 trackers or tiny logos; those aren't a preview.
        if (img.naturalWidth < 80 || img.naturalHeight < 50) tryNext();
      };
      box.replaceChildren(img);
    };
    tryNext();
  }

  function cardItem(s, manifest) {
    const thumb = el("a", { class: "thumb", href: s.url, target: "_blank", rel: "noopener", tabindex: "-1", "aria-hidden": "true" });
    fillThumb(thumb, s, manifest);
    return el("li", { class: "saved-card" },
      thumb,
      el("div", { class: "card-body" },
        el("div", { class: "card-title-row" },
          favicon(s),
          el("a", { href: s.url, target: "_blank", rel: "noopener", class: "saved-title" }, s.title),
          removeBtn(s)),
        el("div", { class: "muted small" }, `${domainOf(s.url)} · Added ${fmtDate.format(s.savedAt)}`),
        el("div", { class: "card-tags" }, ...tagEls(s))));
  }

  async function render() {
    const pool = visibleItems();
    renderTags(pool);
    const list = filtered();
    const hiddenMature = items.length - pool.length;

    for (const b of document.querySelectorAll("#saved-dlg .seg button")) {
      b.setAttribute("aria-pressed", String(b.dataset.view === view));
    }
    $("saved-list").className = `saved-list ${view}`;
    $("saved-range").hidden = f.date !== "custom";
    $("saved-clear").hidden = !filtersActive();
    $("saved-filters").hidden = !pool.length;

    const parts = [];
    if (pool.length) {
      parts.push(list.length === pool.length
        ? `${pool.length} saved ${pool.length === 1 ? "site" : "sites"}`
        : `Showing ${list.length} of ${pool.length} saved sites`);
    }
    if (hiddenMature) parts.push(`${hiddenMature} mature ${hiddenMature === 1 ? "site" : "sites"} hidden (Settings)`);
    $("saved-count").textContent = parts.join(" · ");

    if (!items.length) {
      $("saved-list").replaceChildren(el("li", { class: "muted empty" }, "Nothing saved yet. Hit ☆ on a site you love."));
      return;
    }
    if (!list.length) {
      $("saved-list").replaceChildren(el("li", { class: "muted empty" },
        pool.length ? "No saved sites match these filters. " : "",
        pool.length ? el("button", { type: "button", class: "linkish", onclick: clearFilters }, "Clear filters") : ""));
      return;
    }
    const manifest = view === "cards" ? await shotsManifest() : {};
    $("saved-list").replaceChildren(...list.map((s) => (view === "cards" ? cardItem(s, manifest) : listItem(s))));
  }

  function clearFilters() {
    f.tags.clear();
    f.date = "any";
    f.from = f.to = "";
    $("saved-date").value = "any";
    $("saved-from").value = $("saved-to").value = "";
    render();
  }

  // ---------------------------------------------------------------- wiring

  for (const b of document.querySelectorAll("#saved-dlg .seg button")) {
    b.onclick = () => { view = b.dataset.view; viewPref.set(view); render(); };
  }
  $("saved-date").onchange = (e) => { f.date = e.target.value; render(); };
  $("saved-from").onchange = (e) => { f.from = e.target.value; render(); };
  $("saved-to").onchange = (e) => { f.to = e.target.value; render(); };
  $("saved-sort").onchange = (e) => { f.sort = e.target.value; render(); };
  $("saved-clear").onclick = clearFilters;

  return {
    async open() {
      $("saved-list").replaceChildren(el("li", { class: "muted empty" }, "Loading…"));
      $("saved-dlg").showModal();
      const snap = await getDocs(query(collection(db, "users", ctx.user().uid, "saved"), orderBy("savedAt", "desc")));
      items = snap.docs.map((d) => {
        const s = d.data({ serverTimestamps: "estimate" });
        return {
          id: d.id,
          url: s.url,
          title: s.title || domainOf(s.url),
          categories: s.categories || [],
          savedAt: s.savedAt?.toDate ? s.savedAt.toDate() : new Date(),
          image: s.image,  // undefined for saves made before images were stored
        };
      });
      // One entry per domain (older saves could hold two pages of the same site); the
      // list is newest first, so the most recent save is the one kept.
      const seenDomains = new Set();
      items = items.filter((s) => !seenDomains.has(domainOf(s.url)) && seenDomains.add(domainOf(s.url)));
      render();
    },
  };
}
