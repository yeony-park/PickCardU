import streamlit as st
import os
import sys
import html
import yaml
import json
import time
import re
import markdown
from datetime import datetime
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage, AIMessage

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(ROOT_DIR, "src"))
load_dotenv()

from chroma.retrieval import CardRetriever
from ui import templates as tmpl
from sqlite.crud import (
    ensure_db,
    get_or_create_user,
    update_user_mbti,
    get_user_cards,
    add_user_card,
    remove_user_card_by_name,
    save_chat_message,
    get_chat_history,
    clear_chat_history,
)

ensure_db()

st.set_page_config(
    page_title="PickCardU",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded",
)

css_path = os.path.join(ROOT_DIR, "assets", "style.css")
with open(css_path, "r", encoding="utf-8") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

@st.cache_resource
def init_retriever():
    return CardRetriever()

@st.cache_resource
def init_llm():
    return ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.3,
        api_key=os.getenv("OPENAI_API_KEY"),
    )

retriever = init_retriever()
llm = init_llm()

@st.cache_data
def load_mbti_prompts():
    """prompts.yml에서 MBTI별 프롬프트를 로드"""
    prompts_path = os.path.join(ROOT_DIR, "prompts", "prompts.yml")
    with open(prompts_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

MBTI_PROMPTS = load_mbti_prompts()
MBTI_LIST = list(MBTI_PROMPTS.keys())


@st.cache_data
def load_card_name_map():
    """Load raw card_name → Korean display name mapping from card_name_map.json"""
    map_path = os.path.join(ROOT_DIR, "src", "card_name_map.json")
    try:
        with open(map_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

@st.cache_data
def build_display_to_raw() -> dict[str, list[str]]:
    """Build reverse map: display_name → [raw_name, ...]"""
    card_map = load_card_name_map()
    display_to_raw: dict[str, list[str]] = {}
    for _raw, _display in card_map.items():
        display_to_raw.setdefault(_display, []).append(_raw)
    return display_to_raw

CARD_NAME_MAP = load_card_name_map()
DISPLAY_TO_RAW = build_display_to_raw()

@st.cache_data
def load_rag_rules() -> dict:
    """Load global RAG system rules from rag_rules.yml"""
    rules_path = os.path.join(ROOT_DIR, "prompts", "rag_rules.yml")
    with open(rules_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_system_rules_text(rules: dict) -> str:
    sr = rules["system_rules"]
    lines = [sr["role"].strip(), ""]
    lines.append("## 필수 분석 항목")
    for i, step in enumerate(sr["required_analysis_steps"], 1):
        lines.append(f"\n### 단계 {i}: {step['title']}")
        lines.append(step["description"].strip())
    lines.extend(["", "## 절대 금지 사항"])
    for p in sr["absolute_prohibitions"]:
        lines.append(f"- {p}")
    lines.extend(["", "## 출력 형식", sr["output_format"].strip()])
    return "\n".join(lines)


def md_to_html(text: str) -> str:
    return markdown.markdown(
        text,
        extensions=["tables", "nl2br", "sane_lists"],
    )


@st.cache_data
def load_card_list():
    """return sorted list of available card display names for registration"""
    try:
        info = retriever.get_db_info()
        raw_names = info.get("cards", {}).keys()
        display_names = set()
        for raw in raw_names:
            if not raw or raw == "Unknown":
                continue
            display_names.add(CARD_NAME_MAP.get(raw, raw.replace("_", " ")))
        return sorted(display_names)
    except Exception:
        return []

AVAILABLE_CARDS = load_card_list()

RAG_RULES = load_rag_rules()
SYSTEM_RULES_TEXT = build_system_rules_text(RAG_RULES)


def build_rag_chain(mbti_type: str, has_registered_cards: bool = False):
    """선택된 MBTI에 맞는 RAG 체인을 생성 (보유 카드 유무에 따라 지시문 추가)"""
    mbti_tone = MBTI_PROMPTS[mbti_type]["template"]

    now = datetime.now()
    weekday_kr = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"][now.weekday()]
    is_weekend = now.weekday() >= 5
    time_info = (
        f"[현재 시간 정보]\n"
        f"- 현재 시각 : {now.strftime('%Y-%m-%d %H:%M')} ({weekday_kr})\n"
        f"- 주말 여부 : {'주말(토/일)' if is_weekend else '평일'}\n"
        f"- 시간대 참고 : 카드 혜택 중 시간 조건(예 : 오후 9시~오전 9시, 주말 한정 등)이 있는 경우, "
        f"위 현재 시각을 기준으로 해당 혜택이 지금 적용 가능한지 안내하십시오."
    )

    if has_registered_cards:
        card_instruction = (
            "[보유 카드 우선 답변 원칙]\n"
            "사용자 보유 카드 정보는 [보유 카드 정보] 섹션에, 기타 참고 카드 정보는 [참고 카드 정보] 섹션에 제공됩니다.\n"
            "답변 순서는 아래를 따르십시오:\n\n"
            '1) [보유 카드 정보]에서 질문과 관련된 혜택을 먼저 탐색하십시오.\n'
            '   - 관련 혜택이 있으면 안내하십시오. 보유 카드를 언급할 때는 "고객님이 보유하신 [카드명]의 경우..." 형태로 시작하십시오.\n'
            '2) 보유 카드에서 관련 혜택을 찾지 못한 경우:\n'
            '   - "보유하신 카드에서는 해당 혜택을 찾지 못했습니다." 라고 먼저 안내하십시오.\n'
            '   - [참고 카드 정보]에서 관련 혜택이 있는 카드를 최대 3개까지 찾아 안내하십시오. 3개를 억지로 채울 필요는 없습니다.\n'
            '3) [참고 카드 정보]에서도 찾지 못한 경우 "관련 혜택 정보를 찾지 못했습니다." 라고 답하십시오.'
        )
    else:
        card_instruction = (
            "[카드 미등록 사용자 답변 원칙]\n"
            "사용자가 보유 카드를 등록하지 않은 상태입니다.\n"
            "답변 순서는 아래를 따르십시오:\n\n"
            '1) 답변 서두에 "마이페이지에서 보유하신 카드를 등록하시면 더 정확한 혜택 안내가 가능합니다." 라고 한 줄 안내하십시오.\n'
            '2) [추천 카드 정보]에서 질문과 관련된 혜택이 있는 카드를 최대 3개까지 찾아 안내하십시오. 3개를 억지로 채울 필요는 없습니다.\n'
            '3) 관련 카드를 찾지 못한 경우 "관련 혜택 정보를 찾지 못했습니다." 라고 답하십시오.'
        )

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_RULES_TEXT),
        ("system", mbti_tone),
        ("system", time_info + "\n\n" + card_instruction),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "[참고 자료]\n{context}\n\n[질문]\n{question}"),
    ])
    return prompt | llm | StrOutputParser()


def get_rag_answer(question: str, mbti_type: str, chat_history: list = None) -> str:
    """
    보유 카드 우선 검색하고 부족하면 일반 추천 카드를 검색하여 MBTI 맞춤 LLM 답변 생성

    흐름:
    1) 등록된 카드가 있으면 해당 카드들에서 질문 관련 청크 검색
    2) 보유 카드 결과가 충분하지 않으면 일반 검색으로 추천 카드 보충
    3) 컨텍스트를 [보유 카드 정보] / [추천 카드 정보]로 구분하여 LLM에 전달
    """
    if chat_history is None:
        chat_history = []
    registered = st.session_state.get("registered_cards", [])
    has_registered = bool(registered)

    my_context = ""
    my_card_names_found = set()

    if has_registered:
        my_card_docs = []
        for display_name in registered:
            raw_names = DISPLAY_TO_RAW.get(display_name, [display_name.replace(" ", "_")])
            for raw_name in raw_names:
                docs = retriever.search_by_metadata(question, card_name=raw_name, k=5)
                my_card_docs.extend(docs)

        if my_card_docs:
            my_context_parts = []
            for doc in my_card_docs:
                name = doc.metadata.get("card_name", "")
                text = doc.page_content[:800] if doc.page_content else ""
                if text.strip():
                    my_context_parts.append(f"[{name}]\n{text}")
                    my_card_names_found.add(name)
            my_context = "\n---\n".join(my_context_parts)

    general_results = retriever.search_with_score(question, k=5)
    recommend_parts = []
    for doc, score in general_results:
        card = doc.metadata.get("card_name", "")
        if card in my_card_names_found:
            continue
        text = doc.page_content[:800] if doc.page_content else ""
        if text.strip():
            recommend_parts.append(f"[{card}] (유사도: {score:.4f})\n{text}")
    recommend_context = "\n---\n".join(recommend_parts) if recommend_parts else ""

    if has_registered and my_context and recommend_context:
        context = f"[보유 카드 정보]\n{my_context}\n\n[참고 카드 정보]\n{recommend_context}"
    elif has_registered and my_context:
        context = f"[보유 카드 정보]\n{my_context}"
    elif recommend_context:
        context = f"[추천 카드 정보]\n{recommend_context}"
    else:
        return "죄송합니다. 관련 카드 정보를 찾지 못했습니다."

    chain = build_rag_chain(mbti_type, has_registered_cards=has_registered)
    answer = chain.invoke({"context": context, "question": question, "chat_history": chat_history})
    return answer

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user_id" not in st.session_state:
    st.session_state.user_id = None

if "sessions" not in st.session_state:
    st.session_state.sessions = []
    st.session_state.next_session_id = 0

if "active_session_id" not in st.session_state:
    st.session_state.active_session_id = None

if "selected_mbti" not in st.session_state:
    st.session_state.selected_mbti = None

if "registered_cards" not in st.session_state:
    st.session_state.registered_cards = []

if "page_mode" not in st.session_state:
    st.session_state.page_mode = "splash"  # 'splash', 'login', 'chat', 'mypage'

if "user_name" not in st.session_state:
    st.session_state.user_name = "사용자"

def _sync_from_db():
    """ session state init or update and restore user data from DB (cards, chat history)"""
    uid = st.session_state.user_id
    if uid is None:
        return
    # 보유 카드 복원
    db_cards = get_user_cards(uid)
    st.session_state.registered_cards = [c["card_name"] for c in db_cards]
    # 대화 기록 복원
    history = get_chat_history(uid, limit=500)
    if history:
        messages = [{"role": h["role"], "content": h["content"]} for h in history]
        first_user_msg = next(
            (m["content"] for m in messages if m["role"] == "user"), "이전 대화"
        )
        title = first_user_msg[:25] + ("..." if len(first_user_msg) > 25 else "")
        st.session_state.sessions = [
            {"id": 0, "title": title, "messages": messages},
        ]
        st.session_state.active_session_id = 0
        st.session_state.next_session_id = 1

def get_active_session():
    """현재 활성 세션을 반환. 없으면 None"""
    if not st.session_state.sessions:
        return None
    for s in st.session_state.sessions:
        if s["id"] == st.session_state.active_session_id:
            return s
    return st.session_state.sessions[0] if st.session_state.sessions else None

def create_new_session():
    new_id = st.session_state.next_session_id
    st.session_state.sessions.insert(0, {
        "id": new_id,
        "title": "새 대화",
        "messages": [],
    })
    st.session_state.next_session_id = new_id + 1
    st.session_state.active_session_id = new_id

# splash page
if st.session_state.page_mode == "splash":
    st.markdown(tmpl.SIDEBAR_HIDE_CSS, unsafe_allow_html=True)
    st.markdown(tmpl.SPLASH, unsafe_allow_html=True)
    time.sleep(2.2)
    st.session_state.page_mode = "login"
    st.rerun()

# login page
if not st.session_state.logged_in:
    st.markdown(tmpl.SIDEBAR_HIDE_CSS, unsafe_allow_html=True)
    col_l, col_c, col_r = st.columns([1, 2, 1])

    with col_c:
        img_base64 = tmpl.get_image_base64("PickCardU.png", ROOT_DIR)
        img_data_url = f"data:image/png;base64,{img_base64}"
        st.markdown(f'<div style="text-align: center;"><img src="{img_data_url}" style="width: 450px; height: auto; border-radius: 12px; margin-bottom: 1rem;"></div>', unsafe_allow_html=True)
        st.markdown("---")
        with st.form("login_form"):
            login_name = st.text_input("이름", placeholder="이름을 입력하세요")
            mbti_options_login = [
                f"{m} – {MBTI_PROMPTS[m]['name']}" for m in MBTI_LIST
            ]
            login_mbti_idx = st.selectbox(
                "MBTI",
                range(len(MBTI_LIST)),
                format_func=lambda i: mbti_options_login[i],
            )
            submitted = st.form_submit_button("시작하기 →", use_container_width=True)
        if submitted and login_name.strip():
            user = get_or_create_user(login_name.strip(), MBTI_LIST[login_mbti_idx])
            chosen_mbti = MBTI_LIST[login_mbti_idx]
            if user.mbti != chosen_mbti:
                update_user_mbti(user.user_id, chosen_mbti)
            st.session_state.user_id = user.user_id
            st.session_state.user_name = user.username
            st.session_state.selected_mbti = chosen_mbti
            st.session_state.logged_in = True
            _sync_from_db()
            st.session_state.page_mode = "chat"
            st.rerun()
        elif submitted:
            st.warning("이름을 입력해주세요.")
    st.stop()

with st.sidebar:
    st.markdown(tmpl.SIDEBAR_LOGO, unsafe_allow_html=True)

    st.markdown('<hr class="sidebar-divider">', unsafe_allow_html=True)

    if st.button("➕  새 대화", key="new_chat", use_container_width=True):
        create_new_session()
        st.rerun()

    st.markdown('<p class="sidebar-label-sm">이전 대화</p>', unsafe_allow_html=True)
    with st.container(height=160, border=False):
        for session in st.session_state.sessions:
            is_active = session["id"] == st.session_state.active_session_id
            label = session["title"]
            if st.button(
                label,
                key=f"session_{session['id']}",
                use_container_width=True,
            ):
                st.session_state.active_session_id = session["id"]
                st.session_state.page_mode = "chat"
                st.rerun()

    st.markdown('<hr class="sidebar-divider-lg">', unsafe_allow_html=True)

    if st.button("🪪  My Page", key="mypage_btn", use_container_width=True):
        st.session_state.page_mode = "mypage" if st.session_state.page_mode != "mypage" else "chat"
        st.rerun()

if st.session_state.page_mode == "mypage":
    st.markdown('<div class="chat-wrapper">', unsafe_allow_html=True)

    back_col, _ = st.columns([1, 8])
    with back_col:
        if st.button("← 뒤로", key="back_to_chat"):
            st.session_state.page_mode = "chat"
            st.rerun()

    mbti_label = st.session_state.selected_mbti or "미설정"
    st.markdown(
        tmpl.profile_card(html.escape(st.session_state.user_name), html.escape(mbti_label), ROOT_DIR),
        unsafe_allow_html=True,
    )

    st.markdown('<p class="mypage-section-header">MY CARD</p>', unsafe_allow_html=True)
    st.markdown('<p class="mypage-sub-label">보유 중인 카드</p>', unsafe_allow_html=True)

    if AVAILABLE_CARDS:
        addable = [c for c in AVAILABLE_CARDS if c not in st.session_state.registered_cards]
        if addable:
            col_search, col_btn = st.columns([5, 1])
            with col_search:
                selected_to_add = st.multiselect(
                    "카드 추가",
                    options=addable,
                    default=[],
                    placeholder="카드 이름을 검색하세요...",
                    label_visibility="collapsed",
                )
            with col_btn:
                if st.button("추가", key="add_cards_btn", use_container_width=True):
                    if selected_to_add:
                        for _card_display in selected_to_add:
                            add_user_card(st.session_state.user_id, _card_display, "")
                        st.session_state.registered_cards.extend(selected_to_add)
                        st.rerun()

    if st.session_state.registered_cards:
        cols = st.columns(4)
        for i, card in enumerate(st.session_state.registered_cards):
            with cols[i % 4]:
                st.markdown(tmpl.card_tile(html.escape(card)), unsafe_allow_html=True)
                if st.button("✕", key=f"remove_{i}", use_container_width=True):
                    remove_user_card_by_name(st.session_state.user_id, card)
                    st.session_state.registered_cards.pop(i)
                    st.rerun()
    else:
        st.markdown('<p class="no-cards-msg">아직 등록된 카드가 없습니다.</p>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

else:
    active_session = get_active_session()

    # 세션이 없으면 빈 chat 화면 표시
    if active_session is None:
        chat_html = '<div class="chat-wrapper"><div class="scroll-spacer"></div></div>'
    else:
        chat_html = '<div class="chat-wrapper">'
        current_mbti = st.session_state.get("selected_mbti")
        for msg in active_session["messages"]:
            if msg["role"] == "user":
                content = html.escape(msg["content"]).replace("\n", "<br>")
                chat_html += tmpl.user_bubble(content, mbti_type=current_mbti)
            else:
                content = md_to_html(msg["content"])
                chat_html += tmpl.bot_bubble(content)
        chat_html += '<div class="scroll-spacer"></div></div>'
    st.markdown(chat_html, unsafe_allow_html=True)

    user_input = st.chat_input("궁금한 점이 있다면 입력하세요.")

    if user_input and user_input.strip():
        # 세션이 없으면 새 세션 생성
        if not st.session_state.sessions:
            new_id = st.session_state.next_session_id
            st.session_state.sessions.append({
                "id": new_id,
                "title": user_input[:25] + ("..." if len(user_input) > 25 else ""),
                "messages": [],
            })
            st.session_state.next_session_id = new_id + 1
            st.session_state.active_session_id = new_id
        
        session = get_active_session()

        escaped_input = html.escape(user_input).replace("\n", "<br>")
        st.markdown(tmpl.user_bubble(escaped_input, mbti_type=st.session_state.get("selected_mbti")), unsafe_allow_html=True)

        bot_placeholder = st.empty()
        bot_placeholder.markdown(tmpl.BOT_LOADING, unsafe_allow_html=True)

        mbti = st.session_state.selected_mbti or MBTI_LIST[0]
        history_messages = []
        for m in session["messages"]:
            if m["role"] == "user":
                history_messages.append(HumanMessage(content=m["content"]))
            else:
                history_messages.append(AIMessage(content=m["content"]))
        try:
            bot_reply = get_rag_answer(user_input, mbti_type=mbti, chat_history=history_messages)
        except Exception as e:
            bot_reply = f"죄송합니다. 답변 생성 중 오류가 발생했습니다: {e}"

        rendered_reply = md_to_html(bot_reply)
        bot_placeholder.markdown(tmpl.bot_bubble(rendered_reply), unsafe_allow_html=True)

        session["messages"].append({"role": "user", "content": user_input})
        session["messages"].append({"role": "assistant", "content": bot_reply})

        if st.session_state.get("user_id"):
            save_chat_message(st.session_state.user_id, "user", user_input)
            save_chat_message(st.session_state.user_id, "assistant", bot_reply)
        st.rerun()
