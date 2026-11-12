"""산업안전보건기준에 관한 규칙 원문 → data/law_db.json 변환 스크립트.

법령이 개정되면 이 스크립트를 다시 실행해 조문 DB를 갱신한다.

    python scripts/build_law_db.py            # 공개 저장소에서 최신본 내려받아 변환
    python scripts/build_law_db.py 원문.md     # 이미 받아 둔 마크다운 파일을 변환

원문 출처: 국가법령정보센터(law.go.kr) 법령을 마크다운으로 미러링하는
legalize-kr 공개 저장소. 앱은 이 DB의 조문만 화면에 표시하므로
AI가 조문 내용을 지어낼 수 없다.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

SOURCE_URL = (
    "https://raw.githubusercontent.com/legalize-kr/legalize-kr/main/kr/"
    + urllib.parse.quote("산업안전보건기준에관한규칙/고용노동부령.md")
)
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "law_db.json"

HEADING_RE = re.compile(r"^(#{1,5}) (.+)$")
ARTICLE_RE = re.compile(r"^(제\d+조(?:의\d+)?)(?: \((.*)\))?$")
PARAGRAPH_RE = re.compile(r"^\*\*([①-⑳㉑-㉟])\*\*\s*(.*)$")
HO_RE = re.compile(r"^ {2}(\d+(?:의\d+)?)\\?\.\s*(.*)$")
# <개정 2019.12.26>, <신설 2021.5.28> 같은 개정 이력 표기와 [본조신설 ...] 표기는 화면에서 제외
AMEND_RE = re.compile(r"\s*<(?:개정|신설|삭제|전문개정)[^>]*>")
BRACKET_NOTE_RE = re.compile(r"^\[(?:본조신설|제목개정|전문개정|종전|제\d+조).*\]$")
TABLE_CHARS = set("─│┌┐└┘├┤┬┴┼")
TABLE_PLACEHOLDER = "〔표·그림 생략: 국가법령정보센터 원문 참조〕"


def circled_to_int(ch: str) -> int:
    code = ord(ch)
    if 0x2460 <= code <= 0x2473:  # ①~⑳
        return code - 0x2460 + 1
    return code - 0x3251 + 21  # ㉑~㉟


def clean(line: str) -> str:
    line = AMEND_RE.sub("", line)
    line = line.replace("\\.", ".").replace("&lt;", "<").replace("&gt;", ">")
    return line.rstrip()


def parse_front_matter(lines: list[str]) -> dict:
    meta = {}
    for line in lines:
        m = re.match(r"^(제목|법령MST|공포일자|공포번호|시행일자|출처): (.+)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip().strip("'")
    return meta


def parse_body(body: list[str]) -> list[dict]:
    """조문 본문을 항(①②…) → 호(1. 2. …) 구조로 나눈다."""
    paragraphs: list[dict] = []
    current: dict | None = None

    def start(no: int, head: str) -> dict:
        para = {"no": no, "head": head, "items": []}
        paragraphs.append(para)
        return para

    for raw in body:
        # 이미지 닫는 태그, 조문 사이에 낀 "제2속 …" 같은 소제목은 본문이 아니다.
        if raw.strip() == "</img>" or re.match(r"^\*\*제\d+속 .*\*\*$", raw.strip()):
            continue
        if "<img" in raw or any(c in TABLE_CHARS for c in raw):
            line, is_table = TABLE_PLACEHOLDER, True
        else:
            line, is_table = clean(raw), False
        if not line.strip() or BRACKET_NOTE_RE.match(line.strip()):
            continue

        m = PARAGRAPH_RE.match(line)
        if m:
            current = start(circled_to_int(m.group(1)), m.group(2).strip())
            continue
        if current is None:
            current = start(0, "")  # 항 구분이 없는 조문

        m = HO_RE.match(line)
        if m and not is_table:
            current["items"].append({"no": m.group(1), "text": m.group(2).strip()})
            continue

        # 목(가. 나. …)·표 안내·이어지는 문장은 직전 호(없으면 항 본문)에 붙인다.
        text = line.strip()
        if current["items"]:
            target = current["items"][-1]
            if not (is_table and target["text"].endswith(TABLE_PLACEHOLDER)):
                target["text"] += "\n" + text
        elif not (is_table and current["head"].endswith(TABLE_PLACEHOLDER)):
            current["head"] = (current["head"] + "\n" + text).strip()
    return paragraphs


def build(markdown: str) -> dict:
    lines = markdown.splitlines()
    meta = parse_front_matter(lines[: lines.index("---", 1)])

    articles: dict[str, dict] = {}
    path: dict[int, str] = {}  # 편/장/절/관 제목
    current_no: str | None = None
    body: list[str] = []
    started = False

    def flush() -> None:
        nonlocal current_no, body
        if current_no:
            paragraphs = parse_body(body)
            deleted = not paragraphs or paragraphs[0]["head"].startswith("삭제")
            if not deleted:
                articles[current_no]["paragraphs"] = paragraphs
            else:
                del articles[current_no]
        current_no, body = None, []

    for line in lines:
        m = HEADING_RE.match(line)
        if m:
            level, title = len(m.group(1)), clean(m.group(2)).strip()
            if title == "부칙":
                break
            if level == 5:
                am = ARTICLE_RE.match(title)
                if am:
                    flush()
                    current_no = am.group(1)
                    articles[current_no] = {
                        "title": am.group(2) or "",
                        "path": " > ".join(path[k] for k in sorted(path)),
                    }
                    continue
            elif title.startswith("제"):
                flush()
                started = True
                path[level] = title
                for deeper in [k for k in path if k > level]:
                    del path[deeper]
                continue
        if started and current_no:
            body.append(line)
    flush()
    return {"meta": meta, "articles": articles}


def main() -> None:
    if len(sys.argv) > 1:
        markdown = Path(sys.argv[1]).read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as resp:
            markdown = resp.read().decode("utf-8")
    db = build(markdown)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(f"{db['meta'].get('제목')} (시행 {db['meta'].get('시행일자')})")
    print(f"조문 {len(db['articles'])}개 → {OUT_PATH}")


if __name__ == "__main__":
    main()
