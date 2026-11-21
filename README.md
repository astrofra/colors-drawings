# colors-drawings

Archive des [dessins de fra dans la galerie Colors!](https://www.colorslive.com/artist/fra), et lecteur C permettant de rejouer leurs gestes dans une vidéo. Les **11 dessins publics** ont été récupérés : dix dessins Nintendo DS de novembre 2007 et « Mars », dessiné sur iPhone en mai 2009. Ils contiennent **287 040 commandes**.

Les originaux sont dans [`archive/fra/`](archive/fra/) : un dossier par identifiant, avec le fichier `drawing.drw`, le PNG original, l’aperçu JPEG, les métadonnées JSON, les réponses du site et les informations HTTP de téléchargement. [`catalog.json`](archive/fra/catalog.json) rassemble les titres, dates UTC, descriptions, orientations, dimensions et en-têtes DRW. [`SHA256SUMS`](archive/fra/SHA256SUMS) couvre tous les fichiers de l’archive, y compris les métadonnées.

## Rejouer les dessins

Prérequis : Python **3.10+**, un compilateur C11 (`cc`, Clang ou GCC), **ffmpeg** avec l’encodeur `libx264`. Aucun paquet Python supplémentaire ni accès au site n’est nécessaire pour rejouer l’archive.

```sh
python3 tools/archive.py verify
python3 tools/archive.py list
python3 tools/archive.py render all
```

La compilation du [lecteur C](src/replay.c) est automatique. Python relie sa sortie RGB24 à l’entrée standard de ffmpeg : **aucune séquence d’images intermédiaires n’est stockée**. Chaque commande est exécutée dans l’ordre, y compris les variations de pression et les retournements du canevas. La rotation de présentation de « Mars » est appliquée avant l’encodage.

Chaque export produit dans `videos/` : un MP4 H.264, un PNG de l’état final et un JSON de provenance du rendu. Ces exports sont conservés dans Git avec les originaux et les outils ; le binaire compilé et l’environnement Python local sont ignorés. Les 11 MP4 archivés font 62 secondes chacun, à 30 images/s, en largeur 512 pixels.

```sh
# Un dessin, largeur affichée 1024, 90 secondes de progression + 3 secondes de pause.
python3 tools/archive.py render 1633 --width 1024 --seconds 90 --hold 3

# Plusieurs dessins, dans un autre dossier.
python3 tools/archive.py render 1633 18774 --output exports --width 512

# Une image par commande, sans regroupement ; --seconds est alors ignoré.
python3 tools/archive.py render 1664 --every-command --hold 0
```

`--fps` règle la cadence, `--crf` la qualité H.264 (18 par défaut), et `--overwrite` autorise le remplacement d’un export existant. Le réglage normal regroupe plusieurs commandes entre deux images, sans supprimer de gestes. Le mode `--every-command` produit exactement `nombre_de_commandes + 1` images avant la pause finale, dont la première est le canevas blanc.

Les temps de dessin fournis par ces anciens fichiers valent zéro : la vitesse de la vidéo est choisie à l’export et ne prétend pas restituer les pauses réelles de l’artiste.

## Fidélité du rendu

Il s’agit d’une **reconstruction logicielle**, avec les positions, couleurs, tailles, pressions et brosses enregistrées. Les détails de rasterisation et de mélange peuvent différer du moteur historique. Le PNG final du site est conservé séparément et n’est jamais substitué à la reconstruction dans la vidéo.

Les états finaux des 11 dessins ont été comparés aux PNG du site : l’erreur absolue moyenne RGB est comprise entre **1,68 et 6,43 sur 255** à la résolution de validation. Ces chiffres mesurent un écart de pixels, pas un pourcentage de fidélité. Voir la [planche de comparaison](docs/validation/comparison.jpg), les [mesures](docs/validation/metrics.json) et la [documentation du format](docs/FORMAT.md).

## Télécharger à nouveau la galerie

La découverte utilise un navigateur Chromium sans fenêtre, car le site calcule les noms des fichiers CDN dans son application WebAssembly. Les téléchargements des dessins sont ensuite effectués **un par un**, par Python, avec reprise des fichiers déjà archivés et quelques tentatives supplémentaires en cas d’erreur réseau.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-sync.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python tools/archive.py sync
```

`--browser /chemin/vers/chromium` permet de réutiliser un navigateur installé. Aucun compte Colors! n’est requis. Le script consulte les API publiques de la galerie, conserve leurs réponses et vérifie que les deux listes publiques concordent. Une synchronisation prépare une nouvelle copie dans un dossier temporaire, la vérifie, puis remplace l’archive précédente. Elle refuse de recalculer les empreintes d’une archive existante endommagée. La découverte dépend de l’interface du site observée le 21 novembre 2026 ; le rendu local demeure autonome si elle change ou disparaît.

## Vérifications

```sh
python3 -m unittest discover -s tests -v

# Facultatif : recréer la comparaison visuelle (nécessite Pillow).
python3 -m pip install Pillow
python3 tools/compare.py
```

Les tests contrôlent la progression d’un trait jusqu’à son dernier point, la pression, le rejet des fichiers invalides avant toute image, l’indépendance de l’état final par rapport à la cadence et la lecture des 11 originaux. Les exports ont également été contrôlés avec `ffprobe` : H.264, 1 860 images, 62 secondes, orientation attendue.

Le code du dépôt est sous la [licence MIT](LICENSE). Les textures de brosses et la référence de décodage sont créditées dans [`third_party/`](third_party/README.md). Les originaux et les métadonnées sont conservés tels que téléchargés, avec leurs indications d’auteur.
