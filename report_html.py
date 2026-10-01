"""Raport HTML z przelotu: ile plikow, jak skutecznie, ile czasu oszczedzono.

Jeden samodzielny plik (bez zewnetrznych skryptow, czcionek i obrazkow) - otwiera sie
w kazdej przegladarce, takze offline. Wszystkie liczby pochodza z danych sesji; czas
pracy recznej to zalozenie (czas na plik), ktore uzytkownik moze zmienic.
"""
from __future__ import annotations

import html
import re
import time
from collections import Counter

from verify import needs_check, status_group

SIGNATURE = ('dla projektu „Garrincha 2033” · Wojciech Borowa · '
             'wojciech.borowa@gmail.com')
DEFAULT_MANUAL_SEC = 30      # reczny odczyt + wpisanie daty/numeru + zmiana nazwy jednego pliku
DEFAULT_REVIEW_SEC = 20      # reczne sprawdzenie jednego pliku "do weryfikacji"
FALLBACK_AI_SEC = 4.0        # szacunek czasu AI na plik, gdy sesja nie zapisala czasu


# ------------------------------------------------------------------ liczby
def fmt_int(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


def fmt_dur(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


def guess_collection(records: list[dict]) -> str:
    titles = Counter()
    for r in records:
        t = (r.get("title") or "").strip()
        if not t:
            nd = r.get("name_data") or {}
            t = (nd.get("title") or "").strip()
        if t:
            titles[t] += 1
    return titles.most_common(1)[0][0] if titles else "Kolekcja skanow"


def collect_stats(records: list[dict], run_stats: dict | None = None, *,
                  collection: str = "", manual_sec: float = DEFAULT_MANUAL_SEC,
                  review_sec: float = DEFAULT_REVIEW_SEC, ai_seconds: float | None = None,
                  mode_label: str = "") -> dict:
    run_stats = run_stats or {}
    total = len(records)
    groups = Counter(status_group(r) for r in records)
    unread = groups.get("new", 0)
    error = groups.get("error", 0)
    check = sum(1 for r in records if needs_check(r) and status_group(r) != "error")
    ok = total - unread - error - check
    processed = total - unread
    renamed = sum(1 for r in records if "zmieniono" in (r.get("status") or "").lower())

    confs = [float(r["confidence"]) for r in records if r.get("confidence") is not None]
    bins = [("< 0,5", 0.0, 0.5), ("0,5–0,7", 0.5, 0.7), ("0,7–0,8", 0.7, 0.8),
            ("0,8–0,9", 0.8, 0.9), ("0,9–1,0", 0.9, 1.01)]
    hist = [(lab, sum(1 for c in confs if lo <= c < hi)) for lab, lo, hi in bins]

    years = Counter()
    issues = set()
    dates = []
    for r in records:
        d = (r.get("date_iso") or "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            years[int(d[:4])] += 1
            dates.append(d)
        if r.get("issue_number"):
            issues.add(str(r["issue_number"]))

    known_ai = ai_seconds if ai_seconds is not None else run_stats.get("ai_seconds")
    ai_estimated = not known_ai
    ai_sec = float(known_ai) if known_ai else processed * FALLBACK_AI_SEC
    manual_total = total * manual_sec
    with_program = ai_sec + (check + error) * review_sec
    saved = max(0.0, manual_total - with_program)
    pct = (saved / manual_total * 100) if manual_total else 0.0

    return {
        "collection": collection or guess_collection(records), "mode": mode_label,
        "total": total, "ok": ok, "check": check, "error": error, "unread": unread,
        "processed": processed, "renamed": renamed,
        "success_pct": (ok / processed * 100) if processed else 0.0,
        "avg_conf": (sum(confs) / len(confs)) if confs else None, "hist": hist,
        "years": dict(sorted(years.items())),
        "date_min": min(dates) if dates else "", "date_max": max(dates) if dates else "",
        "issues": len(issues),
        "ai_seconds": ai_sec, "ai_estimated": ai_estimated,
        "per_file": (ai_sec / processed) if processed else 0.0,
        "files_per_hour": (processed / ai_sec * 3600) if ai_sec else 0.0,
        "manual_sec": manual_sec, "review_sec": review_sec,
        "manual_total": manual_total, "with_program": with_program,
        "saved": saved, "saved_pct": pct,
        "generated": time.strftime("%Y-%m-%d %H:%M"),
    }


# ------------------------------------------------------------------ wykresy SVG
C_OK, C_CHECK, C_ERR, C_NEW = "#4fa889", "#d9a441", "#c4573f", "#5d6e87"


def _donut(parts: list[tuple[str, int, str]], center_big: str, center_small: str) -> str:
    r, cx, cy, w = 78, 100, 100, 22
    circ = 2 * 3.14159265 * r
    total = sum(v for _, v, _ in parts) or 1
    off, segs = 0.0, []
    for _, v, col in parts:
        if v <= 0:
            continue
        ln = circ * v / total
        segs.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{col}" '
                    f'stroke-width="{w}" stroke-dasharray="{ln:.2f} {circ - ln:.2f}" '
                    f'stroke-dashoffset="{-off:.2f}" transform="rotate(-90 {cx} {cy})"/>')
        off += ln
    return (f'<svg viewBox="0 0 200 200" class="donut" role="img" aria-label="Podzial wynikow">'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#1d2e47" stroke-width="{w}"/>'
            + "".join(segs) +
            f'<text x="100" y="104" text-anchor="middle" class="d-big">{center_big}</text>'
            f'<text x="100" y="126" text-anchor="middle" class="d-small">{center_small}</text></svg>')


def _bars(items: list[tuple[str, float]], color: str, height: int = 150) -> str:
    n = len(items)
    if not n:
        return ""
    width = 560
    gap = 6 if n <= 20 else 3
    bw = (width - gap * (n - 1)) / n
    mx = max(v for _, v in items) or 1
    out = [f'<svg viewBox="0 0 {width} {height + 34}" class="bars" role="img">']
    step = 1 if n <= 12 else max(1, n // 10)
    for i, (lab, v) in enumerate(items):
        h = (height - 18) * v / mx
        x = i * (bw + gap)
        out.append(f'<rect x="{x:.1f}" y="{height - h:.1f}" width="{bw:.1f}" height="{h:.1f}" '
                   f'rx="2" fill="{color}"/>')
        if n <= 12 and v:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{height - h - 5:.1f}" text-anchor="middle" '
                       f'class="b-val">{fmt_int(v)}</text>')
        if i % step == 0:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{height + 20}" text-anchor="middle" '
                       f'class="b-lab">{html.escape(lab)}</text>')
    out.append("</svg>")
    return "".join(out)


def _year_items(years: dict[int, int]) -> tuple[list[tuple[str, float]], str]:
    if not years:
        return [], ""
    lo, hi = min(years), max(years)
    if hi - lo <= 40:
        return [(str(y), years.get(y, 0)) for y in range(lo, hi + 1)], "Rozklad wg roku"
    dec = Counter()
    for y, c in years.items():
        dec[y // 10 * 10] += c
    return [(f"{d}s", dec[d]) for d in sorted(dec)], "Rozklad wg dekady"


# ------------------------------------------------------------------ HTML
CSS = """
:root{--bg:#0e1a2b;--card:#142238;--line:#243652;--tx:#e9eef5;--mu:#8ea0b9;--ac:#d6ae5e;
--ok:#4fa889;--ck:#d9a441;--er:#c4573f;--nw:#5d6e87}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font-family:"Segoe UI","Helvetica Neue",Arial,sans-serif;
-webkit-font-smoothing:antialiased;padding:48px 24px}
.page{max-width:1040px;margin:0 auto}
header{text-align:center;padding-bottom:34px;border-bottom:1px solid var(--line)}
.eyebrow{font-size:12px;letter-spacing:.28em;text-transform:uppercase;color:var(--mu)}
h1{font-family:Georgia,"Times New Roman",serif;font-weight:400;font-size:40px;line-height:1.15;
margin:14px 0 10px;letter-spacing:.01em}
.sub{color:var(--mu);font-size:15px}
.hero{text-align:center;padding:52px 0 44px}
.hero .lab{font-size:13px;letter-spacing:.3em;text-transform:uppercase;color:var(--ac)}
.hero .num{font-family:Georgia,"Times New Roman",serif;font-size:104px;line-height:1.05;
font-weight:400;margin:14px 0 10px;font-variant-numeric:lining-nums;letter-spacing:-.01em}
.hero .note{color:var(--mu);font-size:17px}
.hero .note b{color:var(--tx);font-weight:600}
.cmp{max-width:760px;margin:0 auto 52px}
.row{display:grid;grid-template-columns:150px 1fr 130px;gap:16px;align-items:center;margin:14px 0}
.row .n{color:var(--mu);font-size:14px}
.row .v{font-size:15px;font-weight:600;text-align:right;font-variant-numeric:tabular-nums}
.track{height:16px;background:#1b2c46;border-radius:3px;overflow:hidden}
.fill{height:100%;border-radius:3px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:28px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:22px 18px;text-align:center}
.kpi .v{font-family:Georgia,serif;font-size:36px;line-height:1.1}
.kpi .l{margin-top:8px;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--mu)}
.panels{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:28px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:24px}
.panel h2{font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--mu);
font-weight:500;margin-bottom:18px}
.q{display:flex;align-items:center;gap:26px}
.donut{width:190px;flex:none}
.d-big{fill:var(--tx);font-family:Georgia,serif;font-size:34px}
.d-small{fill:var(--mu);font-size:11px;letter-spacing:.14em;text-transform:uppercase}
.leg{list-style:none;font-size:14px}
.leg li{display:flex;align-items:center;gap:10px;margin:9px 0;color:var(--tx)}
.leg i{width:10px;height:10px;border-radius:2px;flex:none}
.leg span{margin-left:auto;padding-left:18px;color:var(--mu);font-variant-numeric:tabular-nums}
.bars{width:100%;height:auto;display:block}
.b-lab{fill:var(--mu);font-size:11px}.b-val{fill:var(--tx);font-size:11px}
.facts{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:28px;text-align:center}
.facts div{padding:6px 0}.facts b{display:block;font-family:Georgia,serif;font-weight:400;font-size:24px}
.facts span{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--mu)}
.method{color:var(--mu);font-size:13px;line-height:1.6;text-align:center;max-width:760px;margin:0 auto 34px}
.list{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:24px;margin-bottom:28px}
.list h2{font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--mu);font-weight:500;margin-bottom:12px}
.list td{padding:5px 14px 5px 0;font-size:13px;color:var(--tx);border-bottom:1px solid var(--line)}
.list td.s{color:var(--mu);white-space:nowrap}
footer{border-top:1px solid var(--line);padding-top:20px;text-align:center;color:var(--mu);font-size:12px;
letter-spacing:.04em}
@media(max-width:760px){.kpis,.facts{grid-template-columns:1fr 1fr}.panels{grid-template-columns:1fr}
.hero .num{font-size:68px}.row{grid-template-columns:100px 1fr 100px}.q{flex-direction:column}}
@media print{body{background:#fff;color:#000}}
"""


def build_html(st: dict, flagged: list[tuple[str, str]] | None = None) -> str:
    e = html.escape
    pct_ok = f"{st['success_pct']:.0f}%"
    scale = st["manual_total"] or 1
    w_prog = max(2.0, min(100.0, st["with_program"] / scale * 100))
    per_file = f"{st['per_file']:.1f}".replace(".", ",")
    est = " (szacunek)" if st["ai_estimated"] else ""
    donut = _donut([("ok", st["ok"], C_OK), ("check", st["check"], C_CHECK),
                    ("error", st["error"], C_ERR), ("new", st["unread"], C_NEW)],
                   pct_ok, "pewnie")
    legend = "".join(
        f'<li><i style="background:{c}"></i>{lab}<span>{fmt_int(v)}</span></li>'
        for lab, v, c in (("Odczytane pewnie", st["ok"], C_OK),
                          ("Do sprawdzenia", st["check"], C_CHECK),
                          ("Bledy odczytu", st["error"], C_ERR),
                          ("Nieodczytane", st["unread"], C_NEW)) if v)
    hist = _bars([(lab, v) for lab, v in st["hist"]], "#6f93c4", 130)
    yitems, ytitle = _year_items(st["years"])
    year_panel = (f'<div class="panel"><h2>{ytitle}</h2>{_bars(yitems, "#d6ae5e", 130)}</div>'
                  if yitems else
                  f'<div class="panel"><h2>Zmienione nazwy</h2><div class="kpi" style="border:0">'
                  f'<div class="v">{fmt_int(st["renamed"])}</div><div class="l">plikow</div></div></div>')
    span = ""
    if st["date_min"]:
        span = f'{st["date_min"]} – {st["date_max"]}'
    avg_conf = f'{st["avg_conf"]:.2f}'.replace(".", ",") if st["avg_conf"] is not None else "–"
    flagged_html = ""
    if flagged:
        rows = "".join(f'<tr><td>{e(n)}</td><td class="s">{e(s)}</td></tr>' for n, s in flagged[:60])
        more = f'<p class="sub" style="margin-top:10px">… i {len(flagged) - 60} kolejnych</p>' \
            if len(flagged) > 60 else ""
        flagged_html = (f'<div class="list"><h2>Pliki wymagajace uwagi</h2>'
                        f'<table>{rows}</table>{more}</div>')
    sub = " · ".join(x for x in (st["mode"], span) if x)
    return f"""<!DOCTYPE html>
<html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Raport przelotu – {e(st['collection'])}</title><style>{CSS}</style></head>
<body><div class="page">
<header><div class="eyebrow">Raport z przetwarzania kolekcji</div>
<h1>{e(st['collection'])}</h1><div class="sub">{e(sub)}</div></header>

<section class="hero"><div class="lab">Czas odzyskany</div>
<div class="num">{fmt_dur(st['saved'])}</div>
<div class="note"><b>{st['saved_pct']:.0f}% mniej pracy</b> – {fmt_int(st['total'])}
plikow opracowanych w {fmt_dur(st['with_program'])}{est}</div></section>

<section class="cmp">
<div class="row"><div class="n">Recznie</div><div class="track"><div class="fill"
style="width:100%;background:#4a5b75"></div></div><div class="v">{fmt_dur(st['manual_total'])}</div></div>
<div class="row"><div class="n">Z programem</div><div class="track"><div class="fill"
style="width:{w_prog:.1f}%;background:var(--ac)"></div></div><div class="v">{fmt_dur(st['with_program'])}</div></div>
</section>

<section class="kpis">
<div class="kpi"><div class="v">{fmt_int(st['total'])}</div><div class="l">skanow</div></div>
<div class="kpi"><div class="v">{pct_ok}</div><div class="l">odczytanych pewnie</div></div>
<div class="kpi"><div class="v">{fmt_dur(st['ai_seconds'])}</div><div class="l">czas odczytu AI{est}</div></div>
<div class="kpi"><div class="v">{per_file} s</div><div class="l">na plik</div></div>
</section>

<section class="panels">
<div class="panel"><h2>Wynik odczytu</h2><div class="q">{donut}<ul class="leg">{legend}</ul></div></div>
<div class="panel"><h2>Pewnosc odczytu (srednio {avg_conf})</h2>{hist}</div>
</section>

<section class="panels">{year_panel}
<div class="panel"><h2>Przebieg</h2><div class="facts" style="grid-template-columns:1fr 1fr;margin:0">
<div><b>{fmt_int(st['renamed'])}</b><span>zmienionych nazw</span></div>
<div><b>{fmt_int(st['issues'])}</b><span>numerow wydan</span></div>
<div><b>{fmt_int(st['files_per_hour'])}</b><span>plikow na godzine</span></div>
<div><b>{fmt_int(st['check'] + st['error'])}</b><span>do kontroli reczne</span></div></div></div>
</section>

{flagged_html}
<p class="method">Czas recznej pracy zalozono na {st['manual_sec']:.0f} s na plik (otwarcie skanu, odczyt daty i numeru,
wpisanie nazwy). W czasie z programem uwzgledniono odczyt AI oraz reczna kontrole
{fmt_int(st['check'] + st['error'])} plikow po {st['review_sec']:.0f} s.</p>

<footer>{e(SIGNATURE)}<br>Wygenerowano {e(st['generated'])}</footer>
</div></body></html>"""


def flagged_list(records: list[dict]) -> list[tuple[str, str]]:
    out = []
    for r in records:
        g = status_group(r)
        if g in ("check", "error"):
            out.append((r.get("old_name") or "", "blad odczytu" if g == "error" else "do sprawdzenia"))
    return out
