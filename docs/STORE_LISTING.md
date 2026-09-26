# Fiches boutique — textes prêts à copier

Pour publier l'extension sur **Firefox Add-ons** (addons.mozilla.org) ou le **Chrome Web Store**.

- Archive à envoyer : `python tools/pack_extension.py` → `dist/vdm-extension-<version>.zip`
- Icônes : `extension/icons/icon-128.png` (icône de la fiche)
- Captures d'écran (1280 × 800) : `docs/screenshots/`
- Politique de confidentialité (lien à indiquer) : <https://github.com/mitsinjou-maker/vdm/blob/main/docs/PRIVACY.md>
- Page d'accueil / assistance : <https://github.com/mitsinjou-maker/vdm> · <https://github.com/mitsinjou-maker/vdm/issues>
- Catégorie : *Téléchargement / Download management* (Firefox) · *Productivité / Outils* (Chrome)
- Licence : MIT

---

## Nom

**VDM — capture de vidéos** (anglais : *VDM — video capture*)

## Description courte (132 caractères max.)

FR : Envoie vidéos et fichiers vers VDM, le gestionnaire de téléchargements multi-connexions installé sur votre PC.

EN : Sends videos and files to VDM, the multi-connection download manager installed on your computer.

## Description longue — français

> **Cette extension accompagne le logiciel gratuit VDM, qui doit être installé et lancé sur votre ordinateur** (Windows, Python) : <https://github.com/mitsinjou-maker/vdm>
>
> VDM est un gestionnaire de téléchargements inspiré d'IDM : plusieurs connexions par fichier, reprise après coupure, file d'attente, catégories automatiques, planificateur et limite de vitesse.
>
> **Avec l'extension :**
> - les vidéos lues sur une page sont détectées (compteur sur l'icône) et téléchargeables en un clic, y compris dans les lecteurs intégrés ;
> - clic droit sur un lien, une vidéo ou une page : « Télécharger avec VDM » ;
> - les téléchargements du navigateur peuvent être confiés automatiquement à VDM (règles réglables : types de fichiers, taille minimale) ; si VDM n'est pas lancé, le navigateur garde la main ;
> - choix de la qualité, de la langue audio et des sous-titres pour les sites vidéo compatibles ;
> - suivi des téléchargements en cours dans la popup.
>
> **Vie privée** : aucune donnée n'est collectée. L'extension ne communique qu'avec VDM, sur votre propre ordinateur (127.0.0.1). Pas de statistiques, pas de publicité. Code source ouvert (MIT).
>
> Les contenus protégés par DRM ne sont pas pris en charge. Ne téléchargez que ce que vous avez le droit de copier.

## Long description — English

> **This extension is a companion to the free VDM application, which must be installed and running on your computer** (Windows, Python): <https://github.com/mitsinjou-maker/vdm>
>
> VDM is an IDM-style download manager: multiple connections per file, resume after interruption, queue, automatic categories, scheduler and speed limit.
>
> **With the extension:**
> - videos playing on a page are detected (counter on the icon) and downloadable in one click, including in embedded players;
> - right-click a link, a video or a page: “Download with VDM”;
> - browser downloads can be handed over to VDM automatically (configurable rules: file types, minimum size); if VDM is not running, the browser keeps the download;
> - quality, audio language and subtitle choice for supported video sites;
> - progress of current downloads in the popup.
>
> **Privacy**: no data is collected. The extension only talks to VDM on your own computer (127.0.0.1). No analytics, no ads. Open source (MIT).
>
> DRM-protected content is not supported. Only download what you have the right to copy.

---

## Justification des permissions (demandée par le Chrome Web Store, utile pour Mozilla)

| Permission | Justification (EN, à copier) |
|---|---|
| `host_permissions: <all_urls>` | Detect video/audio streams played on any site the user visits, and read the headers of those media requests, so they can be downloaded by the local VDM application. |
| `webRequest` | Observe media responses (content type, size) to list downloadable videos, and read the Referer/Origin headers the browser sent for them. No request is blocked or modified. |
| `cookies` | Attach the site's cookies to a download the user explicitly requested, so member-only files can be fetched by the local VDM application. Cookies are only sent to 127.0.0.1. |
| `downloads` | Optionally hand browser downloads matching the user's rules over to VDM (can be turned off in the popup). The browser download is only cancelled after VDM accepted it. |
| `contextMenus` | “Download with VDM” entries on links, videos and pages. |
| `tabs` | Read the active tab's URL and title to download the page's video and name the file. |
| `storage` | Remember the user's settings and the per-tab list of detected media (session storage). |
| Remote code | None. All code is included in the package; the extension only sends HTTP requests to the local VDM application at 127.0.0.1:9614. |

**Single purpose (Chrome)** : *Send videos and files from the browser to the user's locally installed VDM download manager.*

## Note pour les relecteurs (champ « Notes to reviewer »)

> This extension is a companion to a local desktop application (VDM, open source: https://github.com/mitsinjou-maker/vdm). Without VDM running, the popup shows “daemon arrêté” and downloads stay in the browser.
> To test: install VDM (`pip install -e .` in the repository), run `vdm gui`, then open any page with a video (e.g. a PeerTube instance or https://archive.org) or right-click a link → “Télécharger ce lien avec VDM”.
> The source is not minified or bundled; the uploaded package is the `extension/` folder of the repository as-is.

## Attention — Chrome Web Store

Le règlement du Chrome Web Store interdit les extensions permettant de télécharger des vidéos YouTube. En l'état (envoi des pages YouTube à VDM), la publication y serait très probablement refusée. Il faudrait une variante « Chrome Web Store » qui n'envoie pas les pages YouTube (YouTube restant utilisable directement depuis VDM : bouton Ajouter ou `vdm add`). Firefox Add-ons n'a pas cette restriction.
