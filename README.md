# VDM — gestionnaire de téléchargements multi-connexions

![Licence MIT](https://img.shields.io/badge/licence-MIT-blue) ![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue) ![Windows](https://img.shields.io/badge/Windows-10%2F11-0078d4)

Un gestionnaire de téléchargements inspiré d'IDM, en ligne de commande **et** en interface graphique :

- **Téléchargement multi-connexions** : un fichier est découpé en segments téléchargés en parallèle (8 connexions par défaut).
- **Segmentation dynamique** : quand une connexion a fini son segment, elle coupe en deux le segment restant le plus long et en reprend la moitié. Aucune connexion ne reste inactive jusqu'à la fin.
- **Reprise** après pause, Ctrl+C, coupure réseau ou redémarrage du PC (fichiers `.part` + `.vdm`).
- **File d'attente** persistante, nombre de téléchargements simultanés réglable, **limite de vitesse globale** modifiable à chaud.
- **Sites vidéo** (YouTube et [des milliers d'autres](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)) et flux **HLS/DASH** via yt-dlp, avec les fragments téléchargés en parallèle.
- **Catégories automatiques** : chaque fichier est rangé dans un sous-dossier selon son type (Vidéos, Musique, Images, Documents, Compressés, Programmes, Autres).
- **Planificateur** : une « file planifiée » qui ne démarre que pendant une plage horaire (ex. 02:00–07:00, certains jours). Action possible quand la file est terminée : ne rien faire, mettre en veille ou éteindre.
- **Langue de la piste audio** : pour les vidéos doublées (YouTube…), choix de la langue (français, anglais…) ; repli sur la piste d'origine si elle n'existe pas, avec un avertissement.
- **Sous-titres** : choix des langues (`fr,en`…), sous-titres automatiques (générés ou traduits par YouTube), en fichiers `.srt` ou intégrés à la vidéo. Si le site refuse les sous-titres, la vidéo est quand même téléchargée, avec un avertissement. Des sous-titres peuvent aussi être **ajoutés après coup** à une vidéo terminée (autre langue, automatiques), sans la retélécharger.
- **Playlists entières** (YouTube et autres) : chaque vidéo devient un téléchargement séparé, numéroté et rangé dans un sous-dossier au nom de la playlist.
- **Interface graphique** (`vdm gui`) : liste triable, filtres par catégorie, carte des segments, menu clic droit, icône dans la zone de notification.
- **Extension Chrome** : détecte les vidéos lues sur une page (compteur sur l'icône), menu clic droit, popup avec l'avancement.

## Installation

Prérequis : [Python 3.10 ou plus récent](https://www.python.org/downloads/) et [Git](https://git-scm.com/).

```bash
git clone https://github.com/mitsinjou-maker/vdm.git
cd vdm
pip install -e .
```

Mise à jour : quittez VDM, puis dans le dossier `vdm` :

```bash
git pull
pip install -e .
```

ffmpeg (fusion vidéo + audio, conversion MP3) est fourni automatiquement par le paquet `imageio-ffmpeg`. Un ffmpeg déjà installé sur le système est utilisé en priorité.

Les outils dont yt-dlp a besoin pour YouTube sont aussi installés automatiquement : le moteur JavaScript **deno** et le module `yt-dlp-ejs` (sans eux, YouTube refuse une partie des vidéos avec une erreur 403).

### Extension Chrome (ou Edge, Brave…)

1. Ouvrir `chrome://extensions` et activer le **Mode développeur**.
2. Cliquer sur **Charger l'extension non empaquetée** et choisir le dossier `vdm/extension`.
3. Lancer `vdm gui` (ou `vdm daemon`) : l'extension lui envoie les téléchargements.

## Utilisation

### Interface graphique

```bash
vdm gui
```

- **Ajouter** (Ctrl+N) : collez une ou plusieurs URL (une par ligne), choisissez la qualité, la **langue audio** et les **sous-titres** (bouton « Voir les pistes de la vidéo » pour lister ce qui existe vraiment, puis menu « Choisir ▾ »), la file (principale ou planifiée) et, si besoin, un dossier.
- Colonne de gauche : filtres par état et par catégorie. Barre de recherche en haut à droite.
- Clic droit sur un téléchargement : ouvrir, ouvrir le dossier, **télécharger des sous-titres…** (vidéo terminée), pause, reprise, changer de file, copier l'adresse, supprimer (avec ou sans le fichier).
- Panneau du bas : détails et **carte des segments** du téléchargement sélectionné.
- **Planificateur** et **Options** (dossier, catégories, simultanés, connexions, limite de vitesse) dans la barre d'outils.
- Fermer la fenêtre la garde dans la zone de notification (comme IDM) ; *clic droit sur l'icône › Quitter* pour tout arrêter.

L'interface remplace `vdm daemon` : l'extension et les commandes `vdm add/list/pause…` fonctionnent pendant qu'elle est ouverte. Ne lancez pas les deux en même temps.

### Ligne de commande

| Commande | Effet |
|---|---|
| `vdm get URL` | Télécharge tout de suite, avec la carte des segments. Ctrl+C met en pause ; relancer la même commande reprend. |
| `vdm daemon` | Lance le gestionnaire : file d'attente, tableau de bord en direct, API pour l'extension. |
| `vdm add URL [URL…]` | Ajoute à la file. Si le daemon est arrêté, le téléchargement démarrera à son lancement. |
| `vdm list` | Affiche la file. |
| `vdm pause 3` / `vdm pause all` | Met en pause. |
| `vdm resume 3` / `vdm resume all` | Reprend, ou relance après une erreur. |
| `vdm remove 3 [--delete]` | Retire de la liste (et supprime les fichiers avec `--delete`). |
| `vdm add --scheduled URL` | Ajoute à la file planifiée. |
| `vdm add --playlist URL` | URL d'une vidéo dans une playlist : prend toute la playlist (une URL de playlist est toujours prise en entier). |
| `vdm langs URL` | Liste les pistes audio et les sous-titres d'une vidéo. |
| `vdm add --subs fr,en URL` | Télécharge aussi les sous-titres français et anglais (`--auto-subs` : accepter les automatiques ; `--embed-subs` : les intégrer à la vidéo). |
| `vdm add -a fr URL` | Télécharge avec la piste audio française (`-a en`, `-a es`, `-a pt-BR`…). |
| `vdm subs 3` | Liste les sous-titres disponibles pour la vidéo n°3 (déjà téléchargée). |
| `vdm subs 3 fr,en --auto --embed` | Ajoute les sous-titres français et anglais à la vidéo n°3 (`--auto` : accepter les automatiques ; `--embed` : les intégrer, sinon fichiers `.srt` à côté). |
| `vdm move 3 --to planifiée` | Change un téléchargement de file (`principale` ou `planifiée`). |
| `vdm schedule` | Affiche le planning. |
| `vdm schedule on --start 02:00 --stop 07:00 --days lun,mar,mer --after arret` | Active et règle le planificateur (`--stop aucune` : jusqu'à la fin de la file ; `--after rien/veille/arret`). |
| `vdm schedule off` | Désactive le planificateur. |
| `vdm gui` | Ouvre l'interface graphique. |
| `vdm clean` | Retire de la liste les téléchargements terminés. |
| `vdm limit 2M` / `vdm limit 0` | Change la limite de vitesse du daemon en cours. |

Options utiles :

- `-o DOSSIER` : destination précise (sinon `~/Downloads/VDM/<catégorie>`).
- `-n 16` : connexions par fichier.
- `-q 720` : qualité, parmi `best`, `1080`, `720`, `480`, `audio`.
- `--subs fr,en`, `--auto-subs`, `--embed-subs` : sous-titres (sinon : réglages des Options).
- `-a fr` : langue de la piste audio (sinon : langue préférée des Options, ou la piste d'origine).
- `--referer URL` : en-tête Referer à envoyer.
- `--kind http|ytdl` : force le moteur (par défaut, VDM choisit tout seul).
- Pour le daemon : `-p 3` (téléchargements simultanés) et `--limit 2M`.

Exemples :

```bash
vdm get "https://exemple.com/film.mp4" -n 16
vdm get "https://www.youtube.com/watch?v=..." -q 1080
vdm daemon -p 3 --limit 5M
vdm add --scheduled "https://www.youtube.com/playlist?list=..."
vdm schedule on --start 01:00 --stop 07:00
```

Les réglages (dossier, catégories, simultanés, connexions, limite, planning) sont enregistrés dans `%APPDATA%\vdm\config.json`. Les options de `vdm daemon` ne s'appliquent qu'à la session en cours.

## Fonctionnement

```
extension Chrome ──POST /api/add──▶ vdm gui / vdm daemon (127.0.0.1:9614)
                                       │ file d'attente (%APPDATA%\vdm\queue.json)
                                       ├─ fichier direct ─▶ engine.py : N connexions Range + segmentation dynamique
                                       └─ page / HLS / DASH ─▶ ytdl.py : yt-dlp, fragments en parallèle
```

- `vdm/engine.py` : le moteur multi-segments (sonde, découpage, vol de segments, reprise, relance après erreur, réduction du nombre de connexions si le serveur répond 429/503).
- `vdm/manager.py` : la file d'attente, l'ordonnancement, les catégories, la file planifiée, le développement des playlists.
- `vdm/config.py` : réglages persistants et table des catégories.
- `vdm/scheduler.py` : plages horaires et action de fin de file.
- `vdm/gui.py` : l'interface graphique PySide6.
- `vdm/server.py` : l'API locale. Elle refuse les requêtes venant de pages web : seuls la CLI et les extensions sont acceptées.
- `vdm/ytdl.py` : l'adaptateur yt-dlp.
- `extension/` : l'extension Manifest V3.

## Limites

- Le planificateur ne fonctionne que si `vdm gui` ou `vdm daemon` est ouvert à l'heure prévue. Sans heure d'arrêt, la file démarre si l'outil tourne dans l'heure qui suit l'heure de départ.
- L'extinction de fin de file laisse une minute pour annuler (`shutdown /a`).
- En cas d'erreur 403 (accès refusé), VDM redemande des adresses neuves au site et réessaie 2 fois avant d'abandonner ; « Reprendre » relance ensuite là où le téléchargement s'était arrêté.
- YouTube limite le nombre de requêtes : après de nombreux téléchargements rapprochés, il peut refuser les sous-titres traduits (erreur 429) ou demander une vérification anti-robot pendant un moment. Il suffit d'attendre.
- Les contenus protégés par DRM (Netflix, Disney+, Spotify…) ne sont pas téléchargeables. C'est volontaire.
- Téléchargez uniquement des contenus que vous avez le droit de copier.
- Pour les sites qui demandent une connexion, l'extension transmet les cookies du navigateur lorsqu'elle envoie un lien direct vers un fichier média. Ils sont stockés dans `queue.json` jusqu'à ce que le téléchargement soit retiré de la liste.

## Licence

[MIT](LICENSE) : libre d'utiliser, modifier et redistribuer ce code, en conservant la mention de licence.
