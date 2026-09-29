"""Czytnik wydan AI - GUI.

Uruchomienie:  python app.py
"""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import cache_db
import export
import naming
import rename_ops
import report_import
import render
import session as session_io
import validate
from verify import VerifyDialog, needs_check
from config import (ALL_EXT, APP_NAME, APP_VERSION, BATCH_SIZE, COLUMNS,
                    FREE_RPM, SESSION_EXT, load_config, save_config)
from openrouter_client import FatalApiError, OpenRouterClient
from worker import ReadWorker

CHECK_ON, CHECK_OFF = "\u2611", "\u2610"


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def make_record(path: Path) -> dict:
    return {
        "path": str(path), "orig_path": str(path), "old_name": path.name,
        "kind": render.kind_of(path), "checked": True,
        "title": None, "language": None, "date_iso": None, "date_raw": None,
        "month_raw": None, "year_printed": None, "issue_number": None,
        "issue_suffix": None, "page_number": None, "is_cover": None,
        "confidence": None, "new_name": "", "status": "nowy", "note": "",
        "raw": "", "model": None, "outlier": False, "outlier_info": "",
        "h2": None, "size": None, "report_flag": False,
    }


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1500x880")
        self.minsize(1150, 640)

        self.cfg = load_config()
        self.records: list[dict] = []
        self.by_iid: dict[str, dict] = {}
        self._iid_seq = 0
        self.session_path: str | None = None
        self.dirty = False

        self.worker: ReadWorker | None = None
        self.queue: queue.Queue = queue.Queue()
        self._thumb_token = 0
        self._thumb_img = None
        self._busy = False
        self.report_index = report_import.Index()
        self._importing = False

        self.var_key = tk.StringVar(value=self.cfg.get("api_key", ""))
        self.var_model = tk.StringVar(value=self.cfg.get("model", ""))
        self.var_batch = tk.IntVar(value=int(self.cfg.get("batch_size", BATCH_SIZE)))
        self.var_rpm = tk.IntVar(value=int(self.cfg.get("rpm", FREE_RPM)))
        self.var_cache = tk.BooleanVar(value=bool(self.cfg.get("use_cache", True)))
        self.var_only_free = tk.BooleanVar(value=bool(self.cfg.get("only_free", True)))
        self.var_title_override = tk.StringVar(value="")
        self.var_filter = tk.StringVar(value="wszystkie")
        self.var_search = tk.StringVar(value="")
        self.var_showkey = tk.BooleanVar(value=False)

        self._build_menu()
        self._build_ui()
        self._bind_keys()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(120, self.poll_queue)
        self.set_status("Gotowy. Wklej klucz API w zakladce 'API i model', potem dodaj pliki.")

    # =============================================================== budowa UI
    def _build_menu(self):
        m = tk.Menu(self)

        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Nowa sesja", accelerator="Ctrl+N", command=self.new_session)
        f.add_command(label="Otworz sesje...", accelerator="Ctrl+O", command=self.open_session)
        f.add_command(label="Zapisz sesje", accelerator="Ctrl+S", command=self.save_session)
        f.add_command(label="Zapisz sesje jako...", accelerator="Ctrl+Shift+S",
                      command=self.save_session_as)
        f.add_separator()
        f.add_command(label="Importuj raporty CSV...", command=self.import_reports)
        f.add_separator()
        f.add_command(label="Eksport do CSV...", command=self.export_csv)
        f.add_command(label="Eksport do Excela...", command=self.export_xlsx)
        f.add_separator()
        f.add_command(label="Zakoncz", command=self.on_close)
        m.add_cascade(label="Plik", menu=f)

        p = tk.Menu(m, tearoff=0)
        p.add_command(label="Dodaj folder...", command=lambda: self.add_folder(False))
        p.add_command(label="Dodaj folder z podfolderami...", command=lambda: self.add_folder(True))
        p.add_command(label="Dodaj pojedyncze pliki...", command=self.add_files)
        p.add_separator()
        p.add_command(label="Usun zaznaczone z listy", command=self.remove_checked)
        p.add_command(label="Wyczysc liste", command=self.clear_list)
        m.add_cascade(label="Pliki", menu=p)

        t = tk.Menu(m, tearoff=0)
        t.add_command(label="Sprawdz spojnosc numer-data", command=self.run_cross_check)
        t.add_command(label="Uzupelnij brakujace lata", command=self.run_fill_years)
        t.add_separator()
        t.add_command(label="Przelicz nowe nazwy", command=self.recompute_all_names)
        t.add_command(label="Edytuj rekord...", command=self.edit_selected)
        t.add_command(label="Weryfikuj niepewne...", accelerator="Ctrl+W", command=self.open_verify)
        t.add_separator()
        t.add_command(label="Cofnij zmiane nazw...", command=self.undo_rename)
        t.add_command(label="Wyczysc cache odczytow", command=self.clear_cache)
        t.add_command(label="Statystyki", command=self.show_stats)
        m.add_cascade(label="Narzedzia", menu=t)

        h = tk.Menu(m, tearoff=0)
        h.add_command(label="O programie", command=self.about)
        m.add_cascade(label="Pomoc", menu=h)

        self.config(menu=m)

    def _build_ui(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6, pady=(6, 0))

        self.tab_main = ttk.Frame(self.nb)
        self.tab_api = ttk.Frame(self.nb)
        self.tab_log = ttk.Frame(self.nb)
        self.nb.add(self.tab_main, text="  Pliki i wyniki  ")
        self.nb.add(self.tab_api, text="  API i model  ")
        self.nb.add(self.tab_log, text="  Log  ")

        self._build_main_tab()
        self._build_api_tab()
        self._build_log_tab()
        self._build_statusbar()

    # --------------------------------------------------------------- zakladka 1
    def _build_main_tab(self):
        top = ttk.Frame(self.tab_main)
        top.pack(fill="x", padx=4, pady=4)

        ttk.Button(top, text="Dodaj folder", command=lambda: self.add_folder(False)).pack(side="left")
        ttk.Button(top, text="+ podfoldery", command=lambda: self.add_folder(True)).pack(side="left", padx=(3, 8))
        ttk.Button(top, text="Dodaj pliki", command=self.add_files).pack(side="left")

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(top, text="Zaznacz wszystko", command=lambda: self.set_all_checked(True)).pack(side="left")
        ttk.Button(top, text="Odznacz wszystko", command=lambda: self.set_all_checked(False)).pack(side="left", padx=3)
        ttk.Button(top, text="Odwroc", command=self.invert_checked).pack(side="left")
        ttk.Button(top, text="Zaznacz podswietlone", command=lambda: self.set_selected_checked(True)).pack(side="left", padx=3)

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(top, text="Filtr:").pack(side="left")
        cb = ttk.Combobox(top, textvariable=self.var_filter, width=16, state="readonly",
                          values=["wszystkie", "do sprawdzenia", "zaznaczone", "nowe", "odczytane",
                                  "brak danych", "bledy", "podejrzane", "bez nowej nazwy"])
        cb.pack(side="left", padx=3)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_tree())
        ttk.Label(top, text="Szukaj:").pack(side="left", padx=(8, 2))
        e = ttk.Entry(top, textvariable=self.var_search, width=18)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: self.refresh_tree())
        ttk.Button(top, text="Filtruj", command=self.refresh_tree).pack(side="left", padx=3)

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        self.btn_verify = ttk.Button(top, text="Weryfikuj (0)", command=self.open_verify)
        self.btn_verify.pack(side="left")

        # ---- panel dzielony: tabela | podglad
        paned = ttk.PanedWindow(self.tab_main, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=4, pady=2)

        left = ttk.Frame(paned)
        paned.add(left, weight=4)

        cols = [c[0] for c in COLUMNS]
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="extended")
        for key, header, width in COLUMNS:
            if key == "check":
                self.tree.heading(key, text=header)
            else:
                self.tree.heading(key, text=header, command=lambda k=key: self.sort_by(k))
            anchor = "center" if key in ("check", "date_iso", "issue", "page", "confidence") else "w"
            self.tree.column(key, width=width, anchor=anchor,
                             stretch=(key in ("old_name", "new_name", "note")))
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscroll=vsb.set, xscroll=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.tree.tag_configure("err", foreground="#b00020")
        self.tree.tag_configure("warn", foreground="#a86400")
        self.tree.tag_configure("ok", foreground="#0a6b2e")
        self.tree.tag_configure("outlier", background="#ffe9c7")
        self.tree.tag_configure("renamed", foreground="#00509e")

        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", lambda e: self.edit_selected())
        self.tree.bind("<space>", self.on_space)

        right = ttk.Frame(paned)
        paned.add(right, weight=1)
        ttk.Label(right, text="Podglad gornej czesci strony:").pack(anchor="w", padx=4, pady=(2, 2))
        self.lbl_thumb = ttk.Label(right, relief="sunken", anchor="center",
                                   text="(zaznacz wiersz)")
        self.lbl_thumb.pack(fill="x", padx=4)
        ttk.Label(right, text="Odczyt / surowa odpowiedz modelu:").pack(anchor="w", padx=4, pady=(8, 2))
        self.txt_detail = ScrolledText(right, height=14, wrap="word", font=("Consolas", 9))
        self.txt_detail.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # ---- pasek akcji
        act = ttk.Frame(self.tab_main)
        act.pack(fill="x", padx=4, pady=(2, 4))
        ttk.Label(act, text="Tytul (nadpisz):").pack(side="left")
        ttk.Entry(act, textvariable=self.var_title_override, width=22).pack(side="left", padx=(3, 10))

        self.btn_read = ttk.Button(act, text="Odczytaj daty za pomoca AI", command=self.start_read)
        self.btn_read.pack(side="left")
        self.btn_pause = ttk.Button(act, text="Pauza", command=self.toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=3)
        self.btn_stop = ttk.Button(act, text="Stop", command=self.stop_read, state="disabled")
        self.btn_stop.pack(side="left")

        ttk.Separator(act, orient="vertical").pack(side="left", fill="y", padx=10)
        self.btn_rename = ttk.Button(act, text="Zmien nazwy", command=self.do_rename)
        self.btn_rename.pack(side="left")
        ttk.Button(act, text="Cofnij zmiane nazw", command=self.undo_rename).pack(side="left", padx=3)
        ttk.Button(act, text="Sprawdz spojnosc", command=self.run_cross_check).pack(side="left", padx=(10, 0))

    # --------------------------------------------------------------- zakladka 2
    def _build_api_tab(self):
        pad = {"padx": 8, "pady": 4}
        box = ttk.LabelFrame(self.tab_api, text="Klucz API OpenRouter")
        box.pack(fill="x", **pad)

        row = ttk.Frame(box)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Klucz:").pack(side="left")
        self.ent_key = ttk.Entry(row, textvariable=self.var_key, width=60, show="*")
        self.ent_key.pack(side="left", padx=6)
        ttk.Checkbutton(row, text="Pokaz", variable=self.var_showkey,
                        command=self._toggle_key).pack(side="left")
        ttk.Button(row, text="Zapisz klucz", command=self.save_key).pack(side="left", padx=6)
        ttk.Button(row, text="Testuj klucz i limity", command=self.test_key).pack(side="left")

        ttk.Label(box, text="Klucz jest zapisywany lokalnie w ~/.gazeta_ai/config.json "
                            "(prawa 600) i NIE trafia do pliku sesji.",
                  foreground="#555").pack(anchor="w", padx=8, pady=(0, 6))

        self.lbl_key_info = ttk.Label(box, text="", foreground="#0a6b2e", justify="left")
        self.lbl_key_info.pack(anchor="w", padx=8, pady=(0, 8))

        box2 = ttk.LabelFrame(self.tab_api, text="Model")
        box2.pack(fill="both", expand=True, **pad)

        row2 = ttk.Frame(box2)
        row2.pack(fill="x", padx=8, pady=6)
        ttk.Button(row2, text="Pobierz liste modeli", command=self.fetch_models).pack(side="left")
        ttk.Checkbutton(row2, text="tylko darmowe (:free)", variable=self.var_only_free,
                        command=self.fetch_models).pack(side="left", padx=10)
        ttk.Label(row2, text="(lista zawsze ograniczona do modeli przyjmujacych obrazy)",
                  foreground="#555").pack(side="left")

        cols = ("id", "name", "ctx", "free")
        self.tree_models = ttk.Treeview(box2, columns=cols, show="headings", height=12)
        for c, h, w in (("id", "ID modelu", 330), ("name", "Nazwa", 280),
                        ("ctx", "Kontekst", 90), ("free", "Darmowy", 80)):
            self.tree_models.heading(c, text=h)
            self.tree_models.column(c, width=w, anchor="w" if c in ("id", "name") else "center")
        vsb2 = ttk.Scrollbar(box2, orient="vertical", command=self.tree_models.yview)
        self.tree_models.configure(yscroll=vsb2.set)
        self.tree_models.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
        vsb2.pack(side="left", fill="y", pady=6, padx=(0, 8))
        self.tree_models.bind("<Double-1>", lambda e: self.choose_model())

        row3 = ttk.Frame(self.tab_api)
        row3.pack(fill="x", **pad)
        ttk.Button(row3, text="Uzyj zaznaczonego modelu", command=self.choose_model).pack(side="left")
        ttk.Label(row3, text="Wybrany model:").pack(side="left", padx=(14, 4))
        ttk.Label(row3, textvariable=self.var_model, foreground="#00509e",
                  font=("TkDefaultFont", 9, "bold")).pack(side="left")

        box3 = ttk.LabelFrame(self.tab_api, text="Parametry przetwarzania")
        box3.pack(fill="x", **pad)
        r = ttk.Frame(box3)
        r.pack(fill="x", padx=8, pady=6)
        ttk.Label(r, text="Skanow w jednym zapytaniu:").pack(side="left")
        ttk.Spinbox(r, from_=1, to=10, width=5, textvariable=self.var_batch).pack(side="left", padx=(4, 16))
        ttk.Label(r, text="Limit zapytan/min:").pack(side="left")
        ttk.Spinbox(r, from_=1, to=120, width=5, textvariable=self.var_rpm).pack(side="left", padx=(4, 16))
        ttk.Checkbutton(r, text="uzywaj cache (nie pyta ponownie o ten sam plik)",
                        variable=self.var_cache).pack(side="left")
        ttk.Label(box3, text="Wiekszy batch = mniej zapytan = szybciej w ramach limitu, "
                             "ale rosnie ryzyko przesuniecia odpowiedzi. Zalecane 5.",
                  foreground="#555").pack(anchor="w", padx=8, pady=(0, 8))
        ttk.Button(box3, text="Zapisz ustawienia", command=self.save_key).pack(anchor="w", padx=8, pady=(0, 8))

    def _build_log_tab(self):
        self.txt_log = ScrolledText(self.tab_log, wrap="word", font=("Consolas", 9))
        self.txt_log.pack(fill="both", expand=True, padx=6, pady=6)
        bar = ttk.Frame(self.tab_log)
        bar.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(bar, text="Wyczysc log",
                   command=lambda: self.txt_log.delete("1.0", "end")).pack(side="left")

    def _build_statusbar(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", side="bottom", padx=6, pady=4)

        self.lbl_op = ttk.Label(bar, text="", width=30, anchor="w")
        self.lbl_op.pack(side="left")
        self.pb = ttk.Progressbar(bar, mode="determinate", maximum=100, length=340)
        self.pb.pack(side="left", padx=6)
        self.lbl_pct = ttk.Label(bar, text="0%", width=6, anchor="w")
        self.lbl_pct.pack(side="left")
        self.lbl_time = ttk.Label(bar, text="", anchor="w")
        self.lbl_time.pack(side="left", padx=8)
        self.lbl_status = ttk.Label(bar, text="", anchor="e")
        self.lbl_status.pack(side="right")

    def _bind_keys(self):
        self.bind_all("<Control-n>", lambda e: self.new_session())
        self.bind_all("<Control-N>", lambda e: self.new_session())
        self.bind_all("<Control-o>", lambda e: self.open_session())
        self.bind_all("<Control-O>", lambda e: self.open_session())
        self.bind_all("<Control-s>", lambda e: self.save_session())
        self.bind_all("<Control-Shift-S>", lambda e: self.save_session_as())
        self.bind_all("<Control-Shift-s>", lambda e: self.save_session_as())
        self.bind_all("<F5>", lambda e: self.refresh_tree())
        self.bind("<Control-w>", lambda e: self.open_verify())
        self.bind("<Control-W>", lambda e: self.open_verify())

    # ============================================================ pomoc/statusy
    def log(self, text: str):
        ts = time.strftime("%H:%M:%S")
        self.txt_log.insert("end", f"[{ts}] {text}\n")
        self.txt_log.see("end")

    def set_status(self, text: str):
        self.lbl_status.config(text=text)

    def set_progress(self, done: int, total: int, op: str = "", elapsed: float | None = None):
        total = max(1, total)
        pct = min(100, int(done * 100 / total))
        self.pb["value"] = pct
        self.lbl_pct.config(text=f"{pct}%")
        self.lbl_op.config(text=f"{op} {done}/{total}" if op else f"{done}/{total}")
        if elapsed is not None and done > 0:
            rate = elapsed / done
            left = rate * max(0, total - done)
            self.lbl_time.config(
                text=f"uplynelo {fmt_time(elapsed)} · pozostalo ok. {fmt_time(left)}")
        elif elapsed is not None:
            self.lbl_time.config(text=f"uplynelo {fmt_time(elapsed)}")

    def clear_progress(self):
        self.pb["value"] = 0
        self.lbl_pct.config(text="0%")
        self.lbl_op.config(text="")
        self.lbl_time.config(text="")

    def mark_dirty(self, flag: bool = True):
        self.dirty = flag
        name = Path(self.session_path).name if self.session_path else "sesja niezapisana"
        self.title(f"{APP_NAME} {APP_VERSION} - {name}{' *' if flag else ''}")

    # =============================================================== lista plikow
    def add_folder(self, recursive: bool):
        folder = filedialog.askdirectory(title="Wskaz folder ze skanami")
        if not folder:
            return
        base = Path(folder)
        it = base.rglob("*") if recursive else base.glob("*")
        found = [p for p in it if p.is_file() and p.suffix.lower() in ALL_EXT]
        self._add_paths(found)

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Wybierz pliki",
            filetypes=[("Skany (PDF i obrazy)", "*.pdf *.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"),
                       ("Wszystkie pliki", "*.*")])
        if paths:
            self._add_paths([Path(p) for p in paths])

    def _add_paths(self, paths: list[Path]):
        existing = {r["path"] for r in self.records}
        new = [p for p in sorted(paths) if str(p) not in existing]
        if not new:
            messagebox.showinfo(APP_NAME, "Nie znaleziono nowych plikow do dodania.")
            return
        total = len(new)
        for i, p in enumerate(new, start=1):
            self.records.append(make_record(p))
            if i % 50 == 0 or i == total:
                self.set_progress(i, total, "Import")
                self.update_idletasks()
        self.log(f"Dodano {total} plikow (lacznie {len(self.records)}).")
        self.clear_progress()
        self.refresh_tree()
        self.mark_dirty()
        self.set_status(f"{len(self.records)} plikow na liscie.")

    def remove_checked(self):
        keep = [r for r in self.records if not r.get("checked")]
        removed = len(self.records) - len(keep)
        if not removed:
            return
        if not messagebox.askyesno(APP_NAME, f"Usunac {removed} pozycji z listy?\n"
                                             "(pliki na dysku pozostaja nietkniete)"):
            return
        self.records = keep
        self.refresh_tree()
        self.mark_dirty()

    def clear_list(self):
        if self.records and not messagebox.askyesno(APP_NAME, "Wyczyscic cala liste?"):
            return
        self.records = []
        self.refresh_tree()
        self.mark_dirty()

    # =============================================================== tabela
    def _visible_records(self) -> list[dict]:
        f = self.var_filter.get()
        q = self.var_search.get().strip().lower()
        out = []
        for r in self.records:
            st = (r.get("status") or "").lower()
            if f == "do sprawdzenia" and not needs_check(r):
                continue
            if f == "zaznaczone" and not r.get("checked"):
                continue
            if f == "nowe" and st != "nowy":
                continue
            if f == "odczytane" and not st.startswith("odczytano"):
                continue
            if f == "brak danych" and not st.startswith("brak danych"):
                continue
            if f == "bledy" and "blad" not in st:
                continue
            if f == "podejrzane" and not r.get("outlier"):
                continue
            if f == "bez nowej nazwy" and r.get("new_name"):
                continue
            if q:
                hay = " ".join(str(r.get(k) or "") for k in
                               ("old_name", "title", "date_iso", "issue_number",
                                "new_name", "status", "note")).lower()
                if q not in hay:
                    continue
            out.append(r)
        return out

    def _row_values(self, r: dict) -> tuple:
        issue = naming.format_issue(r.get("issue_number"), r.get("issue_suffix"))
        conf = r.get("confidence")
        note = r.get("note") or ""
        if r.get("outlier") and r.get("outlier_info"):
            note = (note + " | " if note else "") + r["outlier_info"]
        return (
            CHECK_ON if r.get("checked") else CHECK_OFF,
            r.get("old_name", ""),
            r.get("title") or "",
            r.get("date_iso") or "",
            issue,
            naming.format_page(r.get("page_number")),
            r.get("new_name") or "",
            "" if conf is None else f"{conf:.2f}",
            r.get("status") or "",
            note,
        )

    def _row_tags(self, r: dict) -> tuple:
        st = (r.get("status") or "").lower()
        if r.get("outlier"):
            return ("outlier",)
        if "blad" in st:
            return ("err",)
        if st.startswith("brak danych") or r.get("report_flag"):
            return ("warn",)
        if "zmieniono" in st or "cofnieto" in st:
            return ("renamed",)
        if st.startswith("odczytano") or st == "z raportu":
            return ("ok",)
        return ()

    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.by_iid.clear()
        for r in self._visible_records():
            self._iid_seq += 1
            iid = f"R{self._iid_seq}"
            r["_iid"] = iid
            self.by_iid[iid] = r
            self.tree.insert("", "end", iid=iid, values=self._row_values(r),
                             tags=self._row_tags(r))
        self.update_verify_button()
        checked = sum(1 for r in self.records if r.get("checked"))
        self.set_status(f"{len(self.records)} plikow, zaznaczonych {checked}, "
                        f"widocznych {len(self.by_iid)}")

    def update_row(self, r: dict):
        iid = r.get("_iid")
        if iid and self.tree.exists(iid):
            self.tree.item(iid, values=self._row_values(r), tags=self._row_tags(r))
        self.update_verify_button()

    def update_verify_button(self):
        n = sum(1 for r in self.records if needs_check(r))
        self.btn_verify.config(text=f"Weryfikuj ({n})")

    def on_tree_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        r = self.by_iid.get(iid)
        if r:
            r["checked"] = not r.get("checked")
            self.update_row(r)
            self.mark_dirty()
            checked = sum(1 for x in self.records if x.get("checked"))
            self.set_status(f"{len(self.records)} plikow, zaznaczonych {checked}")
            return "break"

    def on_space(self, event):
        for iid in self.tree.selection():
            r = self.by_iid.get(iid)
            if r:
                r["checked"] = not r.get("checked")
                self.update_row(r)
        self.mark_dirty()
        return "break"

    def set_all_checked(self, flag: bool):
        for r in self.records:
            r["checked"] = flag
        self.refresh_tree()
        self.mark_dirty()

    def invert_checked(self):
        for r in self.records:
            r["checked"] = not r.get("checked")
        self.refresh_tree()
        self.mark_dirty()

    def set_selected_checked(self, flag: bool):
        for iid in self.tree.selection():
            r = self.by_iid.get(iid)
            if r:
                r["checked"] = flag
                self.update_row(r)
        self.mark_dirty()

    def sort_by(self, key: str):
        if not hasattr(self, "_sort_rev"):
            self._sort_rev = {}
        reverse = self._sort_rev.get(key, False)
        self._sort_rev[key] = not reverse

        def sk(r):
            if key == "issue":
                v = naming.format_issue(r.get("issue_number"), r.get("issue_suffix"))
            elif key == "page":
                v = naming.format_page(r.get("page_number"))
            elif key == "confidence":
                return (r.get("confidence") is None, r.get("confidence") or 0)
            else:
                v = r.get(key)
            return (v is None or v == "", str(v or "").lower())

        self.records.sort(key=sk, reverse=reverse)
        self.refresh_tree()

    def on_tree_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        r = self.by_iid.get(sel[-1])
        if not r:
            return
        self.txt_detail.delete("1.0", "end")
        lines = [
            f"Plik:        {r.get('old_name')}",
            f"Sciezka:     {r.get('path')}",
            f"Typ:         {r.get('kind')}",
            f"Tytul:       {r.get('title')}",
            f"Jezyk:       {r.get('language')}",
            f"Data ISO:    {r.get('date_iso')}",
            f"Data (org):  {r.get('date_raw')}",
            f"Miesiac:     {r.get('month_raw')}",
            f"Rok nadruk.: {r.get('year_printed')}",
            f"Nr wydania:  {r.get('issue_number')}  dopisek: {r.get('issue_suffix')}",
            f"Nr strony:   {r.get('page_number')}",
            f"Okladka:     {r.get('is_cover')}",
            f"Pewnosc:     {r.get('confidence')}",
            f"Model:       {r.get('model')}",
            f"Status:      {r.get('status')}",
            f"Uwagi:       {r.get('note')}",
        ]
        if r.get("outlier"):
            lines.append(f"PODEJRZANE:  {r.get('outlier_info')}")
        lines += ["", "--- surowa odpowiedz modelu ---", str(r.get("raw") or "")]
        self.txt_detail.insert("1.0", "\n".join(lines))
        self._load_thumb(r)

    def _load_thumb(self, r: dict):
        self._thumb_token += 1
        token = self._thumb_token
        path = r.get("path")

        def job():
            try:
                img = render.thumbnail(path, width=300)
            except Exception as exc:
                self.queue.put(("thumb_err", {"token": token, "error": str(exc)}))
                return
            self.queue.put(("thumb", {"token": token, "img": img}))

        threading.Thread(target=job, daemon=True).start()

    # =============================================================== API i model
    def _toggle_key(self):
        self.ent_key.config(show="" if self.var_showkey.get() else "*")

    def save_key(self):
        self.cfg.update({
            "api_key": self.var_key.get().strip(),
            "model": self.var_model.get().strip(),
            "batch_size": int(self.var_batch.get()),
            "rpm": int(self.var_rpm.get()),
            "use_cache": bool(self.var_cache.get()),
            "only_free": bool(self.var_only_free.get()),
        })
        save_config(self.cfg)
        self.set_status("Ustawienia zapisane.")
        self.log("Zapisano ustawienia i klucz API lokalnie.")

    def test_key(self):
        key = self.var_key.get().strip()
        if not key:
            messagebox.showwarning(APP_NAME, "Najpierw wklej klucz API.")
            return
        self.set_status("Sprawdzam klucz...")
        self.update_idletasks()
        try:
            info = OpenRouterClient(key).key_info()
        except Exception as exc:
            self.lbl_key_info.config(text=str(exc), foreground="#b00020")
            self.log(f"Test klucza: BLAD - {exc}")
            self.set_status("Klucz niepoprawny.")
            return
        fm = info.get("free_model_daily_requests") or {}
        txt = (f"Klucz dziala. Etykieta: {info.get('label') or '-'}\n"
               f"Zuzycie (dzis): {info.get('usage_daily')}  ·  limit klucza: {info.get('limit')}\n"
               f"Darmowe zapytania dzis: {fm.get('used', '?')}/{fm.get('limit', '?')} "
               f"(pozostalo {fm.get('remaining', '?')})")
        self.lbl_key_info.config(text=txt, foreground="#0a6b2e")
        self.log("Test klucza: OK. " + txt.replace("\n", " | "))
        self.set_status("Klucz poprawny.")

    def fetch_models(self):
        self.set_status("Pobieram liste modeli...")
        self.update_idletasks()
        try:
            models = OpenRouterClient(self.var_key.get().strip()).vision_models(
                only_free=self.var_only_free.get())
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Nie udalo sie pobrac listy modeli:\n{exc}")
            self.set_status("Blad pobierania modeli.")
            return
        self.tree_models.delete(*self.tree_models.get_children())
        for m in models:
            self.tree_models.insert("", "end", values=(
                m.get("id", ""), m.get("name", ""),
                m.get("context_length", ""),
                "tak" if OpenRouterClient.is_free(m) else "nie"))
        self.log(f"Pobrano {len(models)} modeli obslugujacych obrazy"
                 f"{' (tylko darmowe)' if self.var_only_free.get() else ''}.")
        self.set_status(f"Znaleziono {len(models)} pasujacych modeli.")

    def choose_model(self):
        sel = self.tree_models.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Zaznacz model na liscie.")
            return
        model_id = self.tree_models.item(sel[0], "values")[0]
        self.var_model.set(model_id)
        self.save_key()
        self.log(f"Wybrano model: {model_id}")

    # =============================================================== odczyt AI
    def start_read(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "Odczyt juz trwa.")
            return
        if not self.var_key.get().strip():
            messagebox.showwarning(APP_NAME, "Brak klucza API (zakladka 'API i model').")
            self.nb.select(self.tab_api)
            return
        if not self.var_model.get().strip():
            messagebox.showwarning(APP_NAME, "Nie wybrano modelu (zakladka 'API i model').")
            self.nb.select(self.tab_api)
            return

        todo = [r for r in self.records if r.get("checked")]
        if not todo:
            messagebox.showinfo(APP_NAME, "Nie zaznaczono zadnego pliku.")
            return

        batch = max(1, int(self.var_batch.get()))
        est_req = -(-len(todo) // batch)
        if not messagebox.askyesno(
                APP_NAME,
                f"Plikow do odczytu: {len(todo)}\n"
                f"Skanow w zapytaniu: {batch}\n"
                f"Szacowana liczba zapytan: {est_req}\n\n"
                f"Limit darmowy: 20/min oraz 50/dzien bez doladowania\n"
                f"(1000/dzien po jednorazowym zakupie 10 kredytow).\n\n"
                f"Rozpoczac?"):
            return

        self.save_key()
        self._set_running(True)
        self.worker = ReadWorker(todo, self.var_key.get().strip(),
                                 self.var_model.get().strip(), self.queue,
                                 batch_size=batch, use_cache=self.var_cache.get(),
                                 rpm=int(self.var_rpm.get()))
        self.worker.start()
        self.log(f"Start odczytu: {len(todo)} plikow, model {self.var_model.get()}.")

    def _set_running(self, running: bool):
        self._busy = running
        self.btn_read.config(state="disabled" if running else "normal")
        self.btn_pause.config(state="normal" if running else "disabled", text="Pauza")
        self.btn_stop.config(state="normal" if running else "disabled")
        self.btn_rename.config(state="disabled" if running else "normal")

    def toggle_pause(self):
        if not self.worker:
            return
        if self.worker.is_paused:
            self.worker.resume()
            self.btn_pause.config(text="Pauza")
        else:
            self.worker.pause()
            self.btn_pause.config(text="Wznow")

    def stop_read(self):
        if self.worker and messagebox.askyesno(APP_NAME, "Przerwac odczyt?"):
            self.worker.stop()
            self.set_status("Przerywanie...")

    def poll_queue(self):
        try:
            while True:
                kind, data = self.queue.get_nowait()
                self._handle_event(kind, data)
        except queue.Empty:
            pass
        self.after(120, self.poll_queue)

    def _handle_event(self, kind: str, data: dict):
        if kind == "report_progress":
            self.set_progress(data["done"], data["total"], data["op"])
        elif kind == "report_loaded":
            self._reports_loaded(data)
        elif kind == "record":
            r = data["rec"]
            self.compute_name(r)
            self.update_row(r)
            self.mark_dirty()
        elif kind == "plan":
            self.log(f"Plan: {data['total_files']} plikow, z cache {data['from_cache']}, "
                     f"do wyslania {data['to_send']} w {data['batches']} zapytaniach.")
            self._plan_total = data["to_send"]
        elif kind == "progress":
            self.set_progress(data["done"], data["total"], "Odczyt AI", data["elapsed"])
        elif kind == "batch_start":
            self.set_status(f"Zapytanie {data['index']}/{data['count']}...")
        elif kind == "waiting":
            self.log(f"Czekam {data['delay']:.0f}s przed ponowieniem (proba {data['attempt']}).")
            self.set_status(f"Limit/blad - ponawiam za {data['delay']:.0f}s...")
        elif kind == "paused":
            self.set_status("Wstrzymano.")
        elif kind == "resumed":
            self.set_status("Wznowiono.")
        elif kind == "log":
            self.log(data["text"])
        elif kind == "fatal":
            self.log("BLAD KRYTYCZNY: " + data["error"])
            messagebox.showerror(APP_NAME, data["error"])
        elif kind == "finished":
            self._set_running(False)
            msg = (f"Zakonczono{' (przerwane)' if data['stopped'] else ''}: "
                   f"{data['done']}/{data['total']} wyslanych, "
                   f"z cache {data['from_cache']}, czas {fmt_time(data['elapsed'])}.")
            self.log(msg)
            self.set_status(msg)
            self.recompute_all_names()
            self.run_cross_check(silent=True)
        elif kind == "thumb":
            if data["token"] == self._thumb_token:
                from PIL import ImageTk
                self._thumb_img = ImageTk.PhotoImage(data["img"])
                self.lbl_thumb.config(image=self._thumb_img, text="")
        elif kind == "thumb_err":
            if data["token"] == self._thumb_token:
                self.lbl_thumb.config(image="", text=f"(podglad niedostepny)\n{data['error'][:80]}")
                self._thumb_img = None

    # =============================================================== raporty CSV
    def import_reports(self):
        """Wczytuje raporty CSV z innego programu i przenosi z nich daty i numery."""
        if self._importing:
            messagebox.showinfo(APP_NAME, "Import raportow juz trwa.")
            return
        if not self.records:
            messagebox.showinfo(APP_NAME, "Najpierw dodaj pliki - raport uzupelnia "
                                          "rekordy, ktore sa na liscie.")
            return
        paths = filedialog.askopenfilenames(
            title="Wybierz raporty CSV",
            filetypes=[("Raporty CSV", "*.csv"), ("Wszystkie pliki", "*.*")])
        if not paths:
            return
        self._importing = True
        # stan rekordow czytamy tu, w watku GUI; watek roboczy dostaje tylko kopie sciezek
        need = [(r["path"], bool(r.get("h2")), r.get("size") is not None) for r in self.records]

        def job():
            loaded, errors = [], []
            for i, p in enumerate(paths, start=1):
                self.queue.put(("report_progress", {"done": i, "total": len(paths),
                                                    "op": "Wczytywanie raportow"}))
                try:
                    loaded.append((p, report_import.load_report(p)))
                except Exception as exc:
                    errors.append(f"{Path(p).name}: {exc}")
            want_hash = any(e["hash"] for _, rows in loaded for e in rows) \
                or self.report_index.has_hashes()
            hashes, sizes = {}, {}
            for i, (path, has_h, has_s) in enumerate(need, start=1):
                if want_hash and not has_h:
                    hashes[path] = report_import.file_hash_h2(path)
                if not has_s:
                    try:
                        sizes[path] = os.path.getsize(path)
                    except OSError:
                        pass
                if i % 20 == 0 or i == len(need):
                    self.queue.put(("report_progress", {"done": i, "total": len(need),
                                                        "op": "Skroty plikow"}))
            self.queue.put(("report_loaded", {"loaded": loaded, "errors": errors,
                                              "hashes": hashes, "sizes": sizes}))

        self.set_status("Import raportow...")
        threading.Thread(target=job, daemon=True).start()

    def _reports_loaded(self, data: dict):
        self._importing = False
        self.clear_progress()
        for path, rows in data["loaded"]:
            self.report_index.add(rows)
            self.log(f"Raport {Path(path).name}: {len(rows)} wierszy.")
        for r in self.records:
            h = data["hashes"].get(r["path"])
            if h:
                r["h2"] = h
            if r["path"] in data["sizes"]:
                r["size"] = data["sizes"][r["path"]]
        counts = {"pewne": 0, "watpliwe": 0, "sprzeczne": 0, "reczny": 0, "": 0}
        for r in self.records:
            res = report_import.apply(r, self.report_index.find(r))
            counts[res] += 1
            if res in ("pewne", "watpliwe"):
                self.compute_name(r)
        self.refresh_tree()
        self.mark_dirty()
        found = len(self.records) - counts[""]
        msg = (f"Wczytano raportow: {len(data['loaded'])}\n\n"
               f"Plikow z listy znalezionych w raportach: {found} z {len(self.records)}\n"
               f"  - uzupelnione (pewne): {counts['pewne']}\n"
               f"  - watpliwe w raporcie, do sprawdzenia: {counts['watpliwe']}\n"
               f"  - raporty sie roznia, do sprawdzenia: {counts['sprzeczne']}\n"
               f"  - pominiete (poprawione recznie): {counts['reczny']}")
        if data["errors"]:
            msg += "\n\nNie wczytano:\n" + "\n".join(data["errors"])
        self.log(msg.replace("\n\n", " | ").replace("\n", " "))
        self.set_status(f"Raporty: uzupelniono {counts['pewne']}, "
                        f"do sprawdzenia {counts['watpliwe'] + counts['sprzeczne']}.")
        messagebox.showinfo(APP_NAME, msg)

    # =============================================================== nazwy
    def compute_name(self, r: dict):
        name, err = naming.build_new_name(r, self.var_title_override.get().strip())
        r["new_name"] = name
        if not name and r.get("status", "").startswith("odczytano"):
            r["status"] = "brak danych"
            if err and err not in (r.get("note") or ""):
                r["note"] = err
        return name

    def recompute_all_names(self):
        for r in self.records:
            self.compute_name(r)
        self.refresh_tree()
        ready = sum(1 for r in self.records if r.get("new_name"))
        self.set_status(f"Nowe nazwy gotowe dla {ready}/{len(self.records)} plikow.")

    def edit_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Zaznacz wiersz do edycji.")
            return
        r = self.by_iid.get(sel[0])
        if r:
            EditDialog(self, r, self._after_edit)

    def open_verify(self):
        """Duze okno z podgladem strony dla rekordow wymagajacych sprawdzenia.

        Gdy w tabeli podswietlono kilka wierszy - weryfikuje wlasnie je.
        """
        sel = [self.by_iid[i] for i in self.tree.selection() if i in self.by_iid]
        recs = sel if len(sel) > 1 else [r for r in self._visible_records() if needs_check(r)]
        if not recs:
            recs = [r for r in self.records if needs_check(r)]
        if not recs:
            messagebox.showinfo(APP_NAME, "Nie ma rekordow do sprawdzenia.")
            return
        VerifyDialog(self, recs, self._after_edit, self._after_verify)

    def _after_verify(self, saved: int):
        self.refresh_tree()
        left = sum(1 for r in self.records if needs_check(r))
        self.set_status(f"Weryfikacja: poprawiono {saved}, do sprawdzenia zostalo {left}.")

    def _after_edit(self, r: dict):
        r["status"] = "poprawione recznie"
        r["note"] = ""
        r["report_flag"] = False
        self.compute_name(r)
        self.update_row(r)
        self.mark_dirty()

    # =============================================================== walidacja
    def run_cross_check(self, silent: bool = False):
        done = [r for r in self.records if r.get("date_iso") and r.get("issue_number")]
        if not done:
            if not silent:
                messagebox.showinfo(APP_NAME, "Brak odczytanych rekordow do sprawdzenia.")
            return
        summary = validate.cross_check(self.records)
        self.refresh_tree()

        lines = []
        total_out = 0
        for title, info in sorted(summary.items()):
            if not info.get("checked"):
                lines.append(f"{title}: {info['count']} rekordow - {info['reason']}")
                continue
            total_out += info["outliers"]
            dpi = info.get("days_per_issue") or 0
            lines.append(f"{title}: {info['count']} rekordow, "
                         f"srednio {dpi:.1f} dnia na numer, "
                         f"podejrzanych: {info['outliers']}")
        text = "\n".join(lines) if lines else "Brak grup do sprawdzenia."
        self.log("Walidacja krzyzowa:\n  " + text.replace("\n", "\n  "))
        self.set_status(f"Walidacja: {total_out} podejrzanych rekordow.")
        if not silent:
            messagebox.showinfo(APP_NAME, f"Wynik walidacji numer-data:\n\n{text}\n\n"
                                          f"Podejrzane wiersze sa podswietlone.\n"
                                          f"Filtr 'podejrzane' pokaze tylko je.")

    def run_fill_years(self):
        n = validate.fill_missing_years(self.records)
        self.recompute_all_names()
        self.log(f"Uzupelniono rok w {n} rekordach.")
        messagebox.showinfo(APP_NAME, f"Uzupelniono brakujacy rok w {n} rekordach.\n"
                                      f"Sprawdz je przed zmiana nazw.")

    # =============================================================== rename
    def do_rename(self):
        todo = [r for r in self.records if r.get("checked")]
        if not todo:
            messagebox.showinfo(APP_NAME, "Nie zaznaczono zadnego pliku.")
            return
        for r in todo:
            self.compute_name(r)

        plan, skipped = rename_ops.plan_renames(todo, self.var_title_override.get().strip())
        if not plan:
            messagebox.showinfo(APP_NAME, f"Nie ma czego zmieniac.\n"
                                          f"Pominietych: {len(skipped)}.")
            return

        sample = "\n".join(f"  {Path(p['src']).name}\n    -> {Path(p['dst']).name}"
                           for p in plan[:5])
        if not messagebox.askyesno(
                APP_NAME,
                f"Zmienic nazwy {len(plan)} plikow?\n"
                f"Pominietych (brak danych): {len(skipped)}\n\n"
                f"Przyklad:\n{sample}\n\n"
                f"Operacja zostanie zapisana w logu i bedzie mozna ja cofnac."):
            return

        def prog(i, total):
            self.set_progress(i, total, "Zmiana nazw")
            if i % 20 == 0:
                self.update_idletasks()

        log_path, done, errors = rename_ops.apply_renames(plan, progress=prog)
        self.clear_progress()
        self.refresh_tree()
        self.mark_dirty()
        self.log(f"Zmieniono nazwy: {len(done)}, bledow: {len(errors)}. Log: {log_path}")
        messagebox.showinfo(APP_NAME,
                            f"Zmieniono nazwy: {len(done)}\nBledy: {len(errors)}\n"
                            f"Pominiete: {len(skipped)}\n\nLog operacji:\n{log_path}")

    def undo_rename(self):
        logs = rename_ops.list_logs()
        if not logs:
            messagebox.showinfo(APP_NAME, "Brak zapisanych operacji zmiany nazw.")
            return
        UndoDialog(self, logs, self._do_undo)

    def _do_undo(self, log_path: Path):
        def prog(i, total):
            self.set_progress(i, total, "Cofanie")
            if i % 20 == 0:
                self.update_idletasks()

        restored, errors = rename_ops.undo_renames(log_path, self.records, progress=prog)
        self.clear_progress()
        self.refresh_tree()
        self.mark_dirty()
        self.log(f"Cofnieto {restored} zmian nazw, bledow: {len(errors)}.")
        msg = f"Cofnieto: {restored}\nBledy: {len(errors)}"
        if errors:
            msg += "\n\n" + "\n".join(f"{Path(e['dst']).name}: {e['error']}" for e in errors[:8])
        messagebox.showinfo(APP_NAME, msg)

    # =============================================================== sesje
    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        ans = messagebox.askyesnocancel(APP_NAME, "Zapisac biezaca sesje przed kontynuacja?")
        if ans is None:
            return False
        if ans:
            return self.save_session()
        return True

    def new_session(self):
        if not self._confirm_discard():
            return
        self.records = []
        self.session_path = None
        self.refresh_tree()
        self.txt_detail.delete("1.0", "end")
        self.lbl_thumb.config(image="", text="(zaznacz wiersz)")
        self.clear_progress()
        self.mark_dirty(False)
        self.set_status("Nowa sesja.")

    def open_session(self):
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="Otworz sesje",
            filetypes=[("Sesja programu", f"*{SESSION_EXT}"), ("Wszystkie pliki", "*.*")])
        if not path:
            return
        try:
            records, meta = session_io.load_session(path)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Nie udalo sie wczytac sesji:\n{exc}")
            return
        self.records = records
        self.session_path = path
        if meta.get("model"):
            self.var_model.set(meta["model"])
        if meta.get("title_override"):
            self.var_title_override.set(meta["title_override"])
        missing = sum(1 for r in self.records if not Path(r.get("path", "")).exists())
        self.refresh_tree()
        self.mark_dirty(False)
        self.log(f"Wczytano sesje: {path} ({len(records)} rekordow, brakujacych plikow: {missing})")
        if missing:
            messagebox.showwarning(APP_NAME,
                                   f"{missing} plikow z sesji nie istnieje pod zapisana sciezka "
                                   f"(mogly zostac przeniesione lub przemianowane).")

    def save_session(self) -> bool:
        if not self.session_path:
            return self.save_session_as()
        return self._write_session(self.session_path)

    def save_session_as(self) -> bool:
        path = filedialog.asksaveasfilename(
            title="Zapisz sesje jako", defaultextension=SESSION_EXT,
            filetypes=[("Sesja programu", f"*{SESSION_EXT}")])
        if not path:
            return False
        self.session_path = path
        return self._write_session(path)

    def _write_session(self, path: str) -> bool:
        meta = {"model": self.var_model.get(),
                "title_override": self.var_title_override.get(),
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            session_io.save_session(path, self.records, meta)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Nie udalo sie zapisac sesji:\n{exc}")
            return False
        self.mark_dirty(False)
        self.log(f"Zapisano sesje: {path}")
        self.set_status("Sesja zapisana.")
        return True

    # =============================================================== eksport
    def export_csv(self):
        if not self.records:
            messagebox.showinfo(APP_NAME, "Brak danych do eksportu.")
            return
        path = filedialog.asksaveasfilename(title="Eksport do CSV", defaultextension=".csv",
                                            filetypes=[("Plik CSV", "*.csv")])
        if not path:
            return
        try:
            export.export_csv(path, self.records, progress=self._export_prog)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Blad eksportu:\n{exc}")
            return
        self.clear_progress()
        self.log(f"Eksport CSV: {path}")
        messagebox.showinfo(APP_NAME, f"Zapisano {len(self.records)} wierszy:\n{path}")

    def export_xlsx(self):
        if not self.records:
            messagebox.showinfo(APP_NAME, "Brak danych do eksportu.")
            return
        path = filedialog.asksaveasfilename(title="Eksport do Excela", defaultextension=".xlsx",
                                            filetypes=[("Skoroszyt Excel", "*.xlsx")])
        if not path:
            return
        try:
            export.export_xlsx(path, self.records, progress=self._export_prog)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Blad eksportu:\n{exc}")
            return
        self.clear_progress()
        self.log(f"Eksport XLSX: {path}")
        messagebox.showinfo(APP_NAME, f"Zapisano {len(self.records)} wierszy:\n{path}")

    def _export_prog(self, i, total):
        self.set_progress(i, total, "Eksport")
        self.update_idletasks()

    # =============================================================== rozne
    def clear_cache(self):
        n = cache_db.count()
        if not messagebox.askyesno(APP_NAME, f"Usunac {n} zapisanych odczytow z cache?\n"
                                             "Ponowny odczyt tych plikow zuzyje limit zapytan."):
            return
        removed = cache_db.clear()
        self.log(f"Wyczyszczono cache ({removed} wpisow).")

    def show_stats(self):
        total = len(self.records)
        read = sum(1 for r in self.records if (r.get("status") or "").startswith("odczytano"))
        miss = sum(1 for r in self.records if (r.get("status") or "").startswith("brak danych"))
        err = sum(1 for r in self.records if "blad" in (r.get("status") or "").lower())
        out = sum(1 for r in self.records if r.get("outlier"))
        ready = sum(1 for r in self.records if r.get("new_name"))
        renamed = sum(1 for r in self.records if "zmieniono" in (r.get("status") or ""))
        messagebox.showinfo(APP_NAME,
                            f"Plikow na liscie:        {total}\n"
                            f"Odczytanych:             {read}\n"
                            f"Niekompletnych:          {miss}\n"
                            f"Bledow:                  {err}\n"
                            f"Podejrzanych (walidacja):{out}\n"
                            f"Z gotowa nowa nazwa:     {ready}\n"
                            f"Po zmianie nazwy:        {renamed}\n\n"
                            f"Wpisow w cache:          {cache_db.count()}")

    def about(self):
        messagebox.showinfo(
            APP_NAME,
            f"{APP_NAME} {APP_VERSION}\n\n"
            "Odczyt daty i numeru wydania ze skanow czasopism\n"
            "za pomoca modeli multimodalnych przez OpenRouter.\n"
            "Bez OCR - obraz trafia prosto do modelu.\n\n"
            "Format nazwy:\n"
            "  Tytul - RRRR-MM-DD - 000638.pdf\n"
            "  Tytul - RRRR-MM-DD - 000638 - 015.jpg\n"
            "  (wydanie specjalne: 000315-bis)")

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "Odczyt trwa. Przerwac i zamknac program?"):
                return
            self.worker.stop()
        if not self._confirm_discard():
            return
        self.destroy()


class EditDialog(tk.Toplevel):
    """Reczna poprawka pojedynczego rekordu."""

    FIELDS = [("title", "Tytul"), ("date_iso", "Data (RRRR-MM-DD)"),
              ("issue_number", "Nr wydania (cyfry)"), ("issue_suffix", "Dopisek (bis/special)"),
              ("page_number", "Nr strony (cyfry)")]

    def __init__(self, parent: App, rec: dict, on_ok):
        super().__init__(parent)
        self.title("Edycja rekordu")
        self.rec = rec
        self.on_ok = on_ok
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        ttk.Label(self, text=rec.get("old_name", ""), font=("TkDefaultFont", 9, "bold")
                  ).grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 2), sticky="w")
        if rec.get("date_raw"):
            ttk.Label(self, text=f"Odczytane z okladki: {rec.get('date_raw')}",
                      foreground="#555").grid(row=1, column=0, columnspan=2,
                                              padx=10, pady=(0, 8), sticky="w")

        self.vars = {}
        for i, (key, label) in enumerate(self.FIELDS, start=2):
            ttk.Label(self, text=label + ":").grid(row=i, column=0, padx=10, pady=3, sticky="e")
            v = tk.StringVar(value="" if rec.get(key) is None else str(rec.get(key)))
            self.vars[key] = v
            ttk.Entry(self, textvariable=v, width=34).grid(row=i, column=1, padx=10, pady=3)

        btns = ttk.Frame(self)
        btns.grid(row=len(self.FIELDS) + 2, column=0, columnspan=2, pady=10)
        ttk.Button(btns, text="Zapisz", command=self.ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Anuluj", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())

    def ok(self):
        date = self.vars["date_iso"].get().strip()
        if date and not naming.is_valid_date(date):
            messagebox.showwarning("Edycja", "Data musi miec format RRRR-MM-DD.", parent=self)
            return
        for key, var in self.vars.items():
            val = var.get().strip()
            self.rec[key] = val or None
        self.on_ok(self.rec)
        self.destroy()


class UndoDialog(tk.Toplevel):
    """Wybor operacji zmiany nazw do cofniecia."""

    def __init__(self, parent: App, logs: list[Path], on_ok):
        super().__init__(parent)
        self.title("Cofnij zmiane nazw")
        self.on_ok = on_ok
        self.logs = logs
        self.transient(parent)
        self.grab_set()

        ttk.Label(self, text="Wybierz operacje do cofniecia (najnowsza na gorze):"
                  ).pack(anchor="w", padx=10, pady=(10, 4))
        self.lb = tk.Listbox(self, width=68, height=12)
        for p in logs:
            try:
                import json
                d = json.loads(p.read_text(encoding="utf-8"))
                n = len(d.get("done", []))
            except Exception:
                n = "?"
            stamp = p.stem.replace("rename_", "").replace("_", " ")
            self.lb.insert("end", f"{stamp}   -   plikow: {n}")
        self.lb.pack(padx=10, pady=4)
        self.lb.selection_set(0)

        btns = ttk.Frame(self)
        btns.pack(pady=10)
        ttk.Button(btns, text="Cofnij", command=self.ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Anuluj", command=self.destroy).pack(side="left", padx=4)

    def ok(self):
        sel = self.lb.curselection()
        if not sel:
            return
        path = self.logs[sel[0]]
        self.destroy()
        self.on_ok(path)


if __name__ == "__main__":
    App().mainloop()
