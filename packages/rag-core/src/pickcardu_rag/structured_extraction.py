"""One bounded extraction request, independent of the answer model."""
from __future__ import annotations
import logging
import time
from .structured import QueryPlan

# Existing parser50 + parser56 instructions; no new synonyms or typo rules.
INSTRUCTIONS = "카드 질문을 고정 스키마의 조회 조건으로 변환하세요. SQL 자체를 생성하지 않습니다.\n원질문의 명시된 혜택 대상/할인·적립/숫자 제한만 groups에 넣습니다. 각 group의 조건은 동일 카드에서 모두 만족해야 하며 서로 다른 혜택 행이어도 됩니다.\n대상 목록은 한 혜택의 또는 관계이고, 커피와 넷플릭스처럼 함께 필요한 혜택은 한 group 안의 별도 조건입니다.\n커피 혜택은 targets=[커피], action=any, 할인은 discount, 적립/캐시백은 earn입니다. 요구하지 않은 %를 만들지 마세요.\n월 몇 번인가/전월실적과 한도가 얼마인가/최소 결제금액도 알려줘는 requested_fields이며 필터 실패가 아닙니다.\n반대로 월 3회 이상 가능한/전월실적 기준 30만원 이하/월 한도 1만원 이상은 실제 숫자 필터입니다.\n사람의 이용실적·결제금액이나 가정 계산용 숫자는 해당 카드의 최소 실적/혜택률 필터로 오인하지 마세요. 6500원 결제하면 적용되는가의 숫자는 설명 자료입니다.\n금액은 원, 혜택률은 퍼센트 숫자, 횟수는 정수로 출력하세요. '월'과 '일'을 구분하세요.\n기차를 철도로 교체하거나 오타를 임의 보정하지 마세요. 그대로 추출하고 DB 일치 여부에 맡깁니다.\n카드명·발급사는 외부의 독립 범위 처리에서 적용하므로 혜택 targets에 넣지 마세요.\nunsupported_filters에는 표현할 수 없는 명시적 필터만 넣고 상세 설명 요청은 넣지 마세요.\n불명확한 생활 추천처럼 혜택 대상을 특정할 수 없으면 groups=[]입니다. 빈 내부 group은 만들지 마세요.\n모든 필드를 명시하고 요청하지 않은 숫자는 null, 기간은 any로 출력하세요.\n추가 구분 규칙:\n- 요청한 설명과 실제 검색 제약을 먼저 구분하세요. 어떤 값인지 묻는 실적·한도·횟수는 requested_fields입니다.\n- '한도는 얼마', '월 한도를 설명'은 min_cap_krw=null이며 cap_period=any입니다. '월 한도 1만원 이상'만 min_cap_krw=10000, cap_period=month입니다.\n- 전월실적 없는 카드를 명시적으로 찾으면 max_spend_krw=0입니다. 이용자의 전월실적이 0원이라는 상황 설명을 이 필터로 만들지 마세요.\n- 지급 할인액 한도와 할인 대상 결제금액 한도는 다릅니다. 후자의 크기 요청은 cap 필터로 바꾸지 마세요.\n- 할인받을 수 있는가를 묻는 고정 혜택 대상은 조건에 넣고, 요일·시간·결제경로의 적용 여부 질문은 설명 요청으로 둡니다.\n- '특정 시간에 할인되는 카드', '정액 300원인 카드'처럼 스키마가 표현하지 못하는 명시적 제약은 unsupported_filters에 보존하세요.\n- 카드명, 발급사, 원문에 없는 브랜드·동의어를 혜택 대상으로 추가하지 마세요. 기차와 철도는 서로 바꾸지 않습니다.\n- 커피전문점은 질문에 커피가 명시된 경우 기존 커피 범주로 표현할 수 있습니다. 주유를 특정 정유사로 바꾸지 않습니다.\n"

class StructuredExtractor:
    def __init__(self, *, api_key=None, model="gpt-6-luna", client=None):
        self.api_key, self.model, self.client = api_key, model, client

    def extract(self, question):
        started = time.perf_counter()
        usage = dict(model=self.model, reasoning_effort="medium", max_output_tokens=3000,
                     timeout_seconds=60, attempt_count=0, provider_called=False, usage_complete=False)
        plan = None
        try:
            if self.client is None:
                if not self.api_key:
                    raise RuntimeError("OPENAI_API_KEY is not configured")
                from openai import OpenAI
                self.client = OpenAI(api_key=self.api_key, max_retries=0, timeout=60)
            usage.update(provider_called=True, attempt_count=1)
            response = self.client.responses.create(model=self.model, instructions=INSTRUCTIONS,
                input=[dict(role="user", content=question)], tools=[], store=False,
                reasoning={"effort": "medium"}, max_output_tokens=3000,
                text={"format": {"type": "json_schema", "name": "query_plan", "strict": True,
                                  "schema": QueryPlan.model_json_schema()}})
            usage["usage"] = response.usage.model_dump(mode="json") if response.usage is not None else None
            usage["usage_complete"] = usage["usage"] is not None
            usage["response_id"] = response.id
            if response.status != "completed":
                raise ValueError("structured extraction did not complete")
            plan = QueryPlan.model_validate_json(response.output_text)
        except Exception as error:
            usage["error_type"] = type(error).__name__
            logging.getLogger(__name__).warning("structured extraction failed: %s", type(error).__name__)
        usage["extraction_ms"] = round((time.perf_counter()-started)*1000, 3)
        return plan, usage
