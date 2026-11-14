"""판독 결과 기록 저장소.

판독이 끝날 때마다 결과를 한 건의 '점검 기록'으로 저장하고(감독자가 추가한 위험요인 포함),
통계 리포트는 이 기록을 모아 계산한다. 사진은 저장하지 않는다.

저장 위치
- GitHub 저장소(권장): secrets에 GITHUB_TOKEN·GITHUB_REPO가 있으면 그 저장소의 별도 브랜치
  (기본 app-data)에 records.json 파일로 저장한다. 앱 배포 브랜치와 달라 저장해도 앱이 재배포되지 않는다.
- 앱 서버 디스크(임시): 위 설정이 없으면 records/inspections.json에 저장한다.
  Streamlit Community Cloud는 앱이 재시작되면 디스크를 지우므로 오래 보관되지 않는다.
"""

from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

THEMES = ["화기작업", "고소작업", "중장비작업", "일반 및 기타 작업"]

# 1단계 작업 종류 → 리포트용 작업 테마
_TYPE_TO_THEME = {
    "화기작업": "화기작업",
    "고소작업": "고소작업",
    "비계·가설작업": "고소작업",
    "중장비작업": "중장비작업",
}


def photo_themes(work_types: list[str]) -> list[str]:
    """사진의 작업 종류를 테마로 바꾼다. 해당 없는 종류만 있으면 '일반 및 기타 작업'."""
    themes = []
    for work_type in work_types or []:
        theme = _TYPE_TO_THEME.get(work_type, THEMES[-1])
        if theme not in themes:
            themes.append(theme)
    return sorted(themes or [THEMES[-1]], key=THEMES.index)


def build_record(record_id: str, result: dict) -> dict | None:
    """판독 결과 하나를 저장용 기록으로 줄인다. 비작업 사진·데모 결과는 기록하지 않는다(None)."""
    if result.get("demo") or result.get("verdict") == "not_work":
        return None
    stage1 = result.get("stage1") or {}
    themes = photo_themes(stage1.get("work_types") or [])
    findings = []
    for f in result.get("findings", []):
        theme = f.get("work_theme") if f.get("work_theme") in THEMES else themes[0]
        findings.append({
            "hazard_name": f.get("hazard_name", ""),
            "violation_type": f.get("violation_type") or f.get("hazard_name", ""),
            "theme": theme,
            "source": f.get("source", "ai"),
            "articles": [
                {"article": r["article"], "label": r["label"], "reference": bool(r.get("reference"))}
                for r in f.get("refs", [])
            ],
            "actions": list(f.get("actions", [])),
        })
    return {
        "id": record_id,
        "checked_at": result.get("checked_at", ""),
        "scene": stage1.get("scene", ""),
        "work_types": list(stage1.get("work_types") or []),
        "themes": themes,
        "findings": findings,
    }


class StoreError(Exception):
    """저장소 읽기·쓰기 실패(사용자에게 보여 줄 수 있는 문구)."""


class LocalStore:
    durable = False
    where = "앱 서버 임시 저장(앱이 재시작되면 지워질 수 있음)"

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"기록 파일을 읽지 못했습니다: {exc}") from exc

    def save_all(self, records: list[dict]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)


class GitHubStore:
    """GitHub 저장소의 별도 브랜치에 JSON 파일 하나로 기록을 보관한다."""

    durable = True

    def __init__(self, token: str, repo: str, branch: str = "app-data", path: str = "records.json",
                 api: str = "https://api.github.com"):
        self.token, self.repo, self.branch, self.path, self.api = token, repo, branch, path, api.rstrip("/")
        self.where = f"GitHub {repo} 저장소의 {branch} 브랜치"
        self._sha: str | None = None
        self._lock = threading.Lock()

    def _request(self, method: str, url: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "field-safety-app",
            **({"Content-Type": "application/json"} if data else {}),
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else {}

    def _file_url(self) -> str:
        return f"{self.api}/repos/{self.repo}/contents/{urllib.parse.quote(self.path)}"

    def _ensure_branch(self) -> None:
        """데이터 브랜치가 없으면 저장소 기본 브랜치에서 새로 만든다."""
        try:
            self._request("GET", f"{self.api}/repos/{self.repo}/git/ref/heads/{urllib.parse.quote(self.branch)}")
            return
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        info = self._request("GET", f"{self.api}/repos/{self.repo}")
        base = self._request("GET", f"{self.api}/repos/{self.repo}/git/ref/heads/{info['default_branch']}")
        self._request("POST", f"{self.api}/repos/{self.repo}/git/refs",
                      {"ref": f"refs/heads/{self.branch}", "sha": base["object"]["sha"]})

    def load(self) -> list[dict]:
        try:
            meta = self._request("GET", f"{self._file_url()}?ref={urllib.parse.quote(self.branch)}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # 아직 기록이 없거나 브랜치가 없음 — 단, 저장소 자체에 접근할 수 있어야 한다
                try:
                    self._request("GET", f"{self.api}/repos/{self.repo}")
                except urllib.error.HTTPError as repo_exc:
                    raise StoreError(_github_error(repo_exc)) from repo_exc
                self._sha = None
                return []
            raise StoreError(_github_error(exc)) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise StoreError(f"GitHub에 연결하지 못했습니다: {exc}") from exc
        self._sha = meta.get("sha")
        if meta.get("content") is None or meta.get("encoding") == "none":  # 1MB 초과 파일
            blob = self._request("GET", f"{self.api}/repos/{self.repo}/git/blobs/{self._sha}")
            content = blob["content"]
        else:
            content = meta["content"]
        return json.loads(base64.b64decode(content).decode("utf-8") or "[]")

    def save_all(self, records: list[dict]) -> None:
        with self._lock:
            body = {
                "message": f"Update inspection records ({len(records)})",
                "content": base64.b64encode(
                    json.dumps(records, ensure_ascii=False, indent=1).encode("utf-8")).decode("ascii"),
                "branch": self.branch,
            }
            for attempt in range(2):
                try:
                    if self._sha is None:
                        self._ensure_branch()
                    if self._sha:
                        body["sha"] = self._sha
                    out = self._request("PUT", self._file_url(), body)
                    self._sha = out["content"]["sha"]
                    return
                except urllib.error.HTTPError as exc:
                    if exc.code in (409, 422) and attempt == 0:  # 다른 곳에서 바뀜 → 최신 sha로 다시 시도
                        self._sha = None
                        try:
                            meta = self._request("GET", f"{self._file_url()}?ref={urllib.parse.quote(self.branch)}")
                            self._sha = meta.get("sha")
                        except urllib.error.HTTPError:
                            pass
                        body.pop("sha", None)
                        continue
                    raise StoreError(_github_error(exc)) from exc
                except (urllib.error.URLError, TimeoutError) as exc:
                    raise StoreError(f"GitHub에 연결하지 못했습니다: {exc}") from exc


def _github_error(exc: urllib.error.HTTPError) -> str:
    if exc.code == 401:
        return "GitHub 토큰이 올바르지 않습니다. secrets의 GITHUB_TOKEN을 확인해 주세요."
    if exc.code in (403, 404):
        return ("GitHub 저장소에 접근할 수 없습니다. GITHUB_REPO 이름과, 토큰에 해당 저장소의 "
                "Contents 읽기·쓰기 권한이 있는지 확인해 주세요.")
    return f"GitHub 요청이 실패했습니다({exc.code})."


def upsert(records: list[dict], record: dict) -> list[dict]:
    """같은 id(같은 사진·메모·모델로 다시 판독)는 덮어쓰고, 아니면 추가한다."""
    out = [r for r in records if r.get("id") != record["id"]]
    out.append(record)
    out.sort(key=lambda r: r.get("checked_at", ""))
    return out


def merge(records: list[dict], incoming: list[dict]) -> list[dict]:
    """백업 파일을 불러올 때: id가 겹치면 불러온 쪽으로 바꾸고 나머지는 합친다."""
    for record in incoming:
        if isinstance(record, dict) and record.get("id") and record.get("checked_at"):
            records = upsert(records, record)
    return records
