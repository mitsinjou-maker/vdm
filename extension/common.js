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

// direct = lien vers le fichier média lui-même ; media = média repéré sur le réseau
// (fichier ou flux .m3u8/.mpd). Dans ces deux cas on joint les cookies du site, souvent
// nécessaires (vidéos réservées aux abonnés d'une plateforme de cours…).
// seen = en-têtes réellement envoyés par le navigateur pour ce média : prioritaires.
// Pour une page, yt-dlp se débrouille seul.
export async function sendToVdm({ url, referer, headers: seen, title, quality, direct, media, playlist,
                                  queue, audio_lang, subs, subs_auto, subs_embed }) {
  const headers = { "User-Agent": navigator.userAgent };
  if (referer) headers.Referer = referer;
  Object.assign(headers, seen || {});
  if ((direct || media) && !headers.Cookie) {
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
