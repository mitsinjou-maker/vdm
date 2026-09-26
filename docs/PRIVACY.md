# Politique de confidentialité — extension « VDM — capture de vidéos »

*Dernière mise à jour : 26 septembre 2026* · [English version below](#privacy-policy--vdm-video-capture-extension)

## En résumé

L'extension **ne collecte, ne stocke sur un serveur et ne partage aucune donnée**. Elle ne communique qu'avec le logiciel VDM installé **sur votre propre ordinateur**, à l'adresse locale `http://127.0.0.1:9614`. Rien n'est envoyé à l'auteur de l'extension ni à un tiers.

## Données manipulées, et pourquoi

| Donnée | Utilisation | Où elle va |
|---|---|---|
| Adresses (URL) des vidéos et fichiers détectés ou choisis | Afficher la liste dans la popup, lancer le téléchargement | Mémoire de session du navigateur ; VDM sur votre ordinateur quand vous téléchargez |
| En-têtes `Referer` et `Origin` envoyés par le navigateur pour ces médias | Télécharger comme le navigateur (certains lecteurs les vérifient) | VDM sur votre ordinateur |
| Cookies du site du fichier téléchargé | Accéder aux fichiers réservés aux membres (espace client, cours…) | VDM sur votre ordinateur, uniquement pour ce téléchargement |
| Titre de l'onglet | Donner un nom lisible au fichier | VDM sur votre ordinateur |
| Vos réglages (langue audio, sous-titres, règles de capture) | Retenir vos choix | Stockage local du navigateur |

- Ces données ne sont lues **que** lorsqu'une vidéo est détectée ou lorsque vous demandez un téléchargement.
- La liste des médias détectés est effacée quand vous quittez la page ou fermez l'onglet.
- **Navigation privée** : les téléchargements n'y sont jamais capturés.
- Le logiciel VDM conserve la file d'attente (adresses, en-têtes, cookies du téléchargement) dans `%APPDATA%\vdm\queue.json` **sur votre ordinateur**, jusqu'à ce que vous retiriez le téléchargement de la liste.

## Ce que l'extension ne fait pas

- Aucune statistique, aucun traçage, aucune publicité.
- Aucune vente ni transmission de données à des tiers.
- Aucun envoi vers Internet : la seule connexion établie par l'extension est celle vers `127.0.0.1` (votre ordinateur).

## Code source et contact

Le code est public et consultable : <https://github.com/mitsinjou-maker/vdm>. Pour toute question, ouvrez une *issue* sur ce dépôt.

---

# Privacy policy — “VDM video capture” extension

*Last updated: September 26, 2026*

## Summary

The extension **does not collect, store on any server, or share any data**. It only talks to the VDM application installed **on your own computer**, at the local address `http://127.0.0.1:9614`. Nothing is ever sent to the extension author or to any third party.

## Data handled, and why

| Data | Purpose | Where it goes |
|---|---|---|
| URLs of detected or selected media and files | Show them in the popup, start the download | Browser session storage; VDM on your computer when you download |
| `Referer` and `Origin` headers the browser sent for that media | Download the way the browser does (some players check them) | VDM on your computer |
| Cookies of the downloaded file's site | Access member-only files (customer area, courses…) | VDM on your computer, for that download only |
| Tab title | Give the file a readable name | VDM on your computer |
| Your settings (audio language, subtitles, capture rules) | Remember your choices | Browser local storage |

- This data is only read when media is detected or when you ask for a download.
- The list of detected media is cleared when you leave the page or close the tab.
- **Private browsing**: downloads are never captured.
- The VDM application keeps its queue (URLs, headers, cookies of the download) in `%APPDATA%\vdm\queue.json` **on your computer**, until you remove the download from the list.

## What the extension does not do

- No analytics, no tracking, no ads.
- No selling or sharing of data with third parties.
- No transfer over the Internet: the only connection the extension makes is to `127.0.0.1` (your computer).

## Source code and contact

The code is public: <https://github.com/mitsinjou-maker/vdm>. For any question, open an issue on this repository.
