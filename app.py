"""현장 안전 판독 — 사진 한 장으로 산업안전보건기준에 관한 규칙 위반 여부를 판독하는 모바일 웹 앱.

실행:  streamlit run app.py
"""

from __future__ import annotations

import hashlib
import hmac
import html
import io
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
    OpenAI,
    RateLimitError,
)
from PIL import Image, ImageOps, UnidentifiedImageError

import law
from analyzer import AnalysisError, Settings, add_supervisor_findings, analyze, preview_unverifiable
from demo import DEMO_KINDS, demo_result
from report import build_report_pdf

try:  # 아이폰 HEIC 사진 지원(설치돼 있을 때만)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

DEFAULT_MODEL = "gpt-5.6-terra"
IMAGE_SIZES = {"표준 (1600px · 권장)": 1600, "고화질 (2048px · 느림)": 2048, "절약 (1024px · 빠름)": 1024}
THUMB_SIDE = 512     # 1단계(작업 사진 여부)용 저해상도
JPEG_QUALITY = 82
KST = ZoneInfo("Asia/Seoul")
UPLOAD_TYPES = ["jpg", "jpeg", "jfif", "png", "webp", "heic", "heif", "bmp", "gif", "tif", "tiff"]

st.set_page_config(
    page_title="현장 안전 판독",
    page_icon="🦺",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# --------------------------------------------------------------------------
# 모바일 최적화 스타일: 모든 콘텐츠가 화면 폭 안에서 줄바꿈되도록 한다.
# --------------------------------------------------------------------------
st.markdown(
    """
<style>
.block-container {padding: 3.2rem 0.9rem 4rem; max-width: 720px;}
html, body, .stApp {overflow-x: hidden;}
header[data-testid="stHeader"] {background: rgba(243, 245, 249, .94); backdrop-filter: blur(6px);}

/* 머리글: 남색 바탕 + 안전 주황 빗금 띠 */
.sv-hero {position: relative; overflow: hidden; display: flex; align-items: center; gap: 14px;
          padding: 18px 16px 22px; margin-bottom: 14px; border-radius: 18px; color: #fff;
          background: linear-gradient(135deg, #0f1f3d 0%, #1b3a6b 62%, #24508f 100%);
          box-shadow: 0 8px 22px rgba(15, 31, 61, .22);}
.sv-hero::after {content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 6px;
                 background: repeating-linear-gradient(-45deg, #f59e0b 0 12px, #0f1f3d 12px 24px);}
.sv-hero .ic {flex: 0 0 auto; width: 56px; height: 56px; border-radius: 16px; font-size: 32px; line-height: 56px;
              text-align: center; background: rgba(255, 255, 255, .13); border: 1px solid rgba(255, 255, 255, .2);}
.sv-hero .tt {font-size: 1.38rem; font-weight: 800; letter-spacing: -.01em; line-height: 1.25;}
.sv-hero .st {font-size: 0.84rem; color: #c9d6ea; margin-top: 4px; line-height: 1.45;}
.sv-hero .tags {display: flex; flex-wrap: wrap; gap: 6px; margin-top: 9px;}
.sv-hero .tags span {font-size: 0.7rem; font-weight: 700; color: #ffe2b0; padding: 2px 9px; border-radius: 999px;
                     background: rgba(245, 158, 11, .16); border: 1px solid rgba(245, 158, 11, .45);}

/* 사용 안내 3단계 */
.sv-guide {display: flex; gap: 8px; margin: 6px 0 4px;}
.sv-guide .g {flex: 1 1 0; min-width: 0; background: #fff; border: 1px solid #e3e8f0; border-radius: 14px;
              padding: 12px 8px 11px; text-align: center; box-shadow: 0 1px 3px rgba(16, 24, 40, .05);}
.sv-guide .n {display: inline-block; width: 24px; height: 24px; line-height: 24px; border-radius: 50%;
              background: #1b3a6b; color: #fff; font-size: 0.78rem; font-weight: 800;}
.sv-guide .e {font-size: 1.5rem; line-height: 1.5;}
.sv-guide .k {font-size: 0.84rem; font-weight: 800; color: #1b1f24; line-height: 1.3;}
.sv-guide .v {font-size: 0.72rem; color: #6b7686; line-height: 1.35; margin-top: 3px;}
.sv-lock {text-align: center; font-size: 0.8rem; color: #6b7686; margin: 2px 0 10px;}

/* 버튼 */
.stButton button, .stFormSubmitButton button, .stDownloadButton button {min-height: 3.2rem; border-radius: 13px;}
.stButton button p, .stFormSubmitButton button p, .stDownloadButton button p {font-size: 1.06rem; font-weight: 800;}
button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-primaryFormSubmit"] {
  background: linear-gradient(135deg, #ea580c 0%, #c2410c 100%); border: 0;
  box-shadow: 0 6px 16px rgba(194, 65, 12, .30);}
button[data-testid="stBaseButton-primary"]:disabled, button[data-testid="stBaseButton-primaryFormSubmit"]:disabled {
  background: #d7dce4; box-shadow: none;}

/* 사진 올리기 영역 */
[data-testid="stFileUploaderDropzone"] {background: #fff; border: 1.5px dashed #b4c0d3; border-radius: 16px;
                                        padding: 18px 14px; box-shadow: 0 1px 3px rgba(16, 24, 40, .05);}
[data-testid="stFileUploaderDropzone"] button {min-height: 2.9rem; border-radius: 11px; border-color: #1b3a6b;
                                               color: #1b3a6b; font-weight: 800;}
/* 기본 영어 문구(Upload, 30MB per file …)를 한글 안내로 바꾼다 */
[data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p {font-size: 0;}
[data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p::after {
  content: "사진 올리기"; font-size: 1rem; font-weight: 800;}
[data-testid="stFileUploaderDropzoneInstructions"] span {font-size: 0;}
[data-testid="stFileUploaderDropzoneInstructions"] span::after {
  content: "촬영 또는 갤러리 선택"; font-size: 0.8rem; color: #6b7686;}
[data-testid="stButtonGroup"] button {min-height: 2.7rem; font-weight: 700;}

/* 사진·접이식 영역·입력칸 */
.stImage img {border-radius: 14px; max-height: 36vh; object-fit: contain; background: #e9edf3;
              box-shadow: 0 2px 10px rgba(16, 24, 40, .10);}
[data-testid="stExpander"] details {background: #fff; border-radius: 14px; border-color: #e3e8f0;
                                    box-shadow: 0 1px 3px rgba(16, 24, 40, .05);}
[data-testid="stExpander"] summary {font-weight: 700;}
[data-testid="stTextInput"] input {border-radius: 11px;}
[data-testid="stCode"] pre, [data-testid="stCode"] code {white-space: pre-wrap !important; word-break: break-word !important;}

.sv-wrap, .sv-wrap * {box-sizing: border-box; max-width: 100%; overflow-wrap: anywhere; word-break: keep-all;}
.sv-steps {display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 12px;}
.sv-step {flex: 1 1 30%; min-width: 96px; border-radius: 11px; padding: 7px 9px; font-size: 0.8rem; line-height: 1.3;
          background: #fff; color: #5b6573; border: 1px solid #dfe4ea;}
.sv-step b {display: block; font-size: 0.72rem; font-weight: 700; letter-spacing: .02em;}
.sv-step.done {background: #e8f1fb; color: #134a86; border-color: #c5dbf3;}
.sv-step.stop {background: #fff4dd; color: #7a4b00; border-color: #f1d79b;}
.sv-step.skip {opacity: .55;}

.sv-verdict {border-radius: 16px; padding: 15px 16px; margin-bottom: 12px; border: 1px solid transparent;
             box-shadow: 0 2px 8px rgba(16, 24, 40, .06);}
.sv-verdict .t {font-size: 1.15rem; font-weight: 800; line-height: 1.35;}
.sv-verdict .d {font-size: 0.93rem; margin-top: 6px; line-height: 1.5;}
.sv-verdict.na {background: #f1f3f5; border-color: #d5dae0; color: #343a40;}
.sv-verdict.ok {background: #e6f6ec; border-color: #a9dcb9; color: #0f5a2a;}
.sv-verdict.bad {background: #fdebea; border-color: #f2b4af; color: #8f1710;}

.sv-card {border: 1px solid #e3e8f0; border-left: 6px solid #d92d20; border-radius: 16px; background: #fff;
          padding: 15px 14px 13px; margin-bottom: 14px; box-shadow: 0 3px 12px rgba(16, 24, 40, .08);}
.sv-card .no {font-size: 0.74rem; font-weight: 800; color: #d92d20; letter-spacing: .04em;}
.sv-card.sup {border-left-color: #b54708;}
.sv-preview {border-left: 3px solid #b54708; padding: 2px 0 2px 10px; margin: -4px 0 10px;}
.sv-card.sup .no {color: #b54708;}
.sv-card .name {font-size: 1.12rem; font-weight: 800; color: #1b1f24; line-height: 1.35; margin: 2px 0 4px;}
.sv-card .ev {font-size: 0.86rem; color: #57606a; line-height: 1.45;}
.sv-sec {margin-top: 12px; padding-top: 10px; border-top: 1px dashed #d8dde3;}
.sv-lab {font-size: 0.78rem; font-weight: 800; color: #57606a; margin-bottom: 5px;}
.sv-cite {font-size: 0.93rem; font-weight: 700; color: #0b4a8f; line-height: 1.4;}
.sv-law {background: #f6f8fa; border-radius: 8px; padding: 9px 11px; margin: 5px 0 8px; font-size: 0.9rem;
         line-height: 1.6; color: #24292f; white-space: pre-line;}
.sv-card details, .sv-preview details {margin: -2px 0 8px;}
.sv-card summary, .sv-preview summary {font-size: 0.82rem; color: #0b4a8f; cursor: pointer; padding: 4px 0;}
.sv-act {margin: 0; padding-left: 1.25rem;}
.sv-act li {font-size: 1rem; font-weight: 600; color: #1b1f24; line-height: 1.5; margin-bottom: 3px;}
.sv-warn {font-size: 0.86rem; color: #7a4b00; background: #fff4dd; border-radius: 8px; padding: 8px 10px; line-height: 1.5;}
.sv-ref {font-size: 0.86rem; color: #424a53; line-height: 1.5;}
.sv-ref small {color: #8c959f;}
.sv-foot {font-size: 0.74rem; color: #8c959f; line-height: 1.55; margin-top: 22px; padding-top: 12px;
          border-top: 1px solid #e3e8f0; text-align: center;}
</style>
""",
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------
# 설정값
# --------------------------------------------------------------------------
def secret(name: str, default: str = "") -> str:
    """Streamlit secrets → 환경변수 순으로 설정값을 찾는다."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # secrets.toml 자체가 없는 경우
        pass
    return os.environ.get(name, default)


HERO_HTML = (
    '<div class="sv-wrap"><div class="sv-hero"><div class="ic">🦺</div><div>'
    '<div class="tt">현장 안전 판독</div>'
    '<div class="st">작업 사진 한 장으로 안전수칙 위반 여부를 확인합니다</div>'
    '<div class="tags"><span>3단계 판독</span><span>법령 원문 대조</span><span>즉시 조치</span></div>'
    "</div></div></div>"
)

GUIDE_HTML = (
    '<div class="sv-wrap"><div class="sv-guide">'
    '<div class="g"><span class="n">1</span><div class="e">📷</div><div class="k">사진 올리기</div>'
    '<div class="v">촬영하거나 갤러리에서 선택</div></div>'
    '<div class="g"><span class="n">2</span><div class="e">🔍</div><div class="k">안전 판독</div>'
    '<div class="v">작업 여부 → 위반 검증 → 조문 확정</div></div>'
    '<div class="g"><span class="n">3</span><div class="e">📄</div><div class="k">조치 요청</div>'
    '<div class="v">조문·조치 확인 후 PDF 전달</div></div>'
    "</div></div>"
)


def require_password() -> None:
    """APP_PASSWORD가 설정돼 있으면 비밀번호를 맞춰야 앱을 쓸 수 있다(API 요금 도용 방지)."""
    expected = secret("APP_PASSWORD")
    if not expected or st.session_state.get("authed"):
        return
    st.markdown(HERO_HTML, unsafe_allow_html=True)
    st.markdown('<div class="sv-lock">🔒 허가된 사용자만 이용할 수 있습니다</div>', unsafe_allow_html=True)
    with st.form("login", border=False):
        entered = st.text_input("비밀번호", type="password", placeholder="접속 비밀번호를 입력하세요")
        submitted = st.form_submit_button("접속하기", type="primary", width="stretch")
    if submitted:
        if hmac.compare_digest(entered.encode(), expected.encode()):
            st.session_state["authed"] = True
            st.rerun()
        st.error("비밀번호가 맞지 않습니다.")
    st.stop()


# --------------------------------------------------------------------------
# 이미지 압축
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=6)
def prepare_image(data: bytes, max_side: int) -> tuple[bytes, bytes, dict]:
    """고해상도 사진을 전송용 JPEG로 줄인다. (판독용, 1단계용 썸네일, 정보)"""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)  # 휴대폰 세로 사진이 눕지 않도록 회전 보정
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    else:
        img = img.convert("RGB")
    original_size = img.size

    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    main = io.BytesIO()
    img.save(main, "JPEG", quality=JPEG_QUALITY, optimize=True)

    small = img.copy()
    small.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.Resampling.LANCZOS)
    thumb = io.BytesIO()
    small.save(thumb, "JPEG", quality=75, optimize=True)

    info = {
        "original_px": original_size, "sent_px": img.size,
        "original_kb": len(data) / 1024, "sent_kb": main.tell() / 1024,
    }
    return main.getvalue(), thumb.getvalue(), info


# --------------------------------------------------------------------------
# 결과 화면(카드)
# --------------------------------------------------------------------------
def esc(text: str) -> str:
    return html.escape(text or "")


def render_html(markup: str) -> None:
    st.markdown(f'<div class="sv-wrap">{markup}</div>', unsafe_allow_html=True)


def count_by_source(result: dict) -> tuple[int, int]:
    """(AI가 확정한 위반 수, 감독자가 추가한 위반 수)"""
    n_sup = sum(1 for f in result["findings"] if f.get("source") == "supervisor")
    return len(result["findings"]) - n_sup, n_sup


def steps_html(result: dict) -> str:
    verdict = result["verdict"]
    n_ai, n_sup = count_by_source(result)
    if verdict == "not_work":
        step2 = ("진행 안 함", "skip")
    elif n_ai:
        step2 = (f"명백한 위반 {n_ai}건", "done")
    else:
        step2 = ("명백한 위반 없음" if n_sup else "위반 없음 → 종료", "stop")
    if n_ai or n_sup:
        step3 = ("작성 완료" + (f" · 감독자 추가 {n_sup}건" if n_sup else ""), "done")
    else:
        step3 = ("진행 안 함", "skip")
    steps = [
        ("1단계 작업 상황", "작업 사진" if verdict != "not_work" else "비작업 → 종료",
         "done" if verdict != "not_work" else "stop"),
        ("2단계 위반 검증", *step2),
        ("3단계 법령·대책", *step3),
    ]
    chips = "".join(f'<div class="sv-step {cls}"><b>{esc(t)}</b>{esc(d)}</div>' for t, d, cls in steps)
    return f'<div class="sv-steps">{chips}</div>'


def refs_html(refs: list[dict]) -> str:
    """관련 법령 조항(조문 제목 + 원문). 일치하는 조문이 없으면 안내 문구."""
    if not refs:
        return (
            '<div class="sv-warn">원문과 대조해 일치하는 조문을 확정하지 못했습니다. '
            "국가법령정보센터에서 직접 확인해 주세요.</div>"
        )
    parts = []
    for ref in refs:
        parts.append(f'<div class="sv-cite">{esc(law.LAW_NAME)} {esc(ref["label"])}</div>')
        parts.append(f'<div class="sv-law">{esc(ref["text"])}</div>')
        if ref["is_partial"]:
            parts.append(
                f'<details><summary>{esc(ref["article"])} 전체 보기</summary>'
                f'<div class="sv-law">{esc(ref["full_text"])}</div></details>'
            )
    return "".join(parts)


def finding_card_html(f: dict) -> str:
    by_supervisor = f.get("source") == "supervisor"
    parts = [
        f'<div class="no">위험요인 {f["id"]}{" · 감독자 현장 확인으로 추가" if by_supervisor else ""}</div>',
        f'<div class="name">{esc(f["hazard_name"])}</div>',
        f'<div class="ev">{esc(" · ".join(x for x in (f["target"], f["evidence"]) if x))}</div>',
        '<div class="sv-sec"><div class="sv-lab">📜 관련 법령 조항</div>',
        refs_html(f["refs"]),
    ]
    parts.append('</div><div class="sv-sec"><div class="sv-lab">🛠 즉시 조치 사항</div>')
    if f["actions"]:
        parts.append('<ol class="sv-act">' + "".join(f"<li>{esc(a)}</li>" for a in f["actions"]) + "</ol>")
    else:
        parts.append('<div class="sv-warn">조치 사항이 생성되지 않았습니다. 다시 판독해 주세요.</div>')
    parts.append("</div>")
    if f["kosha_guide"]:
        parts.append(
            '<div class="sv-sec"><div class="sv-lab">📘 참고 KOSHA Guide</div>'
            f'<div class="sv-ref">{esc(f["kosha_guide"])}<br>'
            "<small>AI가 기억으로 제시한 지침명입니다. 번호·내용은 KOSHA 누리집에서 확인하세요.</small></div></div>"
        )
    return f'<div class="sv-card{" sup" if by_supervisor else ""}">{"".join(parts)}</div>'


def request_message(result: dict) -> str:
    """현장대리인·현장소장에게 보낼 조치 요청 문구."""
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    lines = [f"[안전조치 요청] {now}", f"안전수칙 위반 {len(result['findings'])}건 확인, 즉시 조치 바랍니다.", ""]
    for f in result["findings"]:
        where = f" ({f['target']})" if f["target"] else ""
        lines.append(f"{f['id']}. {f['hazard_name']}{where}")
        for ref in f["refs"]:
            lines.append(f" - 근거: {law.LAW_NAME} {ref['label']}")
        for i, action in enumerate(f["actions"], 1):
            lines.append(f" - 조치{i}: {action}")
        lines.append("")
    return "\n".join(lines).strip()


@st.cache_data(show_spinner=False, max_entries=8)
def report_pdf(findings_json: str, observations: str, jpeg: bytes, checked_at: str,
               place: str, work: str, inspector: str) -> bytes:
    """같은 내용이면 다시 만들지 않도록 캐시한 요청서 PDF."""
    result = {"findings": json.loads(findings_json), "stage2": {"observations": observations}}
    return build_report_pdf(result, jpeg, datetime.fromisoformat(checked_at), place, work, inspector)


def render_report(result: dict, jpeg: bytes, widget_key: str) -> None:
    """현장대리인·현장소장에게 보내거나 출력할 '개선 요청서' PDF."""
    with st.expander("📄 개선 요청서 PDF (전송·출력용)"):
        place = st.text_input("작업 장소", key="report_place", max_chars=60, placeholder="예: 보일러동 3층 철골 작업구역")
        work = st.text_input(
            "작업 내용 및 공종", key=f"report_work_{widget_key}", max_chars=80,
            value=", ".join(result["stage1"].get("work_types") or []),
        )
        if "report_inspector" not in st.session_state:
            st.session_state["report_inspector"] = secret("REPORT_INSPECTOR")
        inspector = st.text_input("점검자 (선택)", key="report_inspector", max_chars=30)

        checked_at = result.get("checked_at") or datetime.now(KST).isoformat(timespec="minutes")
        try:
            pdf = report_pdf(
                json.dumps(result["findings"], ensure_ascii=False, sort_keys=True),
                (result.get("stage2") or {}).get("observations", ""),
                jpeg, checked_at, place, work, inspector,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"요청서를 만들지 못했습니다: {exc}")
            return
        stamp = datetime.fromisoformat(checked_at).strftime("%Y%m%d_%H%M")
        st.download_button(
            "⬇️ 개선 요청서 PDF 받기", data=pdf, file_name=f"safety_report_{stamp}.pdf",
            mime="application/pdf", type="primary", width="stretch", on_click="ignore",
            key=f"report_dl_{widget_key}",
        )
        st.caption("받은 PDF는 카카오톡·메일로 보내거나 인쇄할 수 있습니다. 감독자가 추가한 위험요인도 함께 들어갑니다.")


def render_unverifiable(result: dict, widget_key: str, can_add: bool, fetch_preview=None) -> list[dict]:
    """사진만으로 판단할 수 없는 항목.

    항목을 체크하면 위반일 경우 적용될 법령 조항을 바로 아래에 보여 주고,
    현장에서 확인해 위반이 맞으면 위험요인으로 추가할 수 있다.
    '추가' 버튼을 누른 경우에만 선택 목록 [{"index", "note"}]을 돌려준다.
    """
    pending = [(i, u) for i, u in enumerate(result["unverifiable"]) if not u.get("added")]
    if not pending:
        return []
    selections = []
    with st.expander(f"사진만으로 판단할 수 없는 항목 {len(pending)}건 (판정 제외 · 현장 확인)", expanded=True):
        st.caption("항목을 체크하면 관련 법령 조항이 아래에 나옵니다. 현장에서 확인해 위반이 맞으면 위험요인으로 추가하세요.")
        for i, item in pending:
            if not st.checkbox(item["text"], key=f"unv_{widget_key}_{i}"):
                item.pop("preview_error", None)  # 다시 체크하면 조문 조회를 재시도한다
                continue
            if "preview" not in item and "preview_error" not in item and fetch_preview:
                with st.spinner("관련 법령 조항을 찾는 중…"):
                    fetch_preview(i)
            if "preview" in item:
                title = "📜 관련 법령 조항" + (f" · 위반일 경우: {item['hazard_name']}" if item["hazard_name"] else "")
                render_html(
                    f'<div class="sv-preview"><div class="sv-lab">{esc(title)}</div>'
                    f'{refs_html(item["preview"]["refs"])}</div>'
                )
            elif item.get("preview_error"):
                st.error(f"조문을 불러오지 못했습니다. 체크를 풀었다가 다시 해 주세요. ({item['preview_error']})")
            else:
                st.caption("데모 모드에서는 조문을 불러올 수 없습니다. OpenAI API 키가 필요합니다.")
            note = st.text_input(
                "현장 확인 내용 (선택)", key=f"unv_note_{widget_key}_{i}", max_chars=200,
                placeholder="예: 높이 약 6m, 작업자 3명 모두 안전대 미체결",
            )
            selections.append({"index": i, "note": note})
        clicked = st.button(
            f"➕ 선택한 {len(selections)}건을 위험요인으로 추가" if selections else "➕ 위험요인으로 추가",
            key=f"unv_add_{widget_key}", width="stretch", disabled=not (selections and can_add),
        )
    return selections if clicked else []


def render_result(result: dict, jpeg: bytes, widget_key: str, can_add: bool, fetch_preview=None) -> list[dict]:
    """결과를 그리고, 감독자가 위험요인으로 추가하겠다고 고른 항목 목록을 돌려준다."""
    verdict, stage1, findings = result["verdict"], result["stage1"], result["findings"]
    if result.get("demo"):
        st.warning("🧪 데모 결과입니다. 올린 사진을 판독한 것이 아니라 화면 확인용 고정 예시입니다.")
    render_html(steps_html(result))
    selections: list[dict] = []

    if verdict == "not_work":
        render_html(
            '<div class="sv-verdict na"><div class="t">[해당없음] 작업 진행 사진이 아닙니다</div>'
            f'<div class="d">{esc(stage1.get("scene", ""))}<br>{esc(stage1.get("reason", ""))}</div></div>'
        )
    elif not findings:
        render_html(
            '<div class="sv-verdict ok"><div class="t">[위험요소 없음 / 안전수칙 준수]</div>'
            f'<div class="d">{esc(result["stage2"].get("observations", ""))}</div></div>'
        )
        selections = render_unverifiable(result, widget_key, can_add, fetch_preview)
    else:
        n_ai, n_sup = count_by_source(result)
        if n_sup and n_ai:
            suffix = f" (감독자 추가 {n_sup}건 포함)"
        elif n_sup:
            suffix = " (감독자 현장 확인)"
        else:
            suffix = ""
        work = ", ".join(stage1.get("work_types") or [])
        render_html(
            f'<div class="sv-verdict bad"><div class="t">⚠️ 안전수칙 위반 {len(findings)}건{suffix}</div>'
            f'<div class="d">{esc(work + " · " if work else "")}{esc(result["stage2"].get("observations", ""))}</div></div>'
            + "".join(finding_card_html(f) for f in findings)
        )
        with st.expander("📋 조치 요청 문구 복사"):
            st.code(request_message(result), language=None, wrap_lines=True)
        render_report(result, jpeg, widget_key)
        selections = render_unverifiable(result, widget_key, can_add, fetch_preview)

    timings = result["timings"]
    if timings:
        detail = " · ".join(f"{k} {v:.1f}초" for k, v in timings.items())
        st.caption(f"판독 시간 {sum(timings.values()):.1f}초 ({detail})")
    return selections


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, AnalysisError):
        return str(exc)
    if isinstance(exc, AuthenticationError):
        return "OpenAI API 키가 올바르지 않습니다. 키를 다시 확인해 주세요."
    if isinstance(exc, RateLimitError):
        return "OpenAI 사용 한도를 초과했거나 크레딧이 부족합니다. 결제·한도 설정을 확인해 주세요."
    if isinstance(exc, NotFoundError):
        return "모델명을 찾을 수 없습니다. 왼쪽 위 메뉴(>)의 설정에서 모델명을 확인해 주세요."
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return "OpenAI 서버에 연결하지 못했습니다. 통신 상태를 확인한 뒤 다시 시도해 주세요."
    if isinstance(exc, APIStatusError):
        return f"OpenAI 요청이 거부되었습니다({exc.status_code}): {exc.message}"
    return f"예상하지 못한 오류가 발생했습니다: {exc}"


# --------------------------------------------------------------------------
# 화면
# --------------------------------------------------------------------------
require_password()

law_meta = law.meta()
api_key = secret("OPENAI_API_KEY")

with st.sidebar:
    st.subheader("설정")
    model = st.text_input("OpenAI 모델명", value=secret("OPENAI_MODEL", DEFAULT_MODEL)).strip()
    size_label = st.selectbox("전송 이미지 크기", list(IMAGE_SIZES))
    if not api_key:
        api_key = st.text_input("OpenAI API 키", type="password", help="이 접속에서만 쓰이고 저장되지 않습니다.")
    st.divider()
    st.caption(
        f"조문 DB: {law.LAW_NAME}\n\n시행 {law_meta.get('시행일자', '?')} · "
        f"고용노동부령 제{law_meta.get('공포번호', '?').lstrip('0')}호"
    )
    st.link_button("국가법령정보센터에서 원문 보기", law.LAW_URL)

st.markdown(HERO_HTML, unsafe_allow_html=True)

demo_kind = ""
if not api_key:
    st.warning("OpenAI API 키가 설정되지 않았습니다. 아래에 입력하거나 secrets에 `OPENAI_API_KEY`를 등록해 주세요.")
    api_key = st.text_input("OpenAI API 키", type="password", key="api_key_main")
    if not api_key and st.toggle("🧪 데모 모드 — 키 없이 화면만 체험 (실제 판독 아님)"):
        demo_kind = DEMO_KINDS[st.selectbox("보여 줄 예시 결과", list(DEMO_KINDS))]

source = st.segmented_control(
    "사진 입력 방법", ["🖼️ 사진 선택", "📷 바로 촬영"], default="🖼️ 사진 선택", label_visibility="collapsed"
)
if source == "📷 바로 촬영":
    upload = st.camera_input("현장 사진 촬영", label_visibility="collapsed")
else:
    upload = st.file_uploader("현장 사진", type=UPLOAD_TYPES, label_visibility="collapsed")

if upload is None:
    st.markdown(GUIDE_HTML, unsafe_allow_html=True)
    st.stop()

try:
    jpeg, thumb, info = prepare_image(upload.getvalue(), IMAGE_SIZES[size_label])
except (UnidentifiedImageError, OSError):
    st.error("이미지를 읽을 수 없습니다. JPG·PNG 형식의 사진으로 다시 시도해 주세요.")
    st.stop()

st.image(jpeg, width="stretch")
st.caption(
    f"전송 크기 {info['sent_px'][0]}×{info['sent_px'][1]} · {info['sent_kb']:.0f}KB "
    f"(원본 {info['original_px'][0]}×{info['original_px'][1]} · {info['original_kb']:.0f}KB)"
)

note = st.text_input(
    "상황 메모 (선택)", placeholder="예: 지상 5m 비계 위 배관 용접", max_chars=200,
    help="사진만으로 알기 어려운 높이·작업 종류 등을 적으면 판독이 정확해집니다.",
)

result_key = hashlib.sha256(jpeg + note.encode() + model.encode() + demo_kind.encode()).hexdigest()
clicked = st.button("🔍 안전 판독 시작", type="primary", width="stretch", disabled=not ((api_key and model) or demo_kind))

if clicked and demo_kind:
    demo = demo_result(demo_kind)
    demo["checked_at"] = datetime.now(KST).isoformat(timespec="minutes")
    st.session_state["result"] = {"key": result_key, "data": demo}
elif clicked:
    client = OpenAI(api_key=api_key, base_url=secret("OPENAI_BASE_URL") or None, timeout=120, max_retries=1)
    cfg = Settings(model=model, fast_model=secret("OPENAI_MODEL_FAST"))
    with st.status("판독 준비 중…", expanded=False) as status:
        try:
            result = analyze(
                client, cfg, jpeg, thumb, note,
                on_stage=lambda stage, message: status.update(label=message),
            )
        except Exception as exc:  # noqa: BLE001 - 현장에서 원인을 바로 알 수 있게 모두 안내한다.
            status.update(label="판독 실패", state="error")
            st.session_state.pop("result", None)
            st.error(friendly_error(exc))
            st.stop()
        status.update(label="판독 완료", state="complete")
    result["checked_at"] = datetime.now(KST).isoformat(timespec="minutes")
    st.session_state["result"] = {"key": result_key, "data": result}

def make_client() -> tuple[OpenAI, Settings]:
    client = OpenAI(api_key=api_key, base_url=secret("OPENAI_BASE_URL") or None, timeout=120, max_retries=1)
    return client, Settings(model=model, fast_model=secret("OPENAI_MODEL_FAST"))


saved = st.session_state.get("result")
if saved and saved["key"] == result_key:
    data = saved["data"]
    can_add = bool(api_key and model) and not data.get("demo")

    def fetch_preview(index: int) -> None:
        """체크한 '판단 불가' 항목의 관련 조문을 찾아 둔다. 실패하면 사유를 항목에 남긴다."""
        try:
            preview_unverifiable(*make_client(), data, index)
        except Exception as exc:  # noqa: BLE001
            data["unverifiable"][index]["preview_error"] = friendly_error(exc)

    selections = render_result(data, jpeg, result_key[:10], can_add, fetch_preview if can_add else None)
    if selections:
        with st.status("선택한 항목을 위험요인으로 추가하는 중…", expanded=False) as status:
            try:
                add_supervisor_findings(*make_client(), data, selections)
            except Exception as exc:  # noqa: BLE001
                status.update(label="추가 실패", state="error")
                st.error(friendly_error(exc))
                st.stop()
        st.rerun()  # 추가된 위험요인 카드를 포함해 결과를 다시 그린다.

render_html(
    '<div class="sv-foot">AI 판독은 감독 업무를 돕는 보조 수단입니다. 사진에 찍히지 않은 상황은 반영되지 않으므로 '
    "최종 판단은 현장에서 직접 확인해 주세요. 조문 본문은 국가법령정보센터 원문"
    f"(시행 {esc(law_meta.get('시행일자', ''))})을 그대로 표시합니다.</div>"
)
