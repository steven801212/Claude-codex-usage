"use strict";
const REFRESH_MS = 60_000;
let data = null;
let skewMs = 0; // serverTime - clientTime

const $ = (sel, root = document) => root.querySelector(sel);

function fmtCountdown(ms) {
  if (ms <= 0) return "已重置，等待更新";
  const s = Math.floor(ms / 1000);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600),
        m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  if (d > 0) return `${d} 天 ${h} 小時 ${pad(m)} 分`;
  return `${h}:${pad(m)}:${pad(sec)}`;
}

function fmtAbs(iso) {
  const d = new Date(iso);
  return d.toLocaleString("zh-TW", { weekday: "short", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}

function fmtAgo(iso) {
  if (!iso) return "從未成功";
  const s = Math.max(0, Math.round((Date.now() + skewMs - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s} 秒前`;
  if (s < 3600) return `${Math.floor(s / 60)} 分鐘前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小時前`;
  return `${Math.floor(s / 86400)} 天前`;
}

function render() {
  if (!data) return;
  for (const [name, p] of Object.entries(data.providers)) {
    const card = $(`.card[data-provider="${name}"]`);
    if (!card) continue;
    card.classList.toggle("is-stale", !!p.stale);
    $(".badge.stale", card).hidden = !p.stale;
    const plan = $(".plan", card);
    plan.hidden = !p.plan; plan.textContent = p.plan || "";
    const err = $(".err", card);
    err.hidden = !p.error; err.textContent = p.error ? `錯誤：${p.error}` : "";
    $(".updated", card).textContent = `最後成功：${fmtAgo(p.last_success)}`;

    for (const key of ["five_hour", "weekly"]) {
      const m = $(`.meter[data-window="${key}"]`, card);
      const w = p.windows && p.windows[key];
      const pct = w && typeof w.used_percent === "number" ? w.used_percent : null;
      $(".pct", m).textContent = pct === null ? "—" : `${Math.round(pct)}%`;
      $(".bar i", m).style.width = pct === null ? "0" : `${Math.min(100, Math.max(0, pct))}%`;
      m.classList.toggle("danger", pct !== null && pct > 95);
      m.classList.toggle("warn", pct !== null && pct > 80 && pct <= 95);
      m.dataset.reset = w && w.resets_at ? w.resets_at : "";
    }
  }
  tick();
}

function tick() {
  const now = Date.now() + skewMs;
  document.querySelectorAll(".meter").forEach((m) => {
    const el = $(".reset", m);
    const iso = m.dataset.reset;
    if (!iso) { el.textContent = data ? "無重置資訊" : "—"; return; }
    el.textContent = `重置倒數 ${fmtCountdown(new Date(iso).getTime() - now)} · ${fmtAbs(iso)}`;
  });
  if (data) {
    document.querySelectorAll(".card").forEach((card) => {
      const p = data.providers[card.dataset.provider];
      if (p) $(".updated", card).textContent = `最後成功：${fmtAgo(p.last_success)}`;
    });
  }
}

async function load(method = "GET") {
  const btn = $("#refresh");
  btn.classList.add("spin");
  try {
    const url = method === "POST" ? "/api/refresh" : "/api/usage";
    const r = await fetch(url, { method, cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    data = await r.json();
    skewMs = new Date(data.server_time).getTime() - Date.now();
    $("#status").textContent = `已更新 ${new Date().toLocaleTimeString("zh-TW", { hour12: false })} · 每 60 秒自動刷新`;
    render();
  } catch (e) {
    $("#status").textContent = `無法連線到伺服器（${e.message}），顯示的是舊資料`;
    document.querySelectorAll(".card").forEach((c) => { c.classList.add("is-stale"); $(".badge.stale", c).hidden = false; });
  } finally {
    btn.classList.remove("spin");
  }
}

$("#refresh").addEventListener("click", () => load("POST"));
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
load();
setInterval(load, REFRESH_MS);
setInterval(tick, 1000);

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
