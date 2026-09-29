"""Pasek stanu kolekcji: wszystkie pliki naraz, kazdy w kolorze swojego stanu.

Cala szerokosc to cala lista (w kolejnosci tabeli). Gdy na jeden piksel przypada
kilka plikow, pokazujemy NAJGORSZY z nich - problem ma rzucac sie w oczy, a nie
ginac wsrod zielonych sasiadow. Klikniecie przenosi do tego miejsca w tabeli.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from verify import GROUP_COLORS, status_group

WEIGHT = {"error": 4, "check": 3, "new": 2, "renamed": 1, "certain": 0}
LABELS = [("certain", "pewne"), ("check", "do sprawdzenia"), ("error", "bledy"),
          ("new", "nieczytane"), ("renamed", "zmienione nazwy")]


class CollectionMap(ttk.Frame):
    def __init__(self, parent, on_click, height: int = 16):
        super().__init__(parent)
        self.on_click = on_click
        self.records: list[dict] = []
        self._groups: list[str] = []
        self._pending = False
        self.canvas = tk.Canvas(self, height=height, highlightthickness=1,
                                highlightbackground="#999", background=GROUP_COLORS["new"],
                                cursor="hand2")
        self.canvas.pack(fill="x", side="top")
        self.lbl = ttk.Label(self, text="", foreground="#444")
        self.lbl.pack(anchor="w", side="top")
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", lambda e: self._legend())

    def set_records(self, records: list[dict]):
        self.records = records
        self.redraw()

    def schedule(self):
        """Odswiezenie przy najblizszej okazji - wiele zmian naraz = jedno rysowanie."""
        if not self._pending:
            self._pending = True
            self.after_idle(self.redraw)

    def redraw(self):
        self._pending = False
        c = self.canvas
        c.delete("all")
        self._groups = [status_group(r) for r in self.records]
        n = len(self._groups)
        w = max(1, c.winfo_width() - 2)
        h = int(c.cget("height"))
        if n:
            run_start, run_color = 0, None
            for x in range(w + 1):
                if x < w:
                    i0 = x * n // w
                    i1 = max(i0 + 1, (x + 1) * n // w)
                    g = max(self._groups[i0:i1], key=lambda k: WEIGHT[k])
                    color = GROUP_COLORS[g]
                else:
                    color = None
                if color != run_color:
                    if run_color is not None:
                        c.create_rectangle(run_start + 1, 1, x + 1, h + 1, fill=run_color, width=0)
                    run_start, run_color = x, color
        self._legend()

    def _legend(self):
        counts = {k: 0 for k, _ in LABELS}
        for g in self._groups:
            counts[g] += 1
        parts = [f"{label}: {counts[k]}" for k, label in LABELS if counts[k]]
        self.lbl.config(text="Stan kolekcji  ·  " + "  ·  ".join(parts) if parts else "")

    def _index_at(self, x: int) -> int | None:
        n = len(self.records)
        w = max(1, self.canvas.winfo_width() - 2)
        if not n:
            return None
        return max(0, min(n - 1, (x - 1) * n // w))

    def _hover(self, e):
        i = self._index_at(e.x)
        if i is None:
            return
        r = self.records[i]
        self.lbl.config(text=f"{i + 1}/{len(self.records)}: {r.get('old_name')} - "
                             f"{r.get('date_iso') or '?'}, nr {r.get('issue_number') or '?'} "
                             f"({r.get('status') or ''})")

    def _click(self, e):
        n = len(self.records)
        w = max(1, self.canvas.winfo_width() - 2)
        if not n:
            return
        # w obrebie klikniecia wybieramy najgorszy plik - ten, ktory widac na pasku
        i0 = max(0, (e.x - 1) * n // w)
        i1 = max(i0 + 1, min(n, e.x * n // w))
        i = max(range(i0, i1), key=lambda k: WEIGHT[self._groups[k]] if k < len(self._groups) else 0)
        self.on_click(self.records[i])
