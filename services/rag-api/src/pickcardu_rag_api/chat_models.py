from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import AnswerResponse, ErrorResponse, ProfileName, QueryRequest


SpendingCategory = Literal['쇼핑', '배달·외식', '카페', '교통', '주유', '여행']
PreferredBenefit = Literal['할인', '포인트 적립', '항공 마일리지']
SurveyField = Literal['monthly_spending', 'spending_categories', 'preferred_benefits']
CardKey = Annotated[str, Field(min_length=1, max_length=64)]


class SurveyContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    monthly_spending: Literal['under-30', '30-50', '50-100', '100-200', 'over-200'] | None = None
    spending_categories: list[SpendingCategory] = Field(default_factory=list, max_length=6)
    preferred_benefits: list[PreferredBenefit] = Field(default_factory=list, max_length=3)

    @field_validator('spending_categories', 'preferred_benefits')
    @classmethod
    def canonical_choices(cls, value, info):
        if len(value) != len(set(value)):
            raise ValueError('survey choices must be unique')
        order = ('쇼핑', '배달·외식', '카페', '교통', '주유', '여행') if info.field_name == 'spending_categories' else (
            '할인', '포인트 적립', '항공 마일리지')
        return sorted(value, key=order.index)


def normalize_survey_context(value) -> dict | None:
    if value is None:
        return None
    survey = SurveyContext.model_validate(value)
    return survey.model_dump(mode='json') if (
        survey.monthly_spending or survey.spending_categories or survey.preferred_benefits) else None


class WalletContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['ready', 'empty', 'needs_review']
    card_keys: list[CardKey] = Field(default_factory=list, max_length=106)

    @model_validator(mode='after')
    def validate_wallet(self):
        if len(set(self.card_keys)) != len(self.card_keys) or any(key != key.strip() for key in self.card_keys):
            raise ValueError('wallet card keys must be unique exact IDs')
        if (self.status == 'ready') != bool(self.card_keys):
            raise ValueError('only a ready wallet may contain cards')
        return self


class TurnInputSnapshot(QueryRequest):
    wallet_context: WalletContext | None = None


class PersonalizationContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    survey_context: SurveyContext | None = None
    wallet_context: WalletContext | None = None


class RecallItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_ref: str = Field(min_length=1, max_length=64)
    card_key: CardKey
    card_name: str = Field(min_length=1)
    issuer: str
    release_id: str | None = None
    profile: ProfileName | None = None


class ExecutionContext(BaseModel):
    """Server-only routing snapshot, not evidence of financial product facts."""
    model_config = ConfigDict(extra='forbid')
    standalone_query: str = Field(min_length=1, max_length=500)
    retrieval_query: str | None = Field(default=None, min_length=1, max_length=1024)
    scope: Literal['global', 'previous', 'clarification'] = 'global'
    operation: Literal['retrieve', 'recall'] = 'retrieve'
    target_card_keys: list[CardKey] | None = Field(default=None, min_length=1, max_length=5)
    use_survey: bool = False
    use_wallet: bool = False
    wallet_operation: Literal['none', 'lookup', 'compare'] = 'none'
    wallet_compare_scope: Literal['relevant', 'all_owned'] = 'relevant'
    clarification: str | None = Field(default=None, min_length=1, max_length=300)
    recall_items: list[RecallItem] = Field(default_factory=list, max_length=5)
    overridden_survey_fields: list[SurveyField] = Field(default_factory=list, max_length=3)
    personalization_context: PersonalizationContext | None = None

    @model_validator(mode='after')
    def validate_decision(self):
        context = self.personalization_context
        if not self.standalone_query.strip() or (self.retrieval_query is not None and not self.retrieval_query.strip()):
            raise ValueError('execution queries must not be blank')
        if len(set(self.overridden_survey_fields)) != len(self.overridden_survey_fields):
            raise ValueError('overridden survey fields must be unique')
        if self.target_card_keys is not None and len(set(self.target_card_keys)) != len(self.target_card_keys):
            raise ValueError('execution targets must be unique')
        if not self.use_wallet and (self.wallet_operation != 'none' or (context and context.wallet_context)):
            raise ValueError('unused wallet must not affect execution')
        if self.use_wallet and (self.wallet_operation == 'none' or not context or not context.wallet_context
                                or context.wallet_context.status != 'ready'):
            raise ValueError('wallet execution requires a ready snapshot')
        if not self.use_survey and (self.overridden_survey_fields or (context and context.survey_context)):
            raise ValueError('unused survey must not affect execution')
        if self.use_survey and (not context or not context.survey_context) and not self.overridden_survey_fields:
            raise ValueError('survey execution requires a survey or explicit overrides')
        if not self.use_survey and not self.use_wallet and context is not None:
            raise ValueError('generic execution must have no personalization context')
        if self.operation == 'recall':
            if (self.scope != 'previous' or not self.recall_items or self.retrieval_query is not None
                    or self.target_card_keys is not None or self.use_survey or self.use_wallet or self.clarification):
                raise ValueError('recall must use only ordered stored references')
            if len({item.source_ref for item in self.recall_items}) != len(self.recall_items):
                raise ValueError('recall references must be unique')
        elif self.scope == 'clarification':
            if (not self.clarification or self.retrieval_query is not None or self.target_card_keys is not None
                    or self.recall_items or self.use_survey or self.use_wallet):
                raise ValueError('clarification must not perform retrieval')
        elif self.retrieval_query is None or self.clarification or self.recall_items:
            raise ValueError('retrieve requires a search query and no recall/clarification')
        elif self.scope == 'previous' and self.target_card_keys is None:
            raise ValueError('previous retrieval requires resolved targets')
        return self


class BrowserSessionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class BrowserSessionResponse(BaseModel):
    status: Literal['ready']


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_conversation_id: UUID
    survey_context: SurveyContext | None = None

    @field_validator('survey_context')
    @classmethod
    def empty_survey_is_none(cls, value):
        normalized = normalize_survey_context(value)
        return SurveyContext.model_validate(normalized) if normalized is not None else None


class Conversation(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class ConversationPage(BaseModel):
    conversations: list[Conversation]
    next_cursor: str | None


class ChatRequest(TurnInputSnapshot):
    client_request_id: UUID
    retry_failed: bool = False


class ChatRewriteUsage(BaseModel):
    provider_called: bool
    model: str | None = None
    latency_ms: float | None = None
    usage: dict[str, Any] | None = None


class ChatMessage(BaseModel):
    id: str
    turn_id: str
    client_request_id: str
    seq: int
    role: Literal['user', 'assistant']
    content: str
    status: Literal['completed', 'pending', 'failed']
    answer: AnswerResponse | None = None
    rewrite_usage: ChatRewriteUsage | None = None
    error: ErrorResponse | None = None
    created_at: str
    input_snapshot: TurnInputSnapshot | None = None


class TurnResponse(BaseModel):
    turn_id: str
    messages: list[ChatMessage]


class MessagesPage(BaseModel):
    messages: list[ChatMessage]
    next_before_seq: int | None
    has_pending: bool
    survey_context: SurveyContext | None = None
