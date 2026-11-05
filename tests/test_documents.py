"""Document deliverables: spec validation, the chart guard, and the three renderers. No LLM calls."""

from __future__ import annotations

import io

import pytest
from pydantic import ValidationError

from orchestrator.documents import (
    ChartSpec,
    DocumentSpec,
    Section,
    chart_svg,
    guard_charts,
    numbers_in,
    pdf_bytes,
    pptx_bytes,
    render_markdown,
    report_html,
    slides_html,
    write_deliverable,
)

EVIL = '<script>alert(1)</script> <img src=x onerror="alert(2)"> [x](javascript:alert(3))'


def chart(**overrides) -> ChartSpec:
    data = {
        "kind": "bar",
        "title": "Revenue by year",
        "labels": ["2023", "2024", "2025"],
        "series": [{"name": "Revenue", "values": [12.5, 14.0, 17.25]}],
        "unit": "USD bn",
        "source": "research step",
    }
    return ChartSpec.model_validate({**data, **overrides})


def doc(with_chart: bool = True) -> DocumentSpec:
    return DocumentSpec(
        title="Market <Review>",
        subtitle="Q3 2026",
        sections=[
            Section(heading="Summary", body="Growth is **strong**.\n\n- point one\n- point two", notes="say hi"),
            Section(heading="Numbers", body="See the chart.", chart=chart() if with_chart else None),
            Section(heading="Risks", body=EVIL),
        ],
    )


def test_chart_series_must_match_labels():
    with pytest.raises(ValidationError, match="labels"):
        chart(series=[{"name": "r", "values": [1, 2]}])


def test_pie_chart_takes_one_series_of_non_negative_values():
    with pytest.raises(ValidationError):
        chart(kind="pie", series=[{"name": "a", "values": [1, 2, 3]}, {"name": "b", "values": [1, 2, 3]}])
    with pytest.raises(ValidationError):
        chart(kind="pie", series=[{"name": "a", "values": [1, -2, 3]}])


def test_numbers_in_normalises_commas_percent_and_currency():
    assert {1234.5, 12.0, 3.0, 2025.0} <= numbers_in("Sales were $1,234.5m, up 12% over 3 years (2025)")
    assert -4.5 in numbers_in("a change of -4.5 points")


def test_guard_keeps_charts_whose_values_appear_in_the_inputs():
    kept, dropped = guard_charts(doc(), "Revenue: 12.5 in 2023, 14 in 2024 and $17.25bn in 2025")
    assert kept.sections[1].chart is not None and dropped == []


def test_guard_tolerates_rounding_but_drops_invented_values():
    _, dropped = guard_charts(doc(), "Revenue: 12.53, 14.02, 17.25")  # model rounded 12.53 -> 12.5
    assert dropped == []
    kept, dropped = guard_charts(doc(), "Revenue: 12.5 and 14.0 only")
    assert kept.sections[1].chart is None
    assert dropped == ["Revenue by year"]
    assert kept.sections[1].body == "See the chart."  # the slide stays


def test_markdown_is_escaped_and_safe():
    out = render_markdown(EVIL + "\n\n**bold** [ok](https://example.com) ![pic](https://example.com/a.png)")
    assert "<script" not in out and "<img" not in out and "href=\"javascript" not in out
    assert "&lt;script&gt;" in out
    assert "<strong>bold</strong>" in out and 'href="https://example.com"' in out


def test_chart_svg_escapes_text_and_draws_each_kind():
    for kind in ("bar", "line", "pie"):
        svg = chart_svg(chart(kind=kind, title="A <b> & c"))
        assert svg.startswith("<svg") and "A &lt;b&gt; &amp; c" in svg and "<b>" not in svg
    assert svg.count("<path") == 3  # one pie slice per label


def test_slides_html_has_a_title_slide_plus_one_per_section_and_is_self_contained():
    out = slides_html(doc())
    assert out.count('class="slide') == 4
    assert "<svg" in out  # the chart
    assert "Market &lt;Review&gt;" in out and "<script>alert" not in out
    assert "say hi" in out  # speaker notes, hidden until toggled
    assert "http://" not in out.replace("http://www.w3.org/2000/svg", "")  # no CDN or remote assets


def test_report_html_turns_charts_into_tables():
    out = report_html(doc())
    assert "<svg" not in out and "<table" in out and "17.25" in out


def test_pdf_renders():
    data = pdf_bytes(doc())
    assert data.startswith(b"%PDF") and len(data) > 2000


def test_pdf_never_fetches_urls():
    from orchestrator import documents

    calls = []
    real = documents._deny_fetch
    documents._deny_fetch = lambda url, *a, **k: (calls.append(url), real(url))[1]
    try:
        spec = DocumentSpec(title="t", sections=[Section(heading="h", body="[l](https://example.com)")])
        pdf_bytes(spec, extra_css="@import url('https://example.com/x.css'); body { background: url('file:///etc/passwd') }")
    finally:
        documents._deny_fetch = real
    assert calls  # every fetch went through the guard, which refuses it


def test_pptx_has_a_title_slide_one_per_section_notes_and_a_table_for_the_chart():
    from pptx import Presentation

    prs = Presentation(io.BytesIO(pptx_bytes(doc())))
    slides = list(prs.slides)
    assert len(slides) == 4
    texts = ["\n".join(s.text_frame.text for s in slide.shapes if s.has_text_frame) for slide in slides]
    assert "Market <Review>" in texts[0] and "point one" in texts[1] and "**" not in texts[1]
    assert slides[1].notes_slide.notes_text_frame.text == "say hi"
    tables = [s for s in slides[2].shapes if s.has_table]
    assert tables and tables[0].table.cell(1, 0).text == "2023"


def test_write_deliverable_saves_each_format(tmp_path):
    for fmt, ext in (("pdf", "pdf"), ("slides", "html"), ("pptx", "pptx")):
        record = write_deliverable(doc(), fmt, tmp_path, "final")
        assert record.name == f"final.{ext}" and record.format == fmt
        assert (tmp_path / record.name).stat().st_size == record.size > 0


def test_guard_accepts_values_in_thousands_millions_or_billions_but_not_digit_groups():
    pop = chart(labels=["India", "China"], series=[{"name": "people", "values": [1463.9, 1416.1]}], unit="m")
    spec = DocumentSpec(title="t", sections=[Section(heading="h", chart=pop)])
    source = "India 1,463,865,525 and China 1,416,096,094"
    assert guard_charts(spec, source)[1] == []  # shown in millions
    billions = chart(labels=["India", "China"], series=[{"name": "people", "values": [1.46, 1.42]}])
    assert guard_charts(spec.model_copy(update={"sections": [Section(heading="h", chart=billions)]}), source)[1] == []
    invented = chart(labels=["India", "China"], series=[{"name": "people", "values": [865, 96]}])  # comma groups only
    assert guard_charts(spec.model_copy(update={"sections": [Section(heading="h", chart=invented)]}), source)[1]


def test_markdown_tables_render_in_slides_and_report_and_flatten_in_pptx():
    from pptx import Presentation

    table = "| Country | People |\n|---|---|\n| India | 1,463 |\n| China | 1,416 |"
    spec = DocumentSpec(title="t", sections=[Section(heading="h", body=table)])
    for out in (slides_html(spec), report_html(spec)):
        assert "<table>" in out and "<td>India</td>" in out and "|---|" not in out
    prs = Presentation(io.BytesIO(pptx_bytes(spec)))
    text = "\n".join(s.text_frame.text for s in list(prs.slides)[1].shapes if s.has_text_frame)
    assert "India · 1,463" in text and "|" not in text and "---" not in text


def test_long_chart_titles_shrink_to_fit():
    svg = chart_svg(chart(title="Five Most Populous Countries (mid-2025, UN World Population Prospects 2024)"))
    size = float(svg.split('class="c-title"')[0].rsplit("font-size:", 1)[1].split("px")[0])
    assert size < 21


def test_pptx_shrinks_long_titles_and_headings_to_one_line():
    from pptx import Presentation

    spec = DocumentSpec(
        title="Key Milestones in the History of Electric Cars",
        sections=[
            Section(heading="Early Pioneers (1828–1881): The First Electric Vehicles", body="x"),
            Section(heading="Short", body="x"),
        ],
    )
    prs = Presentation(io.BytesIO(pptx_bytes(spec)))

    def size_of(slide, text):
        shape = next(s for s in slide.shapes if s.has_text_frame and s.text_frame.text == text)
        return shape.text_frame.paragraphs[0].runs[0].font.size.pt

    slides = list(prs.slides)
    assert size_of(slides[0], spec.title) <= 40
    assert size_of(slides[1], spec.sections[0].heading) < 34 and size_of(slides[2], "Short") == 34


def test_unit_is_not_repeated_when_the_title_already_names_it():
    svg = chart_svg(chart(title="Quarterly Revenue (thousands USD)", unit="thousands USD"))
    visible_title = svg.split('class="c-title"')[1].split("</text>")[0]
    assert visible_title.count("thousands USD") == 1
    assert "Revenue by year (USD bn)" in chart_svg(chart())
    assert report_html(DocumentSpec(title="t", sections=[Section(heading="h", chart=chart(title="Sales in USD bn"))])).count("USD bn") == 1
