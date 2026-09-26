import { api, humanSize, sendToVdm } from "./common.js";

const $ = (id) => document.getElementById(id);
const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
let online = false;

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

async function send(button, job) {
  button.disabled = true;
  const label = button.textContent;
  try {
    await sendToVdm({
      ...job,
      quality: $("quality").value,
      playlist: $("playlist").checked,
      audio_lang: $("lang").value,
      subs: $("subs").value.trim(),
      subs_auto: $("subsAuto").checked,
      subs_embed: $("subsEmbed").checked,
      queue: $("scheduled").checked ? "planifiée" : "principale",
    });
    button.textContent = "Ajouté ✓";
    refreshJobs();
  } catch (e) {
    button.textContent = "Échec";
    button.title = e.message;
  }
  setTimeout(() => { button.textContent = label; button.disabled = !online; }, 2000);
}

$("page").addEventListener("click", (e) => send(e.target, { url: tab.url, title: tab.title }));

async function renderMedia() {
  const key = `tab_${tab.id}`;
  const items = (await chrome.storage.session.get(key))[key] || [];
  const list = $("media");
  list.replaceChildren();
  if (!items.length) {
    list.append(el("li", { className: "empty", textContent: "Aucun média détecté. Lancez la lecture de la vidéo, ou utilisez le bouton ci-dessus." }));
    return;
  }
  for (const item of [...items].reverse()) {
    const btn = el("button", { className: "small", textContent: "↓", title: "Télécharger", disabled: !online });
    // un manifeste HLS/DASH passe par yt-dlp ; un fichier direct par le moteur multi-connexions
    // en-têtes réellement envoyés par le navigateur (lecteur intégré compris) + titre de l'onglet
    btn.addEventListener("click", () => send(btn, {
      url: item.url, referer: tab.url, headers: item.headers, title: tab.title,
      direct: !item.manifest, media: true,
    }));
    const pageHost = (() => { try { return new URL(tab.url).hostname; } catch { return ""; } })();
    const meta = [
      item.manifest ? "flux HLS/DASH" : item.type,
      humanSize(item.size),
      item.via && item.via !== pageHost ? `via ${item.via}` : "",
    ].filter(Boolean).join(" · ");
    list.append(el("li", {},
      el("div", { className: "info" },
        el("div", { className: "name", textContent: item.name, title: item.url }),
        el("div", { className: "meta", textContent: meta })),
      btn));
  }
}

async function refreshJobs() {
  const list = $("jobs");
  let data;
  try {
    data = await api("/jobs");
    online = true;
    $("status").className = "ok";
    $("status").textContent = "daemon connecté";
  } catch {
    online = false;
    $("status").className = "off";
    $("status").textContent = "daemon arrêté";
    list.replaceChildren(el("li", { className: "hint" },
      el("span", { innerHTML: "Lancez <code>vdm gui</code> ou <code>vdm daemon</code>." })));
  }
  $("page").disabled = !online;
  document.querySelectorAll("#media button").forEach((b) => { b.disabled = !online; });
  if (!online) return;

  const jobs = data.jobs.sort((a, b) => b.id - a.id).slice(0, 8);
  list.replaceChildren();
  if (!jobs.length) list.append(el("li", { className: "empty", textContent: "File vide." }));
  for (const j of jobs) {
    const pct = j.status === "terminé" ? 100 : j.size ? (100 * j.downloaded) / j.size : 0;
    const parts = [j.status];
    if (j.size) parts.push(`${humanSize(j.downloaded)} / ${humanSize(j.size)}`);
    if (j.status === "en cours" && j.speed) parts.push(`${humanSize(j.speed)}/s`);
    if (j.queue === "planifiée" && j.status !== "terminé") parts.push("⏰");
    if (j.error) parts.push(j.error);
    list.append(el("li", {},
      el("div", { className: "info" },
        el("div", { className: "name", textContent: j.name, title: j.url }),
        el("div", { className: "meta", textContent: parts.join(" · ") }),
        el("div", { className: "bar" }, el("div", { style: `width:${pct.toFixed(1)}%` })))));
  }
}

// langue audio et sous-titres : on retient les derniers choix
const remembered = { lang: "value", subs: "value", subsAuto: "checked", subsEmbed: "checked" };
for (const [id, prop] of Object.entries(remembered)) {
  try {
    const saved = localStorage.getItem(`vdm-${id}`);
    if (saved !== null) $(id)[prop] = prop === "checked" ? saved === "1" : saved;
  } catch {}
  $(id).addEventListener("change", () => {
    try { localStorage.setItem(`vdm-${id}`, prop === "checked" ? ($(id).checked ? "1" : "0") : $(id).value); } catch {}
  });
}

// la page actuelle contient une playlist ? on propose de la prendre en entier
$("playlist").checked = /[?&]list=|\/playlist\b/.test(tab.url || "");

await refreshJobs();
await renderMedia();
setInterval(refreshJobs, 1000);
chrome.storage.session.onChanged.addListener(renderMedia);
