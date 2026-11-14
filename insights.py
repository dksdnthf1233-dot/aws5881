"""점검 기록을 모아 작업 테마별·위반 유형별 통계와 종합 의견을 만들고, 교육용 PDF로 내보낸다."""

from __future__ import annotations

import io
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

import law
from records import THEMES
from report import FONT_BOLD, FONT_REGULAR, _clean

THEME_COLORS = {  # 화면·PDF 공통 색
    "화기작업": "#f97316",
    "고소작업": "#3b82f6",
    "중장비작업": "#eab308",
    "일반 및 기타 작업": "#94a3b8",
}


@dataclass
class TypeStat:
    name: str
    count: int = 0
    themes: Counter = field(default_factory=Counter)
    articles: Counter = field(default_factory=Counter)
    actions: Counter = field(default_factory=Counter)
    supervisor: int = 0

    @property
    def main_theme(self) -> str:
        return self.themes.most_common(1)[0][0] if self.themes else THEMES[-1]


@dataclass
class Stats:
    start: date
    end: date
    inspections: int
    with_violation: int
    findings: int
    supervisor_findings: int
    theme_inspections: dict[str, int]
    theme_violations: dict[str, int]
    types: list[TypeStat]
    monthly: dict[str, dict[str, int]]  # "2026-11" → 테마 → 위반 건수

    def ratio(self, theme: str) -> float:
        return self.theme_violations[theme] / self.findings if self.findings else 0.0


def record_date(record: dict) -> date | None:
    try:
        return datetime.fromisoformat(record["checked_at"]).date()
    except (KeyError, ValueError, TypeError):
        return None


def in_period(records: list[dict], start: date, end: date) -> list[dict]:
    return [r for r in records if (d := record_date(r)) and start <= d <= end]


def compute(records: list[dict], start: date, end: date) -> Stats:
    theme_inspections = {t: 0 for t in THEMES}
    theme_violations = {t: 0 for t in THEMES}
    types: dict[str, TypeStat] = {}
    monthly: dict[str, dict[str, int]] = defaultdict(lambda: {t: 0 for t in THEMES})
    findings = supervisor = with_violation = 0

    for record in records:
        record_findings = record.get("findings", [])
        themes_here = set(record.get("themes") or [THEMES[-1]])
        themes_here |= {f["theme"] for f in record_findings if f.get("theme") in THEMES}
        for theme in themes_here:
            theme_inspections[theme] += 1
        if record_findings:
            with_violation += 1
        month = record["checked_at"][:7]
        for f in record_findings:
            theme = f.get("theme") if f.get("theme") in THEMES else THEMES[-1]
            findings += 1
            theme_violations[theme] += 1
            monthly[month][theme] += 1
            name = " ".join((f.get("violation_type") or f.get("hazard_name") or "기타").split())
            stat = types.setdefault(name, TypeStat(name))
            stat.count += 1
            stat.themes[theme] += 1
            if f.get("source") == "supervisor":
                stat.supervisor += 1
                supervisor += 1
            for article in f.get("articles", []):
                if not article.get("reference"):
                    stat.articles[article["label"]] += 1
            for action in f.get("actions", []):
                stat.actions[action.strip()] += 1

    ordered = sorted(types.values(), key=lambda t: (-t.count, t.name))
    return Stats(start, end, len(records), with_violation, findings, supervisor,
                 theme_inspections, theme_violations, ordered, dict(sorted(monthly.items())))


def _ga(word: str) -> str:
    """받침에 맞는 주격 조사(이/가)를 붙인다."""
    last = word.rstrip(" '\")")[-1:] if word else ""
    has_final = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    return word + ("이" if has_final else "가")


def summary(stats: Stats) -> list[tuple[str, str]]:
    """종합 의견: (제목, 내용) 목록. 기록에서 바로 계산하므로 같은 기록이면 늘 같은 문구가 나온다."""
    period = f"{stats.start:%Y.%m.%d} ~ {stats.end:%Y.%m.%d}"
    if not stats.inspections:
        return [("기간 요약", f"{period} 기간에 저장된 점검 기록이 없습니다.")]
    lines = [(
        "기간 요약",
        f"{period} 기간에 현장 사진 {stats.inspections}건을 점검해 {stats.with_violation}건에서 "
        f"안전수칙 위반 {stats.findings}건을 확인했습니다"
        + (f"(감독자 추가 {stats.supervisor_findings}건 포함)." if stats.supervisor_findings else "."),
    )]
    if not stats.findings:
        lines.append(("종합", "이 기간에는 확인된 위반 사항이 없습니다. 현재의 안전관리 수준을 유지해 주세요."))
        return lines

    ranked = sorted(THEMES, key=lambda t: (-stats.theme_violations[t], THEMES.index(t)))
    top_theme = ranked[0]
    per_inspection = stats.theme_violations[top_theme] / max(stats.theme_inspections[top_theme], 1)
    lines.append((
        "가장 많이 위반한 작업",
        f"{top_theme} — 위반 {stats.theme_violations[top_theme]}건(전체의 {stats.ratio(top_theme):.0%}), "
        f"점검 1건당 평균 {per_inspection:.1f}건",
    ))
    top = stats.types[0]
    lines.append((
        "가장 많이 위반한 사항",
        f"{top.name} — {top.count}건(전체의 {top.count / stats.findings:.0%}), 주로 {top.main_theme}에서 발생",
    ))

    focus = []
    for number, stat in enumerate(stats.types[:3], 1):
        text = f"{number}) {stat.name}({stat.count}건, {stat.main_theme})"
        if stat.articles:
            text += f" — 근거: {law.LAW_NAME} {stat.articles.most_common(1)[0][0]}"
        actions = [a for a, _ in stat.actions.most_common(2) if a]
        if actions:
            text += f" — 교육 포인트: {' / '.join(actions)}"
        focus.append(text)
    lines.append(("중점 개선 사항", "\n".join(focus)))

    closing = (
        f"{top_theme} 작업 전 안전점검회의(TBM)에서 '{top.name}' 사례를 우선 교육하고, "
        f"{top_theme} 현장 순회 점검 시 해당 항목을 중점 확인할 것을 권고합니다."
    )
    if top.main_theme != top_theme:
        closing = (
            f"{top_theme}의 위반 빈도가 가장 높고, 위반 사항으로는 {_ga(f"{top.main_theme}의 '{top.name}'")} 가장 많습니다. "
            f"두 작업의 작업 전 안전점검회의(TBM)에서 해당 사례를 우선 교육할 것을 권고합니다."
        )
    lines.append(("종합 의견", closing))
    return lines


# --------------------------------------------------------------------------
# 교육용 PDF
# --------------------------------------------------------------------------
_BLACK, _GRAY, _LIGHT = (27, 31, 36), (87, 96, 106), (243, 244, 246)
_PAGE_W, _MARGIN = 210, 14
_W = _PAGE_W - 2 * _MARGIN


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


class _Doc(FPDF):
    def footer(self) -> None:
        self.set_y(-10)
        self.set_font("Nanum", "", 7.5)
        self.set_text_color(*_GRAY)
        self.cell(0, 5, f"{self.page_no()} / {{nb}}", align="C")


def _bars(pdf: FPDF, rows: list[tuple[str, int, str, str]], label_w: float = 52) -> None:
    """가로 막대그래프. rows = (이름, 값, 색, 값 옆 문구)"""
    top = max((v for _, v, _, _ in rows), default=0) or 1
    bar_w = _W - label_w - 34
    for name, value, color, note in rows:
        if pdf.get_y() + 8 > pdf.h - 16:
            pdf.add_page()
        y = pdf.get_y()
        pdf.set_font("Nanum", "", 8.6)
        pdf.set_text_color(*_BLACK)
        pdf.set_xy(_MARGIN, y)
        pdf.cell(label_w - 2, 6.5, _clean(name)[:26], align="R")
        pdf.set_fill_color(236, 239, 243)
        pdf.rect(_MARGIN + label_w, y + 1, bar_w, 4.5, style="F")
        if value:
            pdf.set_fill_color(*_rgb(color))
            pdf.rect(_MARGIN + label_w, y + 1, max(bar_w * value / top, 0.8), 4.5, style="F")
        pdf.set_xy(_MARGIN + label_w + bar_w + 2, y)
        pdf.set_font("Nanum", "B", 8.6)
        pdf.cell(32, 6.5, note)
        pdf.set_y(y + 7)
    pdf.set_fill_color(255, 255, 255)  # 막대 색이 뒤에 오는 표 바탕으로 번지지 않게 되돌린다


def _legend(pdf: FPDF) -> None:
    pdf.set_font("Nanum", "", 7.8)
    pdf.set_text_color(*_GRAY)
    x, y = _MARGIN, pdf.get_y() + 1
    pdf.set_xy(x, y)
    pdf.cell(26, 5, "막대 색(주 발생 작업)")
    x += 27
    for theme in THEMES:
        pdf.set_fill_color(*_rgb(THEME_COLORS[theme]))
        pdf.rect(x, y + 1.3, 2.6, 2.6, style="F")
        pdf.set_xy(x + 3.4, y)
        width = pdf.get_string_width(theme) + 6
        pdf.cell(width, 5, theme)
        x += 3.4 + width
    pdf.set_fill_color(255, 255, 255)
    pdf.set_text_color(*_BLACK)
    pdf.set_y(y + 6)


def build_insight_pdf(stats: Stats) -> bytes:
    pdf = _Doc(format="A4", unit="mm")
    pdf.set_margins(_MARGIN, _MARGIN, _MARGIN)
    pdf.set_auto_page_break(True, 14)
    pdf.add_font("Nanum", "", str(FONT_REGULAR))
    pdf.add_font("Nanum", "B", str(FONT_BOLD))
    pdf.set_title("안전수칙 위반 통계 리포트")
    pdf.add_page()
    pdf.set_draw_color(190, 196, 204)
    pdf.set_line_width(0.2)

    def heading(text: str) -> None:
        if pdf.get_y() + 22 > pdf.h - 16:
            pdf.add_page()
        pdf.ln(4)
        pdf.set_font("Nanum", "B", 11)
        pdf.set_text_color(*_BLACK)
        pdf.cell(0, 7, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)

    pdf.set_font("Nanum", "B", 17)
    pdf.cell(0, 11, "안전수칙 위반 통계 리포트", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Nanum", "", 9.5)
    pdf.set_text_color(*_GRAY)
    pdf.cell(0, 6, f"분석 기간 {stats.start:%Y.%m.%d} ~ {stats.end:%Y.%m.%d}  ·  작업자 안전교육 자료",
             align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(*_BLACK)
    pdf.set_line_width(0.6)
    pdf.line(_MARGIN, pdf.get_y() + 1.5, _PAGE_W - _MARGIN, pdf.get_y() + 1.5)
    pdf.set_draw_color(190, 196, 204)
    pdf.set_line_width(0.2)
    pdf.ln(5)

    label = FontFace(emphasis="B", fill_color=_LIGHT, color=_BLACK)
    plain = FontFace(emphasis="", fill_color=(255, 255, 255), color=_BLACK)
    pdf.set_font("Nanum", "", 9)
    pdf.set_text_color(*_BLACK)
    with pdf.table(col_widths=(1, 1, 1, 1), text_align="CENTER", line_height=5.6, padding=2,
                   first_row_as_headings=False) as table:
        row = table.row()
        for title in ("점검 사진", "위반 확인 사진", "위반 사항", "감독자 추가"):
            row.cell(title, style=label)
        row = table.row()
        for value in (stats.inspections, stats.with_violation, stats.findings, stats.supervisor_findings):
            row.cell(f"{value}건", style=FontFace(emphasis="B", fill_color=(255, 255, 255), color=_BLACK))

    heading("1. 작업별 점검·위반 현황")
    pdf.set_font("Nanum", "", 9)
    with pdf.table(col_widths=(46, 30, 30, 30, 36), text_align="CENTER", line_height=5.4, padding=1.8,
                   headings_style=label) as table:
        row = table.row()
        for title in ("작업 테마", "점검 사진", "위반 건수", "위반 비율", "점검 1건당 위반"):
            row.cell(title)
        for theme in THEMES:
            row = table.row(style=plain)
            row.cell(theme)
            row.cell(f"{stats.theme_inspections[theme]}건")
            row.cell(f"{stats.theme_violations[theme]}건")
            row.cell(f"{stats.ratio(theme):.0%}")
            per = stats.theme_violations[theme] / stats.theme_inspections[theme] if stats.theme_inspections[theme] else 0
            row.cell(f"{per:.1f}건")
    pdf.ln(3)
    _bars(pdf, [(t, stats.theme_violations[t], THEME_COLORS[t],
                 f"{stats.theme_violations[t]}건 ({stats.ratio(t):.0%})") for t in THEMES], label_w=40)

    heading("2. 많이 발생한 위반 사항 (상위 10)")
    if stats.types:
        _bars(pdf, [(t.name, t.count, THEME_COLORS[t.main_theme],
                     f"{t.count}건 ({t.count / stats.findings:.0%})") for t in stats.types[:10]])
        _legend(pdf)
    else:
        pdf.set_font("Nanum", "", 9)
        pdf.cell(0, 6, "확인된 위반 사항이 없습니다.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    if len(stats.monthly) > 1:
        heading("3. 월별 위반 건수")
        pdf.set_font("Nanum", "", 8.6)
        pdf.set_text_color(*_BLACK)
        with pdf.table(col_widths=(26, 30, 30, 30, 34, 22), text_align="CENTER", line_height=5.2,
                       padding=1.6, headings_style=label) as table:
            row = table.row()
            for title in ("월", *THEMES, "합계"):
                row.cell(title)
            for month, counts in stats.monthly.items():
                row = table.row(style=plain)
                row.cell(month.replace("-", "."))
                for theme in THEMES:
                    row.cell(str(counts[theme]))
                row.cell(str(sum(counts.values())))

    heading(f"{4 if len(stats.monthly) > 1 else 3}. 종합 의견 및 중점 개선 사항")
    pdf.set_text_color(*_BLACK)
    pdf.set_font("Nanum", "", 9)
    with pdf.table(col_widths=(38, _W - 38), text_align="LEFT", line_height=5.4, padding=2,
                   first_row_as_headings=False) as table:
        for title, body in summary(stats):
            row = table.row()
            row.cell(title, style=label)
            row.cell(_clean(body), style=plain)

    pdf.ln(2)
    pdf.set_font("Nanum", "", 7.5)
    pdf.set_text_color(*_GRAY)
    pdf.multi_cell(0, 4, "※ 현장 안전 판독 앱으로 판독한 사진과 감독자가 추가한 위험요인을 집계했습니다. "
                         f"법령: {law.LAW_NAME} (시행 {law.meta().get('시행일자', '')})",
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())


def when(record: dict) -> str:
    """기록 시각을 '2026-11-14 09:30' 꼴로."""
    try:
        return datetime.fromisoformat(record["checked_at"]).strftime("%Y-%m-%d %H:%M")
    except (KeyError, ValueError, TypeError):
        return record.get("checked_at", "")


def to_csv(records: list[dict]) -> bytes:
    """위반 사항 한 건을 한 줄로 펼친 CSV(엑셀에서 바로 열리도록 BOM 포함)."""
    import csv

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["점검일시", "작업 테마", "위반 유형", "위험요인", "구분", "관련 조문", "조치 사항", "작업 장면"])
    for record in records:
        if not record.get("findings"):
            writer.writerow([when(record), ", ".join(record.get("themes", [])), "", "위반 없음",
                             "", "", "", record.get("scene", "")])
        for f in record.get("findings", []):
            writer.writerow([
                when(record), f.get("theme", ""), f.get("violation_type", ""), f.get("hazard_name", ""),
                "감독자 추가" if f.get("source") == "supervisor" else "AI 판독",
                " / ".join(a["label"] + (" (참고)" if a.get("reference") else "") for a in f.get("articles", [])),
                " / ".join(f.get("actions", [])), record.get("scene", ""),
            ])
    return ("﻿" + out.getvalue()).encode("utf-8")
