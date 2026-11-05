"""Deliverable files: a publishing step returns a DocumentSpec, rendered here as a PDF report,
a self-contained HTML slide deck, or a PowerPoint file.

Agents write structured content, not markup: templates own the look, so cheap models still
produce consistent, modern files. Agent text is untrusted (it may quote web pages), so markdown
is rendered with raw HTML disabled, images dropped, every SVG string escaped, and the PDF
renderer never fetches a URL. Charts appear only in the HTML deck; the PDF and PowerPoint show
the chart's data as a table. A chart with a value not found in the step's inputs (as written, or in
thousands/millions/billions) is dropped by guard_charts. That catches invented specific figures; it
can't tell whether a value belongs to its label, and small round numbers will usually match something.
"""

from __future__ import annotations

import html
import io
import json
import math
import os
import re
from pathlib import Path
from typing import Literal

from markdown_it import MarkdownIt
from pydantic import BaseModel, Field, model_validator

from .schema import Deliverable, FileRecord

PALETTE = ["#4f46e5", "#0ea5e9", "#f59e0b", "#10b981"]

FORMATS: dict[str, tuple[str, str, str]] = {  # format -> (extension, media type, label)
    "pdf": ("pdf", "application/pdf", "PDF report"),
    "slides": ("html", "text/html", "Slides (web)"),
    "pptx": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "PowerPoint"),
}


# --- the spec a publishing step returns -------------------------------------------------------


class ChartSeries(BaseModel):
    name: str = Field(max_length=60)
    values: list[float] = Field(min_length=2, max_length=24)


class ChartSpec(BaseModel):
    """Only for numbers that appear in the step's inputs; never estimated or invented."""

    kind: Literal["bar", "line", "pie"]
    title: str = Field(max_length=120)
    labels: list[str] = Field(min_length=2, max_length=24, description="category or time labels")
    series: list[ChartSeries] = Field(min_length=1, max_length=4, description="one value per label")
    unit: str = Field("", max_length=20, description='e.g. "%", "USD bn"; empty if none')
    source: str = Field(max_length=200, description="which input step or URL the numbers came from")

    @model_validator(mode="after")
    def _shape(self) -> ChartSpec:
        for s in self.series:
            if len(s.values) != len(self.labels):
                raise ValueError(f"series {s.name!r} has {len(s.values)} values for {len(self.labels)} labels")
        if self.kind == "pie" and (len(self.series) != 1 or any(v < 0 for v in self.series[0].values)):
            raise ValueError("a pie chart takes exactly one series of non-negative values")
        return self


class Section(BaseModel):
    """One slide of a deck, or one section of a report."""

    heading: str = Field(max_length=120)
    body: str = Field("", max_length=3000, description="markdown: short paragraphs, - bullets, **bold**; no HTML")
    notes: str = Field("", max_length=2000, description="speaker notes (slides only)")
    chart: ChartSpec | None = None


class DocumentSpec(BaseModel):
    title: str = Field(max_length=160)
    subtitle: str = Field("", max_length=240)
    sections: list[Section] = Field(min_length=1, max_length=30)

    def to_markdown(self) -> str:
        """Readable text version: the step's output shown in the UI and passed downstream."""
        parts = [f"# {self.title}"] + ([self.subtitle] if self.subtitle else [])
        for s in self.sections:
            parts.append(f"## {s.heading}")
            if s.body:
                parts.append(s.body)
            if s.chart:
                parts.append(f"[chart: {s.chart.title}]")
        return "\n\n".join(parts)


PUBLISHER_INSTRUCTIONS = {
    "pdf": "a PDF report: a clear title, then sections of short paragraphs and bullets",
    "slides": "a slide deck: one idea per slide, a heading plus 3-6 short bullets, speaker notes for detail",
    "pptx": "a PowerPoint deck: one idea per slide, a heading plus 3-6 short bullets, speaker notes for detail",
}


def publisher_instructions(fmt: Deliverable) -> str:
    chart_rule = (
        "Add a chart to a slide only when the task asks for one or the inputs contain at least three comparable "
        "numbers or a series over time. Use only numbers that appear in the inputs, copied exactly; never estimate, "
        "compute or invent values. Name the input step or URL they came from in the chart's source. Charts whose "
        "numbers are not in the inputs are removed."
    )
    return (
        f"You are the publishing step. Turn the inputs into {PUBLISHER_INSTRUCTIONS[fmt]}. Keep every fact from "
        "the inputs that matters, stay accurate, and do not add facts that are not in them. Write plain markdown "
        f"text only (no HTML, no images). {chart_rule}"
    )


# --- chart guard -------------------------------------------------------------------------------

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[float]:
    """Every number in the text, with and without its sign; commas are thousands separators."""
    found: set[float] = set()
    for token in _NUMBER.findall(text):
        try:
            v = float(token.replace(",", ""))
        except ValueError:
            continue
        found.update((v, abs(v)))
    return found


def _supported(value: float, numbers: set[float]) -> bool:
    """The value is an input number, as written or shown in thousands, millions or billions, give or
    take rounding (0.5%)."""
    return any(
        abs(value - n / scale) <= max(0.005 * abs(n / scale), 0.01)
        for n in numbers
        for scale in (1, 1e3, 1e6, 1e9)
    )


def guard_charts(doc: DocumentSpec, source_text: str) -> tuple[DocumentSpec, list[str]]:
    """Drop charts with any value not found in the source text; returns the titles dropped."""
    numbers = numbers_in(source_text)
    dropped: list[str] = []
    sections = []
    for s in doc.sections:
        if s.chart and not all(_supported(v, numbers) for series in s.chart.series for v in series.values):
            dropped.append(s.chart.title)
            s = s.model_copy(update={"chart": None})
        sections.append(s)
    return doc.model_copy(update={"sections": sections}), dropped


# --- shared helpers ------------------------------------------------------------------------------

_md = MarkdownIt("commonmark", {"html": False}).enable("table").disable("image")  # raw HTML is escaped


def render_markdown(text: str) -> str:
    return _md.render(text)


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _num(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v):,}"


def _short(label: str, n: int = 14) -> str:
    return label if len(label) <= n else label[: n - 1] + "…"


# --- chart SVG (HTML deck only) ------------------------------------------------------------------

W, H = 640, 380
PAD_L, PAD_R, PAD_T, PAD_B = 64, 16, 48, 70


def _unit_suffix(chart: ChartSpec) -> str:
    """" (unit)" after the title, unless the title already names the unit."""
    return f" ({_e(chart.unit)})" if chart.unit and chart.unit.lower() not in chart.title.lower() else ""


def chart_svg(chart: ChartSpec) -> str:
    body = _pie(chart) if chart.kind == "pie" else _axes_chart(chart)
    unit = _unit_suffix(chart)
    return (
        f'<svg class="chart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="{_e(chart.title)}">'
        f'<text x="{W / 2}" y="24" style="font-size:{_title_px(chart.title + unit)}px" class="c-title" '
        f'text-anchor="middle">{_e(_short(chart.title, 90))}{unit}</text>'
        f"{body}"
        f'<text x="{W / 2}" y="{H - 6}" class="c-source" text-anchor="middle">Source: {_e(chart.source)}</text>'
        "</svg>"
    )


def _title_px(title: str) -> float:
    """Shrink long titles so they fit the chart's width (about 50 characters at full size)."""
    return round(max(12.0, min(21.0, 21 * 50 / max(len(title), 1))), 1)


def _legend(names: list[str], y: float) -> str:
    if len(names) < 2:
        return ""
    step = (W - PAD_L - PAD_R) / len(names)
    return "".join(
        f'<rect x="{PAD_L + i * step}" y="{y - 9}" width="10" height="10" rx="2" fill="{PALETTE[i]}"/>'
        f'<text x="{PAD_L + i * step + 15}" y="{y}" class="c-legend">{_e(_short(n, 24))}</text>'
        for i, n in enumerate(names)
    )


def _nice_step(raw: float) -> float:
    """Smallest of 1, 2, 2.5 or 5 x 10^n that is >= raw."""
    power = 10 ** math.floor(math.log10(raw))
    return next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw)


def _axes_chart(chart: ChartSpec) -> str:
    values = [v for s in chart.series for v in s.values]
    lo, hi = min(0.0, min(values)), max(0.0, max(values))
    step = _nice_step((hi - lo) / 4 or 1)
    lo, hi = math.floor(lo / step) * step, math.ceil(hi / step) * step
    if hi == lo:
        hi = lo + step
    plot_w, plot_h = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    y_of = lambda v: PAD_T + (hi - v) / (hi - lo) * plot_h  # noqa: E731
    parts = []
    for i in range(round((hi - lo) / step) + 1):  # gridlines and tick labels on round numbers
        v = lo + step * i
        y = y_of(v)
        parts.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}" class="c-grid"/>')
        parts.append(f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" class="c-tick" text-anchor="end">{_num(round(v, 6))}</text>')
    n = len(chart.labels)
    slot = plot_w / n
    for i, label in enumerate(chart.labels):
        x = PAD_L + slot * (i + 0.5)
        parts.append(
            f'<text x="{x:.1f}" y="{H - PAD_B + 18}" class="c-tick" text-anchor="middle">{_e(_short(label))}</text>'
        )
    if chart.kind == "bar":
        group = slot * 0.7
        bar_w = group / len(chart.series)
        for si, s in enumerate(chart.series):
            for i, v in enumerate(s.values):
                x = PAD_L + slot * i + (slot - group) / 2 + si * bar_w
                top, bottom = sorted((y_of(v), y_of(0)))
                parts.append(
                    f'<rect x="{x:.1f}" y="{top:.1f}" width="{bar_w - 2:.1f}" height="{max(bottom - top, 1):.1f}" '
                    f'rx="3" fill="{PALETTE[si]}"><title>{_e(chart.labels[i])}: {_num(v)}</title></rect>'
                )
    else:
        for si, s in enumerate(chart.series):
            pts = [(PAD_L + slot * (i + 0.5), y_of(v)) for i, v in enumerate(s.values)]
            parts.append(
                f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" '
                f'stroke="{PALETTE[si]}" stroke-width="3" stroke-linejoin="round"/>'
            )
            parts.extend(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{PALETTE[si]}">'
                f"<title>{_e(chart.labels[i])}: {_num(s.values[i])}</title></circle>"
                for i, (x, y) in enumerate(pts)
            )
    parts.append(_legend([s.name for s in chart.series], H - PAD_B + 42))
    return "".join(parts)


def _pie(chart: ChartSpec) -> str:
    values = chart.series[0].values
    total = sum(values)
    if total <= 0:
        return f'<text x="{W / 2}" y="{H / 2}" class="c-tick" text-anchor="middle">no data</text>'
    cx, cy, r = 200, 200, 130
    parts, angle = [], -math.pi / 2
    for i, v in enumerate(values):
        sweep = v / total * 2 * math.pi
        color = PALETTE[i % len(PALETTE)] if i < len(PALETTE) else f"hsl({(i * 47) % 360} 65% 55%)"
        x1, y1 = cx + r * math.cos(angle), cy + r * math.sin(angle)
        end = angle + min(sweep, 2 * math.pi - 1e-4)  # a single 100% slice still draws
        x2, y2 = cx + r * math.cos(end), cy + r * math.sin(end)
        large = 1 if sweep > math.pi else 0
        parts.append(
            f'<path d="M{cx},{cy} L{x1:.1f},{y1:.1f} A{r},{r} 0 {large} 1 {x2:.1f},{y2:.1f} Z" fill="{color}" '
            f'stroke="#fff" stroke-width="2"><title>{_e(chart.labels[i])}: {_num(v)}</title></path>'
        )
        ly = 70 + i * 24
        if ly < H - 30:
            parts.append(
                f'<rect x="380" y="{ly - 10}" width="12" height="12" rx="2" fill="{color}"/>'
                f'<text x="398" y="{ly}" class="c-legend">{_e(_short(chart.labels[i], 22))} · '
                f"{v / total:.0%}</text>"
            )
        angle += sweep
    return "".join(parts)


def chart_table(chart: ChartSpec) -> str:
    head = "".join(f"<th>{_e(s.name)}</th>" for s in chart.series)
    rows = "".join(
        f"<tr><td>{_e(label)}</td>" + "".join(f"<td>{_num(s.values[i])}</td>" for s in chart.series) + "</tr>"
        for i, label in enumerate(chart.labels)
    )
    unit = _unit_suffix(chart)
    return (
        f'<table class="data"><caption>{_e(chart.title)}{unit}</caption>'
        f"<thead><tr><th></th>{head}</tr></thead><tbody>{rows}</tbody></table>"
        f'<p class="source">Source: {_e(chart.source)}</p>'
    )


# --- HTML slide deck -----------------------------------------------------------------------------

SLIDES_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden;background:#0b1020;font-family:Inter,"Segoe UI",system-ui,-apple-system,Roboto,sans-serif}
#viewport{position:fixed;inset:0;overflow:hidden}
#world{position:absolute;left:0;top:0;transform-origin:0 0;transition:transform .9s cubic-bezier(.65,0,.35,1)}
.slide{position:absolute;width:1280px;height:720px;border-radius:18px;overflow:hidden;background:#fff;color:#0f172a;
  padding:64px 80px;display:flex;flex-direction:column;box-shadow:0 30px 80px rgba(0,0,0,.35);transition:opacity .6s,filter .6s}
.slide:not(.active){opacity:.35;filter:saturate(.4)}
.overview .slide{opacity:1;filter:none;cursor:pointer}
.overview .slide:hover{outline:8px solid #818cf8}
.slide.title{background:radial-gradient(circle at 20% 20%,#6366f1 0,transparent 55%),radial-gradient(circle at 85% 80%,#0ea5e9 0,transparent 50%),#0b1020;
  color:#fff;justify-content:center}
.slide.title h1{font-size:76px;line-height:1.05;letter-spacing:-.02em;max-width:1050px}
.slide.title p{font-size:30px;margin-top:28px;color:#c7d2fe;max-width:1000px}
.slide h2{font-size:48px;letter-spacing:-.01em;line-height:1.1;margin-bottom:32px;padding-left:22px;border-left:8px solid #4f46e5}
.content{flex:1;display:flex;gap:48px;min-height:0}
.text{flex:1;min-width:0;font-size:30px;line-height:1.45;color:#1e293b;overflow:hidden}
.text p+p,.text ul,.text ol,.text p+ul{margin-top:.55em}
.text ul,.text ol{padding-left:1.1em}.text li{margin:.25em 0}.text li::marker{color:#4f46e5}
.text strong{color:#0f172a}.text a{color:#4f46e5}
.text code{font-size:.85em;background:#eef2ff;padding:.05em .3em;border-radius:6px}
.text table{border-collapse:collapse;font-size:.8em;margin-top:.4em}
.text th,.text td{padding:.25em .9em .25em 0;border-bottom:1px solid #e2e8f0;text-align:left}
.text th{color:#4f46e5;border-bottom:2px solid #4f46e5}
.chartbox{flex:1.15;display:flex;align-items:center;justify-content:center;min-width:0}
.content.chart-only .chartbox{flex:1}
svg.chart{width:100%;height:auto;max-height:540px;font-family:inherit}
.c-title{font-size:21px;font-weight:600;fill:#0f172a}.c-tick{font-size:15px;fill:#64748b}
.c-legend{font-size:15px;fill:#334155}.c-source{font-size:12px;fill:#94a3b8}.c-grid{stroke:#e2e8f0}
.notes{display:none}
#hud{position:fixed;left:0;right:0;bottom:0;height:4px;background:rgba(255,255,255,.08)}
#bar{height:100%;background:linear-gradient(90deg,#6366f1,#0ea5e9);width:0;transition:width .6s}
#count{position:fixed;right:18px;bottom:14px;color:#94a3b8;font-size:14px;font-variant-numeric:tabular-nums}
#hint{position:fixed;left:18px;bottom:14px;color:#64748b;font-size:13px;transition:opacity 1s}
#notes{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);max-width:900px;width:90%;background:rgba(15,23,42,.92);
  color:#e2e8f0;font-size:17px;line-height:1.5;padding:16px 20px;border-radius:12px;display:none;white-space:pre-wrap}
body.show-notes #notes:not(:empty){display:block}
@media print{html,body{overflow:visible;background:#fff}#viewport{position:static}#world{position:static;transform:none!important}
  .slide{position:relative!important;left:auto!important;top:auto!important;opacity:1!important;filter:none!important;
  box-shadow:none;page-break-after:always;border-radius:0}#hud,#count,#hint,#notes{display:none}
  @page{size:1280px 720px;margin:0}}
"""

SLIDES_JS = """
(()=>{
const world=document.getElementById('world'),slides=[...document.querySelectorAll('.slide')];
const W=1280,H=720,GAP=160,cols=Math.ceil(Math.sqrt(slides.length));
let i=0,overview=false;
slides.forEach((s,k)=>{s.style.left=(k%cols)*(W+GAP)+'px';s.style.top=Math.floor(k/cols)*(H+GAP)+'px';
  s.addEventListener('click',()=>{if(overview){overview=false;go(k)}})});
// shrink text that doesn't fit its slide
slides.forEach(s=>{const t=s.querySelector('.text');if(!t)return;let f=30;
  while(t.scrollHeight>t.clientHeight+2&&f>16){f-=1;t.style.fontSize=f+'px'}});
function cam(x,y,w,h){const k=Math.min(innerWidth/(w+80),innerHeight/(h+80));
  world.style.transform=`translate(${(innerWidth-w*k)/2-x*k}px,${(innerHeight-h*k)/2-y*k}px) scale(${k})`}
function render(){document.body.classList.toggle('overview',overview);
  if(overview){const rows=Math.ceil(slides.length/cols);cam(0,0,cols*(W+GAP)-GAP,rows*(H+GAP)-GAP)}
  else{const s=slides[i];cam(s.offsetLeft,s.offsetTop,W,H)}
  slides.forEach((s,k)=>s.classList.toggle('active',k===i));
  document.getElementById('bar').style.width=((i+1)/slides.length*100)+'%';
  document.getElementById('count').textContent=(i+1)+' / '+slides.length;
  const n=slides[i].querySelector('.notes');document.getElementById('notes').textContent=n?n.textContent:'';
  history.replaceState(null,'','#'+(i+1))}
function go(k){i=Math.max(0,Math.min(slides.length-1,k));render()}
addEventListener('keydown',e=>{const k=e.key;
  if(['ArrowRight','ArrowDown','PageDown',' ','Enter'].includes(k)){overview=false;go(i+1)}
  else if(['ArrowLeft','ArrowUp','PageUp','Backspace'].includes(k)){overview=false;go(i-1)}
  else if(k==='Home')go(0);else if(k==='End')go(slides.length-1);
  else if(k==='o'||k==='O'||k==='Escape'){overview=!overview&&k!=='Escape'||(k==='Escape'?false:overview);render()}
  else if(k==='n'||k==='N')document.body.classList.toggle('show-notes');else return;e.preventDefault()});
addEventListener('resize',render);
const start=parseInt(location.hash.slice(1),10);if(start>0)i=Math.min(slides.length,start)-1;
world.style.transition='none';render();requestAnimationFrame(()=>requestAnimationFrame(()=>world.style.transition=''));
setTimeout(()=>document.getElementById('hint').style.opacity=0,6000);
})();
"""


def _slide(section: Section) -> str:
    text = render_markdown(section.body) if section.body.strip() else ""
    chart = chart_svg(section.chart) if section.chart else ""
    if text and chart:
        content = f'<div class="text">{text}</div><div class="chartbox">{chart}</div>'
        cls = "content"
    elif chart:
        content, cls = f'<div class="chartbox">{chart}</div>', "content chart-only"
    else:
        content, cls = f'<div class="text">{text}</div>', "content"
    notes = f'<div class="notes">{_e(section.notes)}</div>' if section.notes else ""
    return f'<section class="slide"><h2>{_e(section.heading)}</h2><div class="{cls}">{content}</div>{notes}</section>'


def slides_html(doc: DocumentSpec) -> str:
    sub = f"<p>{_e(doc.subtitle)}</p>" if doc.subtitle else ""
    slides = f'<section class="slide title"><h1>{_e(doc.title)}</h1>{sub}</section>' + "".join(
        _slide(s) for s in doc.sections
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(doc.title)}</title><style>{SLIDES_CSS}</style></head><body>"
        f'<div id="viewport"><div id="world">{slides}</div></div>'
        '<div id="hud"><div id="bar"></div></div><div id="count"></div>'
        '<div id="hint">← → navigate · O overview · N notes</div><div id="notes"></div>'
        f"<script>{SLIDES_JS}</script></body></html>"
    )


# --- PDF report ----------------------------------------------------------------------------------

REPORT_CSS = """
@page{size:A4;margin:22mm 20mm 24mm;@bottom-center{content:counter(page) " / " counter(pages);font-size:9pt;color:#94a3b8}}
@page:first{@bottom-center{content:none}}
body{font-family:"DejaVu Sans",Inter,"Segoe UI",sans-serif;font-size:10.5pt;line-height:1.55;color:#1e293b}
.cover{border-left:6px solid #4f46e5;padding:6mm 0 6mm 8mm;margin:30mm 0 18mm}
.cover h1{font-size:28pt;line-height:1.15;color:#0f172a;letter-spacing:-.01em}
.cover p{font-size:13pt;color:#475569;margin-top:4mm}
h2{font-size:15pt;color:#0f172a;margin:9mm 0 3mm;padding-bottom:1.5mm;border-bottom:1px solid #e2e8f0;break-after:avoid}
p,ul,ol{margin:0 0 2.5mm}ul,ol{padding-left:6mm}li{margin:1mm 0}li::marker{color:#4f46e5}
a{color:#4f46e5;text-decoration:none}code{background:#eef2ff;padding:0 1mm;border-radius:1mm;font-size:9.5pt}
section>table{border-collapse:collapse;margin:3mm 0;font-size:9.5pt}
section>table th,section>table td{border-bottom:1px solid #e2e8f0;padding:1.2mm 4mm 1.2mm 0;text-align:left}
section>table th{color:#4f46e5;border-bottom:1.5px solid #4f46e5}
table.data{border-collapse:collapse;margin:4mm 0 1mm;font-size:9.5pt;break-inside:avoid}
table.data caption{text-align:left;font-weight:600;margin-bottom:1.5mm;color:#0f172a}
table.data th,table.data td{border-bottom:1px solid #e2e8f0;padding:1.2mm 4mm 1.2mm 0;text-align:right}
table.data th:first-child,table.data td:first-child{text-align:left}
table.data thead th{color:#4f46e5;border-bottom:1.5px solid #4f46e5}
.source{font-size:8.5pt;color:#94a3b8}
"""


def report_html(doc: DocumentSpec, extra_css: str = "") -> str:
    sub = f"<p>{_e(doc.subtitle)}</p>" if doc.subtitle else ""
    sections = "".join(
        f"<section><h2>{_e(s.heading)}</h2>{render_markdown(s.body)}{chart_table(s.chart) if s.chart else ''}</section>"
        for s in doc.sections
    )
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{_e(doc.title)}</title>'
        f"<style>{REPORT_CSS}{extra_css}</style></head><body>"
        f'<div class="cover"><h1>{_e(doc.title)}</h1>{sub}</div>{sections}</body></html>'
    )


def _deny_fetch(url: str, *args, **kwargs):
    raise PermissionError(f"refusing to fetch {url!r} while rendering a document")


def _deny_fetcher():
    from weasyprint import URLFetcher

    class DenyFetcher(URLFetcher):
        def fetch(self, url, headers=None):
            return _deny_fetch(url)

    return DenyFetcher()


def pdf_bytes(doc: DocumentSpec, extra_css: str = "") -> bytes:
    from weasyprint import HTML  # heavy import; only publishing steps need it

    return HTML(string=report_html(doc, extra_css), url_fetcher=_deny_fetcher()).write_pdf()


# --- PowerPoint ----------------------------------------------------------------------------------

_INLINE = re.compile(r"\*\*(.+?)\*\*|\[([^\]]+)\]\([^)]*\)|`([^`]+)`")


def _runs(text: str) -> list[tuple[str, bool]]:
    """Markdown inline text -> (text, bold) runs: **bold** kept as bold, links and code as plain text."""
    out, pos = [], 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            out.append((text[pos : m.start()], False))
        if m.group(1) is not None:
            out.append((m.group(1), True))
        else:
            out.append((m.group(2) or m.group(3), False))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return [(t.replace("*", "").replace("_", " ") if not b else t, b) for t, b in out]


def _lines(body: str) -> list[tuple[str, int]]:
    """Markdown body -> (text, indent level) lines; bullets and numbers become "• " items."""
    lines = []
    for raw in body.splitlines():
        if not raw.strip():
            continue
        indent = (len(raw) - len(raw.lstrip())) // 2
        text = raw.strip()
        if text.startswith("|"):  # a markdown table row: cells joined; the |---| separator dropped
            cells = [c.strip() for c in text.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            lines.append((" · ".join(cells), 0))
            continue
        m = re.match(r"^([-*+]|\d+[.)])\s+(.*)", text)
        if m:
            lines.append(("• " + m.group(2), min(indent, 2)))
        else:
            lines.append((text.lstrip("#").strip(), 0))
    return lines


def _fit_pt(text: str, full: int, fits: int, smallest: int) -> int:
    """Font size that keeps text on about one line: `fits` characters fit at `full` points."""
    return max(smallest, min(full, int(full * fits / max(len(text), 1))))


def pptx_bytes(doc: DocumentSpec) -> bytes:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Emu, Inches, Pt

    ink, muted, accent, dark = RGBColor(0x0F, 0x17, 0x2A), RGBColor(0x47, 0x55, 0x69), RGBColor(0x4F, 0x46, 0xE5), RGBColor(0x0B, 0x10, 0x20)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    def box(slide, x, y, w, h):
        tf = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
        tf.word_wrap = True
        return tf

    def write(tf, text, size, color, bold=False):
        p = tf.paragraphs[0]
        r = p.add_run()
        r.text = text
        r.font.size, r.font.bold, r.font.color.rgb = Pt(size), bold, color
        return p

    # title slide
    slide = prs.slides.add_slide(blank)
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = dark
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.9), Inches(2.6), Inches(0.12), Inches(2.0))
    bar.fill.solid()
    bar.fill.fore_color.rgb = accent
    bar.line.fill.background()
    write(box(slide, 1.25, 2.45, 11, 1.6), doc.title, _fit_pt(doc.title, 48, 30, 28), RGBColor(0xFF, 0xFF, 0xFF), bold=True)
    if doc.subtitle:
        write(box(slide, 1.25, 4.05, 11, 1.2), doc.subtitle, 22, RGBColor(0xC7, 0xD2, 0xFE))

    for section in doc.sections:
        slide = prs.slides.add_slide(blank)
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(0), Emu(0), prs.slide_width, Inches(0.12))
        bar.fill.solid()
        bar.fill.fore_color.rgb = accent
        bar.line.fill.background()
        write(box(slide, 0.8, 0.45, 11.7, 1.0), section.heading, _fit_pt(section.heading, 34, 44, 22), ink, bold=True)
        lines = _lines(section.body)
        text_w = 6.4 if section.chart else 11.7
        size = 22 if sum(len(t) for t, _ in lines) < 450 else 18 if sum(len(t) for t, _ in lines) < 800 else 15
        tf = box(slide, 0.8, 1.65, text_w, 5.3)
        for n, (text, level) in enumerate(lines):
            p = tf.paragraphs[0] if n == 0 else tf.add_paragraph()
            p.level = level
            p.space_after = Pt(8)
            for run_text, bold in _runs(text):
                r = p.add_run()
                r.text = run_text
                r.font.size, r.font.bold, r.font.color.rgb = Pt(size - 2 * level), bold, ink if bold else muted
        if section.chart:
            c = section.chart
            rows, cols = len(c.labels) + 1, len(c.series) + 1
            table = slide.shapes.add_table(
                rows, cols, Inches(7.6), Inches(1.75), Inches(5.0), Inches(min(5.0, 0.4 * rows))
            ).table
            table.cell(0, 0).text = c.unit
            for j, s in enumerate(c.series, start=1):
                table.cell(0, j).text = s.name
            for i, label in enumerate(c.labels, start=1):
                table.cell(i, 0).text = label
                for j, s in enumerate(c.series, start=1):
                    table.cell(i, j).text = _num(s.values[i - 1])
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.text_frame.paragraphs:
                        for r in p.runs:
                            r.font.size = Pt(12)
            src = box(slide, 7.6, 1.75 + min(5.0, 0.4 * rows) + 0.1, 5.0, 0.5)
            write(src, f"{c.title} · Source: {c.source}", 10, muted)
        if section.notes:
            slide.notes_slide.notes_text_frame.text = section.notes

    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


# --- writing files -------------------------------------------------------------------------------


def files_dir() -> Path:
    """Where runs' files live: ORCHESTRATOR_FILES_DIR, default ./artifacts (one folder per run)."""
    return Path(os.environ.get("ORCHESTRATOR_FILES_DIR", "artifacts"))


def render(doc: DocumentSpec, fmt: Deliverable) -> bytes:
    if fmt == "pdf":
        return pdf_bytes(doc)
    if fmt == "slides":
        return slides_html(doc).encode()
    return pptx_bytes(doc)


def write_deliverable(doc: DocumentSpec, fmt: Deliverable, dest_dir: Path, stem: str) -> FileRecord:
    ext, media_type, _ = FORMATS[fmt]
    data = render(doc, fmt)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"{stem}.{ext}"
    (dest_dir / name).write_bytes(data)
    return FileRecord(name=name, format=fmt, media_type=media_type, size=len(data))


def parse_spec(output: object) -> DocumentSpec:
    """A publishing step's output: the spec itself (LLM path) or its JSON (stub runners in tests)."""
    if isinstance(output, DocumentSpec):
        return output
    return DocumentSpec.model_validate(json.loads(str(output)))
