// Détecte les médias qui transitent sur le réseau (comme le « bouton de téléchargement » d'IDM)
// et ajoute des entrées au menu clic droit.
import { api, captureSettings, sendToVdm } from "./common.js";

const MEDIA_TYPE = /^(video\/|audio\/|application\/(x-mpegurl|vnd\.apple\.mpegurl|dash\+xml))/i;
const MEDIA_EXT = /\.(mp4|m4v|webm|mkv|mov|avi|flv|mp3|m4a|ogg|opus|wav|flac|m3u8|mpd)(\?|$)/i;
const MANIFEST = /\.(m3u8|mpd)(\?|$)|mpegurl|dash\+xml/i;
// fragments de flux HLS/DASH : seul le manifeste (.m3u8/.mpd) nous intéresse
const FRAGMENT = /\.(ts|m4s|m4f|cmfv|cmfa)(\?|$)|[/_-](seg|segment|frag|fragment|chunk)[-_]?\d+/i;
// lecteurs qui découpent la vidéo en plages (YouTube…) : passer par le bouton « page »
const RANGED_HOSTS = /(googlevideo\.com|fbcdn\.net|cdninstagram\.com)$/i;
const MIN_SIZE = 300 * 1024;
const MAX_ITEMS = 40;

// --- stockage par onglet (survit à la mise en veille du service worker) ------

let queue = Promise.resolve();
function withTab(tabId, fn) {
  queue = queue.then(async () => {
    const key = `tab_${tabId}`;
    const items = (await chrome.storage.session.get(key))[key] || [];
    const next = fn(items);
    if (next) {
      await chrome.storage.session.set({ [key]: next });
      chrome.action.setBadgeText({ tabId, text: next.length ? String(next.length) : "" });
    }
  }).catch(console.error);
  return queue;
}

function clearTab(tabId) {
  withTab(tabId, () => []);
}

chrome.action.setBadgeBackgroundColor({ color: "#1f7ae0" });

// En-têtes que le navigateur envoie réellement pour chaque requête (Referer, Origin,
// cookies) : un lecteur intégré dans une page envoie ceux de son propre cadre, pas ceux
// de la page principale. VDM les réutilise tels quels pour télécharger comme le navigateur.
const KEPT_HEADERS = ["referer", "origin", "cookie"];
const sentHeaders = new Map(); // requestId -> { Referer, Origin, Cookie }

chrome.webRequest.onSendHeaders.addListener(
  (details) => {
    if (details.tabId < 0) return;
    const kept = {};
    for (const h of details.requestHeaders || []) {
      const name = h.name.toLowerCase();
      if (KEPT_HEADERS.includes(name) && h.value) {
        kept[name === "cookie" ? "Cookie" : name[0].toUpperCase() + name.slice(1)] = h.value;
      }
    }
    sentHeaders.set(details.requestId, kept);
    if (sentHeaders.size > 500) sentHeaders.delete(sentHeaders.keys().next().value); // borne mémoire
  },
  { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other", "object"] },
  // Chrome ne montre Referer et Cookie qu'avec « extraHeaders » ; Firefox les donne toujours
  // et refuserait cette option inconnue : on ne l'ajoute que si le navigateur la connaît.
  chrome.webRequest.OnSendHeadersOptions?.EXTRA_HEADERS
    ? ["requestHeaders", "extraHeaders"]
    : ["requestHeaders"],
);

for (const event of [chrome.webRequest.onCompleted, chrome.webRequest.onErrorOccurred]) {
  event.addListener((d) => sentHeaders.delete(d.requestId), { urls: ["<all_urls>"] });
}

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (details.tabId < 0 || details.statusCode >= 400) return;
    const headers = {};
    for (const h of details.responseHeaders || []) headers[h.name.toLowerCase()] = h.value || "";
    const type = (headers["content-type"] || "").split(";")[0].trim().toLowerCase();
    const url = details.url;
    if (!(MEDIA_TYPE.test(type) || MEDIA_EXT.test(url))) return;
    if (type.startsWith("text/html")) return;

    let host = "";
    try { host = new URL(url).hostname; } catch { return; }
    if (RANGED_HOSTS.test(host)) return;

    const manifest = MANIFEST.test(url) || MANIFEST.test(type);
    if (!manifest && FRAGMENT.test(url)) return;

    const total = /\/(\d+)$/.exec(headers["content-range"] || "");
    const size = total ? Number(total[1]) : Number(headers["content-length"] || 0);
    if (!manifest && size && size < MIN_SIZE) return;

    const sent = sentHeaders.get(details.requestId) || {};
    // hôte du cadre qui a demandé la vidéo (lecteur intégré) ; affiché dans la popup
    let via = "";
    // initiator : Chrome ; documentUrl (adresse exacte du cadre) : Firefox
    try { via = new URL(sent.Referer || details.documentUrl || details.initiator || "").hostname; } catch {}

    withTab(details.tabId, (items) => {
      if (items.some((i) => i.url === url)) return null;
      let name = host;
      try { name = decodeURIComponent(new URL(url).pathname.split("/").pop() || host); } catch {}
      items.push({ url, type: type || "?", size, manifest, name, via, headers: sent, at: Date.now() });
      return items.slice(-MAX_ITEMS);
    });
  },
  { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other", "object"] },
  ["responseHeaders"],
);

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.url) clearTab(tabId); // navigation vers une nouvelle page
});
chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove(`tab_${tabId}`));

// --- menu clic droit ------------------------------------------------------------

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "vdm-page", title: "Télécharger la vidéo de cette page avec VDM", contexts: ["page"] });
    chrome.contextMenus.create({ id: "vdm-link", title: "Télécharger ce lien avec VDM", contexts: ["link"] });
    chrome.contextMenus.create({ id: "vdm-media", title: "Télécharger ce média avec VDM", contexts: ["video", "audio"] });
  });
});

async function flash(tabId, ok) {
  await chrome.action.setBadgeText({ tabId, text: ok ? "OK" : "ERR" });
  await chrome.action.setBadgeBackgroundColor({ tabId, color: ok ? "#1a9e55" : "#d93025" });
  setTimeout(() => {
    chrome.action.setBadgeBackgroundColor({ tabId, color: "#1f7ae0" });
    withTab(tabId, (items) => items); // réaffiche le compteur
  }, 2500);
}

async function detectedItem(tabId, url) {
  const key = `tab_${tabId}`;
  const items = (await chrome.storage.session.get(key))[key] || [];
  return items.find((i) => i.url === url);
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const page = info.pageUrl || tab?.url;
  // dans un lecteur intégré, l'adresse de référence est celle du cadre, pas de la page
  const frame = info.frameUrl || page;
  let job;
  if (info.menuItemId === "vdm-media" && info.srcUrl && !info.srcUrl.startsWith("blob:")) {
    const seen = await detectedItem(tab.id, info.srcUrl); // en-têtes réellement envoyés, si vus
    job = { url: info.srcUrl, referer: frame, headers: seen?.headers, title: tab?.title, direct: true };
  } else if (info.menuItemId === "vdm-link") {
    // tout lien (zip, pdf, exe, page…) : cookies joints pour les espaces réservés aux membres
    job = { url: info.linkUrl, referer: frame, direct: MEDIA_EXT.test(info.linkUrl), link: true };
  } else if (info.frameUrl && info.frameUrl !== page) {
    // clic dans un lecteur intégré : yt-dlp analyse la page du lecteur (PeerTube, Vimeo…)
    job = { url: info.frameUrl, referer: page, title: tab?.title };
  } else {
    // page entière, ou lecteur en blob: (flux MSE) → yt-dlp analyse la page
    job = { url: page, title: tab?.title };
  }
  try {
    await sendToVdm(job);
    flash(tab.id, true);
  } catch (e) {
    console.warn("VDM :", e);
    flash(tab.id, false);
  }
});

// --- capture des téléchargements du navigateur (comme IDM) ------------------------
// Un téléchargement lancé par Chrome/Firefox est confié à VDM s'il correspond aux règles
// de la popup (extension de fichier ou taille minimale). VDM le reçoit D'ABORD, et celui
// du navigateur n'est annulé qu'ensuite : si VDM est arrêté ou refuse, rien n'est perdu.

function fileExt(name) {
  const m = /\.([a-z0-9]{1,5})$/i.exec(name || "");
  return m ? m[1].toLowerCase() : "";
}

async function shouldCapture(item) {
  const s = await captureSettings();
  if (!s.capture || item.incognito || item.byExtensionId) return null; // navigation privée : jamais
  const url = item.finalUrl || item.url;
  if (!/^https?:\/\//i.test(url)) return null;                          // blob:, data:, file:…
  if ((item.mime || "").startsWith("text/html")) return null;           // page enregistrée
  let name = (item.filename || "").split(/[\/]/).pop();
  if (!name) {
    try { name = decodeURIComponent(new URL(url).pathname.split("/").pop()); } catch { name = ""; }
  }
  const exts = s.captureExts.split(/[\s,;]+/).map((e) => e.replace(/^\./, "").toLowerCase()).filter(Boolean);
  const size = item.totalBytes > 0 ? item.totalBytes : item.fileSize > 0 ? item.fileSize : 0;
  const minBytes = Math.max(0, Number(s.captureMinMB) || 0) * 1024 * 1024;
  const matches = exts.includes(fileExt(name)) || (minBytes > 0 && size >= minBytes);
  return matches ? url : null;
}

chrome.downloads?.onCreated.addListener(async (item) => {
  let url;
  try {
    url = await shouldCapture(item);
    if (!url) return;
    await api("/ping"); // VDM absent : exception, le navigateur continue seul
    await sendToVdm({ url, referer: item.referrer || undefined, direct: true });
  } catch (e) {
    if (url) console.warn("VDM : téléchargement laissé au navigateur :", e);
    return;
  }
  try {
    await chrome.downloads.cancel(item.id);
    await chrome.downloads.erase({ id: item.id }); // retire la ligne annulée de la liste du navigateur
  } catch (e) {
    console.warn("VDM : annulation impossible (déjà terminé ?)", e);
  }
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true }).catch(() => []);
  if (tab) flash(tab.id, true);
});
