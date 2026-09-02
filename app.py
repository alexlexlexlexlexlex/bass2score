"""
Basse2Partition -- desktop app.

Drop an audio file, pick an instrument, get back a PDF score (standard
notation + tab) and a MIDI file.

Run with:  python app.py
Package into a native .app / .exe with PyInstaller -- see README.md.
"""
import multiprocessing
import os
import queue
import sys
import threading
import traceback
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pipeline"))

# NB: `import transcribe` pulls in TensorFlow + Spleeter and takes ~30-60s
# the first time. It is deliberately NOT imported at module load, so the
# window shows immediately; it is warmed on a background thread (see
# App._warm_pipeline_import) and imported for real inside the worker.
APP_TITLE = "Basse2Partition"

# Kept in sync with pipeline/transcribe.py::INSTRUMENT_PROFILES. Hard-coded
# here only to avoid the heavy import while building the UI.
INSTRUMENT_CHOICES = ["bass"]


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("600x660")
        self.root.minsize(540, 600)

        self.input_path = tk.StringVar(value="")
        self.instrument = tk.StringVar(value="bass")
        self.status_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.worker_thread = None
        self.last_result = None
        self._pipeline_ready = threading.Event()

        self._build_ui()
        self.root.after(150, self._poll_queue)
        threading.Thread(target=self._warm_pipeline_import, daemon=True).start()

    def _warm_pipeline_import(self):
        """Import the heavy pipeline (TensorFlow + Spleeter) off the UI
        thread so the first 'Générer' click isn't a cold 30-60s import."""
        try:
            import transcribe  # noqa: F401
        except Exception:  # noqa: BLE001 -- surfaced later, at run time
            pass
        finally:
            self._pipeline_ready.set()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        pad = {"padx": 12, "pady": 8}

        title = ttk.Label(self.root, text=APP_TITLE, font=("Helvetica", 18, "bold"))
        title.pack(**pad)

        # --- drop zone ---------------------------------------------------
        self.drop_frame = tk.Frame(
            self.root, bg="#eef1f5", height=140, highlightbackground="#9aa5b1",
            highlightthickness=2,
        )
        self.drop_frame.pack(fill="x", padx=16, pady=8)
        self.drop_frame.pack_propagate(False)

        self.drop_label = tk.Label(
            self.drop_frame,
            text=self._drop_zone_text(),
            bg="#eef1f5", fg="#495057", justify="center", wraplength=460,
        )
        self.drop_label.pack(expand=True)

        browse_btn = ttk.Button(self.root, text="Choisir un fichier...", command=self._browse)
        browse_btn.pack(pady=(0, 8))

        if HAS_DND:
            # Register both the frame and the label on top of it: a drop
            # lands on whichever widget is under the cursor, and the label
            # covers almost the whole drop zone.
            for widget in (self.drop_frame, self.drop_label):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)

        # --- instrument selector ------------------------------------------
        instr_frame = ttk.Frame(self.root)
        instr_frame.pack(fill="x", padx=16, pady=8)
        ttk.Label(instr_frame, text="Instrument :").pack(side="left")
        self.instrument_combo = ttk.Combobox(
            instr_frame, textvariable=self.instrument, state="readonly",
            values=INSTRUMENT_CHOICES,
        )
        self.instrument_combo.pack(side="left", padx=8)
        note = ttk.Label(
            instr_frame,
            text="(seule la basse est disponible pour l'instant)",
            foreground="#868e96",
        )
        note.pack(side="left")

        # --- run button ----------------------------------------------------
        self.run_btn = ttk.Button(self.root, text="Générer la partition", command=self._start)
        self.run_btn.pack(pady=10)

        # --- progress -------------------------------------------------------
        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", padx=16, pady=4)

        self.log = tk.Text(self.root, height=10, state="disabled", bg="#0f172a", fg="#e2e8f0")
        self.log.pack(fill="both", expand=True, padx=16, pady=8)

        # --- result actions ---------------------------------------------
        self.result_frame = ttk.Frame(self.root)
        self.result_frame.pack(fill="x", padx=16, pady=(0, 12))
        self.open_pdf_btn = ttk.Button(
            self.result_frame, text="Ouvrir le PDF", command=self._open_pdf, state="disabled"
        )
        self.open_pdf_btn.pack(side="left", padx=4)
        self.open_folder_btn = ttk.Button(
            self.result_frame, text="Ouvrir le dossier de sortie",
            command=self._open_folder, state="disabled",
        )
        self.open_folder_btn.pack(side="left", padx=4)

    def _drop_zone_text(self):
        if HAS_DND:
            return "Glisse un fichier audio ici\n(mp3, wav, m4a, flac...)\nou utilise le bouton ci-dessous"
        return (
            "Glisser-déposer indisponible (module tkinterdnd2 non installé)\n"
            "Utilise le bouton ci-dessous pour choisir un fichier"
        )

    # ------------------------------------------------------------- actions
    def _browse(self):
        path = filedialog.askopenfilename(
            title="Choisir un fichier audio",
            filetypes=[("Audio", "*.mp3 *.wav *.m4a *.flac *.aac *.ogg"), ("Tous les fichiers", "*.*")],
        )
        if path:
            self._set_input(path)

    def _on_drop(self, event):
        # tkinterdnd2 hands over a Tcl list: paths with spaces are wrapped
        # in {}, and dropping several files at once gives several entries.
        try:
            paths = list(self.root.tk.splitlist(event.data))
        except tk.TclError:
            paths = [event.data.strip("{}")]
        if not paths:
            return
        chosen = next((p for p in paths if os.path.isfile(p)), paths[0])
        self._set_input(chosen)

    def _set_input(self, path):
        self.input_path.set(path)
        self.drop_label.config(text=f"Fichier sélectionné :\n{os.path.basename(path)}")

    def _start(self):
        path = self.input_path.get()
        if not path or not os.path.isfile(path):
            messagebox.showwarning(APP_TITLE, "Choisis d'abord un fichier audio.")
            return
        if self.worker_thread and self.worker_thread.is_alive():
            return

        self.run_btn.config(state="disabled")
        self.open_pdf_btn.config(state="disabled")
        self.open_folder_btn.config(state="disabled")
        self.progress.start(12)
        self._log_clear()
        self._log(f"Traitement de {os.path.basename(path)}...")
        self._log("Cette étape peut prendre plusieurs minutes selon la durée du morceau.")

        instrument = self.instrument.get()
        output_dir = self._make_output_dir(path)

        self.worker_thread = threading.Thread(
            target=self._run_pipeline_worker, args=(path, instrument, output_dir), daemon=True
        )
        self.worker_thread.start()

    def _make_output_dir(self, input_path):
        base = os.path.splitext(os.path.basename(input_path))[0]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        home = os.path.expanduser("~")
        out_dir = os.path.join(home, f"{APP_TITLE}_resultats", f"{base}_{stamp}")
        os.makedirs(out_dir, exist_ok=True)
        return out_dir

    def _run_pipeline_worker(self, path, instrument, output_dir):
        try:
            if not self._pipeline_ready.is_set():
                self.status_queue.put(
                    ("log", "Chargement des modules (TensorFlow)... "
                            "premier lancement, patiente ~30 s.")
                )
                self._pipeline_ready.wait()
            from transcribe import run_pipeline
            result = run_pipeline(path, instrument, output_dir)
            self.status_queue.put(("done", result))
        except Exception as exc:  # noqa: BLE001
            self.status_queue.put(("error", (str(exc), traceback.format_exc())))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.status_queue.get_nowait()
                if kind == "done":
                    self._on_done(payload)
                elif kind == "error":
                    self._on_error(payload)
                elif kind == "log":
                    self._log(payload)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _on_done(self, result):
        self.progress.stop()
        self.run_btn.config(state="normal")
        self.last_result = result
        self._log(f"Terminé. Tempo estimé : {result['tempo_bpm']:.1f} BPM")
        self._log(f"PDF : {result['pdf']}")
        self._log(f"MIDI : {result['midi']}")
        self.open_pdf_btn.config(state="normal")
        self.open_folder_btn.config(state="normal")

    def _on_error(self, payload):
        self.progress.stop()
        self.run_btn.config(state="normal")
        msg, tb = payload
        self._log(f"ERREUR : {msg}")
        self._log(tb)
        messagebox.showerror(APP_TITLE, f"Une erreur est survenue :\n{msg}")

    def _open_pdf(self):
        if self.last_result:
            self._open_path(self.last_result["pdf"])

    def _open_folder(self):
        if self.last_result:
            self._open_path(os.path.dirname(self.last_result["pdf"]))

    @staticmethod
    def _open_path(path):
        if sys.platform == "darwin":
            os.system(f'open "{path}"')
        elif sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606
        else:
            os.system(f'xdg-open "{path}"')

    # ------------------------------------------------------------------ log
    def _log(self, line):
        self.log.config(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _log_clear(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")


def main():
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    # Required in a frozen (PyInstaller) build: without it, any
    # multiprocessing worker re-launches this exe -- i.e. a new window /
    # a hung pool -- instead of running the worker function.
    multiprocessing.freeze_support()
    main()
