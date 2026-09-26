// Communication avec le daemon VDM (vdm daemon) sur 127.0.0.1.
export const API = "http://127.0.0.1:9614/api";

export async function api(path, body) {
  const res = await fetch(API + path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function cookieHeader(url) {
  try {
    const cookies = await chrome.cookies.getAll({ url });
    return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  } catch {
    return "";
  }
}

// Capture des téléchargements du navigateur (réglable dans la popup)
export const CAPTURE_DEFAULTS = {
  capture: true,       // confier à VDM les téléchargements du navigateur
  captureMinMB: 5,     // … d'au moins cette taille
  captureExts: "zip, rar, 7z, tar, gz, iso, img, exe, msi, apk, dmg, mp4, mkv, avi, mov, mp3, flac, pdf",
};

export async function captureSettings() {
  try {
    return { ...CAPTURE_DEFAULTS, ...(await chrome.storage.local.get(Object.keys(CAPTURE_DEFAULTS))) };
  } catch {
    return { ...CAPTURE_DEFAULTS };
  }
}

// direct = lien vers le fichier lui-même ; media = média repéré sur le réseau
// (fichier ou flux .m3u8/.mpd) ; link = lien choisi au clic droit (zip, pdf, page…).
// Dans ces cas on joint les cookies du site, souvent nécessaires (espace membre,
// plateforme de cours…). seen = en-têtes réellement envoyés par le navigateur : prioritaires.
// Pour une page entière, yt-dlp se débrouille seul.
export async function sendToVdm({ url, referer, headers: seen, title, quality, direct, media, link,
                                  playlist, queue, audio_lang, subs, subs_auto, subs_embed }) {
  const headers = { "User-Agent": navigator.userAgent };
  if (referer) headers.Referer = referer;
  Object.assign(headers, seen || {});
  if ((direct || media || link) && !headers.Cookie) {
    const cookie = await cookieHeader(url);
    if (cookie) headers.Cookie = cookie;
  }
  return api("/add", {
    url, title, quality, headers, playlist, queue,
    // undefined : le gestionnaire applique la langue préférée de ses Options
    audio_lang: direct ? undefined : audio_lang,
    subs: direct ? undefined : subs,
    subs_auto: direct ? undefined : subs_auto,
    subs_embed: direct ? undefined : subs_embed,
    kind: direct ? "http" : "auto",
  });
}

export function humanSize(n) {
  if (!n) return "";
  const units = ["o", "Ko", "Mo", "Go"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}
