import re # 정규식 처리 
import unicodedata # Unicode 문자 정규화
from typing import Dict # 타입 힌트

# 신용카드 관련 키워드 목록 => 품질 평가 시 키워드 포함 여부 체크 
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

BROKEN_CHAR_PATTERN = re.compile(r"[�□◻◼◦▪◆◇¤�]") # 깨진 문자(OCR 오류 감지)
KOREAN_PATTERN = re.compile(r"[가-힣]") # 한글 매칭
ALNUM_PATTERN = re.compile(r"[가-힣A-Za-z0-9]") # 한글 + 알파벡 + 숫자 매칭
AMOUNT_PATTERN = re.compile(r"\d[\d,\s]*(원|만원|천원|%)") # 금액/퍼센트 패턴 감지 

# 자동 수정 위험한 패턴 감지 -> 사람 검수 필요 
REVIEW_RISK_PATTERNS = {
    "numeric_risk": [
        re.compile(r"\b\d+(?:\.\d+)?%6\b"), # "10%6" 같은 이상한 퍼센트
        re.compile(r"\b\d+P6\b"), # "20P6" 같은 숫자 + 기호
        re.compile(r"\b\d+dp\b", re.IGNORECASE), # "39dp"
        re.compile(r"\b0\.290\b"), # "0.290" (퍼센트 오인식)
        re.compile(r"\b089\b"), # "089" (0.8% 오인식)
    ], 
    "amount_risk": [
        re.compile(r"\b\d{1,3},\.\d{3}원\b"),
    ],
}

# 특정 OCR 오류 패턴 감지 -> 점수 감점 
PENALTY_RULES = {
    "brand_distortion": {         # 브랜드 왜곡
        "patterns": [
            re.compile(r"비로키C"), # "BC" → "비로키 C"
            re.compile(r"Kbonk", re.IGNORECASE), # "KB" → "Kbonk"
            re.compile(r"Srccrfcard", re.IGNORECASE),
        ],
        "penalty_per_match": 5.0, # 매치당 5 점 감점
        "max_penalty": 15.0, # 최대 15 점
    },
    "odd_spacing": {                # 이상한 공백/붙여쓰기
        "patterns": [
            re.compile(r"카드시논부가서비스률변경할 수있습니다"),
            re.compile(r"카드시논부가서비스률변경활수있습니다"),
        ],
        "penalty_per_match": 8.0,
        "max_penalty": 16.0,
    },
    "odd_word_form": {               # 비정상 단어 조합
        "patterns": [
            re.compile(r"가계지금대출금라"),
            re.compile(r"부가서비스률"),
            re.compile(r"차갑되다"),
            re.compile(r"매줌"),
            re.compile(r"반은 금액"),
            re.compile(r"계약올"),
            re.compile(r"반영원"),
            re.compile(r"제유업체름"),
            re.compile(r"부과하능"),
        ],
        "penalty_per_match": 6.0,
        "max_penalty": 18.0,
    },
}
TOTAL_PENALTY_CAP = 30.0 # 전체 감점 상한선

# 줄 단위 공백 정리 
def normalize_whitespace(text: str) -> str:
    # 줄 단위 공백을 정리해 후속 점수 계산이 흔들리지 않게 합니다.
    lines = [line.strip() for line in text.splitlines()] # 각 줄 앞뒤 공백 제거 
    return "\n".join(line for line in lines if line)

# OCR/native 텍스트 종합 정제 
def normalize_extracted_text(text: str) -> str: 
    # OCR/native 추출 결과를 공통 규칙으로 정규화합니다.
    text = unicodedata.normalize("NFKC", text or "") # Unicode 정규화
    text = text.replace("\x00", " ") # 널 문자 제거
    text = re.sub(r"[ \t]+", " ", text) # 중복 공백 제거
    text = re.sub(r"\s+([%])", r"\1", text) # "% 앞 공백 제거
    text = re.sub(r"(\d)\s+(원|만원|천원|%)", r"\1\2", text) # "10 000 원" → "10000 원"
    text = re.sub(r"(전)\s+(월)\s+(실)\s+(적)", r"\1\2\3\4", text) # "전 월 실 적" → "전월실적"
    text = re.sub(r"(연)\s+(회)\s+(비)", r"\1\2\3", text) # "연 회 비" → "연회비"
    text = normalize_whitespace(text)
    return text.strip()

# 품질 평가 함수 
def evaluate_text_quality(text: str) -> Dict[str, float]:
    # 텍스트 길이, 한글 비율, 키워드, 깨진 문자 비율 등을 종합해 품질 점수를 계산합니다.
    normalized = normalize_extracted_text(text)
    length = len(normalized)

    # 빈 텍스트면 0점 반환
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
    # 지표 계산 
    korean_count = len(KOREAN_PATTERN.findall(normalized)) # 한글 개수/비율
    alnum_count = len(ALNUM_PATTERN.findall(normalized)) # 알파벳/숫자 개수/비율
    broken_count = len(BROKEN_CHAR_PATTERN.findall(normalized)) # 깨진 문자 개수/비율
    keyword_hits = sum(1 for keyword in FINANCE_KEYWORDS if keyword in normalized) # 금융 키워드 히트 수
    amount_hits = len(AMOUNT_PATTERN.findall(normalized)) # 금액 패턴 히트 수

    korean_ratio = korean_count / length
    alnum_ratio = alnum_count / length
    broken_ratio = broken_count / length

    # 점수 계산 - 총점 100점 
    length_score = min(length / 800.0, 1.0) * 30.0 # 길이: 30 점 (800 자 이상)
    korean_score = min(korean_ratio / 0.25, 1.0) * 25.0 # 한글 비율: 25 점 (25% 이상)
    alnum_score = min(alnum_ratio / 0.55, 1.0) * 15.0 # 알파벳/숫자: 15 점 (55% 이상)
    keyword_score = min(keyword_hits / 4.0, 1.0) * 20.0 # 키워드: 20 점 (4 개 이상)
    amount_score = min(amount_hits / 5.0, 1.0) * 10.0 # 금액: 10 점 (5 개 이상)
    broken_penalty = min(broken_ratio / 0.02, 1.0) * 25.0 # 깨진 문자 감점: 최대 25 점
    # OCR 오류 감점 => OCR 오류 패턴 감지 -> 추가 감점 
    penalty_info = detect_penalty_patterns(normalized)
    ocr_penalty = penalty_info["penalty_score"]

    # 최종 점수 => 점수 = (양적 점수) - (꺠진 문자 감점) - (OCR 오류 감점)
    score = max(
        0.0,
        round(
            length_score
            + korean_score
            + alnum_score
            + keyword_score
            + amount_score
            - broken_penalty
            - ocr_penalty,
            2,
        ),
    )

    return {
        "score": score,
        "length": float(length),
        "korean_ratio": round(korean_ratio, 4),
        "alnum_ratio": round(alnum_ratio, 4),
        "broken_ratio": round(broken_ratio, 4),
        "keyword_hits": float(keyword_hits),
        "amount_hits": float(amount_hits),
        "penalty_score": ocr_penalty, # OCR 감점 점수
        "penalty_flags": penalty_info["penalty_flags"], # 감지된 오류 유형
        "penalty_matches": penalty_info["penalty_matches"], # 실제 오류 문자열
        "penalty_count": penalty_info["penalty_count"], # 오류 개수
    }

# 패턴 감지 함수 -> 자동 수정 위험한 패턴 감지 
# 일치하는 패턴 수집, 중복 제거, 검수 필요 여부 판단 
def detect_review_risks(text: str) -> Dict[str, object]:
    # 숫자/금액처럼 자동 수정이 위험한 패턴을 찾아 검수 필요 여부를 판단합니다.
    normalized = normalize_extracted_text(text)
    matches = []

    for risk_type, patterns in REVIEW_RISK_PATTERNS.items(): # REVIEW_RISK_PATTERNS로 텍스트 스캔
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

# OCR오류 패턴 감지 -> 점수 감점 
# 카테고리별 패턴 매칭, 중복 제거, 감점 계산(매치당 점수, 최대 한도), 전체 감점 상한선 적용 (TOTAL_PENALTY_CAP = 30)
def detect_penalty_patterns(text: str) -> Dict[str, object]:
    # 브랜드 왜곡, 붙여쓰기 붕괴, 비정상 단어 조합 같은 품질 저하 패턴을 감지합니다.
    normalized = normalize_extracted_text(text)
    matches = []
    total_penalty = 0.0

    for penalty_type, rule in PENALTY_RULES.items(): # PENALTY_RULES 로 텍스트 스캔
        type_matches = []
        seen = set()
        for pattern in rule["patterns"]:
            for match in pattern.finditer(normalized):
                matched_text = match.group(0)
                if matched_text in seen:
                    continue
                seen.add(matched_text)
                type_matches.append(matched_text)

        if not type_matches:
            continue

        penalty_value = min(len(type_matches) * rule["penalty_per_match"], rule["max_penalty"])
        total_penalty += penalty_value
        matches.append(
            {
                "penalty_type": penalty_type,
                "matches": type_matches,
                "penalty_value": penalty_value,
            }
        )

    total_penalty = min(total_penalty, TOTAL_PENALTY_CAP)

    return {
        "penalty_score": round(total_penalty, 2),
        "penalty_count": sum(len(item["matches"]) for item in matches),
        "penalty_flags": [item["penalty_type"] for item in matches],
        "penalty_matches": [match for item in matches for match in item["matches"]],
    }

# 저품질 텍스트 판정 
# 조건 : 점수 < 35 또는 길이 < 80자 
def is_low_quality_text(text: str, min_score: float = 35.0, min_length: int = 80) -> bool:
    # 계산된 점수와 최소 길이를 기준으로 저품질 텍스트 여부를 판정합니다.
    metrics = evaluate_text_quality(text)
    return metrics["score"] < min_score or metrics["length"] < min_length
