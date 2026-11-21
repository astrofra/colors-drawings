# Formats DRW de cette collection

Cette implémentation est limitée aux versions **1002 (Nintendo DS)** et **1071 (iPhone)** effectivement présentes dans l’archive. Elle refuse les autres versions, brosses ou opérations qu’elle ne sait pas interpréter. Elle ne constitue pas un lecteur général des fichiers Colors! 3D ou Colors Live actuels.

## En-tête

Les nombres sont en little endian. L’en-tête fait **64 octets**, suivi de `command_count` mots de 32 bits. Le fichier doit mesurer exactement `64 + 4 × command_count` octets. La signature est `!loC`, soit le mot `0x436f6c21`.

| Position | Contenu observé |
|---:|---|
| 0 | Signature |
| 4 | Version du format, 1002 ou 1071 |
| 8 | Version originale de Colors! |
| 12 | Dernière version ayant enregistré le fichier |
| 16 | Nombre de traits |
| 20 | Temps enregistré ; zéro dans cette collection |
| 24 | Nombre d’enregistrements |
| 28 | Plateforme : 0 = DS, 3 = iPhone |
| 32 | Rapport de dimensions flottant ; zéro dans les fichiers DS |
| 36–56 | Mots réservés, conservés sans interprétation |
| 60 | Nombre de commandes |

Il ne faut pas appliquer directement l’en-tête de 320 octets des versions plus récentes à ces fichiers : cela supprimerait les 64 premières commandes.

## Commandes

Les deux bits de poids faible désignent le type. Les coordonnées sont normalisées séparément selon la largeur et la hauteur du canevas de travail. Elles peuvent sortir du canevas ; les brosses sont alors découpées aux limites.

| Type | Champs |
|---:|---|
| 0 : point | pression = bits 2–9 ; x = bits 10–20 ; y = bits 21–31 |
| 1 : fin de trait | En DS 1002, bits 2–9 = pression du dernier point ; les autres bits sont nuls. En 1071, valeur observée = 1. |
| 2 : couleur / miroir | bleu = bits 2–9 ; vert = 10–17 ; rouge = 18–25 ; miroir horizontal = bit 26 ; vertical = bit 27 |
| 3 : brosse | taille = bits 2–17 ; les champs suivants dépendent de la version |

Position d’un point : `x_pixels = (x - 512) × largeur / 1024`, `y_pixels = (y - 512) × hauteur / 1024`. Pression = `pression / 255`. Rayon nominal = `taille × largeur / 65535`.

| Version | Contrôle de pression | Type de brosse | Opacité |
|---:|---|---|---|
| 1002 | bits 18–19 | bits 20–21 | bits 22–29, divisés par 255 |
| 1071 | bits 18–20 | bits 21–23 | bits 24–31, divisés par 255 |

Contrôles utilisés : 0 = taille/opacité fixes ; 1 = pression sur l’opacité ; 2 = pression sur la taille ; 3 = pression sur les deux. Brosses utilisées : 0 = dure, 1 = douce. Les bits 2–9 d’une fin de trait DS ne désignent **pas** un calque.

Ces différences ont été vérifiées sur les fichiers de la galerie et par comparaison des états finaux aux PNG conservés. La [référence de James Daniel](https://github.com/jaames/colors-drw-parser/blob/723bda7a4deef490d887ebd1694eef2a42d4caf0/DrwParser.ts) documente notamment le flux de commandes plus récent ; les décalages de la version 1002 ont été établis à partir des originaux archivés.

## Rasterisation et limites

Le lecteur C interpole les positions et la pression entre deux points, puis applique des empreintes de brosse dont les masques alpha sont intégrés au binaire. L’interpolation des textures est bilinéaire. Un masque de couverture par trait conserve, pour chaque pixel, la couverture maximale atteinte et la compose sur l’état du canevas au début du trait. Cela évite de superposer artificiellement l’opacité à chaque échantillon du même geste.

C’est un modèle reconstruit. L’anticrénelage des très petits traits, la réponse de pression, le mélange des brosses douces, l’espacement des empreintes et les arrondis peuvent différer de Colors!. Les dimensions sont déduites du PNG original ; la rotation de présentation vient du champ `o` de la galerie historique. « Mars » a une rotation de trois quarts de tour dans le sens horaire, appliquée par ffmpeg. Les gestes restent dans le repère de dessin pendant la reconstruction.

Le programme ne lit jamais le PNG de référence pour produire ses images. Il ne prétend pas retrouver les temps d’inactivité perdus. Les deux modes d’export appliquent tous les mots du flux : le mode standard les répartit entre un nombre choisi d’images ; `--every-command` expose un état après chaque commande.

## Acquisition et provenance

Le 21 novembre 2026, le site public utilise un WebSocket `wss://www.colorslive.com/_1275`. Les requêtes `init`, `artist.get` et `painting.get` fournissent le profil, la liste des identifiants et les métadonnées, y compris les commentaires. Les réponses complètes sont enregistrées dans `discovery.json` ; les métadonnées de chaque dessin sont aussi disponibles individuellement.

L’interface historique `/_1231/artist/fra` fournit une seconde liste et les rotations. `/_1231/details/IDENTIFIANT` fournit la description de la page, ses commentaires et ses champs d’affichage. Les octets des réponses sont conservés dans `gallery.json` et les fichiers `details.json`.

L’application calcule les noms CDN du type `IDENTIFIANT-SUFFIXE`. Le script observe ses requêtes d’images publiques avec Playwright et réutilise exactement les noms découverts pour récupérer `/drw/…drw`, `/png/…png` et `/jpg_1280x720/…jpg`. Les originaux déjà présents sont vérifiés avant synchronisation puis recopiés sans modification. `SHA256SUMS` couvre l’intégralité de l’archive résultante, hors le manifeste lui-même.

## Interface C autonome

```sh
cc -std=c11 -O3 -Wall -Wextra -Wpedantic src/replay.c -lm -o build/replay
build/replay archive/fra/1633/drawing.drw 512 384 1800 60 final.ppm \
  | ffmpeg -f rawvideo -pixel_format rgb24 -video_size 512x384 -framerate 30 \
    -i pipe:0 -an -c:v libx264 -crf 18 -pix_fmt yuv420p dessin.mp4
```

Les arguments du lecteur sont : fichier, largeur, hauteur, nombre d’images de progression, nombre d’images de pause, puis éventuellement un PPM final. La première image est blanche ; la dernière image de progression contient toutes les commandes. stdout est réservé aux pixels, stderr aux diagnostics. Python se charge de vérifier séparément le statut du lecteur et celui de ffmpeg, d’ajouter les métadonnées à la vidéo et de corriger l’orientation de présentation.
