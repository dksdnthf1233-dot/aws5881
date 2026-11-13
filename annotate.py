"""현장 사진 위에 위험요인 위치(박스·번호 핀)를 그린다.

화면에 보이는 사진과 PDF 개선 요청서에 들어가는 사진이 같은 그림이 되도록
표시는 모두 이 모듈에서 이미지에 직접 그려 넣는다.

좌표는 모두 사진 크기에 대한 비율(0~1)이다.
  box = [x1, y1, x2, y2]   AI가 2단계에서 감지한 영역
  pin = [x, y]             감독자가 사진을 눌러 직접 찍은 위치(있으면 box보다 우선)
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_BOLD = Path(__file__).resolve().parent / "fonts" / "NanumGothic-Bold.ttf"

AI_COLOR = (217, 45, 32)          # AI가 사진에서 확정한 위반: 빨강
SUPERVISOR_COLOR = (181, 71, 8)   # 감독자가 추가·지정한 항목: 주황
MIN_BOX = 0.02                    # 이보다 작은 박스는 위치를 특정하지 못한 것으로 본다


def normalize_box(raw: dict | None) -> list[float] | None:
    """AI가 준 0~1000 좌표를 0~1 비율로 바꾼다. 쓸 수 없는 값이면 None."""
    try:
        x1, y1, x2, y2 = (float(raw[k]) / 1000 for k in ("x1", "y1", "x2", "y2"))
    except (TypeError, KeyError, ValueError):
        return None
    x1, x2 = sorted((min(max(x1, 0.0), 1.0), min(max(x2, 0.0), 1.0)))
    y1, y2 = sorted((min(max(y1, 0.0), 1.0), min(max(y2, 0.0), 1.0)))
    if x2 - x1 < MIN_BOX or y2 - y1 < MIN_BOX:
        return None
    return [round(v, 4) for v in (x1, y1, x2, y2)]


def marker_of(finding: dict) -> dict | None:
    """위험요인 하나의 표시 정보. 표시할 위치가 없으면 None.

    anchor는 번호 핀의 중심(비율 좌표)이다.
    """
    color = SUPERVISOR_COLOR if finding.get("source") == "supervisor" or finding.get("pin") else AI_COLOR
    pin, box = finding.get("pin"), finding.get("box")
    if pin:
        return {"id": finding["id"], "kind": "pin", "anchor": list(pin), "box": None, "color": color}
    if box:
        return {"id": finding["id"], "kind": "box", "anchor": [box[0], box[1]], "box": list(box), "color": color}
    return None


def markers_of(findings: list[dict]) -> list[dict]:
    return [m for m in (marker_of(f) for f in findings) if m]


def pin_radius_ratio(width: int, height: int) -> tuple[float, float]:
    """번호 핀 반지름을 (가로 비율, 세로 비율)로 돌려준다. 화면의 터치 영역 계산에도 쓴다."""
    radius = max(16, round(max(width, height) * 0.03))
    return radius / width, radius / height


def render(jpeg: bytes, markers: list[dict]) -> bytes:
    """표시를 그려 넣은 JPEG를 돌려준다. 표시가 없으면 원본을 그대로 돌려준다."""
    if not markers:
        return jpeg
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    width, height = img.size
    draw = ImageDraw.Draw(img)
    radius = max(16, round(max(width, height) * 0.03))
    line = max(3, round(max(width, height) / 220))
    font = ImageFont.truetype(str(FONT_BOLD), round(radius * 1.25))

    # JSON을 거쳐 온 표시 정보는 색상이 리스트이므로 튜플로 되돌린다.
    markers = [{**m, "color": tuple(m["color"])} for m in markers]

    for m in markers:  # 박스를 먼저 모두 그리고
        if m["box"]:
            x1, y1, x2, y2 = m["box"]
            rect = [x1 * width, y1 * height, x2 * width, y2 * height]
            draw.rectangle(rect, outline=(255, 255, 255), width=line + 2)
            draw.rectangle(rect, outline=m["color"], width=line)

    for m in markers:  # 번호 핀은 박스에 가려지지 않게 그 위에 그린다
        cx = min(max(m["anchor"][0] * width, radius + 2), width - radius - 2)
        cy = min(max(m["anchor"][1] * height, radius + 2), height - radius - 2)
        draw.ellipse([cx - radius - 3, cy - radius - 3, cx + radius + 3, cy + radius + 3], fill=(255, 255, 255))
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=m["color"])
        draw.text((cx, cy), str(m["id"]), font=font, fill=(255, 255, 255), anchor="mm")

    out = io.BytesIO()
    img.save(out, "JPEG", quality=88, optimize=True)
    return out.getvalue()
