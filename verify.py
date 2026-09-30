"""Okno weryfikacji: duzy podglad strony obok pol do recznej poprawki.

Przechodzi po kolei przez rekordy wymagajace sprawdzenia. Enter zapisuje i przechodzi
dalej, Esc / strzalki pomijaja rekord, PageUp/PageDown zmieniaja strone PDF-a.
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import naming
import render
from config import VERIFY_DPI

MONTHS = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca",
          "sierpnia", "wrzesnia", "pazdziernika", "listopada", "grudnia"]
WEEKDAYS = ["poniedzialek", "wtorek", "sroda", "czwartek", "piatek", "sobota", "niedziela"]
ZOOM_STEP = 1.2
ZOOM_MIN, ZOOM_MAX = 0.05, 8.0


def needs_check(r: dict) -> bool:
    """Czy rekord wymaga weryfikacji recznej.

    Po odczycie AI sa tylko dwa stany: zielony (odczyt kompletny i zgodny z nazwa pliku
    oraz raportem) albo do weryfikacji. Pewnosc modelu nie decyduje o kolorze - widac ja
    w kolumnie Pewnosc i mozna po niej filtrowac."""
    st = (r.get("status") or "").lower()
    if st in ("nowy", "") or "recznie" in st or "zmieniono" in st or "cofnieto" in st:
        return False
    if "blad" in st or r.get("issues"):
        return True
    return not r.get("name_complete", bool(r.get("new_name")))


def check_reasons(r: dict) -> list[str]:
    """Dlaczego rekord jest do weryfikacji - krotko, po ludzku."""
    if not needs_check(r):
        return []
    st = (r.get("status") or "").lower()
    out = list(r.get("issues") or [])
    if "blad" in st:
        out.append("blad odczytu" + (f": {r['note']}" if r.get("note") else ""))
    if not r.get("name_complete", bool(r.get("new_name"))):
        out.append("niekompletna nowa nazwa" + (f" ({r['name_missing']})" if r.get("name_missing") else ""))
    return out


# Grupy stanu - te same kolory w tabeli i na pasku kolekcji.
GROUP_COLORS = {
    "certain": "#2e8b3e",   # pewne - mozna zmieniac nazwy
    "check": "#e09a1f",     # do sprawdzenia
    "error": "#c0392b",     # blad odczytu
    "renamed": "#2f6fb0",   # nazwa juz zmieniona
    "new": "#c9cdd2",       # jeszcze nieczytane
}


def status_group(r: dict) -> str:
    st = (r.get("status") or "").lower()
    if "zmieniono" in st or "cofnieto" in st:
        return "renamed"
    if st in ("nowy", ""):
        return "new"
    if "blad" in st:
        return "error"
    return "check" if needs_check(r) else "certain"


def open_external(path: str) -> None:
    """Otwiera plik w domyslnym programie systemu (np. przegladarce PDF)."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class VerifyDialog(tk.Toplevel):
    DIGIT_FIELDS = ("day", "month", "year", "issue_number")

    def __init__(self, parent, records: list[dict], on_save, on_close, start_page: int = 0,
                 pages_mode: bool = False):
        super().__init__(parent)
        self.title("Weryfikacja")
        self.geometry("1300x860")
        self.minsize(900, 560)
        self.transient(parent)

        self.records = records
        self.start_page = max(0, int(start_page or 0))   # strona PDF-a wysylana do AI
        self.pages_mode = pages_mode                      # kolekcja stron: AI ustala dzien i miesiac
        self._ai = None
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
        vcmd = (self.register(lambda t: t.isdigit() or t == ""), "%P")
        big = ("TkDefaultFont", 13)

        drow = ttk.Frame(form)
        drow.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))
        for key, label, width in (("day", "Dzien", 3), ("month", "Miesiac", 3), ("year", "Rok", 5)):
            box = ttk.Frame(drow)
            box.pack(side="left", padx=(0, 10))
            ttk.Label(box, text=label).pack(anchor="w")
            v = tk.StringVar()
            e = ttk.Entry(box, textvariable=v, width=width, font=big, justify="center",
                          validate="key", validatecommand=vcmd)
            e.pack()
            self.vars[key], self.entries[key] = v, e
        self.lbl_date = ttk.Label(form, text="", foreground="#00509e")
        self.lbl_date.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))

        for i, (key, label) in enumerate((("issue_number", "Nr wydania"),
                                          ("issue_suffix", "Dopisek"), ("title", "Tytul")), start=2):
            ttk.Label(form, text=label + ":").grid(row=i, column=0, sticky="e", pady=3)
            v = tk.StringVar()
            kw = {"validate": "key", "validatecommand": vcmd} if key == "issue_number" else {}
            e = ttk.Entry(form, textvariable=v, width=22, font=("TkDefaultFont", 11), **kw)
            e.grid(row=i, column=1, sticky="we", padx=(6, 0), pady=3)
            self.vars[key], self.entries[key] = v, e
        form.columnconfigure(1, weight=1)

        for key in ("day", "month", "year"):
            self.vars[key].trace_add("write", lambda *a: self._date_changed())
        self.vars["issue_number"].trace_add("write", lambda *a: self._update_suggestion())
        for key, step in (("day", "day"), ("month", "month"), ("year", "year")):
            self.entries[key].bind("<Up>", lambda e, s=step: self._step(s, +1))
            self.entries[key].bind("<Down>", lambda e, s=step: self._step(s, -1))
        for key in self.DIGIT_FIELDS:
            self.entries[key].bind("<p>", lambda e: self.take_suggestion())
            self.entries[key].bind("<P>", lambda e: self.take_suggestion())
            self.entries[key].bind("<FocusIn>", lambda e: e.widget.select_range(0, "end"))

        sug = ttk.Frame(right)
        sug.pack(fill="x", padx=8, pady=(6, 8))
        self.lbl_sugg = ttk.Label(sug, text="", wraplength=340, justify="left", foreground="#8a1c1c")
        self.lbl_sugg.pack(anchor="w")
        self.btn_sugg = ttk.Button(sug, text="Wstaw odczyt AI  [P]", command=self.take_suggestion)
        self.btn_sugg.pack(anchor="w", pady=(3, 0))
        ttk.Label(right, text="Tab - nastepne pole  ·  strzalki - dzien/miesiac/rok o jeden  ·  "
                              "Ctrl+strzalki - poprzedni/nastepny plik", foreground="#666",
                  wraplength=340, justify="left").pack(anchor="w", padx=8, pady=(0, 8))

        b = ttk.Frame(right)
        b.pack(fill="x", padx=8)
        ttk.Button(b, text="Zapisz i dalej  [Enter]", command=self.save_next).pack(fill="x")
        ttk.Button(b, text="Pomin  [Esc]", command=lambda: self.move(1)).pack(fill="x", pady=3)
        ttk.Button(b, text="Poprzedni  [Ctrl+strzalka w gore]", command=lambda: self.move(-1)).pack(fill="x")
        ttk.Button(b, text="Otworz w przegladarce PDF", command=self.open_file).pack(fill="x", pady=(12, 3))
        ttk.Button(b, text="Zakoncz weryfikacje", command=self.close).pack(fill="x", pady=(12, 0))

        ttk.Label(right, text="Odczyt modelu:").pack(anchor="w", padx=8, pady=(12, 2))
        self.txt = tk.Text(right, height=10, wrap="word", font=("Consolas", 9),
                           background="#f4f4f4", relief="flat")
        self.txt.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        self.bind("<Return>", lambda e: self.save_next())
        self.bind("<KP_Enter>", lambda e: self.save_next())
        self.bind("<Escape>", lambda e: self.move(1))
        self.bind("<Control-Down>", lambda e: self.move(1))
        self.bind("<Control-Up>", lambda e: self.move(-1))
        self.bind("<Alt-p>", lambda e: self.take_suggestion())
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
        reasons = check_reasons(r)
        if reasons:
            info.append("do weryfikacji: " + "; ".join(reasons))
        elif r.get("note"):
            info.append(str(r["note"]))
        self.lbl_info.config(text="  |  ".join(info))
        for key in ("issue_number", "issue_suffix", "title"):
            val = r.get(key)
            self.vars[key].set("" if val is None else str(val))
        self._set_date(r.get("date_iso"))
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "\n".join([
            f"Data (org):  {r.get('date_raw') or ''}",
            f"Miesiac:     {r.get('month_raw') or ''}",
            f"Rok nadruk.: {r.get('year_printed') or ''}",
            f"Nr strony:   {r.get('page_number') or ''}",
            "", str(r.get("raw") or ""),
        ]))
        self._update_suggestion()
        e = self.entries["day"]
        e.focus_set()
        e.select_range(0, "end")
        e.icursor("end")
        try:
            self.pages = render.page_count(r["path"])
        except Exception:
            self.pages = 1
        self.goto_page(min(self.start_page, self.pages - 1), force=True)

    def move(self, step: int):
        n = self.idx + step
        if n < 0:
            return "break"
        if n >= len(self.records):
            self.close()
            return "break"
        self.show(n)
        return "break"

    # ------------------------------------------------------------- pola daty
    def _set_date(self, date_iso):
        y = m = d = ""
        if date_iso and naming.is_valid_date(date_iso):
            y, m, d = date_iso.split("-")
        self.vars["day"].set(d)
        self.vars["month"].set(m)
        self.vars["year"].set(y)

    def _date(self) -> _dt.date | None:
        try:
            return _dt.date(int(self.vars["year"].get()), int(self.vars["month"].get()),
                            int(self.vars["day"].get()))
        except (ValueError, TypeError):
            return None

    def _date_changed(self):
        d = self._date()
        if d is None:
            filled = any(self.vars[k].get() for k in ("day", "month", "year"))
            self.lbl_date.config(text="niepelna albo bledna data" if filled else "",
                                 foreground="#b00020")
        else:
            self.lbl_date.config(text=f"{WEEKDAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]} {d.year}",
                                 foreground="#00509e")
        self._update_suggestion()

    def _step(self, what: str, delta: int):
        d = self._date()
        if d is not None:
            if what == "day":
                d = d + _dt.timedelta(days=delta)
            elif what == "month":
                m = d.month - 1 + delta
                y, m = d.year + m // 12, m % 12 + 1
                d = d.replace(year=y, month=m, day=min(d.day, 28 if m == 2 else 30 if m in (4, 6, 9, 11) else 31))
            else:
                try:
                    d = d.replace(year=d.year + delta)
                except ValueError:
                    d = d.replace(year=d.year + delta, day=28)
            self._set_date(d.isoformat())
        else:
            v = self.vars[what]
            lim = {"day": (1, 31), "month": (1, 12), "year": (1800, 2100)}[what]
            try:
                n = int(v.get()) + delta
            except ValueError:
                n = lim[0] if what != "year" else 1950
            n = max(lim[0], min(lim[1], n))
            v.set(f"{n:02d}" if what != "year" else str(n))
        self.entries[what].icursor("end")
        return "break"

    # ------------------------------------------------------------- odczyt AI
    def _ai_values(self) -> tuple[str | None, str | None, str | None]:
        """(data, numer, dopisek) odczytane przez AI dla biezacego rekordu."""
        r = self.records[self.idx]
        date = r.get("ai_date")
        issue = sfx = None
        if r.get("ai_issue") and not self.pages_mode:
            parts = str(r["ai_issue"]).split(" ", 1)
            issue, sfx = parts[0], (parts[1] if len(parts) > 1 else None)
        return date, issue, sfx

    def _update_suggestion(self):
        """Pod polami: co odczytalo AI, gdy rozni sie od wartosci w polach
        (w polach jest wartosc z nazwy pliku - ona ma pierwszenstwo)."""
        r = self.records[self.idx]
        date, issue, _sfx = self._ai_values()
        if not r.get("ai") or (not date and not issue):
            self._ai = None
            self.lbl_sugg.config(text="AI nie podalo daty ani numeru." if r.get("ai") else "")
            self.btn_sugg.state(["disabled"])
            return
        d = self._date()
        cur_date = d.isoformat() if d else None
        cur_issue = self.vars["issue_number"].get().strip().lstrip("0") or None
        diff = []
        if date and date != cur_date:
            y, m, dd = date.split("-")
            full = f"{int(dd)} {MONTHS[int(m) - 1]} {y}"
            if cur_date and cur_date[4:] == date[4:]:
                diff.append(f"inny rok: {y}  (cala data: {full})")
            else:
                diff.append(f"data {full}")
        if issue and issue.lstrip("0") != cur_issue:
            diff.append(f"nr {issue}")
        self._ai = (date, issue) if diff else None
        if diff:
            self.lbl_sugg.config(text="AI odczytalo " + ", ".join(diff), foreground="#8a1c1c")
            self.btn_sugg.state(["!disabled"])
        else:
            self.lbl_sugg.config(text="Wartosci w polach zgadzaja sie z odczytem AI.",
                                 foreground="#0a6b2e")
            self.btn_sugg.state(["disabled"])

    def take_suggestion(self):
        """Wstawia do pol wartosci odczytane przez AI."""
        date, issue, sfx = self._ai_values()
        if self._ai:
            if issue:
                self.vars["issue_number"].set(issue)
                self.vars["issue_suffix"].set(sfx or "")
            if date:
                self._set_date(date)
            self.entries["day"].focus_set()
        return "break"

    def save_next(self):
        r = self.records[self.idx]
        parts = [self.vars[k].get().strip() for k in ("day", "month", "year")]
        date_iso = None
        if any(parts):
            d = self._date()
            if d is None or not naming.is_valid_date(d.isoformat()):
                messagebox.showwarning("Weryfikacja", "Data jest niepelna albo bledna - uzupelnij "
                                       "dzien, miesiac i czterocyfrowy rok (albo wyczysc wszystkie trzy).",
                                       parent=self)
                return "break"
            date_iso = d.isoformat()
        r["date_iso"] = date_iso
        for k in ("issue_number", "issue_suffix", "title"):
            r[k] = self.vars[k].get().strip() or None
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
        fit = self.fit_width if self.page == min(self.start_page, self.pages - 1) else self.fit_page
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
                msg = str(exc)
                self.after(0, lambda: self._show_error(token, msg))
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
