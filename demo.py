"""데모 모드용 예시 결과.

OpenAI 키 없이 휴대폰에서 촬영·업로드·결과 화면이 제대로 나오는지만 확인하기 위한 것이다.
여기서 돌려주는 결과는 올린 사진과 무관한 고정 예시이며, 조문 본문만 실제 조문 DB에서 가져온다.
"""

from __future__ import annotations

import law

DEMO_KINDS = {
    "위반 확정 예시": "violation",
    "안전수칙 준수 예시": "compliant",
    "해당없음 예시": "not_work",
}

_WORK_STAGE1 = {
    "scene": "비계 위에서 작업자가 배관을 용접하고 있다.",
    "is_work": True,
    "work_types": ["화기작업", "고소작업"],
    "reason": "용접 불꽃과 작업 자세가 확인된다.",
}


def demo_result(verdict: str) -> dict:
    result = {
        "demo": True, "verdict": verdict, "stage1": _WORK_STAGE1, "stage2": None,
        "findings": [], "unverifiable": [], "timings": {},
    }
    if verdict == "not_work":
        result["stage1"] = {
            "scene": "사무실 책상 위에 노트북과 서류가 놓여 있다.",
            "is_work": False, "work_types": [],
            "reason": "작업 행위나 가동 중인 장비가 보이지 않는다.",
        }
    elif verdict == "compliant":
        result["stage2"] = {
            "observations": "작업자 2명이 안전모와 안전대를 착용하고, 안전난간이 설치된 비계 위에서 배관을 용접하고 있다. "
                            "작업 지점 옆에 소화기와 불티 비산방지포가 놓여 있다.",
        }
        result["unverifiable"] = [{
            "text": "화재감시자 배치 여부는 화면 밖일 수 있어 현장 확인이 필요하다.",
            "hazard_name": "용접작업 중 화재감시자 미배치", "target": "",
        }]
    else:
        result["stage2"] = {
            "observations": "높이 약 5미터 비계 상단에서 작업자 1명이 배관을 용접하고 있고, 하부에 다른 작업자 1명이 서 있다.",
        }
        result["findings"] = [
            {
                "id": 1, "hazard_name": "고소작업 중 안전대 미체결",
                "target": "사진 왼쪽 비계 상단 작업자 1명",
                "evidence": "안전대를 착용했으나 죔줄 고리가 어디에도 걸려 있지 않다.",
                "refs": [law.cite("제44조", 1), law.cite("제32조", 1, ["2"])],
                "actions": ["해당 고소작업 즉시 중지", "안전대 죔줄을 부착설비에 체결한 뒤 작업 재개"],
                "kosha_guide": "", "source": "ai",
            },
            {
                "id": 2, "hazard_name": "안전모 미착용",
                "target": "비계 하부 작업자 1명",
                "evidence": "안전모 없이 일반 모자만 쓰고 있다.",
                "refs": [law.cite("제32조", 1, ["1"])],
                "actions": ["안전모 착용 후 작업구역 출입"],
                "kosha_guide": "", "source": "ai",
            },
        ]
    return result
