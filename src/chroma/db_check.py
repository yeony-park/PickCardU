"""
ChromaDB 웹 대시보드 - Streamlit
http://localhost:8501 에서 접속하여 ChromaDB의 내용을 시각적으로 탐색합니다.
"""

import streamlit as st
import sys
import os
from pathlib import Path

# src 디렉토리를 Python path에 추가
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / "src"))

from retrieval import CardRetriever
import pandas as pd

st.set_page_config(page_title="카드 조회 대시보드", layout="wide")

# 제목 및 설명
st.title("카드 정보 조회 대시보드")
st.markdown("ChromaDB에 저장된 카드 정보를 검색하고 탐색합니다.")

# Retriever 초기화 (캐시에 저장)
@st.cache_resource
def load_retriever():
    return CardRetriever()

retriever = load_retriever()

# 사이드바
with st.sidebar:
    st.header("⚙️ 설정")
    search_type = st.radio("검색 방식", ["유사도 검색", "점수 포함 검색"])
    top_k = st.slider("조회 결과 수", 1, 10, 5)
    use_grouping = st.checkbox("카드별 그룹화 검색 (중복 제거)", value=False)
    use_batch = st.checkbox("배치 검색 (다중 쿼리)", value=False)
    
    # DB 정보
    st.divider()
    st.subheader("📊 DB 정보")
    info = retriever.get_db_info()
    col1, col2 = st.columns(2)
    with col1:
        st.metric("총 청크", info["total_chunks"])
    with col2:
        st.metric("총 카드", info["total_cards"])

# 탭 구성
tab1, tab2, tab3 = st.tabs(["🔍 검색", "📋 카드 목록", "📈 통계"])

# ===== TAB 1: 검색 =====
with tab1:
    st.subheader("카드 정보 검색")
    
    # 검색 입력 UI
    with st.form("search_form"):
        if use_batch:
            query_input = st.text_area(
                "검색어를 입력하세요 (줄바꿈으로 여러 쿼리 입력 가능)",
                placeholder="예:\n여행 혜택\n카페 할인\n마일리지",
                height=100
            )
            search_btn = st.form_submit_button("🔎 배치 검색", use_container_width=True)
        else:
            col1, col2 = st.columns([3, 1])
            with col1:
                query_input = st.text_input("검색어를 입력하세요", placeholder="예: 편의점 할인, 스타벅스, 마일리지")
            with col2:
                search_btn = st.form_submit_button("🔎 검색", use_container_width=True)
    
    if search_btn and query_input:
        try:
            with st.spinner("검색 중..."):
                if use_batch:
                    # 배치 검색: 여러 쿼리 처리
                    queries = [q.strip() for q in query_input.split('\n') if q.strip()]
                    if not queries:
                        st.warning("⚠️ 검색어를 입력해주세요.")
                    else:
                        batch_results = retriever.batch_search(queries, k=top_k, deduplicate=True)
                        
                        if not batch_results:
                            st.info("🔍 검색 결과가 없습니다. 다른 검색어로 시도해보세요.")
                        else:
                            st.success(f"✅ {len(batch_results)}개의 결과를 찾았습니다. (쿼리: {len(queries)}개)")
                            
                            # CSV 다운로드
                            csv_data = []
                            for idx, (doc, score, source) in enumerate(batch_results, 1):
                                csv_data.append({
                                    "순번": idx,
                                    "카드명": doc.metadata.get('card_name', 'N/A'),
                                    "점수": f"{score:.4f}",
                                    "출처": doc.metadata.get('source', 'N/A'),
                                    "타입": doc.metadata.get('type', 'N/A'),
                                    "내용": doc.page_content[:200]
                                })
                            csv_df = pd.DataFrame(csv_data)
                            csv = csv_df.to_csv(index=False, encoding='utf-8-sig')
                            st.download_button("📥 CSV 다운로드", csv, "search_results.csv", "text/csv")
                            
                            # 결과 표시
                            SNIPPET_LEN = 250
                            for idx, (doc, score, source) in enumerate(batch_results, 1):
                                full_text = doc.page_content or ""
                                if len(full_text) <= SNIPPET_LEN:
                                    snippet = full_text
                                else:
                                    snippet = full_text[:SNIPPET_LEN].rsplit(" ", 1)[0] + "..."
                                
                                with st.container():
                                    col_title, col_score = st.columns([4, 1])
                                    with col_title:
                                        st.markdown(f"**[{idx}] {doc.metadata.get('card_name', 'N/A')}**")
                                    with col_score:
                                        st.metric("점수", f"{score:.4f}")
                                    
                                    st.markdown(f"**출처:** {doc.metadata.get('source', 'N/A')}")
                                    st.markdown(f"**타입:** {doc.metadata.get('type', 'N/A')}")
                                    st.markdown("---")
                                    st.text_area("요약", snippet, height=120, disabled=True, key=f"doc_snip_{idx}")
                                    with st.expander("전체 보기", expanded=False):
                                        st.text_area("전체 내용", full_text, height=300, disabled=True, key=f"doc_full_{idx}")
                else:
                    # 단일 검색
                    if use_grouping:
                        results = retriever.search_grouped(query_input, k=top_k)
                    else:
                        if search_type == "유사도 검색":
                            results = retriever.search(query_input, k=top_k)
                        else:
                            results = retriever.search_with_score(query_input, k=top_k)
                    
                    if not results:
                        st.info("🔍 검색 결과가 없습니다. 다른 검색어로 시도해보세요.")
                    else:
                        st.success(f"✅ {len(results)}개의 결과를 찾았습니다." + (" (카드별 그룹화)" if use_grouping else ""))
                        
                        # CSV 다운로드
                        csv_data = []
                        for idx, item in enumerate(results, 1):
                            score = None
                            if isinstance(item, (list, tuple)) and len(item) == 2:
                                doc, score = item
                            else:
                                doc = item
                            
                            csv_data.append({
                                "순번": idx,
                                "카드명": doc.metadata.get('card_name', 'N/A'),
                                "점수": f"{score:.4f}" if score else "N/A",
                                "출처": doc.metadata.get('source', 'N/A'),
                                "타입": doc.metadata.get('type', 'N/A'),
                                "내용": doc.page_content[:200] if doc.page_content else ""
                            })
                        csv_df = pd.DataFrame(csv_data)
                        csv = csv_df.to_csv(index=False, encoding='utf-8-sig')
                        st.download_button("📥 CSV 다운로드", csv, "search_results.csv", "text/csv")
                        
                        # 결과 표시
                        SNIPPET_LEN = 250
                        for idx, item in enumerate(results, 1):
                            score = None
                            if isinstance(item, (list, tuple)) and len(item) == 2:
                                doc, score = item
                            else:
                                doc = item

                            full_text = doc.page_content or ""
                            if len(full_text) <= SNIPPET_LEN:
                                snippet = full_text
                            else:
                                snippet = full_text[:SNIPPET_LEN].rsplit(" ", 1)[0] + "..."

                            with st.container():
                                col_title, col_score = st.columns([4, 1])
                                with col_title:
                                    st.markdown(f"**[{idx}] {doc.metadata.get('card_name', 'N/A')}**")
                                with col_score:
                                    if score is not None:
                                        st.metric("유사도", f"{score:.4f}")

                                st.markdown(f"**출처:** {doc.metadata.get('source', 'N/A')}")
                                st.markdown(f"**타입:** {doc.metadata.get('type', 'N/A')}")
                                st.markdown("---")

                                st.text_area("요약", snippet, height=120, disabled=True, key=f"doc_snip_{idx}")

                                with st.expander("전체 보기", expanded=False):
                                    st.text_area("전체 내용", full_text, height=300, disabled=True, key=f"doc_full_{idx}")
        
        except Exception as e:
            st.error(f"❌ 검색 중 오류가 발생했습니다:\n{str(e)}")
            st.info("💡 다른 검색어로 시도하거나, 잠시 후 다시 시도해주세요.")

# ===== TAB 2: 카드 목록 =====
with tab2:
    st.subheader("📋 저장된 모든 카드")
    
    info = retriever.get_db_info()
    cards = info['cards']
    
    # 데이터프레임 생성
    df = pd.DataFrame(list(cards.items()), columns=['카드명', '청크 수'])
    df = df.sort_values('청크 수', ascending=False)
    
    # 통계
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("총 카드 수", len(cards))
    with col2:
        st.metric("평균 청크 수", f"{df['청크 수'].mean():.1f}")
    with col3:
        st.metric("최대 청크 수", df['청크 수'].max())
    
    st.divider()
    
    # CSV 다운로드
    csv = df.to_csv(index=False, encoding='utf-8-sig')
    st.download_button("📥 카드 목록 CSV 다운로드", csv, "card_list.csv", "text/csv")
    
    # 테이블
    st.dataframe(df, use_container_width=True, hide_index=True)
    
    # 차트
    st.bar_chart(df.set_index('카드명')['청크 수'].head(15))

# ===== TAB 3: 통계 =====
with tab3:
    st.subheader("📈 ChromaDB 통계")
    
    info = retriever.get_db_info()
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("총 청크", info["total_chunks"])
    with col2:
        st.metric("총 카드", info["total_cards"])
    with col3:
        st.metric("DB 위치", "/app/chroma_db")
    with col4:
        avg_chunks = info["total_chunks"] / max(info["total_cards"], 1)
        st.metric("카드당 평균 청크", f"{avg_chunks:.1f}")
    
    st.divider()
    
    # 카드별 청크 분포
    st.subheader("카드별 청크 분포")
    df_dist = pd.DataFrame(list(info['cards'].items()), columns=['카드', '청크'])
    df_dist = df_dist.sort_values('청크', ascending=False).head(20)
    st.bar_chart(df_dist.set_index('카드')['청크'])
    
    st.divider()
    
    # 상위 카드
    st.subheader("Top 10 카드 (청크 기준)")
    top_cards = sorted(info['cards'].items(), key=lambda x: x[1], reverse=True)[:10]
    for rank, (card, count) in enumerate(top_cards, 1):
        st.write(f"{rank}. **{card}**: {count}개 청크")
