// Détecte les médias qui transitent sur le réseau (comme le « bouton de téléchargement » d'IDM)
// et ajoute des entrées au menu clic droit.
import { sendToVdm } from "./common.js";

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

    withTab(details.tabId, (items) => {
      if (items.some((i) => i.url === url)) return null;
      const name = decodeURIComponent(new URL(url).pathname.split("/").pop() || host);
      items.push({ url, type: type || "?", size, manifest, name, at: Date.now() });
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

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const page = info.pageUrl || tab?.url;
  let job;
  if (info.menuItemId === "vdm-media" && info.srcUrl && !info.srcUrl.startsWith("blob:")) {
    job = { url: info.srcUrl, referer: page, direct: true };
  } else if (info.menuItemId === "vdm-link") {
    job = { url: info.linkUrl, referer: page, direct: MEDIA_EXT.test(info.linkUrl) };
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
