"""현장 안전 판독 — 사진 한 장으로 산업안전보건기준에 관한 규칙 위반 여부를 판독하는 모바일 웹 앱.

실행:  streamlit run app.py
"""

from __future__ import annotations

import hashlib
import hmac
import html
import io
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
from analyzer import AnalysisError, Settings, analyze
from demo import DEMO_KINDS, demo_result

try:  # 아이폰 HEIC 사진 지원(설치돼 있을 때만)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

DEFAULT_MODEL = "gpt-5.6-terra"
IMAGE_SIZES = {"표준 (1600px · 권장)": 1600, "고화질 (2048px · 느림)": 2048, "절약 (1024px · 빠름)": 1024}
THUMB_SIDE = 512     # 1단계(작업 사진 여부)용 저해상도
JPEG_QUALITY = 82
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
.block-container {padding: 2.8rem 0.9rem 4rem; max-width: 720px;}
html, body, .stApp {overflow-x: hidden;}
h1 {font-size: 1.45rem !important; padding-bottom: 0.2rem !important;}
.stButton button {min-height: 3.2rem;}
.stButton button p {font-size: 1.08rem; font-weight: 700;}
.stImage img {border-radius: 10px; max-height: 36vh; object-fit: contain; background: #f1f3f5;}
[data-testid="stCode"] pre, [data-testid="stCode"] code {white-space: pre-wrap !important; word-break: break-word !important;}

.sv-wrap, .sv-wrap * {box-sizing: border-box; max-width: 100%; overflow-wrap: anywhere; word-break: keep-all;}
.sv-steps {display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 12px;}
.sv-step {flex: 1 1 30%; min-width: 96px; border-radius: 8px; padding: 6px 8px; font-size: 0.8rem; line-height: 1.3;
          background: #eef1f5; color: #5b6573; border: 1px solid #dfe4ea;}
.sv-step b {display: block; font-size: 0.72rem; font-weight: 700; letter-spacing: .02em;}
.sv-step.done {background: #e8f1fb; color: #134a86; border-color: #c5dbf3;}
.sv-step.stop {background: #fff4dd; color: #7a4b00; border-color: #f1d79b;}
.sv-step.skip {opacity: .55;}

.sv-verdict {border-radius: 12px; padding: 14px 16px; margin-bottom: 12px; border: 1px solid transparent;}
.sv-verdict .t {font-size: 1.15rem; font-weight: 800; line-height: 1.35;}
.sv-verdict .d {font-size: 0.93rem; margin-top: 6px; line-height: 1.5;}
.sv-verdict.na {background: #f1f3f5; border-color: #d5dae0; color: #343a40;}
.sv-verdict.ok {background: #e6f6ec; border-color: #a9dcb9; color: #0f5a2a;}
.sv-verdict.bad {background: #fdebea; border-color: #f2b4af; color: #8f1710;}

.sv-card {border: 1px solid #e1e4e8; border-left: 6px solid #d92d20; border-radius: 12px; background: #fff;
          padding: 14px 14px 12px; margin-bottom: 14px; box-shadow: 0 1px 3px rgba(16,24,40,.06);}
.sv-card .no {font-size: 0.74rem; font-weight: 800; color: #d92d20; letter-spacing: .04em;}
.sv-card .name {font-size: 1.12rem; font-weight: 800; color: #1b1f24; line-height: 1.35; margin: 2px 0 4px;}
.sv-card .ev {font-size: 0.86rem; color: #57606a; line-height: 1.45;}
.sv-sec {margin-top: 12px; padding-top: 10px; border-top: 1px dashed #d8dde3;}
.sv-lab {font-size: 0.78rem; font-weight: 800; color: #57606a; margin-bottom: 5px;}
.sv-cite {font-size: 0.93rem; font-weight: 700; color: #0b4a8f; line-height: 1.4;}
.sv-law {background: #f6f8fa; border-radius: 8px; padding: 9px 11px; margin: 5px 0 8px; font-size: 0.9rem;
         line-height: 1.6; color: #24292f; white-space: pre-line;}
.sv-card details {margin: -2px 0 8px;}
.sv-card summary {font-size: 0.82rem; color: #0b4a8f; cursor: pointer; padding: 4px 0;}
.sv-act {margin: 0; padding-left: 1.25rem;}
.sv-act li {font-size: 1rem; font-weight: 600; color: #1b1f24; line-height: 1.5; margin-bottom: 3px;}
.sv-warn {font-size: 0.86rem; color: #7a4b00; background: #fff4dd; border-radius: 8px; padding: 8px 10px; line-height: 1.5;}
.sv-ref {font-size: 0.86rem; color: #424a53; line-height: 1.5;}
.sv-ref small {color: #8c959f;}
.sv-foot {font-size: 0.76rem; color: #8c959f; line-height: 1.5; margin-top: 18px;}
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


def require_password() -> None:
    """APP_PASSWORD가 설정돼 있으면 비밀번호를 맞춰야 앱을 쓸 수 있다(API 요금 도용 방지)."""
    expected = secret("APP_PASSWORD")
    if not expected or st.session_state.get("authed"):
        return
    st.title("🦺 현장 안전 판독")
    entered = st.text_input("비밀번호", type="password")
    if entered:
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


def steps_html(result: dict) -> str:
    verdict = result["verdict"]
    n = len(result["findings"])
    steps = [
        ("1단계 작업 상황", "작업 사진" if verdict != "not_work" else "비작업 → 종료",
         "done" if verdict != "not_work" else "stop"),
        ("2단계 위반 검증",
         {"not_work": "진행 안 함", "compliant": "위반 없음 → 종료", "violation": f"명백한 위반 {n}건"}[verdict],
         {"not_work": "skip", "compliant": "stop", "violation": "done"}[verdict]),
        ("3단계 법령·대책", "작성 완료" if verdict == "violation" else "진행 안 함",
         "done" if verdict == "violation" else "skip"),
    ]
    chips = "".join(f'<div class="sv-step {cls}"><b>{esc(t)}</b>{esc(d)}</div>' for t, d, cls in steps)
    return f'<div class="sv-steps">{chips}</div>'


def finding_card_html(f: dict) -> str:
    parts = [
        f'<div class="no">위험요인 {f["id"]}</div>',
        f'<div class="name">{esc(f["hazard_name"])}</div>',
        f'<div class="ev">{esc(" · ".join(x for x in (f["target"], f["evidence"]) if x))}</div>',
        '<div class="sv-sec"><div class="sv-lab">📜 관련 법령 조항</div>',
    ]
    if f["refs"]:
        for ref in f["refs"]:
            parts.append(f'<div class="sv-cite">{esc(law.LAW_NAME)} {esc(ref["label"])}</div>')
            parts.append(f'<div class="sv-law">{esc(ref["text"])}</div>')
            if ref["is_partial"]:
                parts.append(
                    f'<details><summary>{esc(ref["article"])} 전체 보기</summary>'
                    f'<div class="sv-law">{esc(ref["full_text"])}</div></details>'
                )
    else:
        parts.append(
            '<div class="sv-warn">원문과 대조해 일치하는 조문을 확정하지 못했습니다. '
            "국가법령정보센터에서 직접 확인해 주세요.</div>"
        )
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
    return f'<div class="sv-card">{"".join(parts)}</div>'


def request_message(result: dict) -> str:
    """현장대리인·현장소장에게 보낼 조치 요청 문구."""
    now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")
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


def render_unverifiable(items: list[str]) -> None:
    if not items:
        return
    with st.expander(f"사진만으로 판단할 수 없는 항목 {len(items[:5])}건 (판정 제외 · 현장 확인)"):
        for item in items[:5]:
            st.markdown(f"- {item}")


def render_result(result: dict) -> None:
    verdict, stage1 = result["verdict"], result["stage1"]
    if result.get("demo"):
        st.warning("🧪 데모 결과입니다. 올린 사진을 판독한 것이 아니라 화면 확인용 고정 예시입니다.")
    render_html(steps_html(result))

    if verdict == "not_work":
        render_html(
            '<div class="sv-verdict na"><div class="t">[해당없음] 작업 진행 사진이 아닙니다</div>'
            f'<div class="d">{esc(stage1.get("scene", ""))}<br>{esc(stage1.get("reason", ""))}</div></div>'
        )
    elif verdict == "compliant":
        render_html(
            '<div class="sv-verdict ok"><div class="t">[위험요소 없음 / 안전수칙 준수]</div>'
            f'<div class="d">{esc(result["stage2"].get("observations", ""))}</div></div>'
        )
        render_unverifiable(result["unverifiable"])
    else:
        findings = result["findings"]
        work = ", ".join(stage1.get("work_types") or [])
        render_html(
            f'<div class="sv-verdict bad"><div class="t">⚠️ 안전수칙 위반 {len(findings)}건</div>'
            f'<div class="d">{esc(work + " · " if work else "")}{esc(result["stage2"].get("observations", ""))}</div></div>'
            + "".join(finding_card_html(f) for f in findings)
        )
        with st.expander("📋 조치 요청 문구 복사"):
            st.code(request_message(result), language=None, wrap_lines=True)
        render_unverifiable(result["unverifiable"])

    timings = result["timings"]
    if not timings:
        return
    detail = " · ".join(f"{k} {v:.1f}초" for k, v in timings.items())
    st.caption(f"판독 시간 {sum(timings.values()):.1f}초 ({detail})")


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

st.title("🦺 현장 안전 판독")
st.caption("작업 사진을 올리면 산업안전보건기준에 관한 규칙 위반 여부·조문·즉시 조치를 알려드립니다.")

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
    st.info("위 버튼을 누르면 휴대폰 카메라로 촬영하거나 갤러리에서 사진을 고를 수 있습니다.")
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
    st.session_state["result"] = {"key": result_key, "data": demo_result(demo_kind)}
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
    st.session_state["result"] = {"key": result_key, "data": result}

saved = st.session_state.get("result")
if saved and saved["key"] == result_key:
    render_result(saved["data"])

render_html(
    '<div class="sv-foot">AI 판독은 감독 업무를 돕는 보조 수단입니다. 사진에 찍히지 않은 상황은 반영되지 않으므로 '
    "최종 판단은 현장에서 직접 확인해 주세요. 조문 본문은 국가법령정보센터 원문"
    f"(시행 {esc(law_meta.get('시행일자', ''))})을 그대로 표시합니다.</div>"
)
