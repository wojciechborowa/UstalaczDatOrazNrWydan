"""Czytnik wydan AI - GUI.

Uruchomienie:  python app.py
"""
from __future__ import annotations

import os
import queue
import sys
import webbrowser
import re
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import assess
import cache_db
import filename_patterns
from collection_map import CollectionMap
import export
import name_facts
import naming
import rename_ops
import updater
import report_import
import render
import report_html
import session as session_io
import usage
from verify import VerifyDialog, check_reasons, needs_check, open_external, status_group
from config import (ALL_EXT, APP_DIR, APP_NAME, APP_VERSION, BATCH_SIZE, CACHE_DB, COLUMN_MAX, COLUMNS,
                    CONFIG_FILE,
                    FREE_RPM, PROVIDERS, SESSION_EXT,
                    load_config, save_config)
from assess import MODE_ISSUES, MODE_PAGES, MODES
from gemini_client import FALLBACK_MODELS, GeminiClient, is_free_guess
from openrouter_client import FatalApiError, OpenRouterClient
from prompt import DEFAULT_RULES

# klucze konfiguracji per dostawca: (klucz API, model, limit zapytan/min, domyslny limit)
PROVIDER_CFG = {
    "openrouter": ("api_key", "model", "rpm", FREE_RPM),
    "gemini": ("gemini_key", "gemini_model", "gemini_rpm", 10),
}
from worker import ReadWorker

CHECK_ON, CHECK_OFF = "\u2611", "\u2610"


MODEL_KINDS = [("obrazy (czytaja skany)", "obrazy"), ("tekst", "tekst"),
               ("audio / glos", "audio"), ("embeddings", "embeddings"),
               ("generowanie obrazu / wideo", "generowanie"), ("wszystkie", "wszystkie")]


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
        "raw": "", "model": None, "h2": None, "size": None,
        "name_data": None, "pattern": "", "name_complete": False, "name_facts": None,
        "ai": None, "ai_date": None, "ai_issue": None, "issues": None, "report_data": None,
    }


def parse_conf_filter(text: str):
    """Filtr pewnosci: '<0.8', '<=0.5', '>0.9', '0.5-0.8', 'brak'. Zwraca funkcje
    (pewnosc -> bool) albo None, gdy filtr jest pusty / 'dowolna'. Rzuca ValueError."""
    t = (text or "").strip().lower().replace(",", ".").replace(" ", "")
    if t in ("", "dowolna", "wszystkie"):
        return None
    if t == "brak":
        return lambda c: c is None
    m = re.fullmatch(r"(<=|>=|<|>|=)?(\d*\.?\d+)", t)
    if m:
        op, v = m.group(1) or "=", float(m.group(2))
        ops = {"<": lambda c: c < v, "<=": lambda c: c <= v, ">": lambda c: c > v,
               ">=": lambda c: c >= v, "=": lambda c: abs(c - v) < 0.005}
        f = ops[op]
        return lambda c: c is not None and f(c)
    m = re.fullmatch(r"(\d*\.?\d+)-(\d*\.?\d+)", t)
    if m:
        lo, hi = sorted((float(m.group(1)), float(m.group(2))))
        return lambda c: c is not None and lo <= c <= hi
    raise ValueError("nie rozumiem filtru pewnosci - wpisz np. <0.8, >=0.9, 0.5-0.8 albo brak")


CONF_PRESETS = ["dowolna", "<0.5", "<0.7", "<0.8", "<0.9", ">=0.9", "0.5-0.8", "brak"]


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
        self.run_stats = {"ai_seconds": 0.0, "ai_sent": 0, "ai_cache": 0, "runs": 0}
        self.import_dirs: list[str] = []
        self.dirty = False

        self.worker: ReadWorker | None = None
        self.queue: queue.Queue = queue.Queue()
        self._thumb_token = 0
        self._thumb_img = None
        self._busy = False
        self.report_index = report_import.Index()
        self.session_patterns: list[dict] = []   # wlasne wzorce nazw tej sesji
        self.mode: str | None = None             # tryb sesji: kolekcja wydan / kolekcja stron
        self.page_index = 0                      # strona PDF-a wysylana do AI (od 0)
        self.usage = usage.Usage()               # liczniki zapytan liczone przez program
        self._importing = False

        prov = self.cfg.get("provider", "openrouter")
        if prov not in PROVIDER_CFG:
            prov = "openrouter"
        k_key, k_model, k_rpm, d_rpm = PROVIDER_CFG[prov]
        self.var_provider = tk.StringVar(value=prov)
        self._provider_shown = prov
        self.var_key = tk.StringVar(value=self.cfg.get(k_key, ""))
        self.var_model = tk.StringVar(value=self.cfg.get(k_model, ""))
        self.var_batch = tk.IntVar(value=int(self.cfg.get("batch_size", BATCH_SIZE)))
        self.var_rpm = tk.IntVar(value=int(self.cfg.get(k_rpm, d_rpm)))
        self.var_tpm = tk.IntVar(value=0)
        self.var_rpd = tk.IntVar(value=0)
        self.var_cache = tk.BooleanVar(value=bool(self.cfg.get("use_cache", True)))
        self.var_only_free = tk.BooleanVar(value=bool(self.cfg.get("only_free", True)))
        self.var_model_kind = tk.StringVar(value=self.cfg.get("model_kind") or MODEL_KINDS[0][0])
        self.var_title_override = tk.StringVar(value="")
        self.var_filter = tk.StringVar(value="wszystkie")
        self.var_conf = tk.StringVar(value="dowolna")
        self.var_search = tk.StringVar(value="")
        self.var_showkey = tk.BooleanVar(value=False)

        self._build_menu()
        self._build_ui()
        self._bind_keys()

        self._load_limits()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(120, self.poll_queue)
        self.after(1000, self._usage_tick)
        self._update_mode_label()
        self.set_status("Gotowy. Wklej klucz API w zakladce 'API i model', potem dodaj pliki.")

    # =============================================================== budowa UI
    def _build_menu(self):
        m = tk.Menu(self)

        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Nowa sesja", accelerator="Ctrl+N", command=self.new_session)
        f.add_command(label="Otworz sesje...", accelerator="Ctrl+O", command=self.open_session)
        f.add_command(label="Wczytaj ostatnia sesje", accelerator="Ctrl+Shift+O",
                      command=self.open_last_session)
        self.menu_recent = tk.Menu(f, tearoff=0, postcommand=self._build_recent_menu)
        f.add_cascade(label="Ostatnie sesje", menu=self.menu_recent)
        f.add_command(label="Zapisz sesje", accelerator="Ctrl+S", command=self.save_session)
        f.add_command(label="Zapisz sesje jako...", accelerator="Ctrl+Shift+S",
                      command=self.save_session_as)
        f.add_command(label="Ustawienia sesji (tryb, strona dla AI)...", command=self.session_settings)
        f.add_separator()
        f.add_command(label="Importuj raporty CSV...", command=self.import_reports)
        f.add_separator()
        f.add_command(label="Zakoncz", command=self.on_close)
        m.add_cascade(label="Sesja", menu=f)

        p = tk.Menu(m, tearoff=0)
        p.add_command(label="Dodaj folder...", command=lambda: self.add_folder(False))
        p.add_command(label="Dodaj folder z podfolderami...", command=lambda: self.add_folder(True))
        p.add_command(label="Dodaj pojedyncze pliki...", command=self.add_files)
        p.add_separator()
        p.add_command(label="Wzorce nazw plikow...", command=self.edit_patterns)
        p.add_separator()
        p.add_command(label="Usun zaznaczone z listy", command=self.remove_checked)
        p.add_command(label="Wyczysc liste", command=self.clear_list)
        m.add_cascade(label="Pliki", menu=p)

        t = tk.Menu(m, tearoff=0)
        t.add_command(label="Ponow odczyt AI podswietlonych (bez cache)",
                      command=lambda: self.reread_selected(False))
        t.add_command(label="Ponow odczyt AI podswietlonych - obraz dokladny",
                      command=lambda: self.reread_selected(True))
        t.add_separator()
        t.add_command(label="Przelicz nowe nazwy", command=self.recompute_all_names)
        t.add_command(label="Edytuj rekord...", command=self.edit_selected)
        t.add_command(label="Otworz plik", accelerator="P", command=self.open_selected_files)
        t.add_command(label="Weryfikuj...", accelerator="Ctrl+W", command=self.open_verify)
        t.add_separator()
        t.add_command(label="Cofnij zmiane nazw...", command=self.undo_rename)
        t.add_command(label="Cache odczytow: rozmiar i czyszczenie...", command=self.clear_cache)
        t.add_command(label="Zuzycie limitow AI (liczniki programu)", command=self.show_usage)
        t.add_command(label="Statystyki", command=self.show_stats)
        t.add_command(label="Raport HTML z tej sesji...", command=self.make_report)
        t.add_separator()
        t.add_command(label="Eksport do CSV...", command=self.export_csv)
        t.add_command(label="Eksport do Excela...", command=self.export_xlsx)
        m.add_cascade(label="Narzedzia", menu=t)

        w = tk.Menu(m, tearoff=0)
        w.add_command(label="Dopasuj kolumny do zawartosci", accelerator="Ctrl+D",
                      command=self.autofit_columns)
        w.add_command(label="Pokaz/ukryj kolumny: prawy przycisk na naglowku tabeli", state="disabled")
        m.add_cascade(label="Widok", menu=w)

        h = tk.Menu(m, tearoff=0)
        h.add_command(label="Sprawdz aktualizacje...", command=self.check_updates)
        h.add_command(label="Aktualizuj program z pliku ZIP...", command=self.update_from_zip)
        h.add_command(label="Cofnij ostatnia aktualizacje...", command=self.rollback_update)
        h.add_command(label="Zrodlo aktualizacji (repozytorium, galaz)...", command=self.update_source)
        h.add_separator()
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
        ttk.Button(top, text="Zazn. podswietlone", command=lambda: self.set_selected_checked(True)).pack(side="left", padx=3)
        ttk.Button(top, text="Zazn. widoczne", command=self.check_only_visible).pack(side="left")

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(top, text="Filtr:").pack(side="left")
        cb = ttk.Combobox(top, textvariable=self.var_filter, width=18, state="readonly",
                          values=["wszystkie", "do weryfikacji", "zielone (pewne)", "zaznaczone", "nowe",
                                  "bledy", "niezgodne z nazwa/raportem", "niekompletny odczyt",
                                  "bez nowej nazwy", "luzne (bez wzorca)", "z raportem"])
        cb.pack(side="left", padx=3)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_tree())
        ttk.Label(top, text="Pewnosc:").pack(side="left", padx=(8, 2))
        cc = ttk.Combobox(top, textvariable=self.var_conf, width=9, values=CONF_PRESETS)
        cc.pack(side="left")
        cc.bind("<<ComboboxSelected>>", lambda e: self.refresh_tree())
        cc.bind("<Return>", lambda e: self.refresh_tree())
        ttk.Label(top, text="Szukaj:").pack(side="left", padx=(8, 2))
        e = ttk.Entry(top, textvariable=self.var_search, width=18)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: self.refresh_tree())
        ttk.Button(top, text="Filtruj", command=self.refresh_tree).pack(side="left", padx=3)


        # ---- tryb sesji
        mrow = ttk.Frame(self.tab_main)
        mrow.pack(fill="x", padx=4, pady=(0, 2))
        self.lbl_mode = ttk.Label(mrow, text="", font=("TkDefaultFont", 9, "bold"), foreground="#00509e")
        self.lbl_mode.pack(side="left")
        ttk.Button(mrow, text="Ustawienia sesji...", command=self.session_settings).pack(side="left", padx=8)
        self.lbl_usage_main = ttk.Label(mrow, text="", foreground="#555")
        self.lbl_usage_main.pack(side="right")

        # ---- pasek stanu kolekcji
        self.cmap = CollectionMap(self.tab_main, self.jump_to_record)
        self.cmap.pack(fill="x", padx=4, pady=(0, 2))

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
            width = int((self.cfg.get("col_widths") or {}).get(key, width))
            self.tree.column(key, width=width, anchor=anchor, stretch=False)
        hidden = set(self.cfg.get("hidden_cols") or [])
        self.tree.configure(displaycolumns=[c for c in cols if c not in hidden])
        self.header_menu = tk.Menu(self, tearoff=0)
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscroll=vsb.set, xscroll=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        # kolory grup: zielony = pewne, pomaranczowy = do weryfikacji, czerwony = blad
        self.tree.tag_configure("certain", foreground="#0a6b2e")
        self.tree.tag_configure("check", foreground="#b36b00")
        self.tree.tag_configure("error", foreground="#b00020")
        self.tree.tag_configure("renamed", foreground="#00509e")
        self.tree.tag_configure("new", foreground="#555555")

        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", lambda e: self.edit_selected())
        self.tree.bind("<space>", self.on_space)
        self.tree.bind("<p>", lambda e: self.open_selected_files())
        self.tree.bind("<P>", lambda e: self.open_selected_files())
        self.tree.bind("<Button-3>", self.on_tree_menu)
        self.tree_menu = tk.Menu(self, tearoff=0)
        self.tree_menu.add_command(label="Otworz plik  [P]", command=self.open_selected_files)
        self.tree_menu.add_command(label="Edytuj rekord...", command=self.edit_selected)
        self.tree_menu.add_command(label="Weryfikuj...", command=self.open_verify)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label="Ponow odczyt AI (bez cache)",
                                   command=lambda: self.reread_selected(False))
        self.tree_menu.add_command(label="Ponow odczyt AI - obraz dokladny",
                                   command=lambda: self.reread_selected(True))

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
        ttk.Entry(act, textvariable=self.var_title_override, width=22).pack(side="left", padx=(3, 3))
        ttk.Button(act, text="Wzorce nazw...", command=self.edit_patterns).pack(side="left", padx=(0, 10))

        self.btn_read = ttk.Button(act, text="Odczytaj daty za pomoca AI", command=self.start_read)
        self.btn_read.pack(side="left")
        self.btn_pause = ttk.Button(act, text="Pauza", command=self.toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=3)
        self.btn_stop = ttk.Button(act, text="Stop", command=self.stop_read, state="disabled")
        self.btn_stop.pack(side="left")
        self.btn_verify = ttk.Button(act, text="Weryfikuj (0)", command=self.open_verify)
        self.btn_verify.pack(side="left", padx=(10, 0))

        ttk.Separator(act, orient="vertical").pack(side="left", fill="y", padx=10)
        self.btn_rename = ttk.Button(act, text="Zmien nazwy", command=self.do_rename)
        self.btn_rename.pack(side="left")
        ttk.Button(act, text="Cofnij zmiane nazw", command=self.undo_rename).pack(side="left", padx=3)

    # --------------------------------------------------------------- zakladka 2
    def _build_api_tab(self):
        pad = {"padx": 8, "pady": 4}
        box0 = ttk.LabelFrame(self.tab_api, text="Dostawca AI")
        box0.pack(fill="x", **pad)
        row0 = ttk.Frame(box0)
        row0.pack(fill="x", padx=8, pady=6)
        for pid, label in PROVIDERS.items():
            ttk.Radiobutton(row0, text=label, value=pid, variable=self.var_provider,
                            command=self.switch_provider).pack(side="left", padx=(0, 16))
        self.lbl_provider_hint = ttk.Label(box0, text="", foreground="#555", justify="left")
        self.lbl_provider_hint.pack(anchor="w", padx=8, pady=(0, 6))

        box = ttk.LabelFrame(self.tab_api, text="Klucz API")
        self.box_key = box
        box.pack(fill="x", **pad)

        row = ttk.Frame(box)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Klucz:").pack(side="left")
        self.ent_key = ttk.Entry(row, textvariable=self.var_key, width=60, show="*")
        self.ent_key.pack(side="left", padx=6)
        ttk.Checkbutton(row, text="Pokaz", variable=self.var_showkey,
                        command=self._toggle_key).pack(side="left")
        ttk.Button(row, text="Zapisz klucz", command=self.save_key).pack(side="left", padx=6)
        ttk.Button(row, text="Testuj klucz", command=self.test_key).pack(side="left")

        rowk = ttk.Frame(box)
        rowk.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(rowk, text="Zapisane klucze:").pack(side="left")
        self.var_saved_key = tk.StringVar()
        self.cb_keys = ttk.Combobox(rowk, textvariable=self.var_saved_key, width=40, state="readonly")
        self.cb_keys.pack(side="left", padx=6)
        self.cb_keys.bind("<<ComboboxSelected>>", lambda e: self.use_saved_key())
        ttk.Button(rowk, text="Dodaj biezacy do listy...", command=self.add_saved_key).pack(side="left")
        ttk.Button(rowk, text="Usun z listy", command=self.remove_saved_key).pack(side="left", padx=3)
        ttk.Label(rowk, text="(np. klucz platny i darmowy - przelaczasz recznie)",
                  foreground="#555").pack(side="left", padx=6)

        ttk.Label(box, text=f"Klucze sa zapisywane lokalnie w {CONFIG_FILE} "
                            "i NIE trafiaja do pliku sesji.",
                  foreground="#555").pack(anchor="w", padx=8, pady=(0, 6))

        self.lbl_key_info = ttk.Label(box, text="", foreground="#0a6b2e", justify="left")
        self.lbl_key_info.pack(anchor="w", padx=8, pady=(0, 8))

        box2 = ttk.LabelFrame(self.tab_api, text="Model")
        box2.pack(fill="both", expand=True, **pad)

        row2 = ttk.Frame(box2)
        row2.pack(fill="x", padx=8, pady=6)
        ttk.Button(row2, text="Pobierz liste modeli", command=self.fetch_models).pack(side="left")
        self.chk_only_free = ttk.Checkbutton(row2, text="tylko darmowe",
                                             variable=self.var_only_free, command=self.fetch_models)
        self.chk_only_free.pack(side="left", padx=10)
        ttk.Label(row2, text="Rodzaj modeli:").pack(side="left", padx=(10, 4))
        self.cb_kind = ttk.Combobox(row2, textvariable=self.var_model_kind, state="readonly",
                                    width=30, values=[k[0] for k in MODEL_KINDS])
        self.cb_kind.pack(side="left")
        self.cb_kind.bind("<<ComboboxSelected>>", lambda e: self.fetch_models())
        self.lbl_kind_hint = ttk.Label(row2, text="", foreground="#555")
        self.lbl_kind_hint.pack(side="left", padx=8)

        cols = ("id", "name", "ctx", "free", "kind")
        self.tree_models = ttk.Treeview(box2, columns=cols, show="headings", height=12)
        for c, h, w in (("id", "ID modelu", 300), ("name", "Nazwa", 250),
                        ("ctx", "Kontekst", 80), ("free", "Darmowy", 90), ("kind", "Rodzaj", 110)):
            self.tree_models.heading(c, text=h)
            self.tree_models.column(c, width=w, anchor="w" if c in ("id", "name") else "center")
        vsb2 = ttk.Scrollbar(box2, orient="vertical", command=self.tree_models.yview)
        self.tree_models.configure(yscroll=vsb2.set)
        self.tree_models.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
        vsb2.pack(side="left", fill="y", pady=6, padx=(0, 8))
        self.tree_models.tag_configure("recent", background="#fff4cc")
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
        ttk.Checkbutton(r, text="uzywaj cache (nie pyta ponownie o ten sam plik)",
                        variable=self.var_cache).pack(side="left")
        rl = ttk.Frame(box3)
        rl.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(rl, text="Limity wybranego modelu:  zapytan/min").pack(side="left")
        ttk.Spinbox(rl, from_=1, to=2000, width=6, textvariable=self.var_rpm).pack(side="left", padx=(4, 10))
        ttk.Label(rl, text="tokenow/min").pack(side="left")
        ttk.Spinbox(rl, from_=0, to=100000000, increment=10000, width=10,
                    textvariable=self.var_tpm).pack(side="left", padx=(4, 10))
        ttk.Label(rl, text="zapytan/dzien").pack(side="left")
        ttk.Spinbox(rl, from_=0, to=1000000, width=8, textvariable=self.var_rpd).pack(side="left", padx=(4, 10))
        ttk.Button(rl, text="Domyslne dla modelu", command=self.default_limits).pack(side="left")
        ttk.Label(box3, text="0 = bez limitu. Wartosci domyslne sa orientacyjne (darmowy plan) - aktualne "
                             "limity Twojego klucza pokazuje AI Studio / OpenRouter. Program sam liczy "
                             "wyslane zapytania i tokeny, zwalnia przed limitem na minute i zatrzymuje "
                             "odczyt po wyczerpaniu limitu dziennego.",
                  foreground="#555", wraplength=1100, justify="left").pack(anchor="w", padx=8)
        self.lbl_usage = ttk.Label(box3, text="", foreground="#00509e")
        self.lbl_usage.pack(anchor="w", padx=8, pady=(4, 2))
        ttk.Label(box3, text="Wiekszy batch = mniej zapytan = szybciej w ramach limitu. Kazdy skan "
                             "dostaje identyfikator na obrazie, wiec wyniki nie pomyla sie miedzy "
                             "plikami. Zalecane 5.",
                  foreground="#555").pack(anchor="w", padx=8, pady=(0, 8))
        ttk.Button(box3, text="Zapisz ustawienia", command=self.save_key).pack(anchor="w", padx=8, pady=(0, 8))

        box4 = ttk.LabelFrame(self.tab_api, text="Prompt")
        box4.pack(fill="x", **pad)
        r4 = ttk.Frame(box4)
        r4.pack(fill="x", padx=8, pady=6)
        ttk.Button(r4, text="Zasady odczytu i uwagi o kolekcji...",
                   command=self.edit_prompt).pack(side="left")
        self.lbl_prompt = ttk.Label(r4, text="", foreground="#555")
        self.lbl_prompt.pack(side="left", padx=10)
        self._update_provider_ui()

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

        self.lbl_counts = ttk.Label(bar, text="Wgrane: 0  ·  widoczne: 0  ·  zaznaczone: 0",
                                    font=("TkDefaultFont", 9, "bold"), anchor="w")
        self.lbl_counts.pack(side="left", padx=(0, 18))
        self.lbl_op = ttk.Label(bar, text="Gotowy", width=40, anchor="w")
        self.lbl_op.pack(side="left")
        self.prog_frame = ttk.Frame(bar)          # widoczny tylko w trakcie operacji
        self.pb = ttk.Progressbar(self.prog_frame, mode="determinate", maximum=100, length=340)
        self.pb.pack(side="left", padx=6)
        self.lbl_pct = ttk.Label(self.prog_frame, text="0%", width=6, anchor="w")
        self.lbl_pct.pack(side="left")
        self.lbl_time = ttk.Label(self.prog_frame, text="", anchor="w")
        self.lbl_time.pack(side="left", padx=8)
        self._prog_gen = 0
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
        self.bind_all("<Control-Shift-O>", lambda e: self.open_last_session())
        self.bind("<Control-d>", lambda e: self.autofit_columns())
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
        self._prog_gen += 1
        self._last_total = total
        if not self.prog_frame.winfo_ismapped():
            self.prog_frame.pack(side="left")
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

    def _run_bg(self, op: str, work, on_done, indeterminate: bool = False):
        """Dlugie zadanie w watku tla: pasek postepu zyje, okno nie zamarza.
        work(progress) -> wynik; on_done(wynik, wyjatek) wola sie w watku GUI."""
        if getattr(self, "_bg_busy", False):
            messagebox.showinfo(APP_NAME, "Trwa inna operacja - poczekaj, az sie skonczy.")
            return False
        self._bg_busy = True
        self._bg_op = op
        t0 = time.time()
        self._bg_indeterminate = indeterminate
        if indeterminate:
            self.lbl_op.config(text=f"{op}...")      # sam licznik, bez paska
        else:
            self.set_progress(0, 1, op)

        def prog(i, total):
            step = max(1, (total or 200) // 200)
            if i % step == 0 or i == total:
                self.queue.put(("bg_progress", {"done": i, "total": total, "op": op,
                                                "elapsed": time.time() - t0}))

        def run():
            try:
                res, err = work(prog), None
            except Exception as exc:  # noqa: BLE001
                res, err = None, exc
            self.queue.put(("bg_done", {"cb": on_done, "res": res, "err": err}))

        threading.Thread(target=run, daemon=True).start()
        return True

    def clear_progress(self):
        self._prog_gen += 1
        self.prog_frame.pack_forget()
        self.pb["value"] = 0
        self.lbl_pct.config(text="0%")
        self.lbl_op.config(text="Gotowy")
        self.lbl_time.config(text="")

    def finish_progress(self, total: int, op: str = ""):
        """Krotko pokazuje 100%, potem chowa pasek (o ile w miedzyczasie nie ruszyla nowa operacja)."""
        self.set_progress(total, total, op)
        gen = self._prog_gen
        self.after(900, lambda: self.clear_progress() if self._prog_gen == gen else None)

    def mark_dirty(self, flag: bool = True):
        self.dirty = flag
        name = Path(self.session_path).name if self.session_path else "sesja niezapisana"
        self.title(f"{APP_NAME} {APP_VERSION} - {name}{' *' if flag else ''}")

    # =============================================================== lista plikow
    def _import_start_dir(self) -> str | None:
        """Od ostatnio uzytego folderu importu (z tej sesji, potem z poprzednich)."""
        for d in list(self.import_dirs) + list(self.cfg.get("import_dirs") or []):
            if d and Path(d).is_dir():
                return d
        return None

    def _remember_import_dir(self, folder: str):
        folder = str(folder)
        self.import_dirs = [folder] + [d for d in self.import_dirs if d != folder]
        self.import_dirs = self.import_dirs[:10]
        glob = [d for d in (self.cfg.get("import_dirs") or []) if d != folder]
        self.cfg["import_dirs"] = ([folder] + glob)[:10]
        save_config(self.cfg)

    def add_folder(self, recursive: bool):
        kw = {"initialdir": self._import_start_dir()} if self._import_start_dir() else {}
        folder = filedialog.askdirectory(title="Wskaz folder ze skanami", **kw)
        if not folder:
            return
        base = Path(folder)
        self._remember_import_dir(folder)
        self.set_status("Skanowanie folderu...")

        def work(prog):
            # os.walk daje typ wpisu bez osobnego zapytania do dysku o kazdy plik
            found = []
            for root, _dirs, files in os.walk(base):
                for f in files:
                    if os.path.splitext(f)[1].lower() in ALL_EXT:
                        found.append(Path(root) / f)
                prog(len(found), 0)
                if not recursive:
                    break
            return found

        def done(found, err):
            if err:
                messagebox.showerror(APP_NAME, f"Blad skanowania folderu:\n{err}")
                return
            self._add_paths(found)

        self._run_bg("Skanowanie folderu", work, done, indeterminate=True)

    def add_files(self):
        kw = {"initialdir": self._import_start_dir()} if self._import_start_dir() else {}
        paths = filedialog.askopenfilenames(
            title="Wybierz pliki", **kw,
            filetypes=[("Skany (PDF i obrazy)", "*.pdf *.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"),
                       ("Wszystkie pliki", "*.*")])
        if paths:
            self._remember_import_dir(str(Path(paths[0]).parent))
            self._add_paths([Path(p) for p in paths])

    def _add_paths(self, paths: list[Path]):
        if getattr(self, "_bg_busy", False):
            messagebox.showinfo(APP_NAME, "Trwa inna operacja - poczekaj, az sie skonczy.")
            return
        existing = {r["path"] for r in self.records}
        new = [p for p in sorted(paths) if str(p) not in existing]
        if not new:
            messagebox.showinfo(APP_NAME, "Nie znaleziono nowych plikow do dodania.")
            return
        if not self.mode:
            imgs = sum(1 for p in new if render.kind_of(p) == "image")
            choice = ModeDialog.ask(self, "Tryb sesji",
                                    MODE_PAGES if imgs * 2 > len(new) else MODE_ISSUES, 0)
            if choice is None:
                return
            self.mode, self.page_index = choice
            self._update_mode_label()
        total = len(new)
        patterns = self.patterns()
        added = []
        self._bg_busy = True
        t0 = time.time()

        def step(start: int):
            end = min(start + 40, total)
            for p in new[start:end]:
                r = make_record(p)
                self.apply_name_data(r, patterns)
                self.records.append(r)
                added.append(r)
            self.set_progress(end, total, "Import", time.time() - t0)
            if end < total:
                self.after(1, lambda: step(end))
                return
            self._bg_busy = False
            self.log(f"Dodano {total} plikow (lacznie {len(self.records)}). "
                     f"Wzorce nazw: {self.pattern_summary(added)}")
            self.finish_progress(total, "Import")
            self.refresh_tree()
            self.mark_dirty()
            self.autofit_columns()
            self.set_status(f"{len(self.records)} plikow na liscie. Wzorce nazw: "
                            f"{self.pattern_summary(added)}")
            if self.report_index.sources:
                # wczytane wczesniej raporty od razu obejmuja tez nowe pliki
                self._run_report_job([], quiet=True)

        self.after(1, lambda: step(0))

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
        try:
            conf_ok = parse_conf_filter(self.var_conf.get())
        except ValueError as exc:
            self.set_status(str(exc))
            conf_ok = None
        out = []
        for r in self.records:
            st = (r.get("status") or "").lower()
            issues = " ".join(r.get("issues") or [])
            if conf_ok is not None and not conf_ok(r.get("confidence")):
                continue
            if f == "do weryfikacji" and not needs_check(r):
                continue
            if f == "zielone (pewne)" and status_group(r) not in ("certain", "renamed"):
                continue
            if f == "zaznaczone" and not r.get("checked"):
                continue
            if f == "nowe" and st != "nowy":
                continue
            if f == "bledy" and "blad" not in st:
                continue
            if f == "niezgodne z nazwa/raportem" and "a w nazwie pliku" not in issues \
                    and "raport" not in issues:
                continue
            if f == "niekompletny odczyt" and not any(w in issues for w in ("nie odczytalo", "nie rozstrzygnelo",
                                                                           "niejednoznaczna", "nie istnieje")):
                continue
            if f == "z raportem" and not r.get("report_data"):
                continue
            if f == "bez nowej nazwy" and r.get("name_complete"):
                continue
            if f == "luzne (bez wzorca)" and r.get("name_data"):
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
        # powod weryfikacji zawsze widoczny w Uwagach
        extra = [x for x in check_reasons(r) if x not in note]
        if extra:
            note = (note + " | " if note else "") + "DO WERYFIKACJI: " + "; ".join(extra)
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
            r.get("pattern") or "",
            note,
        )

    def _row_tags(self, r: dict) -> tuple:
        return (status_group(r),)

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
        self.update_counts()
        self.cmap.set_records(self.records)
        checked = sum(1 for r in self.records if r.get("checked"))
        self.set_status(f"{len(self.records)} plikow, zaznaczonych {checked}, "
                        f"widocznych {len(self.by_iid)}")

    def update_row(self, r: dict):
        iid = r.get("_iid")
        if iid and self.tree.exists(iid):
            self.tree.item(iid, values=self._row_values(r), tags=self._row_tags(r))
        if not getattr(self, "_counts_pending", False):
            # wiele zmian naraz (np. odczyt 2000 plikow) = jedno przeliczenie
            self._counts_pending = True
            self.after_idle(self._refresh_counts)

    def _refresh_counts(self):
        self._counts_pending = False
        self.update_counts()
        self.update_verify_button()
        self.cmap.redraw()

    def update_counts(self):
        checked = sum(1 for r in self.records if r.get("checked"))
        self.lbl_counts.config(text=f"Wgrane: {len(self.records)}  ·  widoczne: {len(self.by_iid)}"
                                    f"  ·  zaznaczone: {checked}")

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

    def jump_to_record(self, r: dict):
        """Klikniecie w pasek kolekcji: pokaz ten plik w tabeli."""
        if r.get("_iid") not in self.by_iid:
            self.var_filter.set("wszystkie")
            self.var_search.set("")
            self.refresh_tree()
        iid = r.get("_iid")
        if iid and self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)

    def check_only_visible(self):
        """Zaznacza rekordy widoczne w tabeli (po filtrze), reszte odznacza."""
        visible = {id(r) for r in self.by_iid.values()}
        for r in self.records:
            r["checked"] = id(r) in visible
        self.refresh_tree()
        self.mark_dirty()

    def open_selected_files(self):
        """Otwiera podswietlone pliki w domyslnym programie (np. przegladarce PDF)."""
        recs = [self.by_iid[i] for i in self.tree.selection() if i in self.by_iid]
        if not recs:
            messagebox.showinfo(APP_NAME, "Podswietl wiersz, ktory chcesz otworzyc.")
            return "break"
        if len(recs) > 10 and not messagebox.askyesno(
                APP_NAME, f"Otworzyc {len(recs)} plikow naraz?"):
            return "break"
        for r in recs:
            try:
                open_external(r["path"])
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Nie udalo sie otworzyc:\n{r['path']}\n\n{exc}")
                break
        return "break"

    def on_tree_menu(self, event):
        if self.tree.identify_region(event.x, event.y) == "heading":
            self._show_header_menu(event)
            return
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        if self.tree.selection():
            self.tree_menu.tk_popup(event.x_root, event.y_root)

    # --------------------------------------------------------------- kolumny
    def _show_header_menu(self, event):
        m = self.header_menu
        m.delete(0, "end")
        m.add_command(label="Dopasuj kolumny do zawartosci", command=self.autofit_columns)
        m.add_separator()
        shown = set(self.tree["displaycolumns"])
        if shown == {"#all"}:
            shown = {c[0] for c in COLUMNS}
        self._col_vars = {}
        for key, header, _w in COLUMNS:
            if key == "check":
                continue
            v = tk.BooleanVar(value=key in shown)
            self._col_vars[key] = v
            m.add_checkbutton(label=header, variable=v, command=lambda k=key: self._toggle_column(k))
        m.tk_popup(event.x_root, event.y_root)

    def _toggle_column(self, key: str):
        hidden = set(self.cfg.get("hidden_cols") or [])
        if self._col_vars[key].get():
            hidden.discard(key)
        else:
            hidden.add(key)
        self.cfg["hidden_cols"] = sorted(hidden)
        self.tree.configure(displaycolumns=[c[0] for c in COLUMNS if c[0] not in hidden])
        save_config(self.cfg)

    def autofit_columns(self):
        """Szerokosc kazdej kolumny wg najdluzszego tekstu (w granicach COLUMN_MAX)."""
        from tkinter import font as tkfont
        style = ttk.Style()
        try:
            fnt = tkfont.nametofont(style.lookup("Treeview", "font") or "TkDefaultFont")
        except tk.TclError:
            fnt = tkfont.nametofont("TkDefaultFont")
        try:
            hfnt = tkfont.nametofont(style.lookup("Treeview.Heading", "font") or "TkHeadingFont")
        except tk.TclError:
            hfnt = fnt
        rows = list(self.by_iid.values())[:1500]   # probka wystarczy, a duze listy nie spowalniaja
        for i, (key, header, _w) in enumerate(COLUMNS):
            if key == "check":
                continue
            width = hfnt.measure(header) + 24
            texts = {self._row_values(r)[i] for r in rows}
            for t in texts:
                width = max(width, fnt.measure(str(t)) + 18)
            width = min(width, COLUMN_MAX.get(key, 180))
            self.tree.column(key, width=width)
        self._save_column_widths()

    def _save_column_widths(self):
        self.cfg["col_widths"] = {c[0]: int(self.tree.column(c[0], "width")) for c in COLUMNS}
        save_config(self.cfg)

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
            if key == "old_name":
                nd = r.get("name_data") or {}
                lp = str(nd.get("lp") or "")
                if lp.isdigit():
                    # kolekcje: lp liczone od nowa w kazdej dekadzie - najpierw rok z nazwy, potem lp
                    title = str(nd.get("title") or "").lower()
                    return (False, (0, title, int(nd.get("year") or 0), int(lp), r.get("old_name", "")))
                return (False, (1, str(r.get("old_name") or "").lower()))
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
            f"Wg AI:       data {r.get('ai_date') or '-'}, nr {r.get('ai_issue') or '-'}",
            f"Z nazwy:     {self._facts_text(r)}",
            f"Z raportu:   {report_import.describe(r) or '-'}",
            f"Model:       {r.get('model')}",
            f"Status:      {r.get('status')}",
            f"Uwagi:       {r.get('note')}",
            f"Do weryfik.: {'; '.join(check_reasons(r)) or '-'}",
        ]
        lines += ["", "--- surowa odpowiedz modelu ---", str(r.get("raw") or "")]
        self.txt_detail.insert("1.0", "\n".join(lines))
        self._load_thumb(r)

    def _load_thumb(self, r: dict):
        self._thumb_token += 1
        token = self._thumb_token
        path = r.get("path")
        page = self.page_index if self.mode == MODE_ISSUES else 0

        def job():
            try:
                img = render.thumbnail(path, width=300, page=page)
            except Exception as exc:
                self.queue.put(("thumb_err", {"token": token, "error": str(exc)}))
                return
            self.queue.put(("thumb", {"token": token, "img": img}))

        threading.Thread(target=job, daemon=True).start()

    @staticmethod
    def _facts_text(r: dict) -> str:
        nf = r.get("name_facts") or {}
        parts = []
        if nf.get("year"):
            parts.append(f"rok {nf['year']}")
        if nf.get("month"):
            parts.append(f"miesiac {int(nf['month']):02d}")
        if nf.get("day"):
            parts.append(f"dzien {int(nf['day']):02d}")
        if nf.get("issue"):
            parts.append(f"nr {nf['issue']}" + (f" {nf['suffix']}" if nf.get("suffix") else ""))
        if not parts:
            return "-"
        return ", ".join(parts) + (" (wzorzec)" if nf.get("source") == "wzorzec" else " (nazwa luzna)")

    # =============================================================== API i model
    def _toggle_key(self):
        self.ent_key.config(show="" if self.var_showkey.get() else "*")

    def _client(self, key: str | None = None, model: str = ""):
        """Klient wybranego dostawcy - z licznikami limitow programu."""
        key = self.var_key.get().strip() if key is None else key
        limits = self._limits()
        cls = GeminiClient if self.var_provider.get() == "gemini" else OpenRouterClient
        return cls(key, model, rpm=max(1, limits["rpm"] or 60), usage=self.usage, limits=limits)

    # ------------------------------------------------------------ limity modelu
    def _limits_key(self, prov: str | None = None) -> str:
        return f"{prov or self.var_provider.get()}|{self.var_model.get().strip()}"

    def _limits(self) -> dict:
        def num(var, default=0):
            try:
                return max(0, int(var.get()))
            except (tk.TclError, ValueError):
                return default
        return {"rpm": num(self.var_rpm, 10), "tpm": num(self.var_tpm), "rpd": num(self.var_rpd)}

    def _load_limits(self):
        """Limity dla wybranego dostawcy i modelu: zapisane albo domyslne z tabeli."""
        lim = (self.cfg.get("limits") or {}).get(self._limits_key())
        if not lim:
            lim = usage.default_limits(self.var_provider.get(), self.var_model.get())
        self.var_rpm.set(int(lim.get("rpm") or 10))
        self.var_tpm.set(int(lim.get("tpm") or 0))
        self.var_rpd.set(int(lim.get("rpd") or 0))
        self._refresh_usage()

    def _store_limits(self, prov: str | None = None):
        if self.var_model.get().strip():
            self.cfg.setdefault("limits", {})[self._limits_key(prov)] = self._limits()

    def default_limits(self):
        lim = usage.default_limits(self.var_provider.get(), self.var_model.get())
        self.var_rpm.set(lim["rpm"])
        self.var_tpm.set(lim["tpm"])
        self.var_rpd.set(lim["rpd"])
        self.save_key()

    def _usage_key(self) -> str:
        return usage.Usage.key(self.var_provider.get(), self.var_model.get().strip(),
                               self.var_key.get().strip())

    def _refresh_usage(self):
        if not hasattr(self, "lbl_usage"):
            return
        if not self.var_model.get().strip():
            self.lbl_usage.config(text="Zuzycie: wybierz model.")
            self.lbl_usage_main.config(text="")
            return
        txt = usage.describe(self.usage.snapshot(self._usage_key()), self._limits())
        self.lbl_usage.config(text="Zuzycie (liczone przez program): " + txt)
        self.lbl_usage_main.config(text=f"{self.var_model.get().strip()}: " + txt.split("  ·  limit")[0])

    def _usage_tick(self):
        try:
            self._refresh_usage()
        except Exception:
            pass
        self.after(2000 if self._busy else 10000, self._usage_tick)

    def show_usage(self):
        snap = self.usage.snapshot(self._usage_key())
        lim = self._limits()
        lines = [f"Dostawca: {PROVIDERS[self.var_provider.get()]}",
                 f"Model: {self.var_model.get() or '-'}", "",
                 usage.describe(snap, lim).replace("  ·  ", "\n"), "",
                 f"Srednio tokenow na skan: {snap.get('tok_per_img') or '? (jeszcze nie zmierzono)'}",
                 "", "Liczniki obejmuja tylko zapytania wyslane przez ten program z tym kluczem."]
        n = sum(1 for r in self.records if r.get("checked"))
        if n:
            lines += ["", f"Dla {n} zaznaczonych plikow:"]
            lines += usage.advice(snap, lim, n, max(1, int(self.var_batch.get() or 1)))
        messagebox.showinfo(APP_NAME, "\n".join(lines))

    # ----------------------------------------------------------- zapisane klucze
    def _saved_keys(self) -> list[dict]:
        return list(self.cfg.get(f"{self.var_provider.get()}_keys") or [])

    def _refresh_saved_keys(self):
        keys = self._saved_keys()
        self.cb_keys.config(values=[f"{k['label']}  (...{k['key'][-4:]})" for k in keys])
        cur = self.var_key.get().strip()
        match = [i for i, k in enumerate(keys) if k["key"] == cur]
        self.var_saved_key.set(self.cb_keys.cget("values")[match[0]] if match else "")

    def use_saved_key(self):
        i = self.cb_keys.current()
        keys = self._saved_keys()
        if 0 <= i < len(keys):
            self.var_key.set(keys[i]["key"])
            self.save_key()
            self.lbl_key_info.config(text="")
            self.log(f"Uzyty zapisany klucz: {keys[i]['label']}")

    def add_saved_key(self):
        from tkinter import simpledialog
        key = self.var_key.get().strip()
        if not key:
            messagebox.showinfo(APP_NAME, "Najpierw wpisz klucz w polu 'Klucz'.")
            return
        keys = self._saved_keys()
        if any(k["key"] == key for k in keys):
            messagebox.showinfo(APP_NAME, "Ten klucz juz jest na liscie.")
            return
        label = simpledialog.askstring(APP_NAME, "Nazwa klucza (np. 'platny', 'darmowy'):",
                                       parent=self)
        if not label:
            return
        keys.append({"label": label.strip(), "key": key})
        self.cfg[f"{self.var_provider.get()}_keys"] = keys
        save_config(self.cfg)
        self._refresh_saved_keys()

    def remove_saved_key(self):
        i = self.cb_keys.current()
        keys = self._saved_keys()
        if 0 <= i < len(keys) and messagebox.askyesno(APP_NAME, f"Usunac z listy klucz "
                                                               f"'{keys[i]['label']}'?"):
            del keys[i]
            self.cfg[f"{self.var_provider.get()}_keys"] = keys
            save_config(self.cfg)
            self._refresh_saved_keys()

    def _store_provider_fields(self, prov: str):
        k_key, k_model, k_rpm, _ = PROVIDER_CFG[prov]
        self.cfg[k_key] = self.var_key.get().strip()
        self.cfg[k_model] = self.var_model.get().strip()
        try:
            self.cfg[k_rpm] = int(self.var_rpm.get())
        except (tk.TclError, ValueError):
            pass
        self._store_limits(prov)

    def switch_provider(self):
        """Zmiana dostawcy: zapamietuje pola poprzedniego, wczytuje pola nowego."""
        old, new = self._provider_shown, self.var_provider.get()
        if old == new:
            return
        self._store_provider_fields(old)
        k_key, k_model, k_rpm, d_rpm = PROVIDER_CFG[new]
        self.var_key.set(self.cfg.get(k_key, ""))
        self.var_model.set(self.cfg.get(k_model, ""))
        self.var_rpm.set(int(self.cfg.get(k_rpm, d_rpm)))
        self._provider_shown = new
        self.cfg["provider"] = new
        save_config(self.cfg)
        self.tree_models.delete(*self.tree_models.get_children())
        self.lbl_key_info.config(text="")
        self._load_limits()
        self._update_provider_ui()
        self.log(f"Dostawca AI: {PROVIDERS[new]}")

    def _update_provider_ui(self):
        prov = self.var_provider.get()
        self.box_key.config(text=f"Klucz API {PROVIDERS[prov]}")
        if prov == "gemini":
            self.lbl_provider_hint.config(text=(
                "Klucz: aistudio.google.com/apikey. Darmowe limity zaleza od modelu "
                "(Flash-Lite ma ich najwiecej); aktualne pokazuje AI Studio.\n"
                "Odpowiedz ma wymuszony format JSON, wiec rzadziej sie psuje."))
            self.lbl_kind_hint.config(text="(darmowe wg nazwy modelu - to przyblizenie, "
                                           "odpowiedzialnosc po stronie uzytkownika)")
        else:
            self.lbl_provider_hint.config(text=(
                "Klucz: openrouter.ai/keys. Darmowo 50 zapytan/dzien, "
                "po jednorazowym zakupie 10 kredytow 1000/dzien."))
            self.lbl_kind_hint.config(text="")
        self._refresh_saved_keys()
        custom = bool((self.cfg.get("prompt_rules") or "").strip())
        notes = bool((self.cfg.get("collection_notes") or "").strip())
        self.lbl_prompt.config(text=("wlasne zasady" if custom else "zasady domyslne") +
                               (" + uwagi o kolekcji" if notes else ""))

    def save_key(self):
        self._store_provider_fields(self.var_provider.get())
        self.cfg.update({
            "provider": self.var_provider.get(),
            "batch_size": int(self.var_batch.get()),
            "use_cache": bool(self.var_cache.get()),
            "only_free": bool(self.var_only_free.get()),
            "model_kind": self.var_model_kind.get(),
        })
        save_config(self.cfg)
        self._refresh_saved_keys()
        self._refresh_usage()
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
            info = self._client(key).key_info()
        except Exception as exc:
            self.lbl_key_info.config(text=str(exc), foreground="#b00020")
            self.log(f"Test klucza: BLAD - {exc}")
            self.set_status("Klucz niepoprawny.")
            return
        if self.var_provider.get() == "gemini":
            txt = (f"Klucz dziala. Dostepnych modeli czytajacych obrazy: {info.get('models')}.\n"
                   f"Gemini nie podaje limitow przez API - program liczy zuzycie sam "
                   f"(ponizej, w 'Parametry przetwarzania'); faktyczne limity pokazuje AI Studio.")
        else:
            fm = info.get("free_model_daily_requests") or {}
            txt = (f"Klucz dziala. Etykieta: {info.get('label') or '-'}\n"
                   f"Zuzycie (dzis): {info.get('usage_daily')}  ·  limit klucza: {info.get('limit')}\n"
                   f"Darmowe zapytania dzis: {fm.get('used', '?')}/{fm.get('limit', '?')} "
                   f"(pozostalo {fm.get('remaining', '?')})")
        self.lbl_key_info.config(text=txt, foreground="#0a6b2e")
        self.log("Test klucza: OK. " + txt.replace("\n", " | "))
        self.set_status("Klucz poprawny.")

    def _kind_key(self) -> str:
        label = self.var_model_kind.get()
        return next((k for t, k in MODEL_KINDS if t == label), "obrazy")

    def _recent_models(self) -> list[str]:
        return list(self.cfg.get(f"recent_models_{self.var_provider.get()}") or [])

    def _touch_recent_model(self, model_id: str):
        if not model_id:
            return
        key = f"recent_models_{self.var_provider.get()}"
        lst = [m for m in (self.cfg.get(key) or []) if m != model_id]
        self.cfg[key] = ([model_id] + lst)[:10]
        save_config(self.cfg)

    def fetch_models(self):
        self.set_status("Pobieram liste modeli...")
        self.update_idletasks()
        gemini = self.var_provider.get() == "gemini"
        kind = self._kind_key()
        only_free = self.var_only_free.get()
        rows, err = [], None
        try:
            if gemini:
                for m in self._client().models(kind):
                    free = is_free_guess(m["id"])
                    if only_free and not free:
                        continue
                    rows.append((m["id"], m.get("displayName", ""), m.get("inputTokenLimit", ""),
                                 "chyba tak" if free else "raczej nie", m.get("kind", "")))
            else:
                for m in OpenRouterClient(self.var_key.get().strip()).models(kind, only_free=only_free):
                    rows.append((m.get("id", ""), m.get("name", ""), m.get("context_length", ""),
                                 "tak" if OpenRouterClient.is_free(m) else "nie", m.get("kind", "")))
        except Exception as exc:
            err = exc
        if err is not None and gemini:
            # bez listy tez da sie pracowac - pokazujemy modele zapasowe
            rows = [(m, "(lista zapasowa)", "", "", "obrazy") for m in FALLBACK_MODELS]
            self.log(f"Nie udalo sie pobrac listy modeli Gemini ({err}) - pokazuje liste zapasowa.")
        elif err is not None:
            messagebox.showerror(APP_NAME, f"Nie udalo sie pobrac listy modeli:\n{err}")
            self.set_status("Blad pobierania modeli.")
            return
        # ostatnio uzywane na gorze (w kolejnosci uzycia), reszta bez zmian
        recent = self._recent_models()
        order = {m: i for i, m in enumerate(recent)}
        rows.sort(key=lambda r: order.get(r[0], len(order)))
        self.tree_models.delete(*self.tree_models.get_children())
        for values in rows:
            is_recent = values[0] in order
            vals = list(values)
            if is_recent:
                vals[1] = (str(vals[1]) + "  [ostatnio uzywany]").strip()
            self.tree_models.insert("", "end", values=vals, tags=("recent",) if is_recent else ())
        self.log(f"Pobrano {len(rows)} modeli (rodzaj: {self.var_model_kind.get()}"
                 f"{', tylko darmowe' if only_free else ''}).")
        self.set_status(f"Znaleziono {len(rows)} pasujacych modeli.")

    def choose_model(self):
        sel = self.tree_models.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Zaznacz model na liscie.")
            return
        model_id = self.tree_models.item(sel[0], "values")[0]
        self._store_limits()
        self.var_model.set(model_id)
        self._load_limits()
        self._touch_recent_model(model_id)
        self.save_key()
        self.log(f"Wybrano model: {model_id}")

    def edit_prompt(self):
        PromptDialog(self, self.cfg.get("prompt_rules") or "",
                     self.cfg.get("collection_notes") or "", self._prompt_saved)

    def _prompt_saved(self, rules: str, notes: str):
        self.cfg["prompt_rules"] = rules
        self.cfg["collection_notes"] = notes
        save_config(self.cfg)
        self._update_provider_ui()
        self.log("Zapisano zasady odczytu i uwagi o kolekcji.")

    # =============================================================== odczyt AI
    def start_read(self, records: list[dict] | None = None, use_cache: bool | None = None,
                   detail: bool = False, confirm: bool = True):
        """Odczyt AI zaznaczonych plikow (albo podanej listy - ponowny odczyt)."""
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
        if not self.ensure_mode():
            return

        todo = records if records is not None else [r for r in self.records if r.get("checked")]
        if not todo:
            messagebox.showinfo(APP_NAME, "Nie zaznaczono zadnego pliku.")
            return

        batch = 1 if detail else max(1, int(self.var_batch.get()))
        self.save_key()
        if confirm:
            what = ("dzien i miesiac (reszta z nazwy pliku)" if self.mode == MODE_PAGES
                    else f"data i numer wydania ze strony {self.page_index + 1}")
            snap = self.usage.snapshot(self._usage_key())
            lines = [f"Tryb: {MODES[self.mode]} - AI ustala: {what}.",
                     f"Dostawca: {PROVIDERS[self.var_provider.get()]}, model {self.var_model.get()}",
                     f"Plikow do odczytu: {len(todo)}"
                     + (" (czesc moze byc w cache)" if (self.var_cache.get() if use_cache is None
                                                        else use_cache) else ""), ""]
            lines += usage.advice(snap, self._limits(), len(todo), batch)
            if not messagebox.askyesno(APP_NAME, "\n".join(lines) + "\n\nRozpoczac?"):
                return

        self._touch_recent_model(self.var_model.get().strip())
        for r in todo:
            r["_pending"] = True      # zdejmowane, gdy przyjdzie wynik tego pliku
        self._set_running(True)
        self.worker = ReadWorker(todo, self._client(), self.var_model.get().strip(),
                                 self.queue, batch_size=batch,
                                 use_cache=self.var_cache.get() if use_cache is None else use_cache,
                                 rules=self.cfg.get("prompt_rules"),
                                 notes=self.cfg.get("collection_notes"),
                                 detail=detail, mode=self.mode, page=self.page_index)
        self.worker.start()
        self.log(f"Start odczytu: {len(todo)} plikow, {MODES[self.mode]}"
                 + (f", strona {self.page_index + 1}" if self.mode == MODE_ISSUES else "")
                 + f", {PROVIDERS[self.var_provider.get()]}, model {self.var_model.get()}"
                 + (", obraz dokladny" if detail else "") + ".")

    def reread_selected(self, detail: bool):
        """Ponowny odczyt AI podswietlonych wierszy - zawsze nowe zapytanie (bez cache)."""
        recs = [self.by_iid[i] for i in self.tree.selection() if i in self.by_iid]
        if not recs:
            messagebox.showinfo(APP_NAME, "Podswietl wiersze do ponownego odczytu.")
            return
        manual = [r for r in recs if "recznie" in (r.get("status") or "")]
        if manual and not messagebox.askyesno(
                APP_NAME, f"{len(manual)} z nich poprawiono recznie. Odczytac je ponownie "
                          "(reczna poprawka zostanie zastapiona odczytem AI)?"):
            recs = [r for r in recs if r not in manual]
        for r in recs:
            if "recznie" in (r.get("status") or ""):
                r["status"] = "nowy"
        if recs:
            self.start_read(recs, use_cache=False, detail=detail)

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
        if kind == "bg_progress":
            if getattr(self, "_bg_indeterminate", False):
                self.lbl_op.config(text=f"{data['op']}: znaleziono {data['done']}")
            else:
                self.set_progress(data["done"], data["total"], data["op"], data["elapsed"])
        elif kind == "bg_done":
            self._bg_busy = False
            was_text_only = getattr(self, "_bg_indeterminate", False)
            self._bg_indeterminate = False
            if was_text_only:
                self.clear_progress()
            else:
                self.finish_progress(getattr(self, "_last_total", 1), getattr(self, "_bg_op", ""))
            data["cb"](data["res"], data["err"])
        elif kind == "report_progress":
            self.set_progress(data["done"], data["total"], data["op"])
        elif kind == "report_loaded":
            self._reports_loaded(data)
        elif kind == "record":
            r = data["rec"]
            r.pop("_pending", None)
            self.compute_name(r)
            self.update_row(r)
            self.mark_dirty()
        elif kind == "plan":
            self.log(f"Plan: {data['total_files']} plikow, z cache {data['from_cache']}, "
                     f"do wyslania {data['to_send']} w {data['batches']} zapytaniach.")
            self._plan_total = data["to_send"]
        elif kind == "progress":
            self.set_progress(data["done"], data["total"], "Odczyt AI", data["elapsed"])
            self._refresh_usage()
        elif kind == "batch_start":
            self.set_status(f"Zapytanie {data['index']}/{data['count']}...")
        elif kind == "waiting":
            self.log(f"Czekam {data['delay']:.0f}s przed ponowieniem (proba {data['attempt']}).")
            self.set_status(f"Limit/blad - ponawiam za {data['delay']:.0f}s...")
        elif kind == "throttle":
            self.set_status(f"Limit na minute wg licznika programu - czekam {data['delay']:.0f}s...")
            self._refresh_usage()
        elif kind == "paused":
            self.set_status("Wstrzymano.")
        elif kind == "resumed":
            self.set_status("Wznowiono.")
        elif kind == "log":
            self.log(data["text"])
        elif kind == "fatal":
            self.log("BLAD KRYTYCZNY: " + data["error"])
            messagebox.showerror(APP_NAME, data["error"])
        elif kind == "daily_limit":
            self.log("LIMIT DZIENNY: " + data["error"])
            self._daily_limit_msg = data["error"]
        elif kind == "finished":
            self._set_running(False)
            msg = (f"Zakonczono{' (przerwane)' if data['stopped'] else ''}: "
                   f"{data['done']}/{data['total']} wyslanych, "
                   f"z cache {data['from_cache']}, czas {fmt_time(data['elapsed'])}.")
            self.log(msg)
            self.set_status(msg)
            if data["stopped"]:
                self.clear_progress()
            else:
                self.finish_progress(data["total"], "Odczyt AI")
            rs = self.run_stats
            rs["ai_seconds"] = float(rs.get("ai_seconds") or 0) + float(data["elapsed"])
            rs["ai_sent"] = int(rs.get("ai_sent") or 0) + int(data["done"])
            rs["ai_cache"] = int(rs.get("ai_cache") or 0) + int(data["from_cache"])
            rs["runs"] = int(rs.get("runs") or 0) + 1
            self.mark_dirty()
            self.recompute_all_names()
            self.autofit_columns()
            self._refresh_usage()
            pending = [r for r in (self.worker.records if self.worker else []) if r.pop("_pending", False)]
            if data.get("daily_limit") and self.worker is not None:
                # do wznowienia zostaja zaznaczone tylko pliki, ktorych nie odczytano
                ids = {id(r) for r in pending}
                for r in self.worker.records:
                    r["checked"] = id(r) in ids
                left = len(pending)
                self.refresh_tree()
                messagebox.showwarning(APP_NAME, getattr(self, "_daily_limit_msg", "Limit dzienny.")
                                       + f"\n\nNieodczytanych plikow: {left} - zostaly zaznaczone, "
                                         "wystarczy pozniej kliknac 'Odczytaj daty za pomoca AI'.")
            else:
                left = sum(1 for r in self.records if needs_check(r))
                green = sum(1 for r in self.records if status_group(r) == "certain")
                self.set_status(msg + f" Zielone: {green}, do weryfikacji: {left}.")
                if not data["stopped"] and data["done"] + data["from_cache"] > 0:
                    self.after(300, self.show_run_summary)
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
        self._run_report_job(list(paths), quiet=False)

    def _run_report_job(self, paths: list[str], quiet: bool):
        if self._importing:
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
            self.queue.put(("report_loaded", {"loaded": loaded, "errors": errors, "quiet": quiet,
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
        counts = {"znaleziony": 0, "reczny": 0, "": 0}
        for r in self.records:
            res = report_import.apply(r, self.report_index.find(r))
            counts[res] += 1
            if res == "znaleziony":
                self.assess(r)
        self.recompute_all_names()
        self.mark_dirty()
        read = [r for r in self.records if r.get("report_data") and r.get("ai")]
        diff = sum(1 for r in read if any("raport" in x for x in r.get("issues") or []))
        msg = (f"Wczytano raportow: {len(data['loaded'])}\n\n"
               f"Plikow z listy znalezionych w raportach: {counts['znaleziony']} z {len(self.records)}\n"
               f"  - juz odczytanych przez AI: {len(read)}, w tym niezgodnych z raportem: {diff}\n"
               f"  - pominiete (poprawione recznie): {counts['reczny']}\n\n"
               "Raport nie zmienia danych - jest porownywany z odczytem AI, tak jak nazwa pliku. "
               "Zgodne = zielone, niezgodne = do weryfikacji.")
        if data["errors"]:
            msg += "\n\nNie wczytano:\n" + "\n".join(data["errors"])
        self.log(msg.replace("\n\n", " | ").replace("\n", " "))
        self.set_status(f"Raporty: znaleziono {counts['znaleziony']} plikow, "
                        f"niezgodnych z odczytem AI: {diff}.")
        if not data.get("quiet"):
            messagebox.showinfo(APP_NAME, msg)

    # =============================================================== wzorce nazw
    def patterns(self) -> list:
        return filename_patterns.BUILTIN + filename_patterns.user_patterns(self.session_patterns)

    def apply_name_data(self, r: dict, patterns=None):
        """Dane z nazwy pliku -> rekord. Tytul, numer i strona od razu trafiaja do pol
        nowego rekordu; wszystko, co jednoznaczne (rok, data, numer), sluzy potem do
        porownania z odczytem AI."""
        nd = filename_patterns.parse(r.get("old_name") or Path(r["path"]).name,
                                     patterns if patterns is not None else self.patterns())
        r["name_data"] = nd
        r["pattern"] = nd["pattern"] if nd else "luzne"
        r["name_facts"] = name_facts.facts(r) or None
        st = (r.get("status") or "").lower()
        if nd and st in ("nowy", ""):
            if nd.get("title") and not r.get("title"):
                r["title"] = nd["title"]
            if nd.get("issue"):
                r["issue_number"], r["issue_suffix"] = nd["issue"], nd.get("suffix")
            if nd.get("page"):
                r["page_number"] = nd["page"]
        elif nd and self.mode == MODE_PAGES and "recznie" not in st:
            # kolekcja stron: numer wydania i strona sa w nazwie pliku - ona wygrywa
            # z odczytem AI i ze starymi danymi sesji (np. sprzed poprawki wzorca -ORG)
            if nd.get("issue"):
                r["issue_number"], r["issue_suffix"] = nd["issue"], nd.get("suffix")
            if nd.get("page"):
                r["page_number"] = nd["page"]
        self.assess(r)
        self.compute_name(r)

    def assess(self, r: dict):
        """Zielony albo do weryfikacji - wg odpowiedzi AI, nazwy pliku i raportu."""
        assess.evaluate(r, self.mode or MODE_ISSUES)

    def edit_patterns(self):
        PatternsDialog(self, self.session_patterns, self.cfg.get("pattern_history") or [],
                       [r.get("old_name") or "" for r in self.records], self.set_patterns)

    def set_patterns(self, items: list[dict]):
        self.session_patterns = items
        hist = [h for h in self.cfg.get("pattern_history") or [] if h not in items]
        self.cfg["pattern_history"] = (items + hist)[:20]
        save_config(self.cfg)
        pats = self.patterns()
        for r in self.records:
            self.apply_name_data(r, pats)
        self.refresh_tree()
        self.autofit_columns()
        self.mark_dirty()
        summary = self.pattern_summary(self.records)
        self.log(f"Wzorce nazw zmienione. {summary}")
        self.set_status(f"Wzorce nazw: {summary}")

    @staticmethod
    def pattern_summary(recs: list[dict]) -> str:
        counts: dict[str, int] = {}
        for r in recs:
            counts[r.get("pattern") or "luzne"] = counts.get(r.get("pattern") or "luzne", 0) + 1
        return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))

    # =============================================================== tryb sesji
    def _update_mode_label(self):
        if not hasattr(self, "lbl_mode"):
            return
        if not self.mode:
            self.lbl_mode.config(text="Tryb sesji: nie wybrano (program zapyta przy dodaniu plikow)")
        elif self.mode == MODE_PAGES:
            self.lbl_mode.config(text="Tryb: Kolekcja stron - AI ustala dzien i miesiac, "
                                      "tytul, rok, numer i strona z nazwy pliku")
        else:
            self.lbl_mode.config(text=f"Tryb: Kolekcja wydan - AI ustala tytul, date i numer "
                                      f"ze strony {self.page_index + 1} PDF-a")

    def ensure_mode(self) -> bool:
        """Tryb musi byc wybrany, zanim cokolwiek zostanie odczytane."""
        if self.mode:
            return True
        choice = ModeDialog.ask(self, "Tryb sesji", assess.guess_mode(self.records) if self.records else None, 0)
        if choice is None:
            return False
        self.mode, self.page_index = choice
        self._update_mode_label()
        for r in self.records:
            self.assess(r)
            self.compute_name(r)
        self.refresh_tree()
        self.mark_dirty()
        return True

    def session_settings(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "Trwa odczyt - poczekaj, az sie skonczy.")
            return
        choice = ModeDialog.ask(self, "Ustawienia sesji", self.mode, self.page_index)
        if choice is None or choice == (self.mode, self.page_index):
            return
        old_page = self.page_index
        self.mode, self.page_index = choice
        self._update_mode_label()
        for r in self.records:
            self.assess(r)
            self.compute_name(r)
        self.refresh_tree()
        self.mark_dirty()
        self.log(f"Ustawienia sesji: {MODES[self.mode]}"
                 + (f", strona dla AI: {self.page_index + 1}" if self.mode == MODE_ISSUES else ""))
        if self.mode == MODE_ISSUES and old_page != self.page_index and any(r.get("ai") for r in self.records):
            messagebox.showinfo(APP_NAME, "Nowa strona dotyczy kolejnych odczytow. Pliki odczytane "
                                          "wczesniej (z innej strony) zaznacz i odczytaj ponownie - "
                                          "cache ma osobne wpisy dla kazdej strony.")

    # =============================================================== nazwy
    def compute_name(self, r: dict):
        name, err = naming.build_new_name(r, self.var_title_override.get().strip())
        r["new_name"] = name
        r["name_complete"] = not err
        r["name_missing"] = err
        return name

    def recompute_all_names(self):
        for r in self.records:
            self.compute_name(r)
        self.refresh_tree()
        ready = sum(1 for r in self.records if r.get("name_complete"))
        self.set_status(f"Kompletne nowe nazwy: {ready}/{len(self.records)} plikow.")

    def edit_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Zaznacz wiersz do edycji.")
            return
        r = self.by_iid.get(sel[0])
        if r:
            EditDialog(self, r, self._after_edit)

    def open_verify(self):
        """Okno z duzym podgladem strony - najpierw wybor zakresu rekordow."""
        sel = [self.by_iid[i] for i in self.tree.selection() if i in self.by_iid]
        VerifyScopeDialog(self, len(sel), self._verify_run)

    def verify_scope(self, scope: str, only_check: bool) -> list[dict]:
        visible = {id(r) for r in self.by_iid.values()}
        if scope == "podswietlone":
            recs = [self.by_iid[i] for i in self.tree.selection() if i in self.by_iid]
        elif scope == "widoczne":
            recs = [r for r in self.records if id(r) in visible]
        elif scope == "zaznaczone":
            recs = [r for r in self.records if r.get("checked")]
        elif scope == "widoczne_zaznaczone":
            recs = [r for r in self.records if id(r) in visible and r.get("checked")]
        else:
            recs = list(self.records)
        if only_check:
            recs = [r for r in recs if needs_check(r)]
        return recs

    def _verify_run(self, scope: str, only_check: bool):
        recs = self.verify_scope(scope, only_check)
        if not recs:
            messagebox.showinfo(APP_NAME, "W wybranym zakresie nie ma rekordow do weryfikacji.")
            return
        VerifyDialog(self, recs, self._after_edit, self._after_verify,
                     start_page=self.page_index if self.mode == MODE_ISSUES else 0,
                     pages_mode=self.mode == MODE_PAGES)

    def _after_verify(self, saved: int):
        self.refresh_tree()
        left = sum(1 for r in self.records if needs_check(r))
        self.set_status(f"Weryfikacja: poprawiono {saved}, do weryfikacji zostalo {left}.")

    def _after_edit(self, r: dict):
        r["status"] = "poprawione recznie"
        r["issues"] = None
        r["note"] = ""
        self.compute_name(r)
        self.update_row(r)
        self.mark_dirty()

    # =============================================================== rename
    def rename_scope(self, scope: str) -> list[dict]:
        visible = {id(r) for r in self.by_iid.values()}
        certain = lambda r: status_group(r) == "certain"  # noqa: E731
        if scope == "pewne":
            return [r for r in self.records if certain(r)]
        if scope == "zaznaczone":
            return [r for r in self.records if r.get("checked")]
        if scope == "widoczne":
            return [r for r in self.records if id(r) in visible]
        return [r for r in self.records if id(r) in visible and certain(r)]

    def do_rename(self):
        for r in self.records:
            self.compute_name(r)
        RenameDialog(self, self._rename_run)

    def _rename_run(self, scope: str, include_incomplete: bool):
        todo = [r for r in self.rename_scope(scope)
                if "zmieniono" not in (r.get("status") or "")]
        if not include_incomplete:
            todo = [r for r in todo if r.get("name_complete")]
        if not todo:
            messagebox.showinfo(APP_NAME, "W wybranym zakresie nie ma plikow do zmiany nazwy.")
            return

        override = self.var_title_override.get().strip()
        self.set_status("Planowanie zmiany nazw...")
        self._run_bg("Planowanie", lambda prog: rename_ops.plan_renames(todo, override, progress=prog),
                     lambda res, err: self._rename_planned(res, err))

    def _rename_planned(self, res, err):
        if err:
            messagebox.showerror(APP_NAME, f"Blad planowania:\n{err}")
            return
        plan, skipped = res
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

        def done(res2, err2):
            self.refresh_tree()
            self.mark_dirty()
            if err2:
                messagebox.showerror(APP_NAME, f"Blad zmiany nazw:\n{err2}")
                return
            log_path, done_l, errors = res2
            self.log(f"Zmieniono nazwy: {len(done_l)}, bledow: {len(errors)}. Log: {log_path}")
            messagebox.showinfo(APP_NAME,
                                f"Zmieniono nazwy: {len(done_l)}\nBledy: {len(errors)}\n"
                                f"Pominiete: {len(skipped)}\n\nLog operacji:\n{log_path}")

        self._run_bg("Zmiana nazw", lambda prog: rename_ops.apply_renames(plan, progress=prog), done)

    def undo_rename(self):
        logs = rename_ops.list_logs()
        if not logs:
            messagebox.showinfo(APP_NAME, "Brak zapisanych operacji zmiany nazw.")
            return
        UndoDialog(self, logs, self._do_undo)

    def _do_undo(self, log_path: Path):
        def done(res, err):
            self.refresh_tree()
            self.mark_dirty()
            if err:
                messagebox.showerror(APP_NAME, f"Blad cofania:\n{err}")
                return
            restored, errors = res
            self.log(f"Cofnieto {restored} zmian nazw, bledow: {len(errors)}.")
            msg = f"Cofnieto: {restored}\nBledy: {len(errors)}"
            if errors:
                msg += "\n\n" + "\n".join(f"{Path(e['dst']).name}: {e['error']}" for e in errors[:8])
            messagebox.showinfo(APP_NAME, msg)

        self._run_bg("Cofanie", lambda prog: rename_ops.undo_renames(
            log_path, self.records, progress=prog), done)

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
        choice = ModeDialog.ask(self, "Nowa sesja", None, 0)
        if choice is None:
            return
        self.records = []
        self.session_path = None
        self.session_patterns = []
        self.import_dirs = []
        self.run_stats = {"ai_seconds": 0.0, "ai_sent": 0, "ai_cache": 0, "runs": 0}
        self.mode, self.page_index = choice
        self._update_mode_label()
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
            title="Otworz sesje", initialdir=self._session_dir(),
            filetypes=[("Sesja programu", f"*{SESSION_EXT}"), ("Wszystkie pliki", "*.*")])
        if path:
            self._load_session(path)

    def open_last_session(self):
        recent = self._recent_sessions()
        if not recent:
            messagebox.showinfo(APP_NAME, "Nie ma jeszcze zapisanych ani otwieranych sesji.")
            return
        if self._confirm_discard():
            self._load_session(recent[0])

    def _open_recent(self, path: str):
        if self._confirm_discard():
            self._load_session(path)

    def _recent_sessions(self) -> list[str]:
        return [p for p in self.cfg.get("recent_sessions", []) if Path(p).exists()]

    def _remember_session(self, path: str):
        path = str(Path(path).resolve())
        lst = [p for p in self.cfg.get("recent_sessions", []) if p != path]
        self.cfg["recent_sessions"] = [path] + lst[:9]
        self.cfg["last_session_dir"] = str(Path(path).parent)
        save_config(self.cfg)

    def _build_recent_menu(self):
        self.menu_recent.delete(0, "end")
        recent = self._recent_sessions()
        if not recent:
            self.menu_recent.add_command(label="(brak)", state="disabled")
            return
        for i, p in enumerate(recent, start=1):
            pp = Path(p)
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(pp.stat().st_mtime))
            self.menu_recent.add_command(label=f"{i}. {pp.stem}   ({stamp}, {pp.parent})",
                                         command=lambda p=p: self._open_recent(p))

    def _session_dir(self) -> str:
        last = self.cfg.get("last_session_dir")
        if last and Path(last).is_dir():
            return last
        recent = self._recent_sessions()
        if recent:
            return str(Path(recent[0]).parent)
        if self.records:
            return str(Path(self.records[0]["path"]).parent)
        return str(Path.home())

    def default_session_name(self) -> str:
        """'Jornal do Brasil (RJ) - 1970-1979' - najczestszy tytul i dominujaca dekada."""
        title = self.var_title_override.get().strip()
        if not title:
            counts: dict[str, int] = {}
            for r in self.records:
                t = (r.get("title") or "").strip()
                if t:
                    counts[t] = counts.get(t, 0) + 1
            title = max(counts, key=counts.get) if counts else "Sesja"
        title = re.sub(r'[\\/:*?"<>|]+', "-", title).strip(" .") or "Sesja"
        decades: dict[int, int] = {}
        for r in self.records:
            m = re.match(r"(\d{4})-", r.get("date_iso") or "")
            year = int(m.group(1)) if m else (r.get("name_data") or {}).get("year")
            if year:
                decades[int(year) // 10 * 10] = decades.get(int(year) // 10 * 10, 0) + 1
        if decades:
            d = max(decades, key=decades.get)
            return f"{title} - {d}-{d + 9}"
        return title

    def _load_session(self, path: str):
        try:
            records, meta = session_io.load_session(path)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Nie udalo sie wczytac sesji:\n{exc}")
            return
        self.records = records
        self.session_path = path
        if meta.get("model") and meta.get("provider", "openrouter") == self.var_provider.get():
            self.var_model.set(meta["model"])
            self._load_limits()
        if meta.get("title_override"):
            self.var_title_override.set(meta["title_override"])
        self.session_patterns = list(meta.get("patterns") or [])
        self.run_stats = {"ai_seconds": 0.0, "ai_sent": 0, "ai_cache": 0, "runs": 0,
                          **(meta.get("run_stats") or {})}
        self.import_dirs = list(meta.get("import_dirs") or [])
        if not self.import_dirs:
            # starsza sesja: foldery najczesciej wystepujace w sciezkach plikow
            from collections import Counter
            cnt = Counter(str(Path(r.get("path", "")).parent) for r in self.records if r.get("path"))
            self.import_dirs = [d for d, _ in cnt.most_common(5)]
        mode = meta.get("mode")
        page = int(meta.get("page") or 1) - 1
        if mode not in MODES:
            # sesja ze starszej wersji programu - pytamy o tryb, z propozycja wg typu plikow
            choice = ModeDialog.ask(self, f"Tryb sesji: {Path(path).stem}", assess.guess_mode(records), 0,
                                    info="Ta sesja pochodzi ze starszej wersji programu i nie ma "
                                         "zapisanego trybu. Wybierz go - program zapamieta wybor "
                                         "przy zapisie sesji.")
            mode, page = choice if choice else (assess.guess_mode(records), 0)
            migrated = True
        else:
            migrated = False
        self.mode, self.page_index = mode, max(0, page)
        self._update_mode_label()
        legacy = 0
        for r in self.records:
            legacy += self._migrate_record(r)
            self.apply_name_data(r)      # dane z nazwy + ocena (zielony / do weryfikacji)
        missing = sum(1 for r in self.records if not Path(r.get("path", "")).exists())
        self.refresh_tree()
        self.autofit_columns()
        self.mark_dirty(migrated)
        self._remember_session(path)
        self.title(f"{APP_NAME} {APP_VERSION} - {Path(path).stem}{' *' if migrated else ''}")
        self.log(f"Wczytano sesje: {path} ({len(records)} rekordow, brakujacych plikow: {missing}, "
                 f"tryb: {MODES[self.mode]})")
        if legacy:
            self.log(f"Przeniesiono {legacy} rekordow ze starszej wersji programu - ocenione od nowa "
                     "wg odpowiedzi AI, nazwy pliku i raportow (bez kalendarza wydan).")
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
            initialdir=self._session_dir(),
            initialfile=self.default_session_name() + SESSION_EXT,
            filetypes=[("Sesja programu", f"*{SESSION_EXT}")])
        if not path:
            return False
        self.session_path = path
        return self._write_session(path)

    def _migrate_record(self, r: dict) -> int:
        """Rekord ze starszej sesji: odpowiedz AI do pola 'ai', dane z raportu do
        'report_data', a wartosci wstawione przez dawne kontrole (kalendarz wydan,
        strony wydania) usuniete. Zwraca 1, gdy cos przeniesiono."""
        if r.get("ai") is not None or r.get("issues") is not None:
            return 0
        st = (r.get("status") or "").lower()
        if "recznie" in st or "zmieniono" in st or "cofnieto" in st or st in ("", "nowy"):
            return 0
        if "blad" in st:
            return 0
        if "raport" in st and not r.get("model"):
            # dawniej dane z raportu wpisywano do rekordu - teraz sa tylko do porownania
            if r.get("date_iso") or r.get("issue_number"):
                r["report_data"] = [{"date_iso": r.get("date_iso"), "issue_number": r.get("issue_number"),
                                     "issue_suffix": r.get("issue_suffix"), "src": "raport (starsza sesja)",
                                     "status": "", "note": ""}]
            for k in ("date_iso", "issue_number", "issue_suffix", "confidence"):
                r[k] = None
            r["status"], r["note"] = "nowy", ""
            return 1
        ai = assess.ai_from_legacy(r)
        if ai is None:
            r["status"], r["note"] = "nowy", ""
            return 1
        r["ai"] = ai
        r["status"] = assess.STATUS_OK
        for k in ("cal_filled", "group_filled"):
            r.pop(k, None)
        return 1

    def _write_session(self, path: str) -> bool:
        meta = {"model": self.var_model.get(), "provider": self.var_provider.get(),
                "patterns": self.session_patterns, "mode": self.mode or MODE_ISSUES,
                "page": self.page_index + 1,
                "title_override": self.var_title_override.get(),
                "run_stats": self.run_stats,
                "import_dirs": self.import_dirs,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            session_io.save_session(path, self.records, meta)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Nie udalo sie zapisac sesji:\n{exc}")
            return False
        self.mark_dirty(False)
        self._remember_session(path)
        self.title(f"{APP_NAME} {APP_VERSION} - {Path(path).stem}")
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
        except ImportError:
            if not self._install_openpyxl():
                csv_path = str(Path(path).with_suffix(".csv"))
                if messagebox.askyesno(APP_NAME, "Nie udalo sie przygotowac eksportu do Excela.\n"
                                                 f"Zapisac zamiast tego plik CSV (Excel go otworzy)?\n\n{csv_path}"):
                    export.export_csv(csv_path, self.records, progress=self._export_prog)
                    self.clear_progress()
                    messagebox.showinfo(APP_NAME, f"Zapisano {len(self.records)} wierszy:\n{csv_path}")
                return
            try:
                export.export_xlsx(path, self.records, progress=self._export_prog)
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Blad eksportu:\n{exc}")
                return
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Blad eksportu:\n{exc}")
            return
        self.clear_progress()
        self.log(f"Eksport XLSX: {path}")
        messagebox.showinfo(APP_NAME, f"Zapisano {len(self.records)} wierszy:\n{path}")

    def _install_openpyxl(self) -> bool:
        """Brak biblioteki do plikow .xlsx - proponujemy doinstalowanie (pip)."""
        if not messagebox.askyesno(APP_NAME, "Do eksportu do Excela potrzebna jest biblioteka "
                                             "'openpyxl', ktorej brakuje.\n\nZainstalowac ja teraz? "
                                             "(potrzebny internet, zwykle kilkanascie sekund)"):
            return False
        import importlib
        import subprocess
        self.set_status("Instaluje openpyxl...")
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            res = subprocess.run([sys.executable, "-m", "pip", "install", "openpyxl"],
                                 capture_output=True, text=True, timeout=300)
            ok = res.returncode == 0
            if not ok:
                self.log("pip install openpyxl: " + (res.stderr or res.stdout)[-800:])
        except Exception as exc:
            ok = False
            self.log(f"pip install openpyxl: {exc}")
        finally:
            self.config(cursor="")
        if ok:
            importlib.invalidate_caches()
            self.log("Zainstalowano openpyxl.")
            return True
        messagebox.showwarning(APP_NAME, "Instalacja sie nie udala. Mozesz sprobowac recznie w konsoli:\n"
                                         "pip install openpyxl\n\nSzczegoly w zakladce Log.")
        return False

    def _export_prog(self, i, total):
        self.set_progress(i, total, "Eksport")
        self.update_idletasks()

    # =============================================================== rozne
    def clear_cache(self):
        n = cache_db.count()
        if not messagebox.askyesno(APP_NAME, f"Cache: {n} zapisanych odczytow, "
                                             f"{cache_db.size_mb():.1f} MB\n({CACHE_DB})\n\n"
                                             f"Usunac wszystkie?\n"
                                             "Ponowny odczyt tych plikow zuzyje limit zapytan."):
            return
        removed = cache_db.clear()
        self.log(f"Wyczyszczono cache ({removed} wpisow).")

    # =============================================================== raport HTML
    def _report_stats(self, collection: str, manual: float, review: float,
                      ai_seconds: float | None) -> dict:
        return report_html.collect_stats(
            self.records, self.run_stats, collection=collection, manual_sec=manual,
            review_sec=review, ai_seconds=ai_seconds, mode_label=MODES.get(self.mode, ""))

    def show_run_summary(self):
        """Podsumowanie po przelocie: wyniki, czas i oszczednosc - z opcja raportu HTML."""
        if not self.records:
            return
        st = self._report_stats("", float(self.cfg.get("manual_sec") or report_html.DEFAULT_MANUAL_SEC),
                                float(self.cfg.get("review_sec") or report_html.DEFAULT_REVIEW_SEC), None)
        est = " (szacunek)" if st["ai_estimated"] else ""
        txt = (f"Przetworzono: {st['processed']} z {st['total']} plikow\n"
               f"  odczytane pewnie:   {st['ok']}  ({st['success_pct']:.0f}%)\n"
               f"  do sprawdzenia:     {st['check']}\n"
               f"  bledy odczytu:      {st['error']}\n"
               f"  nieodczytane:       {st['unread']}\n\n"
               f"Czas odczytu AI:      {fmt_time(st['ai_seconds'])}{est}"
               f"  ({st['per_file']:.1f} s na plik)\n"
               f"Recznie zajeloby:     {fmt_time(st['manual_total'])}\n"
               f"Z programem:          {fmt_time(st['with_program'])}\n\n"
               f"OSZCZEDZONO:          {fmt_time(st['saved'])}  ({st['saved_pct']:.0f}%)\n\n"
               "Wygenerowac raport HTML (do zrzutu ekranu)?")
        if messagebox.askyesno(APP_NAME + " - podsumowanie przelotu", txt):
            self.make_report()

    def make_report(self):
        if not self.records:
            messagebox.showinfo(APP_NAME, "Lista jest pusta - nie ma z czego zrobic raportu.")
            return
        ReportDialog(self, report_html.guess_collection(self.records), self.cfg,
                     self.run_stats.get("ai_seconds") or None, self._build_report)

    def _build_report(self, collection: str, manual: float, review: float,
                      ai_seconds: float | None, with_names: bool):
        self.cfg["manual_sec"], self.cfg["review_sec"] = manual, review
        save_config(self.cfg)
        st = self._report_stats(collection, manual, review, ai_seconds)
        page = report_html.build_html(
            st, report_html.flagged_list(self.records) if with_names else None)
        safe = re.sub(r'[<>:"/\\|?*]+', "", collection).strip() or "kolekcja"
        path = filedialog.asksaveasfilename(
            title="Zapisz raport HTML", defaultextension=".html", initialdir=self._session_dir(),
            initialfile=f"Raport - {safe} - {time.strftime('%Y-%m-%d')}.html",
            filetypes=[("Raport HTML", "*.html")])
        if not path:
            return
        try:
            Path(path).write_text(page, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Nie udalo sie zapisac raportu:\n{exc}")
            return
        self.log(f"Zapisano raport HTML: {path}")
        webbrowser.open(Path(path).resolve().as_uri())

    def show_stats(self):
        total = len(self.records)
        new = sum(1 for r in self.records if status_group(r) == "new")
        err = sum(1 for r in self.records if status_group(r) == "error")
        green = sum(1 for r in self.records if status_group(r) == "certain")
        check = sum(1 for r in self.records if needs_check(r))
        ready = sum(1 for r in self.records if r.get("name_complete"))
        renamed = sum(1 for r in self.records if "zmieniono" in (r.get("status") or ""))
        confs = [r["confidence"] for r in self.records if r.get("confidence") is not None]
        avg = f"{sum(confs) / len(confs):.2f}" if confs else "-"
        messagebox.showinfo(APP_NAME,
                            f"Tryb sesji:              {MODES.get(self.mode, '-')}\n\n"
                            f"Plikow na liscie:        {total}\n"
                            f"Nieczytanych:            {new}\n"
                            f"Zielonych (pewnych):     {green}\n"
                            f"Do weryfikacji:          {check}\n"
                            f"  w tym bledow odczytu:  {err}\n"
                            f"Z gotowa nowa nazwa:     {ready}\n"
                            f"Po zmianie nazwy:        {renamed}\n"
                            f"Srednia pewnosc modelu:  {avg}\n\n"
                            f"Wpisow w cache:          {cache_db.count()}")

    def about(self):
        messagebox.showinfo(
            APP_NAME,
            f"{APP_NAME} {APP_VERSION}\n\n"
            "Odczyt daty i numeru wydania ze skanow czasopism\n"
            "za pomoca modeli multimodalnych (OpenRouter, Google Gemini).\n\n"
            "Kolekcja wydan: AI ustala tytul, date i numer wydania.\n"
            "Kolekcja stron: AI ustala dzien i miesiac, reszta z nazwy pliku.\n\n"
            "Format nazwy:\n"
            "  Tytul - RRRR-MM-DD - 000638.pdf\n"
            "  Tytul - RRRR-MM-DD - 000638 - 015.jpg\n"
            "  kolekcje: nazwa wejsciowa z uzupelniona data\n\n"
            f"Folder programu: {updater.APP_ROOT}\n"
            f"Aktualizacje: {self._update_repo()} / {self._update_branch()}")

    # =============================================================== aktualizacje
    def _update_repo(self) -> str:
        return self.cfg.get("update_repo") or updater.DEFAULT_REPO

    def _update_branch(self) -> str:
        return self.cfg.get("update_branch") or updater.DEFAULT_BRANCH

    def update_source(self):
        from tkinter import simpledialog
        repo = simpledialog.askstring(APP_NAME, "Repozytorium na GitHubie (wlasciciel/nazwa):",
                                      initialvalue=self._update_repo(), parent=self)
        if not repo:
            return
        branch = simpledialog.askstring(APP_NAME, "Galaz:", initialvalue=self._update_branch(),
                                        parent=self)
        if not branch:
            return
        self.cfg["update_repo"], self.cfg["update_branch"] = repo.strip(), branch.strip()
        save_config(self.cfg)
        self.log(f"Zrodlo aktualizacji: {repo} / {branch}")

    def _busy_check(self) -> bool:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "Trwa odczyt - poczekaj, az sie skonczy.")
            return True
        return False

    def check_updates(self):
        if self._busy_check():
            return
        repo, branch = self._update_repo(), self._update_branch()
        self.set_status("Sprawdzam aktualizacje...")
        self.update_idletasks()
        try:
            data = updater.download(repo, branch)
            info = updater.inspect(data)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Nie udalo sie sprawdzic aktualizacji:\n{exc}")
            self.set_status("Blad sprawdzania aktualizacji.")
            return
        if not info["changed"] and not info["added"]:
            messagebox.showinfo(APP_NAME, f"Masz najnowsza wersje ({APP_VERSION}).")
            self.set_status("Program jest aktualny.")
            return
        changes = updater.remote_changes(repo, branch)
        info["notes"] = "\n".join("  - " + c for c in changes[:8])
        self._offer_update(info, f"GitHub: {repo} / {branch}")

    def update_from_zip(self):
        if self._busy_check():
            return
        path = filedialog.askopenfilename(title="Paczka aktualizacji",
                                          filetypes=[("Paczka ZIP", "*.zip"), ("Wszystkie pliki", "*.*")])
        if not path:
            return
        try:
            info = updater.inspect(Path(path).read_bytes())
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Nie mozna uzyc tej paczki:\n{exc}")
            return
        if not info["changed"] and not info["added"]:
            messagebox.showinfo(APP_NAME, "Ta paczka niczego nie zmienia - masz juz te pliki.")
            return
        self._offer_update(info, Path(path).name)

    def _offer_update(self, info: dict, source: str):
        files = info["changed"] + [f + " (nowy)" for f in info["added"]]
        listing = "\n".join("  " + f for f in files[:15]) + (
            f"\n  ... i {len(files) - 15} innych" if len(files) > 15 else "")
        newer = info.get("version") or "?"
        warn = ""
        if newer != "?" and updater.version_tuple(newer) < updater.version_tuple(APP_VERSION):
            warn = "\n\nUWAGA: to STARSZA wersja niz obecna."
        msg = (f"Zrodlo: {source}\nWersja: {APP_VERSION} -> {newer}{warn}\n\n"
               f"Pliki do podmiany ({len(files)}):\n{listing}")
        if info.get("notes"):
            msg += f"\n\nZmiany:\n{info['notes']}"
        msg += ("\n\nPrzed podmiana program zrobi kopie zapasowa i zapisze sesje, "
                "potem uruchomi sie ponownie. Aktualizowac?")
        if not messagebox.askyesno(APP_NAME, msg):
            return
        reopen = self._save_before_restart()
        if reopen is False:
            return
        try:
            backup = updater.apply(info)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Aktualizacja nie powiodla sie:\n{exc}")
            return
        self.log(f"Zaktualizowano do {newer}. Kopia zapasowa: {backup}")
        messagebox.showinfo(APP_NAME, f"Zaktualizowano ({len(files)} plikow).\n"
                                      "Program uruchomi sie ponownie.")
        self._restart(reopen)

    def rollback_update(self):
        if self._busy_check():
            return
        lst = updater.backups()
        if not lst:
            messagebox.showinfo(APP_NAME, "Nie ma kopii zapasowej do przywrocenia.")
            return
        if not messagebox.askyesno(APP_NAME, f"Przywrocic pliki programu sprzed ostatniej "
                                             f"aktualizacji ({lst[0].name})?\n"
                                             "Program uruchomi sie ponownie."):
            return
        reopen = self._save_before_restart()
        if reopen is False:
            return
        try:
            meta = updater.rollback()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Nie udalo sie cofnac aktualizacji:\n{exc}")
            return
        self.log(f"Cofnieto aktualizacje do wersji {meta.get('from_version')}.")
        self._restart(reopen)

    def _save_before_restart(self):
        """Zapisuje sesje przed restartem. Zwraca sciezke do ponownego otwarcia,
        None (brak sesji) albo False (uzytkownik zrezygnowal)."""
        if not self.records:
            return None
        if self.session_path:
            return self.session_path if self.save_session() else False
        path = str(APP_DIR / "sesja_przed_aktualizacja.gsess")
        return path if self._write_session(path) else False

    def _restart(self, session: str | None):
        import subprocess
        args = [sys.executable, str(updater.APP_ROOT / "app.py")]
        if session:
            args += ["--open", session]
        try:
            self._save_column_widths()
        except Exception:
            pass
        subprocess.Popen(args, cwd=str(updater.APP_ROOT))
        self.dirty = False
        self.destroy()

    def on_close(self):
        try:
            self._save_column_widths()   # szerokosci ustawione recznie zostaja na nastepny raz
        except Exception:
            pass
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
        if date:
            iso = naming.normalize_date(date)
            if not iso:
                messagebox.showwarning("Edycja", "Nie rozumiem daty. Wpisz RRRR-MM-DD albo DD.MM.RRRR.",
                                       parent=self)
                return
            self.vars["date_iso"].set(iso)
        for key, var in self.vars.items():
            val = var.get().strip()
            self.rec[key] = val or None
        self.on_ok(self.rec)
        self.destroy()


class ModeDialog(tk.Toplevel):
    """Tryb sesji: kolekcja wydan (PDF-y, AI ustala date i numer z wybranej strony)
    albo kolekcja stron (pojedyncze skany, AI ustala dzien i miesiac)."""

    TEXTS = {
        MODE_ISSUES: ("Kolekcja wydan",
                      "Pliki PDF z calymi wydaniami. AI odczytuje tytul, date i numer wydania "
                      "z jednej strony (domyslnie pierwszej), program sklada z nich nowa nazwe."),
        MODE_PAGES: ("Kolekcja stron",
                     "Pliki graficzne z pojedynczymi stronami albo rozkladowkami. Tytul, rok, numer "
                     "wydania i numer strony sa w nazwie pliku - AI ustala tylko dzien i miesiac."),
    }

    @classmethod
    def ask(cls, parent, title: str, mode: str | None, page: int, info: str = ""):
        dlg = cls(parent, title, mode, page, info)
        parent.wait_window(dlg)
        return dlg.result

    def __init__(self, parent, title: str, mode: str | None, page: int, info: str):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        if info:
            ttk.Label(self, text=info, wraplength=520, justify="left", foreground="#a86400"
                      ).pack(anchor="w", padx=12, pady=(12, 0))
        ttk.Label(self, text="Jaki to rodzaj kolekcji?", font=("TkDefaultFont", 10, "bold")
                  ).pack(anchor="w", padx=12, pady=(12, 6))
        self.var_mode = tk.StringVar(value=mode or "")
        for key, (label, desc) in self.TEXTS.items():
            ttk.Radiobutton(self, text=label, value=key, variable=self.var_mode,
                            command=self._update).pack(anchor="w", padx=16, pady=(6, 0))
            ttk.Label(self, text=desc, wraplength=480, justify="left", foreground="#555"
                      ).pack(anchor="w", padx=38)
            if key == MODE_ISSUES:
                row = ttk.Frame(self)
                row.pack(anchor="w", padx=38, pady=(4, 0))
                ttk.Label(row, text="Strona PDF-a wysylana do AI:").pack(side="left")
                self.var_page = tk.IntVar(value=page + 1)
                self.spin = ttk.Spinbox(row, from_=1, to=999, width=5, textvariable=self.var_page)
                self.spin.pack(side="left", padx=6)
                ttk.Label(row, text="(1 = pierwsza; zmien, gdy gazeta ma date i numer na innej stronie)",
                          foreground="#555").pack(side="left")
        btns = ttk.Frame(self)
        btns.pack(pady=12)
        self.btn_ok = ttk.Button(btns, text="OK", command=self.ok)
        self.btn_ok.pack(side="left", padx=4)
        ttk.Button(btns, text="Anuluj", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self._update()
        self.grab_set()

    def _update(self):
        self.spin.state(["!disabled"] if self.var_mode.get() == MODE_ISSUES else ["disabled"])
        self.btn_ok.state(["!disabled"] if self.var_mode.get() in MODES else ["disabled"])

    def ok(self):
        mode = self.var_mode.get()
        if mode not in MODES:
            return
        try:
            page = max(1, int(self.var_page.get()))
        except (tk.TclError, ValueError):
            messagebox.showwarning(APP_NAME, "Podaj numer strony (1, 2, 3...).", parent=self)
            return
        self.result = (mode, page - 1 if mode == MODE_ISSUES else 0)
        self.destroy()


class VerifyScopeDialog(tk.Toplevel):
    """Zakres weryfikacji: wszystkie / widoczne / zaznaczone / widoczne i zaznaczone."""

    SCOPES = [("wszystkie", "wszystkie"), ("widoczne", "widoczne (po filtrze)"),
              ("zaznaczone", "zaznaczone (\u2611)"), ("widoczne_zaznaczone", "widoczne i zaznaczone")]

    def __init__(self, parent: App, n_selected: int, on_ok):
        super().__init__(parent)
        self.title("Weryfikuj")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.app, self.on_ok = parent, on_ok
        ttk.Label(self, text="Ktore rekordy weryfikowac?", font=("TkDefaultFont", 10, "bold")
                  ).pack(anchor="w", padx=12, pady=(12, 6))
        scopes = list(self.SCOPES)
        if n_selected > 1:
            scopes.append(("podswietlone", "podswietlone wiersze"))
        self.var_scope = tk.StringVar(value="podswietlone" if n_selected > 1 else "wszystkie")
        self.var_only = tk.BooleanVar(value=True)
        self.labels = {}
        for key, label in scopes:
            rb = ttk.Radiobutton(self, text=label, value=key, variable=self.var_scope, command=self._update)
            rb.pack(anchor="w", padx=20)
            self.labels[key] = (rb, label)
        ttk.Checkbutton(self, text="tylko do weryfikacji (bez zielonych i nieczytanych)",
                        variable=self.var_only, command=self._update).pack(anchor="w", padx=12, pady=(10, 2))
        self.lbl = ttk.Label(self, text="", foreground="#00509e")
        self.lbl.pack(anchor="w", padx=12, pady=6)
        btns = ttk.Frame(self)
        btns.pack(pady=(4, 12))
        ttk.Button(btns, text="Weryfikuj", command=self.ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Anuluj", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self._update()

    def _update(self):
        only = self.var_only.get()
        for key, (rb, label) in self.labels.items():
            rb.config(text=f"{label}:  {len(self.app.verify_scope(key, only))}")
        n = len(self.app.verify_scope(self.var_scope.get(), only))
        self.lbl.config(text=f"Rekordow do przejrzenia: {n}")

    def ok(self):
        scope, only = self.var_scope.get(), self.var_only.get()
        self.destroy()
        self.on_ok(scope, only)


class PatternsDialog(tk.Toplevel):
    """Wzorce nazw plikow wejsciowych w tej sesji - z podgladem na biezacych plikach."""

    HELP = ("Pola: {lp} liczba porzadkowa, {tytul} tytul, {rok} rok (4 cyfry), {mm} miesiac, "
            "{dd} dzien, {nr} numer wydania (z dopiskiem bis/s/special), {str} strona "
            "(z -OST), {*} cokolwiek.\nPrzyklady: {rok}-{nr}   ·   {tytul} {rok} nr {nr}   ·   "
            "FF{rok}-{mm}\nSpacje dopasuja sie do dowolnej liczby spacji. Wzorce wbudowane "
            "(kolekcje, format wyjsciowy) dzialaja zawsze i sa sprawdzane jako pierwsze.")

    def __init__(self, parent: App, items: list[dict], history: list[dict], names: list[str], on_ok):
        super().__init__(parent)
        self.title("Wzorce nazw plikow")
        self.geometry("900x640")
        self.transient(parent)
        self.grab_set()
        self.items = [dict(i) for i in items]
        self.history, self.names, self.on_ok = history, names, on_ok

        ttk.Label(self, text=self.HELP, wraplength=860, justify="left", foreground="#444"
                  ).pack(anchor="w", padx=10, pady=(10, 6))
        self.lst = ttk.Treeview(self, columns=("p", "t"), show="headings", height=7)
        self.lst.heading("p", text="Wzorzec")
        self.lst.heading("t", text="Tytul dla tych plikow (gdy nie ma go w nazwie)")
        self.lst.column("p", width=480)
        self.lst.column("t", width=360)
        self.lst.pack(fill="x", padx=10)
        self.lst.bind("<<TreeviewSelect>>", self._select)

        row = ttk.Frame(self)
        row.pack(fill="x", padx=10, pady=6)
        ttk.Label(row, text="Wzorzec:").pack(side="left")
        self.var_p = tk.StringVar()
        self.cb = ttk.Combobox(row, textvariable=self.var_p, width=44,
                               values=[h.get("pattern", "") for h in history])
        self.cb.pack(side="left", padx=4)
        self.cb.bind("<<ComboboxSelected>>", self._from_history)
        ttk.Label(row, text="Tytul:").pack(side="left", padx=(8, 0))
        self.var_t = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_t, width=26).pack(side="left", padx=4)
        self.var_p.trace_add("write", lambda *a: self._preview())

        row2 = ttk.Frame(self)
        row2.pack(fill="x", padx=10)
        ttk.Button(row2, text="Dodaj", command=self.add).pack(side="left")
        ttk.Button(row2, text="Zmien zaznaczony", command=self.change).pack(side="left", padx=3)
        ttk.Button(row2, text="Usun", command=self.remove).pack(side="left")
        ttk.Button(row2, text="W gore", command=lambda: self.move(-1)).pack(side="left", padx=(12, 3))
        ttk.Button(row2, text="W dol", command=lambda: self.move(1)).pack(side="left")
        self.lbl_err = ttk.Label(row2, text="", foreground="#b00020")
        self.lbl_err.pack(side="left", padx=10)

        ttk.Label(self, text="Podglad na plikach z listy:").pack(anchor="w", padx=10, pady=(10, 2))
        self.txt = ScrolledText(self, height=14, wrap="none", font=("Consolas", 9))
        self.txt.pack(fill="both", expand=True, padx=10)

        btns = ttk.Frame(self)
        btns.pack(pady=8)
        ttk.Button(btns, text="Zapisz i przelicz", command=self.ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Anuluj", command=self.destroy).pack(side="left", padx=4)
        self._refresh()

    # ----------------------------------------------------------------------
    def _refresh(self):
        self.lst.delete(*self.lst.get_children())
        for i, it in enumerate(self.items):
            self.lst.insert("", "end", iid=str(i), values=(it["pattern"], it.get("title", "")))
        self._preview()

    def _select(self, _e=None):
        sel = self.lst.selection()
        if sel:
            it = self.items[int(sel[0])]
            self.var_p.set(it["pattern"])
            self.var_t.set(it.get("title", ""))

    def _from_history(self, _e=None):
        for h in self.history:
            if h.get("pattern") == self.var_p.get():
                self.var_t.set(h.get("title", ""))

    def _valid(self) -> dict | None:
        text = self.var_p.get().strip()
        try:
            filename_patterns.compile_pattern(text)
        except (ValueError, re.error) as exc:
            self.lbl_err.config(text=str(exc))
            return None
        self.lbl_err.config(text="")
        return {"pattern": text, "title": self.var_t.get().strip()}

    def add(self):
        it = self._valid()
        if it:
            self.items.append(it)
            self._refresh()

    def change(self):
        sel = self.lst.selection()
        it = self._valid()
        if sel and it:
            self.items[int(sel[0])] = it
            self._refresh()

    def remove(self):
        sel = self.lst.selection()
        if sel:
            del self.items[int(sel[0])]
            self._refresh()

    def move(self, d: int):
        sel = self.lst.selection()
        if not sel:
            return
        i = int(sel[0])
        j = i + d
        if 0 <= j < len(self.items):
            self.items[i], self.items[j] = self.items[j], self.items[i]
            self._refresh()
            self.lst.selection_set(str(j))

    def _preview(self):
        items = list(self.items)
        typed = self.var_p.get().strip()
        if typed and all(typed != i["pattern"] for i in items):
            try:
                filename_patterns.compile_pattern(typed)
                items.append({"pattern": typed, "title": self.var_t.get().strip()})
                self.lbl_err.config(text="")
            except (ValueError, re.error) as exc:
                self.lbl_err.config(text=str(exc))
        pats = filename_patterns.BUILTIN + filename_patterns.user_patterns(items)
        res = filename_patterns.preview(pats, self.names)
        out = [f"Plikow na liscie: {len(self.names)}", ""]
        for key, n in sorted(res["counts"].items(), key=lambda kv: -kv[1]):
            out.append(f"{n:6d}  {key}")
            for name, d in res["samples"].get(key, []):
                got = ", ".join(f"{k}={v}" for k, v in d.items()
                                if k in ("lp", "title", "year", "month", "day", "issue", "suffix", "page")
                                and v not in (None, ""))
                out.append(f"          {name}\n             -> {got}")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "\n".join(out))

    def ok(self):
        self.destroy()
        self.on_ok(self.items)



class ReportDialog(tk.Toplevel):
    """Ustawienia raportu HTML: nazwa kolekcji, zalozenia czasowe, opcjonalna lista plikow."""

    def __init__(self, parent, collection: str, cfg: dict, ai_seconds, on_ok):
        super().__init__(parent)
        self.title("Raport HTML z sesji")
        self.transient(parent)
        self.resizable(False, False)
        self.on_ok = on_ok
        self.v_col = tk.StringVar(value=collection)
        self.v_manual = tk.StringVar(value=str(cfg.get("manual_sec") or report_html.DEFAULT_MANUAL_SEC))
        self.v_review = tk.StringVar(value=str(cfg.get("review_sec") or report_html.DEFAULT_REVIEW_SEC))
        self.v_ai = tk.StringVar(value=fmt_time(ai_seconds) if ai_seconds else "")
        self.v_names = tk.BooleanVar(value=False)
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        rows = [("Nazwa kolekcji:", self.v_col, 38),
                ("Czas reczny na 1 plik (s):", self.v_manual, 8),
                ("Czas kontroli 1 pliku 'do sprawdzenia' (s):", self.v_review, 8),
                ("Czas odczytu AI (gg:mm:ss; puste = szacunek):", self.v_ai, 10)]
        for i, (lab, var, w) in enumerate(rows):
            ttk.Label(f, text=lab).grid(row=i, column=0, sticky="w", pady=3, padx=(0, 10))
            ttk.Entry(f, textvariable=var, width=w).grid(row=i, column=1, sticky="w", pady=3)
        ttk.Checkbutton(f, text="Dolacz liste plikow wymagajacych uwagi (nazwy plikow)",
                        variable=self.v_names).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Label(f, foreground="#666", wraplength=420, justify="left",
                  text="Sciezki folderow nie trafiaja do raportu. Czas reczny to zalozenie: "
                       "otwarcie skanu, odczyt daty i numeru, wpisanie nazwy.").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(4, 10))
        bt = ttk.Frame(f)
        bt.grid(row=6, column=0, columnspan=2, sticky="e")
        ttk.Button(bt, text="Generuj raport", command=self._ok).pack(side="left", padx=4)
        ttk.Button(bt, text="Anuluj", command=self.destroy).pack(side="left")
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()

    def _ok(self):
        try:
            manual = float(self.v_manual.get().replace(",", "."))
            review = float(self.v_review.get().replace(",", "."))
            t = self.v_ai.get().strip()
            ai = None
            if t:
                parts = [int(x) for x in t.split(":")]
                while len(parts) < 3:
                    parts.insert(0, 0)
                ai = parts[0] * 3600 + parts[1] * 60 + parts[2]
            if manual <= 0 or review < 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning(APP_NAME, "Sprawdz liczby: czasy w sekundach, czas AI jako gg:mm:ss.",
                                   parent=self)
            return
        col, names = self.v_col.get().strip(), self.v_names.get()
        self.destroy()
        self.on_ok(col or "Kolekcja skanow", manual, review, ai, names)

class RenameDialog(tk.Toplevel):
    """Zakres zmiany nazw: pewne / zaznaczone / widoczne / pewne widoczne."""

    SCOPES = [("pewne", "tylko pewne (zielone)"), ("zaznaczone", "tylko zaznaczone (\u2611)"),
              ("widoczne", "tylko widoczne (po filtrze)"), ("pewne_widoczne", "pewne sposrod widocznych")]

    def __init__(self, parent: App, on_ok):
        super().__init__(parent)
        self.title("Zmien nazwy")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.app, self.on_ok = parent, on_ok
        ttk.Label(self, text="Ktorym plikom zmienic nazwy?", font=("TkDefaultFont", 10, "bold")
                  ).pack(anchor="w", padx=12, pady=(12, 6))
        self.var_scope = tk.StringVar(value="pewne")
        self.var_inc = tk.BooleanVar(value=False)
        self.labels = {}
        for key, label in self.SCOPES:
            rb = ttk.Radiobutton(self, text=label, value=key, variable=self.var_scope, command=self._update)
            rb.pack(anchor="w", padx=20)
            self.labels[key] = (rb, label)
        ttk.Checkbutton(self, text="takze niekompletne (z brakami w nazwie: rrrr, mm-dd, nnnnnn)",
                        variable=self.var_inc, command=self._update).pack(anchor="w", padx=12, pady=(10, 2))
        self.lbl = ttk.Label(self, text="", foreground="#00509e")
        self.lbl.pack(anchor="w", padx=12, pady=6)
        btns = ttk.Frame(self)
        btns.pack(pady=(4, 12))
        ttk.Button(btns, text="Dalej", command=self.ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Anuluj", command=self.destroy).pack(side="left", padx=4)
        self._update()

    def _count(self, scope: str) -> int:
        recs = [r for r in self.app.rename_scope(scope) if "zmieniono" not in (r.get("status") or "")]
        if not self.var_inc.get():
            recs = [r for r in recs if r.get("name_complete")]
        return len(recs)

    def _update(self):
        for key, (rb, label) in self.labels.items():
            rb.config(text=f"{label}:  {self._count(key)}")
        self.lbl.config(text=f"Plikow do zmiany nazwy: {self._count(self.var_scope.get())} "
                             f"(bez tych, ktorym juz zmieniono nazwe)")

    def ok(self):
        scope, inc = self.var_scope.get(), self.var_inc.get()
        self.destroy()
        self.on_ok(scope, inc)


class PromptDialog(tk.Toplevel):
    """Edycja zasad odczytu (czesc promptu) i uwag o konkretnej kolekcji."""

    def __init__(self, parent: App, rules: str, notes: str, on_ok):
        super().__init__(parent)
        self.title("Zasady odczytu")
        self.geometry("820x720")
        self.on_ok = on_ok
        self.transient(parent)
        self.grab_set()

        ttk.Label(self, text="Zasady odczytu (wysylane do modelu). Format odpowiedzi i "
                             "identyfikatory obrazow dodaje program - ich tu nie ma.",
                  wraplength=780, justify="left").pack(anchor="w", padx=10, pady=(10, 4))
        self.txt_rules = ScrolledText(self, height=24, wrap="word", font=("Consolas", 9))
        self.txt_rules.pack(fill="both", expand=True, padx=10)
        self.txt_rules.insert("1.0", rules.strip() or DEFAULT_RULES)

        ttk.Label(self, text="Uwagi o tej kolekcji (np. \"data jest w stopce strony 3\", "
                             "\"numeracja od 1 co roku\"). Moga byc po polsku.",
                  wraplength=780, justify="left").pack(anchor="w", padx=10, pady=(10, 4))
        self.txt_notes = ScrolledText(self, height=5, wrap="word", font=("Consolas", 9))
        self.txt_notes.pack(fill="x", padx=10)
        self.txt_notes.insert("1.0", notes)

        btns = ttk.Frame(self)
        btns.pack(pady=10)
        ttk.Button(btns, text="Zapisz", command=self.ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Przywroc domyslne zasady", command=self.reset).pack(side="left", padx=4)
        ttk.Button(btns, text="Anuluj", command=self.destroy).pack(side="left", padx=4)

    def reset(self):
        self.txt_rules.delete("1.0", "end")
        self.txt_rules.insert("1.0", DEFAULT_RULES)

    def ok(self):
        rules = self.txt_rules.get("1.0", "end").strip()
        if rules == DEFAULT_RULES.strip():
            rules = ""  # domyslne nie sa zapisywane - nowa wersja programu da nowe domyslne
        self.on_ok(rules, self.txt_notes.get("1.0", "end").strip())
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
    app = App()
    if "--open" in sys.argv:
        # ponowne uruchomienie po aktualizacji - wracamy do tej samej sesji
        i = sys.argv.index("--open")
        if i + 1 < len(sys.argv) and Path(sys.argv[i + 1]).exists():
            app.after(200, lambda: app._load_session(sys.argv[i + 1]))
    app.mainloop()
