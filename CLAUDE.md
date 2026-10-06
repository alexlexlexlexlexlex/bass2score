# CLAUDE.md

Appli desktop Python/Tkinter : un fichier audio entre, un PDF (portée +
tablature) et un MIDI sortent. Tout le calcul est local, quelques minutes
par morceau.

Le README couvre l'installation et l'usage. Ce fichier ne les répète pas :
il documente ce qui est contre-intuitif dans le code, parce que presque
chaque bizarrerie ici contourne un bug réel.

## Commandes

Depuis la racine du repo, venv activé :

```bash
python app.py                                      # l'appli
python pipeline/transcribe.py in.mp3 --instrument bass --output-dir out/
./build_mac_app.sh                                 # .app (doit tourner sur un Mac)
.\build_windows_app.ps1                            # .exe (doit tourner sur Windows)
```

Pas de suite de tests. Pour itérer sur le pipeline, passer par la CLI
`transcribe.py` avec un vrai fichier audio : c'est le même code que l'UI
sans les 30-60 s d'import ni le clic.

## Architecture

| Fichier | Rôle |
| --- | --- |
| `app.py` | UI Tkinter + thread worker. Ne connaît du pipeline que `run_pipeline()`. |
| `pipeline/transcribe.py` | Orchestration et `INSTRUMENT_PROFILES` (point d'extension instruments). |
| `pipeline/spleeter_compat.py` | Shims TF1→TF2 pour faire tourner Spleeter (2021) sur TensorFlow moderne. |
| `pipeline/model_download.py` | Télécharge les modèles Spleeter depuis les releases GitHub de deezer/spleeter. |
| `pipeline/lilypond_export.py` | Exporteur MIDI → LilyPond maison (portée + tablature). |
| `pipeline/bundled_bins.py` | Résout ffmpeg/ffprobe/lilypond : le PATH en dev, le `bin/` du bundle en frozen. |

Flux : `separate_stem` → `transcribe_to_midi` → `enforce_monophonic` →
`estimate_tempo_bpm` → `midi_to_score`. Résultats dans
`~/Basse2Partition_resultats/<morceau>_<date>/`, modèles dans
`~/.basse2partition/models/`.

## Contraintes à ne pas « corriger »

**`pipeline/` n'est pas un package.** Pas d'`__init__.py`, et les modules
s'importent à plat (`from bundled_bins import bin_path`). C'est
[app.py:27](app.py:27) qui le rend possible, en ajoutant `pipeline/` au
`sys.path`. Passer à `from pipeline.transcribe import ...` casserait à la
fois la CLI directe et le `--add-data pipeline:pipeline` de PyInstaller.

**`spleeter` et `basic-pitch` s'installent `--no-deps`.** Leurs pins
(`numpy<1.24`, `librosa==0.8.0`, `tensorflow<2.15.1`) n'ont pas de roues en
Python 3.12. Les versions réellement nécessaires au runtime sont listées
dans `requirements.txt` ; ne jamais réinstaller ces deux paquets avec leurs
dépendances.

**`tensorflow-cpu` sur Windows/Linux, `tensorflow` sur macOS.** Le nom
`tensorflow-cpu` n'existe pas sur macOS, où `tensorflow` est déjà CPU-only.
Les marqueurs d'environnement sont déjà en place dans `requirements.txt`.

**`Separator(..., multiprocess=False)`.** Sinon Spleeter ouvre un
`multiprocessing.Pool` à la construction et, en build frozen, chaque worker
relance l'exécutable — fenêtres fantômes ou blocage. Le découpage est déjà
fait à la main, le pool n'apportait rien. Même raison pour le
`multiprocessing.freeze_support()` avant `main()`.

**Séparation par tranches de 60 s avec 2 s de recouvrement.** Ça borne la
mémoire (~2 Go par tranche). Donner la piste entière à Spleeter fait
croître la mémoire avec la durée du morceau et part en OOM sur une machine
modeste.

**`ModelProvider.DEFAULT_MODEL_PATH` est patché en plus de `$MODEL_PATH`.**
L'attribut de classe est lu depuis l'env var une seule fois, à l'import :
poser la variable au moment de l'appel arrive trop tard, et Spleeter tente
alors de re-télécharger le modèle via `httpx[http2]`, qu'on n'embarque pas.

**L'import de `transcribe` reste hors du chargement de module.** Il tire
TensorFlow et Spleeter (~30-60 s) ; il est préchauffé sur un thread de fond
pour que la fenêtre s'affiche tout de suite. Ne pas le remonter en haut de
`app.py`.

**Les mtimes des `.go` de LilyPond sont rafraîchis en fin de build.**
PyInstaller réinitialise les dates, les sources `.scm` paraissent alors
plus récentes que leur bytecode, et Guile recompile au lancement — lent, et
parfois fatal.

**`lilypond_export.py` n'utilise pas l'export LilyPond de music21**, qui a
un bug de réutilisation de contexte quand la même musique est instanciée
sous deux portées (`Staff` + `TabStaff`) avec des `Voice` imbriquées.

**`INSTRUMENT_CHOICES` est dupliqué dans `app.py`.** Volontaire : éviter
l'import lourd pendant la construction de l'UI. À garder en phase avec
`INSTRUMENT_PROFILES` dans `pipeline/transcribe.py`.

## Ajouter un instrument

Une entrée dans `INSTRUMENT_PROFILES`, une dans `INSTRUMENT_CHOICES`. Les
deux cas qui demandent plus que ça (batterie, et la piste « other »
réellement polyphonique) sont détaillés dans le README.
