"""산업안전보건기준에 관한 규칙 조문 DB 조회.

조문 본문은 항상 data/law_db.json(국가법령정보센터 원문 변환본)에서 꺼내 쓴다.
AI는 조문 '번호'만 고르고, 화면에 나오는 조문 내용은 이 모듈이 원문 그대로 제공한다.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "law_db.json"
LAW_NAME = "산업안전보건기준에 관한 규칙"
LAW_URL = "https://www.law.go.kr/법령/산업안전보건기준에관한규칙"

_ARTICLE_NO_RE = re.compile(r"(\d+)\s*조?\s*(?:의\s*(\d+))?")


@lru_cache(maxsize=1)
def _db() -> dict:
    return json.loads(DB_PATH.read_text(encoding="utf-8"))


def meta() -> dict:
    return _db()["meta"]


def normalize(article_no: str) -> str | None:
    """'42', '제42조', '제241조의2 제1항' 등을 DB 키('제241조의2')로 바꾼다. 없는 조문이면 None."""
    m = _ARTICLE_NO_RE.search(article_no or "")
    if not m:
        return None
    key = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return key if key in _db()["articles"] else None


@lru_cache(maxsize=1)
def index_text() -> str:
    """AI에게 주는 조문 색인(장·절 제목 + 조문번호 + 조문 제목)."""
    lines: list[str] = []
    last_path = None
    for no, art in _db()["articles"].items():
        if art["path"] != last_path:
            last_path = art["path"]
            lines.append(f"\n[{last_path}]")
        lines.append(f"{no} {art['title']}")
    return "\n".join(lines).strip()


def _paragraph_text(para: dict, ho: list[str] | None = None) -> str:
    """항 본문. ho가 주어지면 해당 호만 남기고 나머지는 생략 표시한다."""
    lines = [para["head"]] if para["head"] else []
    items = para["items"]
    if ho:
        wanted = {str(h).strip() for h in ho}
        selected = [it for it in items if it["no"] in wanted]
        if selected:  # 존재하지 않는 호 번호만 온 경우에는 전체 호를 보여준다.
            lines += [f"{it['no']}. {it['text']}" for it in selected]
            if len(selected) < len(items):
                lines.append("(그 밖의 호 생략)")
            return "\n".join(lines)
    lines += [f"{it['no']}. {it['text']}" for it in items]
    return "\n".join(lines)


def full_text(article_no: str) -> str:
    """조문 전체 원문(AI 대조용·'조문 전체 보기'용)."""
    art = _db()["articles"][article_no]
    parts = []
    for para in art["paragraphs"]:
        prefix = f"제{para['no']}항 " if para["no"] else ""
        parts.append(prefix + _paragraph_text(para))
    return "\n".join(parts)


def cite(article_no: str, paragraph: int = 0, ho: list[str] | None = None) -> dict | None:
    """화면 표시용 인용 정보. 존재하지 않는 조문이면 None(= AI가 지어낸 번호는 버려진다)."""
    key = normalize(article_no)
    if key is None:
        return None
    art = _db()["articles"][key]
    paras = art["paragraphs"]
    para = next((p for p in paras if p["no"] == paragraph and p["no"] != 0), None)

    label = f"{key}({art['title']})" if art["title"] else key
    if para is not None:
        label += f" 제{para['no']}항"
        valid_ho = [h for h in (ho or []) if any(it["no"] == str(h).strip() for it in para["items"])]
        if valid_ho:
            label += " " + "·".join(f"제{h}호" for h in valid_ho)
        text = _paragraph_text(para, valid_ho)
    elif len(paras) == 1:  # 항 구분이 없는 조문
        valid_ho = [h for h in (ho or []) if any(it["no"] == str(h).strip() for it in paras[0]["items"])]
        if valid_ho:
            label += " " + "·".join(f"제{h}호" for h in valid_ho)
        text = _paragraph_text(paras[0], valid_ho)
    else:  # 항 번호가 없거나 잘못된 경우 조문 전체를 보여준다.
        text = full_text(key)

    return {
        "article": key,
        "label": label,
        "text": text,
        "full_text": full_text(key),
        "is_partial": text != full_text(key),
    }
