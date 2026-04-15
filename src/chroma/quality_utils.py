import re
import unicodedata
from typing import Dict

FINANCE_KEYWORDS = (
    "전월실적",
    "연회비",
    "할인",
    "적립",
    "한도",
    "국내",
    "해외",
    "가맹점",
    "혜택",
    "캐시백",
    "포인트",
    "마일리지",
)

BROKEN_CHAR_PATTERN = re.compile(r"[�□◻◼◦▪◆◇¤�]")
KOREAN_PATTERN = re.compile(r"[가-힣]")
ALNUM_PATTERN = re.compile(r"[가-힣A-Za-z0-9]")
AMOUNT_PATTERN = re.compile(r"\d[\d,\s]*(원|만원|천원|%)")
REVIEW_RISK_PATTERNS = {
    "numeric_risk": [
        re.compile(r"\b\d+(?:\.\d+)?%6\b"),
        re.compile(r"\b\d+P6\b"),
        re.compile(r"\b\d+dp\b", re.IGNORECASE),
        re.compile(r"\b0\.290\b"),
        re.compile(r"\b089\b"),
    ],
    "amount_risk": [
        re.compile(r"\b\d{1,3},\.\d{3}원\b"),
    ],
}


def normalize_whitespace(text: str) -> str:
    # 줄 단위 공백을 정리해 후속 점수 계산이 흔들리지 않게 합니다.
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def normalize_extracted_text(text: str) -> str:
    # OCR/native 추출 결과를 공통 규칙으로 정규화합니다.
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([%])", r"\1", text)
    text = re.sub(r"(\d)\s+(원|만원|천원|%)", r"\1\2", text)
    text = re.sub(r"(전)\s+(월)\s+(실)\s+(적)", r"\1\2\3\4", text)
    text = re.sub(r"(연)\s+(회)\s+(비)", r"\1\2\3", text)
    text = normalize_whitespace(text)
    return text.strip()


def evaluate_text_quality(text: str) -> Dict[str, float]:
    # 텍스트 길이, 한글 비율, 키워드, 깨진 문자 비율 등을 종합해 품질 점수를 계산합니다.
    normalized = normalize_extracted_text(text)
    length = len(normalized)

    if length == 0:
        return {
            "score": 0.0,
            "length": 0,
            "korean_ratio": 0.0,
            "alnum_ratio": 0.0,
            "broken_ratio": 1.0,
            "keyword_hits": 0,
            "amount_hits": 0,
        }

    korean_count = len(KOREAN_PATTERN.findall(normalized))
    alnum_count = len(ALNUM_PATTERN.findall(normalized))
    broken_count = len(BROKEN_CHAR_PATTERN.findall(normalized))
    keyword_hits = sum(1 for keyword in FINANCE_KEYWORDS if keyword in normalized)
    amount_hits = len(AMOUNT_PATTERN.findall(normalized))

    korean_ratio = korean_count / length
    alnum_ratio = alnum_count / length
    broken_ratio = broken_count / length

    length_score = min(length / 800.0, 1.0) * 30.0
    korean_score = min(korean_ratio / 0.25, 1.0) * 25.0
    alnum_score = min(alnum_ratio / 0.55, 1.0) * 15.0
    keyword_score = min(keyword_hits / 4.0, 1.0) * 20.0
    amount_score = min(amount_hits / 5.0, 1.0) * 10.0
    broken_penalty = min(broken_ratio / 0.02, 1.0) * 25.0

    score = max(
        0.0,
        round(length_score + korean_score + alnum_score + keyword_score + amount_score - broken_penalty, 2),
    )

    return {
        "score": score,
        "length": float(length),
        "korean_ratio": round(korean_ratio, 4),
        "alnum_ratio": round(alnum_ratio, 4),
        "broken_ratio": round(broken_ratio, 4),
        "keyword_hits": float(keyword_hits),
        "amount_hits": float(amount_hits),
    }


def detect_review_risks(text: str) -> Dict[str, object]:
    # 숫자/금액처럼 자동 수정이 위험한 패턴을 찾아 검수 필요 여부를 판단합니다.
    normalized = normalize_extracted_text(text)
    matches = []

    for risk_type, patterns in REVIEW_RISK_PATTERNS.items():
        for pattern in patterns:
            for match in pattern.finditer(normalized):
                matches.append(
                    {
                        "risk_type": risk_type,
                        "match": match.group(0),
                    }
                )

    unique_matches = []
    seen = set()
    for item in matches:
        key = (item["risk_type"], item["match"])
        if key in seen:
            continue
        seen.add(key)
        unique_matches.append(item)

    return {
        "review_needed": bool(unique_matches),
        "review_count": len(unique_matches),
        "review_flags": sorted({item["risk_type"] for item in unique_matches}),
        "review_matches": [item["match"] for item in unique_matches],
    }


def is_low_quality_text(text: str, min_score: float = 35.0, min_length: int = 80) -> bool:
    # 계산된 점수와 최소 길이를 기준으로 저품질 텍스트 여부를 판정합니다.
    metrics = evaluate_text_quality(text)
    return metrics["score"] < min_score or metrics["length"] < min_length
