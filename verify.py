"""Okno weryfikacji: duzy podglad strony obok pol do recznej poprawki.

Przechodzi po kolei przez rekordy wymagajace sprawdzenia. Enter zapisuje i przechodzi
dalej, Esc / strzalki pomijaja rekord, PageUp/PageDown zmieniaja strone PDF-a.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import naming
import render
from config import VERIFY_CONFIDENCE, VERIFY_DPI

ZOOM_STEP = 1.2
ZOOM_MIN, ZOOM_MAX = 0.05, 8.0


def needs_check(r: dict) -> bool:
    """Czy rekord powinien trafic do weryfikacji recznej."""
    if r.get("report_flag"):
        return True
    st = (r.get("status") or "").lower()
    if st == "nowy" or "recznie" in st or "zmieniono" in st or "cofnieto" in st:
        return False
    if "blad" in st or st.startswith("brak danych") or r.get("outlier"):
        return True
    conf = r.get("confidence")
    if conf is not None and conf < VERIFY_CONFIDENCE:
        return True
    return not r.get("new_name")


def open_external(path: str) -> None:
    """Otwiera plik w domyslnym programie systemu (np. przegladarce PDF)."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class VerifyDialog(tk.Toplevel):
    FIELDS = [("date_iso", "Data"), ("issue_number", "Nr wydania"),
              ("issue_suffix", "Dopisek"), ("title", "Tytul")]

    def __init__(self, parent, records: list[dict], on_save, on_close):
        super().__init__(parent)
        self.title("Weryfikacja")
        self.geometry("1300x860")
        self.minsize(900, 560)
        self.transient(parent)

        self.records = records
        self.on_save = on_save
        self.on_close_cb = on_close
        self.idx = 0
        self.page = 0
        self.pages = 1
        self.saved = 0
        self._img = None          # PIL.Image biezacej strony
        self._tk_img = None
        self._zoom = 1.0
        self._token = 0
        self._cache: dict[tuple[str, int], object] = {}

        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.show(0)
        self.grab_set()

    # ------------------------------------------------------------------ UI
    def _build(self):
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6, pady=6)
        # podglad dostaje ~3/4 szerokosci, formularz reszte
        def place_sash(e):
            if e.width > 800:
                paned.sashpos(0, e.width - 380)
                paned.unbind("<Configure>")
        paned.bind("<Configure>", place_sash)

        left = ttk.Frame(paned)
        paned.add(left, weight=4)
        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=(0, 4))
        self.btn_prev_page = ttk.Button(bar, text="< Strona", width=9,
                                        command=lambda: self.goto_page(self.page - 1))
        self.btn_prev_page.pack(side="left")
        self.lbl_page = ttk.Label(bar, text="", width=12, anchor="center")
        self.lbl_page.pack(side="left")
        self.btn_next_page = ttk.Button(bar, text="Strona >", width=9,
                                        command=lambda: self.goto_page(self.page + 1))
        self.btn_next_page.pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="Winieta", command=self.fit_width).pack(side="left")
        ttk.Button(bar, text="Cala strona", command=self.fit_page).pack(side="left", padx=3)
        ttk.Button(bar, text="-", width=3, command=lambda: self.zoom_by(1 / ZOOM_STEP)).pack(side="left")
        ttk.Button(bar, text="+", width=3, command=lambda: self.zoom_by(ZOOM_STEP)).pack(side="left", padx=3)
        ttk.Label(bar, text="kolko = zoom, przeciaganie = przesuw",
                  foreground="#666").pack(side="left", padx=8)

        self.canvas = tk.Canvas(left, background="#404040", highlightthickness=0,
                                cursor="fleur")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind("<MouseWheel>", self._on_wheel)                          # Windows / macOS
        self.canvas.bind("<Button-4>", lambda e: self._zoom_at(ZOOM_STEP, e.x, e.y))    # Linux
        self.canvas.bind("<Button-5>", lambda e: self._zoom_at(1 / ZOOM_STEP, e.x, e.y))

        right = ttk.Frame(paned, width=360)
        paned.add(right, weight=1)

        self.lbl_counter = ttk.Label(right, text="", font=("TkDefaultFont", 11, "bold"))
        self.lbl_counter.pack(anchor="w", padx=8, pady=(4, 2))
        self.lbl_file = ttk.Label(right, text="", wraplength=340, justify="left")
        self.lbl_file.pack(anchor="w", padx=8)
        self.lbl_info = ttk.Label(right, text="", wraplength=340, justify="left",
                                  foreground="#a86400")
        self.lbl_info.pack(anchor="w", padx=8, pady=(4, 8))

        form = ttk.Frame(right)
        form.pack(fill="x", padx=8)
        self.vars: dict[str, tk.StringVar] = {}
        self.entries: dict[str, ttk.Entry] = {}
        for i, (key, label) in enumerate(self.FIELDS):
            ttk.Label(form, text=label + ":").grid(row=i, column=0, sticky="e", pady=3)
            v = tk.StringVar()
            e = ttk.Entry(form, textvariable=v, width=22, font=("TkDefaultFont", 11))
            e.grid(row=i, column=1, sticky="we", padx=(6, 0), pady=3)
            self.vars[key] = v
            self.entries[key] = e
        form.columnconfigure(1, weight=1)
        ttk.Label(right, text="Data: RRRR-MM-DD albo DD.MM.RRRR", foreground="#666"
                  ).pack(anchor="w", padx=8, pady=(2, 10))

        b = ttk.Frame(right)
        b.pack(fill="x", padx=8)
        ttk.Button(b, text="Zapisz i dalej  [Enter]", command=self.save_next).pack(fill="x")
        ttk.Button(b, text="Pomin  [Esc]", command=lambda: self.move(1)).pack(fill="x", pady=3)
        ttk.Button(b, text="Poprzedni  [strzalka w gore]", command=lambda: self.move(-1)).pack(fill="x")
        ttk.Button(b, text="Otworz w przegladarce PDF", command=self.open_file).pack(fill="x", pady=(12, 3))
        ttk.Button(b, text="Zakoncz weryfikacje", command=self.close).pack(fill="x", pady=(12, 0))

        ttk.Label(right, text="Odczyt modelu:").pack(anchor="w", padx=8, pady=(12, 2))
        self.txt = tk.Text(right, height=10, wrap="word", font=("Consolas", 9),
                           background="#f4f4f4", relief="flat")
        self.txt.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        self.bind("<Return>", lambda e: self.save_next())
        self.bind("<KP_Enter>", lambda e: self.save_next())
        self.bind("<Escape>", lambda e: self.move(1))
        self.bind("<Down>", lambda e: self.move(1))
        self.bind("<Up>", lambda e: self.move(-1))
        self.bind("<Prior>", lambda e: self.goto_page(self.page - 1))
        self.bind("<Next>", lambda e: self.goto_page(self.page + 1))
        self.canvas.bind("<Configure>", lambda e: self._redraw() if self._img is not None else None)

    # -------------------------------------------------------------- rekordy
    def show(self, idx: int):
        self.idx = idx
        r = self.records[idx]
        self.lbl_counter.config(text=f"Plik {idx + 1} z {len(self.records)}"
                                     f"   (poprawione: {self.saved})")
        self.lbl_file.config(text=r.get("old_name", ""))
        info = [f"Status: {r.get('status') or '-'}"]
        if r.get("confidence") is not None:
            info.append(f"pewnosc {r['confidence']:.2f}")
        if r.get("note"):
            info.append(str(r["note"]))
        if r.get("outlier_info"):
            info.append(str(r["outlier_info"]))
        self.lbl_info.config(text="  |  ".join(info))
        for key, v in self.vars.items():
            val = r.get(key)
            v.set("" if val is None else str(val))
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "\n".join([
            f"Data (org):  {r.get('date_raw') or ''}",
            f"Miesiac:     {r.get('month_raw') or ''}",
            f"Rok nadruk.: {r.get('year_printed') or ''}",
            f"Nr strony:   {r.get('page_number') or ''}",
            "", str(r.get("raw") or ""),
        ]))
        e = self.entries["date_iso"]
        e.focus_set()
        e.select_range(0, "end")
        e.icursor("end")
        try:
            self.pages = render.page_count(r["path"])
        except Exception:
            self.pages = 1
        self.goto_page(0, force=True)

    def move(self, step: int):
        n = self.idx + step
        if n < 0:
            return "break"
        if n >= len(self.records):
            self.close()
            return "break"
        self.show(n)
        return "break"

    def save_next(self):
        r = self.records[self.idx]
        vals = {k: v.get().strip() for k, v in self.vars.items()}
        if vals["date_iso"]:
            iso = naming.normalize_date(vals["date_iso"])
            if not iso:
                messagebox.showwarning("Weryfikacja",
                                       "Nie rozumiem daty. Wpisz RRRR-MM-DD albo DD.MM.RRRR.",
                                       parent=self)
                return "break"
            vals["date_iso"] = iso
        for k, v in vals.items():
            r[k] = v or None
        self.on_save(r)
        self.saved += 1
        return self.move(1)

    def open_file(self):
        path = self.records[self.idx].get("path")
        try:
            open_external(path)
        except Exception as exc:
            messagebox.showerror("Weryfikacja", f"Nie udalo sie otworzyc pliku:\n{exc}", parent=self)

    def close(self):
        self._token += 1
        try:
            self.grab_release()
        finally:
            self.destroy()
        self.on_close_cb(self.saved)

    # --------------------------------------------------------------- strony
    def goto_page(self, page: int, force: bool = False):
        if not force and (page < 0 or page >= self.pages or page == self.page):
            return "break"
        self.page = max(0, min(page, self.pages - 1))
        self.lbl_page.config(text=f"{self.page + 1} / {self.pages}")
        self.btn_prev_page.state(["!disabled"] if self.page > 0 else ["disabled"])
        self.btn_next_page.state(["!disabled"] if self.page < self.pages - 1 else ["disabled"])

        path = self.records[self.idx]["path"]
        key = (path, self.page)
        self._token += 1
        token = self._token
        fit = self.fit_width if self.page == 0 else self.fit_page
        if key in self._cache:
            self._set_image(self._cache[key], fit)
            return "break"
        self._img = None
        self.canvas.delete("all")
        self.canvas.create_text(self.canvas.winfo_width() // 2 or 300, 40,
                                text="Wczytywanie...", fill="#ddd")

        def job():
            try:
                img = render.load_page(path, key[1], dpi=VERIFY_DPI)
            except Exception as exc:
                self.after(0, lambda: self._show_error(token, str(exc)))
                return
            self.after(0, lambda: self._loaded(token, key, img, fit))

        threading.Thread(target=job, daemon=True).start()
        return "break"

    def _loaded(self, token, key, img, fit):
        if len(self._cache) > 12:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = img
        if token == self._token and self.winfo_exists():
            self._set_image(img, fit)

    def _show_error(self, token, msg):
        if token != self._token or not self.winfo_exists():
            return
        self.canvas.delete("all")
        self.canvas.create_text(20, 40, anchor="w", fill="#ffb4b4",
                                text=f"Podglad niedostepny:\n{msg[:200]}")

    def _set_image(self, img, fit):
        self._img = img
        self.update_idletasks()
        fit()

    # ----------------------------------------------------------------- zoom
    def fit_width(self):
        """Szerokosc strony = szerokosc okna, widok od gory (tam jest winieta)."""
        if self._img is None:
            return
        cw = max(100, self.canvas.winfo_width())
        self._zoom = cw / self._img.size[0]
        self._redraw(top=True)

    def fit_page(self):
        if self._img is None:
            return
        cw = max(100, self.canvas.winfo_width())
        ch = max(100, self.canvas.winfo_height())
        self._zoom = min(cw / self._img.size[0], ch / self._img.size[1])
        self._redraw(top=True)

    def zoom_by(self, factor: float):
        self._zoom_at(factor, self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2)

    def _on_wheel(self, e):
        self._zoom_at(ZOOM_STEP if e.delta > 0 else 1 / ZOOM_STEP, e.x, e.y)

    def _zoom_at(self, factor: float, x: int, y: int):
        if self._img is None:
            return
        new = max(ZOOM_MIN, min(ZOOM_MAX, self._zoom * factor))
        if new == self._zoom:
            return
        # punkt obrazu pod kursorem ma zostac pod kursorem
        cx, cy = self.canvas.canvasx(x), self.canvas.canvasy(y)
        ix, iy = cx / self._zoom, cy / self._zoom
        self._zoom = new
        w, h = self._redraw()
        self.canvas.xview_moveto(max(0.0, ix * new - x) / w)
        self.canvas.yview_moveto(max(0.0, iy * new - y) / h)

    def _redraw(self, top: bool = False) -> tuple[int, int]:
        from PIL import Image, ImageTk
        w, h = self._img.size
        size = (max(1, int(w * self._zoom)), max(1, int(h * self._zoom)))
        resample = Image.LANCZOS if self._zoom < 1 else Image.BILINEAR
        self._tk_img = ImageTk.PhotoImage(self._img.resize(size, resample))
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._tk_img)
        self.canvas.configure(scrollregion=(0, 0, size[0], size[1]))
        if top:
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
        return size
