"""'현장 안전점검 결과 및 개선 요청서' PDF 생성.

판독 결과(위험요인·조문·조치 사항)와 현장 사진을 A4 한 장짜리 서식으로 만들어
현장대리인·현장소장에게 파일로 보내거나 출력해 전달할 수 있게 한다.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace
from PIL import Image

import law

FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT_REGULAR = FONT_DIR / "NanumGothic-Regular.ttf"
FONT_BOLD = FONT_DIR / "NanumGothic-Bold.ttf"

PAGE_W, MARGIN = 210, 12
CONTENT_W = PAGE_W - 2 * MARGIN
MAX_LAW_CHARS = 480        # 표가 한 쪽을 넘지 않도록 조문 인용 길이를 제한
WEEKDAYS = "월화수목금토일"

BLACK, GRAY, LIGHT = (27, 31, 36), (87, 96, 106), (243, 244, 246)
RED = (200, 30, 20)

_HANJA_NOTE_RE = re.compile(r"\([^()가-힣]*[㐀-鿿豈-﫿][^()가-힣]*\)")


@lru_cache(maxsize=1)
def _glyphs() -> frozenset[int]:
    return frozenset(TTFont(str(FONT_REGULAR)).getBestCmap())


def _clean(text: str, markdown: bool = False) -> str:
    """글꼴에 없는 글자(한자 병기, 이모지 등)를 걸러 PDF에 빈 네모가 찍히지 않게 한다."""
    text = _HANJA_NOTE_RE.sub("", text or "")
    glyphs = _glyphs()
    text = "".join(c for c in text if c in "\n\t" or ord(c) in glyphs).replace("\t", " ")
    if markdown:  # 표 안에서 굵은 글씨 표시(**)로 오해될 수 있는 기호를 무력화
        for mark, safe in (("**", "＊＊"), ("__", "＿＿"), ("--", "－－"), ("~~", "～～")):
            text = text.replace(mark, safe)
    return text.strip()


def format_datetime(dt: datetime) -> str:
    return f"{dt.year}년 {dt.month}월 {dt.day}일 ({WEEKDAYS[dt.weekday()]}) {dt:%H:%M}"


def _law_cell(finding: dict) -> str:
    blocks = []
    for ref in finding.get("refs", []):
        text = _clean(ref["text"], markdown=True)
        if len(text) > MAX_LAW_CHARS:
            text = text[:MAX_LAW_CHARS].rstrip() + " … (이하 생략 · 원문 참조)"
        blocks.append(f"**{_clean(law.LAW_NAME + ' ' + ref['label'], markdown=True)}**\n{text}")
    return "\n\n".join(blocks) or "해당 조문 미확정 (국가법령정보센터 확인 필요)"


class _Report(FPDF):
    def footer(self) -> None:
        self.set_y(-10)
        self.set_font("Nanum", "", 7.5)
        self.set_text_color(*GRAY)
        self.cell(0, 5, f"{self.page_no()} / {{nb}}", align="C")


def build_report_pdf(result: dict, jpeg: bytes, checked_at: datetime,
                     place: str = "", work: str = "", inspector: str = "") -> bytes:
    findings = result["findings"]
    date_text = format_datetime(checked_at)

    pdf = _Report(format="A4", unit="mm")
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_auto_page_break(True, 14)
    pdf.add_font("Nanum", "", str(FONT_REGULAR))
    pdf.add_font("Nanum", "B", str(FONT_BOLD))
    pdf.set_title("현장 안전점검 결과 및 개선 요청서")
    pdf.add_page()
    pdf.set_draw_color(190, 196, 204)
    pdf.set_line_width(0.2)

    label = FontFace(emphasis="B", fill_color=LIGHT, color=BLACK)
    plain = FontFace(emphasis="", color=BLACK)

    def heading(text: str, color=BLACK, doc: FPDF | None = None) -> None:
        doc = doc or pdf
        doc.ln(4)
        doc.set_font("Nanum", "B", 10.5)
        doc.set_text_color(*color)
        doc.cell(0, 7, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        doc.set_text_color(*BLACK)

    # ---- 제목 ----
    pdf.set_font("Nanum", "B", 17)
    pdf.set_text_color(*BLACK)
    pdf.cell(0, 11, "현장 안전점검 결과 및 개선 요청서", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(*BLACK)
    pdf.set_line_width(0.6)
    pdf.line(MARGIN, pdf.get_y() + 1, PAGE_W - MARGIN, pdf.get_y() + 1)
    pdf.set_draw_color(190, 196, 204)
    pdf.set_line_width(0.2)
    pdf.ln(5)

    # ---- 기본 정보 ----
    pdf.set_font("Nanum", "", 9)
    with pdf.table(col_widths=(28, 65, 28, 65), line_height=5.2, first_row_as_headings=False,
                   text_align="LEFT", padding=1.6) as table:
        row = table.row()
        row.cell("점검 일시", style=label)
        row.cell(date_text, style=plain)
        row.cell("점검자", style=label)
        row.cell(_clean(inspector) or "-", style=plain)
        row = table.row()
        row.cell("작업 장소", style=label)
        row.cell(_clean(place) or "-", style=plain, colspan=3)
        row = table.row()
        row.cell("작업 내용 및 공종", style=label)
        row.cell(_clean(work) or "-", style=plain, colspan=3)

    # ---- 현장 사진 ----
    heading("[첨부 1] 점검 대상 현장 사진")
    with Image.open(io.BytesIO(jpeg)) as img:
        ratio = img.width / img.height
    height = min(58, 110 / ratio)
    width = height * ratio
    if pdf.get_y() + height > pdf.h - 16:
        pdf.add_page()
    y = pdf.get_y() + 1
    pdf.image(io.BytesIO(jpeg), x=(PAGE_W - width) / 2, y=y, w=width, h=height)
    pdf.set_y(y + height + 1)

    # ---- 총평 ----
    heading("현장 총평 및 종합 소견")
    summary = (
        f"「{law.LAW_NAME}」에 따라 현장 작업 사진을 점검한 결과 안전수칙 위반 **{len(findings)}건**"
        "이 확인되어 아래와 같이 즉시 개선을 요청합니다."
    )
    observations = _clean((result.get("stage2") or {}).get("observations", ""), markdown=True)
    demands = [
        "아래 항목별 조치 사항을 즉시 이행하고 그 결과를 점검자에게 통보할 것",
        "작업 전 안전점검회의(TBM)에서 본 지적 사항을 모든 작업자에게 공유할 것",
    ]
    body = summary
    if observations:
        body += f"\n**점검 소견**  {observations}"
    body += "\n**현장 긴급 이행 요구사항**\n" + "\n".join(f" - {d}" for d in demands)
    pdf.set_font("Nanum", "", 9)
    with pdf.table(col_widths=(CONTENT_W,), line_height=5.4, first_row_as_headings=False,
                   text_align="LEFT", padding=2.4, markdown=True) as table:
        table.row().cell(body, style=FontFace(fill_color=(250, 250, 251), color=BLACK))

    # ---- 위험요인 세부 내역 ----
    head = FontFace(emphasis="B", fill_color=LIGHT, color=BLACK)

    def draw_details(doc: FPDF, items: list[dict]) -> None:
        heading(f"식별된 위험 요인 및 법령·대책 세부 내역 ({len(findings)}건)", RED, doc)
        doc.set_font("Nanum", "", 8.2)
        with doc.table(col_widths=(8, 38, 94, 46), line_height=4.3, text_align="LEFT",
                       padding=1.5, markdown=True, headings_style=head) as table:
            row = table.row()
            for title in ("No.", "위반 항목", "관련 법령", "조치 요지"):
                row.cell(title, align="C")
            for no, f in enumerate(items, 1):
                draw_row(table, no, f)

    def draw_row(table, no: int, f: dict) -> None:
        item = f"**{_clean(f['hazard_name'], markdown=True)}**"
        if f.get("target"):
            item += f"\n위치: {_clean(f['target'], markdown=True)}"
        if f.get("source") == "supervisor":
            item += "\n(감독자 현장 확인)"
        actions = "\n".join(
            f"{i}. {_clean(a, markdown=True)}" for i, a in enumerate(f.get("actions", []), 1)
        ) or "-"
        row = table.row()
        row.cell(str(no), align="C")
        row.cell(item)
        row.cell(_law_cell(f))
        row.cell(actions)

    # 제목과 첫 항목이 현재 쪽에 함께 들어가지 않으면 새 쪽에서 시작한다(제목만 홀로 남는 것 방지).
    with pdf.offset_rendering() as dummy:
        draw_details(dummy, findings[:1])
    if dummy.page_break_triggered:
        pdf.add_page()
    draw_details(pdf, findings)

    # ---- 출처 ----
    pdf.ln(2)
    pdf.set_font("Nanum", "", 7.5)
    pdf.set_text_color(*GRAY)
    pdf.multi_cell(
        0, 4,
        f"※ 법령 출처: {law.LAW_NAME} (시행 {law.meta().get('시행일자', '')}) · 국가법령정보센터 www.law.go.kr",
        new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )
    pdf.set_text_color(*BLACK)

    # ---- 확인 서명 ----
    if pdf.get_y() + 30 > pdf.h - 16:
        pdf.add_page()
    pdf.ln(3)
    pdf.set_font("Nanum", "", 9)
    confirm = (
        "**위 현장 안전점검 결과를 확인하고, 지적된 개선 조치 사항을 즉시 이행할 것을 확인합니다.**\n\n"
        f"점검확인일자: {checked_at.year}년 {checked_at.month}월 {checked_at.day}일"
        "                현장소장(현장대리인):                              (서명 또는 인)"
    )
    with pdf.table(col_widths=(CONTENT_W,), line_height=5.6, first_row_as_headings=False,
                   text_align="CENTER", padding=3, markdown=True) as table:
        table.row().cell(confirm)

    return bytes(pdf.output())
