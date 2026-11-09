// Firebase bootstrap shared by every page.
import { initializeApp } from "https://www.gstatic.com/firebasejs/11.0.2/firebase-app.js";
import {
  getAuth, GoogleAuthProvider, onAuthStateChanged, signInWithPopup, signOut,
} from "https://www.gstatic.com/firebasejs/11.0.2/firebase-auth.js";
import {
  getFirestore, doc, getDoc,
} from "https://www.gstatic.com/firebasejs/11.0.2/firebase-firestore.js";
import { firebaseConfig } from "./firebase-config.js";

export const configured = !String(firebaseConfig.apiKey).includes("REPLACE_ME");

export const app = configured ? initializeApp(firebaseConfig) : null;
export const auth = configured ? getAuth(app) : null;
export const db = configured ? getFirestore(app) : null;

export function signIn() {
  return signInWithPopup(auth, new GoogleAuthProvider());
}

export function logOut() {
  return signOut(auth);
}

export function watchUser(cb) {
  if (!configured) { cb(null); return () => {}; }
  return onAuthStateChanged(auth, cb);
}

export async function isAdmin(user) {
  if (!user) return false;
  try {
    return (await getDoc(doc(db, "admins", user.uid))).exists();
  } catch {
    return false;
  }
}

let categoriesPromise;
export function loadCategories() {
  categoriesPromise ??= fetch("data/categories.json")
    .then((r) => r.json())
    .then((j) => j.categories.map(({ id, label, emoji }) => ({ id, label, emoji })));
  return categoriesPromise;
}

export function faviconFor(url) {
  try {
    return `https://icons.duckduckgo.com/ip3/${new URL(url).hostname}.ico`;
  } catch {
    return "";
  }
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function safeStorage(key, fallback) {
  return {
    get() {
      try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; }
    },
    set(v) {
      try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* private mode */ }
    },
  };
}
