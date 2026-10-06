# Basse2Partition

Appli desktop : dépose un fichier audio, choisis un instrument (basse pour
l'instant), récupère un PDF (portée + tablature) et un MIDI de la ligne
transcrite.

## Ce que c'est vraiment

Ce n'est pas un petit utilitaire de quelques Mo : le pipeline embarque
TensorFlow (pour la séparation de pistes) et des modèles de transcription
ML. Compte plusieurs centaines de Mo une fois les dépendances installées,
et de l'ordre de **quelques minutes de calcul par morceau** sur un
ordinateur portable classique (le traitement tourne intégralement en local,
rien n'est envoyé sur un serveur).

## Sur quelle machine builder

Deux scripts sont fournis : `build_mac_app.sh` (macOS) et
`build_windows_app.ps1` (Windows, via Chocolatey). Chacun doit tourner sur
son OS cible — pas de cross-compilation. Une CI GitHub Actions
(`.github/workflows/build.yml`) peut aussi builder les deux automatiquement
sur des runners hébergés par GitHub si tu préfères ne pas builder en local.

## Installation (une fois) — Windows

```powershell
# Dépendances système (Chocolatey -- https://chocolatey.org/install si pas déjà fait)
choco install -y python312 ffmpeg lilypond

# Récupérer le projet
git clone https://github.com/alexlexlexlexlexlex/bass2score.git
cd bass2score

# Environnement Python
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install --no-deps spleeter==2.1.0
pip install --no-deps basic-pitch==0.4.0

# Lancer l'appli
python app.py
```

Sur Windows sans Chocolatey / sans droits admin : récupère les versions
portables de `ffmpeg` et `lilypond` (archives zip, aucune installation) et
ajoute leurs dossiers `bin` au `PATH` de la session avant de lancer l'appli.
`build_windows_app.ps1` sait aussi les télécharger tout seul si `choco` est
absent.

## Installation (une fois) — macOS

```bash
# Dépendances système
brew install python@3.12 python-tk@3.12 ffmpeg lilypond

# Récupérer le projet
git clone https://github.com/alexlexlexlexlexlex/bass2score.git
cd bass2score

# Environnement Python
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install --no-deps spleeter==2.1.0     # voir note dans requirements.txt
pip install --no-deps basic-pitch==0.4.0  # voir note dans requirements.txt

# Lancer l'appli
python app.py
```

`python-tk@3.12` n'est pas optionnel : le Python de Homebrew est livré
sans Tkinter, alors que `app.py` est une interface Tkinter. Sans ce
paquet l'appli s'arrête sur `ModuleNotFoundError: No module named
'tkinter'` avant d'afficher sa fenêtre.

Au premier traitement, le modèle Spleeter (~150 Mo) et le modèle
basic-pitch se téléchargent/chargent automatiquement — connexion internet
nécessaire la première fois seulement.

## Utilisation

1. Glisser un fichier audio dans la zone (ou "Choisir un fichier...")
2. Sélectionner l'instrument
3. "Générer la partition" — la fenêtre reste réactive, le calcul tourne en
   arrière-plan (voir le log en bas)
4. À la fin : boutons pour ouvrir le PDF ou le dossier de résultats
   (`~/Basse2Partition_resultats/<nom_du_morceau>_<date>/`)

## Empaqueter en vraie appli Mac (.app)

Une fois l'installation ci-dessus faite et testée avec `python app.py` :

```bash
./build_mac_app.sh
```

Produit `dist/Basse2Partition.app`, à glisser dans `/Applications`.
**Important** : ce script doit tourner sur un Mac (PyInstaller ne fait pas
de cross-compilation depuis Linux/Windows vers macOS).

`ffmpeg` et `lilypond` sont copiés **dans** le bundle : Homebrew ne sert
qu'à la machine qui builde, et le `.app` produit se double-clique sur un
Mac où rien n'est installé. C'est ce qui alourdit le bundle, et c'est
voulu — l'appli est faite pour être distribuée telle quelle.

## Étendre à d'autres instruments

Le séparateur (Spleeter 4stems) sort déjà 4 pistes : `vocals`, `drums`,
`bass`, `other`. Ajouter un instrument = ajouter une entrée dans
`INSTRUMENT_PROFILES` (`pipeline/transcribe.py`). Deux nuances déjà notées
en commentaire dans le code :
- **drums** : basic-pitch est un modèle de hauteur de note, pas adapté à la
  transcription rythmique/percussion — il faudrait un modèle de détection
  d'onsets dédié.
- **vocals / other** : la ligne "other" (guitare/clavier/synthé) est
  souvent réellement polyphonique (accords) ; l'étape "forcer le
  monophonique" actuelle (adaptée à la basse) ne conviendrait pas telle
  quelle, il faudrait une notation multi-voix.

## Limites à connaître

- La séparation de pistes (Spleeter, 2021) est datée ; Demucs ferait
  généralement mieux mais n'a pas pu être testé dans l'environnement de
  développement (poids du modèle hébergés sur un domaine bloqué). À
  reconsidérer si la qualité de séparation devient un problème en usage
  réel.
- La transcription automatique reste automatique : attends-toi à des
  erreurs sur les passages avec beaucoup de harmoniques/slap ou des notes
  très courtes. Corrige à l'oreille avant usage sérieux.
- Le tempo est estimé automatiquement (détection de beat) ; pour un
  morceau au tempo très irrégulier, la partition peut être mal barrée.
