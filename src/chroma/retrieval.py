"""
Retrieval Strategy - ChromaDB에서 관련 카드 정보 검색
사용자 쿼리를 받아 유사도가 높은 카드 정보 문서를 반환합니다.
"""

import os
import sys
import logging
from typing import List, Dict, Optional, Tuple
from collections import OrderedDict
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
import chromadb

# embedding.py의 함수들을 import하기 위해 경로 설정
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from data_loader import load_pdfs_as_documents
from chunking import chunk_documents

load_dotenv()

# 로깅 설정
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# 구성 (환경 변수로 오버라이드 가능)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
MAX_SEARCH_CACHE = int(os.getenv("MAX_SEARCH_CACHE", "128"))
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "langchain")

# 로컬 실행 기준 경로 설정
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROMA_DB_PATH = os.path.join(BASE_DIR, "chroma_db")
CLEAN_DATA_PATH = os.path.join(BASE_DIR, "data", "clean_data")
OCR_TXT_PATH = os.path.join(BASE_DIR, "data", "ocr_output")


class CardRetriever:
    """
    카드 정보 검색 시스템
    ChromaDB에서 사용자 쿼리와 유사한 카드 정보를 검색합니다.
    """
    
    def __init__(self, db_path: str = CHROMA_DB_PATH, search_type: str = "similarity"):
        """
        Parameters:
            db_path: ChromaDB 저장 경로
            search_type: 검색 타입 ("similarity" 또는 "mmr")
        """
        self.db_path = db_path
        self.embeddings = OpenAIEmbeddings(
            model=EMBEDDING_MODEL,
            api_key=os.getenv("OPENAI_API_KEY")
        )
        
        # ChromaDB 로드 또는 생성
        self.vectorstore = self._init_or_create_vectorstore()
        
        # Retriever 생성 (기본: 유사도 검색, k=5)
        self.retriever = self.vectorstore.as_retriever(
            search_type=search_type,
            search_kwargs={"k": 5}
        )

        # 간단한 LRU 캐시 (인스턴스 레벨)
        self._search_cache: OrderedDict = OrderedDict()
        self._max_cache = MAX_SEARCH_CACHE
    
    def _init_or_create_vectorstore(self):
        """
        ChromaDB를 초기화하거나 생성합니다.
        빈 DB인 경우 데이터를 자동으로 로드하고 저장합니다.
        """
        # ChromaDB 클라이언트 (PersistentClient 사용)
        try:
            client = chromadb.PersistentClient(path=self.db_path)
            collections = client.list_collections()

            # 안전하게 컬렉션 확인
            if collections and len(collections) > 0:
                # 시도: 지정 컬렉션 이름으로 찾기, 없으면 첫 컬렉션 사용
                selected = None
                for c in collections:
                    if getattr(c, "name", None) == CHROMA_COLLECTION_NAME:
                        selected = c
                        break
                if selected is None:
                    selected = collections[0]

                try:
                    if selected.count() > 0:
                        logger.info("기존 ChromaDB 로드 성공 (데이터 존재)")
                        vectorstore = Chroma(
                            persist_directory=self.db_path,
                            embedding_function=self.embeddings,
                            client=client
                        )
                        return vectorstore
                except Exception:
                    logger.warning("컬렉션 카운트 확인 중 오류가 발생했습니다. 데이터 재생성 시도")

            logger.info("ChromaDB가 비어있거나 접근 불가합니다. 데이터를 로드합니다...")
            return self._load_and_store_data()

        except Exception as e:
            logger.warning(f"기존 ChromaDB 로드 실패: {e}")
            logger.info("새로운 ChromaDB를 생성합니다...")
            return self._load_and_store_data()
    
    def _load_and_store_data(self):
        """
        데이터를 로드하고 ChromaDB에 저장합니다.
        """
        logger.info("데이터 로드 및 임베딩 시작...")
        
        # 1. 데이터 로드
        logger.info("PDF 파일 로드 중...")
        clean_docs = load_pdfs_as_documents(CLEAN_DATA_PATH)
        
        logger.info("OCR 텍스트 파일 로드 중...")
        ocr_docs = self._load_ocr_txt_as_documents(OCR_TXT_PATH)
        
        all_docs = clean_docs + ocr_docs
        logger.info(f"전체 문서 수: {len(all_docs)}")
        
        if len(all_docs) == 0:
            raise ValueError("❌ 로드된 문서가 없습니다!")
        
        # 2. 청킹
        logger.info("텍스트 분할 중...")
        chunked_docs = chunk_documents(all_docs)
        logger.info(f"청크 수: {len(chunked_docs)}")
        
        # 3. 임베딩 및 저장
        logger.info("ChromaDB에 저장 중...")
        vectorstore = Chroma.from_documents(
            documents=chunked_docs,
            embedding=self.embeddings,
            persist_directory=self.db_path
        )
        logger.info(f"총 {len(chunked_docs)}개 청크 저장 완료!")
        return vectorstore
    
    @staticmethod
    def _load_ocr_txt_as_documents(folder_path: str):
        """OCR 텍스트 파일을 Document로 로드"""
        docs = []
        if not os.path.exists(folder_path):
            return docs
        
        for filename in os.listdir(folder_path):
            if filename.lower().endswith(".txt"):
                filepath = os.path.join(folder_path, filename)
                with open(filepath, "r", encoding="utf-8") as f:
                    text = f.read().strip()
                if not text:
                    continue
                
                doc = Document(
                    page_content=text,
                    metadata={
                        "source": filename,
                        "card_name": os.path.splitext(filename)[0],
                        "type": "ocr"
                    }
                )
                docs.append(doc)
        
        logger.info(f"OCR txt 문서 수: {len(docs)}")
        return docs
    
    def search(self, query: str, k: int = 5) -> List[Document]:
        """
        기본 유사도 검색
        
        Parameters:
            query: 사용자 질의
            k: 반환할 상위 문서 수 (기본값: 5)
            
        Returns:
            관련 카드 정보 문서 리스트
        """
        # 캐시 확인
        key = ("sim", query, k)
        cached = self._cache_get(key)
        if cached is not None:
            logger.debug("검색 캐시 히트")
            return cached

        results = self.vectorstore.similarity_search(query, k=k)
        self._cache_set(key, results)
        return results
    
    def search_with_score(self, query: str, k: int = 5) -> List[tuple]:
        """
        유사도 점수와 함께 검색
        
        Parameters:
            query: 사용자 질의
            k: 반환할 상위 문서 수
            
        Returns:
            [(document, score), ...] 형태의 리스트
        """
        key = ("score", query, k)
        cached = self._cache_get(key)
        if cached is not None:
            logger.debug("검색(점수) 캐시 히트")
            return cached

        results = self.vectorstore.similarity_search_with_score(query, k=k)
        self._cache_set(key, results)
        return results

    def search_grouped(self, query: str, k: int = 5, candidates_multiplier: int = 3) -> List[Tuple[Document, float]]:
        """
        카드명으로 그룹화하여 카드당 최고 유사도 청크 하나만 반환합니다.
        - candidates_multiplier: 초기 후보 수를 k * multiplier 만큼 가져와 그룹화 후 상위 k개를 반환
        Returns: [(Document, score), ...]
        """
        search_k = max(k * candidates_multiplier, k)
        logger.info(f"그룹화 검색: query='{query}', candidates={search_k}, top_k={k}")
        raw = self.search_with_score(query, k=search_k)

        grouped: Dict[str, Tuple[Document, float]] = {}
        for doc, score in raw:
            card = doc.metadata.get("card_name", "Unknown")
            # 높은 점수 우선
            if card not in grouped or score > grouped[card][1]:
                grouped[card] = (doc, score)

        # 그룹에서 score 기준으로 정렬 후 상위 k개 반환
        sorted_items = sorted(grouped.values(), key=lambda x: x[1], reverse=True)
        return sorted_items[:k]
    
    def search_by_metadata(self, query: str, card_name: str = None, doc_type: str = None, k: int = 5) -> List[Document]:
        """
        메타데이터 필터링과 함께 검색
        특정 카드 또는 문서 타입으로 검색 범위를 좁힐 수 있습니다.
        
        Parameters:
            query: 사용자 질의
            card_name: 카드명으로 필터링 (예: "BC Green Card")
            doc_type: 문서 타입 필터링 ("clean" 또는 "ocr")
            k: 반환할 상위 문서 수
            
        Returns:
            필터링된 관련 문서 리스트
        """
        # 메타데이터 필터 구성
        where_filter = {}
        if card_name:
            where_filter["card_name"] = {"$eq": card_name}
        if doc_type:
            where_filter["type"] = {"$eq": doc_type}
        
        # 필터 적용 검색
        if where_filter:
            results = self.vectorstore.similarity_search(
                query, 
                k=k,
                filter=where_filter
            )
        else:
            results = self.vectorstore.similarity_search(query, k=k)
        
        return results
    
    def get_retriever(self, search_type: str = "similarity", k: int = 5):
        """
        LangChain Retriever 객체 반환
        다른 LangChain 컴포넌트와 연동할 때 사용합니다.
        
        Parameters:
            search_type: "similarity" 또는 "mmr"
            k: 반환할 문서 수
            
        Returns:
            LangChain Retriever 객체
        """
        return self.vectorstore.as_retriever(
            search_type=search_type,
            search_kwargs={"k": k}
        )
    
    def get_db_info(self) -> dict:
        """
        ChromaDB 기본 정보 조회
        """
        try:
            client = chromadb.PersistentClient(path=self.db_path)
            collections = client.list_collections()
            
            if not collections or len(collections) == 0:
                return {
                    "total_chunks": 0,
                    "total_cards": 0,
                    "cards": {},
                    "db_path": self.db_path
                }
            
            collection = collections[0]
            try:
                count = collection.count()
            except Exception:
                logger.warning("컬렉션 카운트 조회 실패, 대략적 개수로 0 처리")
                count = 0
            
            # 카드별 청크 수 계산
            results = collection.get(include=["metadatas"])
            card_counts = {}
            for metadata in results['metadatas']:
                card_name = metadata.get('card_name', 'Unknown')
                card_counts[card_name] = card_counts.get(card_name, 0) + 1
            
            return {
                "total_chunks": count,
                "total_cards": len(card_counts),
                "cards": card_counts,
                "db_path": self.db_path
            }
        except Exception as e:
            logger.error(f"DB 정보 조회 실패: {e}")
            return {
                "total_chunks": 0,
                "total_cards": 0,
                "cards": {},
                "db_path": self.db_path
            }

    def batch_search(self, queries: List[str], k: int = 5, deduplicate: bool = True) -> List[Tuple[Document, float, str]]:
        """
        여러 쿼리를 한 번에 검색하고 결과를 병합합니다.
        MBTI별 여러 관련 쿼리("여행 혜택", "카페 할인" 등)를 동시 처리하는 데 유용합니다.
        
        Parameters:
            queries: 검색 쿼리 리스트
            k: 각 쿼리당 상위 문서 수
            deduplicate: True면 중복 카드 제거 후 점수 평균화
            
        Returns:
            [(Document, avg_score, source_query), ...] 형태의 통합 결과
        """
        logger.info(f"배치 검색 시작: {len(queries)}개 쿼리")
        
        # 모든 쿼리에 대해 검색 실행
        all_results = {}  # {(card_name, source): (Document, scores_list)}
        
        for query in queries:
            results = self.search_with_score(query, k=k)
            for doc, score in results:
                card = doc.metadata.get("card_name", "Unknown")
                key = (card, query)
                if key not in all_results:
                    all_results[key] = (doc, [])
                all_results[key][1].append(score)
        
        # 중복 제거 및 점수 평균화
        if deduplicate:
            dedup_results = {}  # {card_name: (Document, avg_score)}
            for (card, _), (doc, scores) in all_results.items():
                avg_score = sum(scores) / len(scores)
                if card not in dedup_results or avg_score > dedup_results[card][1]:
                    dedup_results[card] = (doc, avg_score)
            
            # (Document, avg_score, "batch") 형태로 반환
            result_list = [(doc, score, "batch") for doc, score in dedup_results.values()]
        else:
            result_list = [(doc, scores[0], query) for (card, query), (doc, scores) in all_results.items()]
        
        # 점수 기준 정렬
        result_list.sort(key=lambda x: x[1], reverse=True)
        logger.info(f"배치 검색 완료: {len(result_list)}개 결과")
        return result_list

    def format_for_llm(self, results: List[Tuple[Document, float]], max_chars: int = 300) -> str:
        """
        검색 결과를 LLM 프롬프트에 직접 삽입 가능한 형식으로 변환합니다.
        
        Parameters:
            results: [(Document, score), ...] 형태의 검색 결과
            max_chars: 각 청크 최대 문자 수 (긴 청크는 자동 축약)
            
        Returns:
            LLM 프롬프트용 포맷된 문자열
        """
        if not results:
            return "[검색 결과 없음]"
        
        formatted_lines = []
        for idx, (doc, score) in enumerate(results, 1):
            card = doc.metadata.get("card_name", "Unknown")
            content = doc.page_content or ""
            
            # 길이 제한
            if len(content) > max_chars:
                content = content[:max_chars].rsplit(" ", 1)[0] + "..."
            
            # 형식화
            line = f"[{idx}] {card}\n점수: {score:.4f}\n내용: {content}\n---"
            formatted_lines.append(line)
        
        return "\n".join(formatted_lines)

    def search_by_keywords(self, query: str, keywords: Optional[List[str]] = None, k: int = 5) -> List[Tuple[Document, float]]:
        """
        키워드 기반 카테고리 필터링을 통한 검색.
        예: keywords=["여행", "항공"] → 해당 카드명이나 내용에 포함된 결과만 반환
        
        Parameters:
            query: 사용자 질의
            keywords: 포함할 키워드 리스트 (None이면 필터링 없음)
            k: 반환할 상위 문서 수
            
        Returns:
            [(Document, score), ...] 형태의 필터링된 결과
        """
        results = self.search_with_score(query, k=k * 3)  # 충분한 후보 가져오기
        
        if not keywords:
            return results[:k]
        
        # 키워드 필터링
        filtered = []
        for doc, score in results:
            card = doc.metadata.get("card_name", "").lower()
            content = doc.page_content.lower() if doc.page_content else ""
            
            # 하나 이상의 키워드 포함 여부 확인
            if any(kw.lower() in card or kw.lower() in content for kw in keywords):
                filtered.append((doc, score))
        
        logger.info(f"키워드 필터링: {len(results)} → {len(filtered)} 결과")
        return filtered[:k] if filtered else results[:k]  # 없으면 필터링 없이 반환

    # -----------------
    # 내부 캐시 헬퍼
    # -----------------
    def _cache_get(self, key):
        val = self._search_cache.get(key)
        if val is not None:
            # LRU: 이동
            self._search_cache.move_to_end(key)
        return val

    def _cache_set(self, key, value):
        self._search_cache[key] = value
        self._search_cache.move_to_end(key)
        if len(self._search_cache) > self._max_cache:
            # 오래된 항목 제거
            removed = self._search_cache.popitem(last=False)
            logger.debug(f"검색 캐시 제거: {removed[0]}")


def example_searches():
    """검색 예제"""
    print("\n" + "="*80)
    print("🔍 카드 검색 시스템 테스트")
    print("="*80)
    
    # Retriever 초기화
    retriever = CardRetriever()
    
    # DB 정보 출력
    info = retriever.get_db_info()
    print(f"\n📊 DB 정보:")
    print(f"   총 청크: {info['total_chunks']}")
    print(f"   총 카드: {info['total_cards']}")
    
    # 검색 예제 1: 기본 검색
    print(f"\n{'='*80}")
    print(f"[예제 1] 편의점 할인 검색")
    print(f"{'='*80}")
    results = retriever.search("편의점 할인", k=3)
    for idx, doc in enumerate(results, 1):
        print(f"\n[{idx}] 카드명: {doc.metadata.get('card_name', 'N/A')}")
        print(f"    출처: {doc.metadata.get('source', 'N/A')}")
        print(f"    내용: {doc.page_content[:100]}...")
    
    # 검색 예제 2: 유사도 점수 포함
    print(f"\n{'='*80}")
    print(f"[예제 2] 스타벅스 혜택 검색 (점수 포함)")
    print(f"{'='*80}")
    results = retriever.search_with_score("스타벅스 카페 할인", k=3)
    for idx, (doc, score) in enumerate(results, 1):
        print(f"\n[{idx}] 유사도: {score:.4f}")
        print(f"    카드명: {doc.metadata.get('card_name', 'N/A')}")
        print(f"    출처: {doc.metadata.get('source', 'N/A')}")
        print(f"    내용: {doc.page_content[:100]}...")
    
    # 검색 예제 3: 메타데이터 필터링
    print(f"\n{'='*80}")
    print(f"[예제 3] OCR 문서만 검색")
    print(f"{'='*80}")
    results = retriever.search_by_metadata("마일리지 적립", doc_type="ocr", k=3)
    for idx, doc in enumerate(results, 1):
        print(f"\n[{idx}] 카드명: {doc.metadata.get('card_name', 'N/A')}")
        print(f"    타입: {doc.metadata.get('type', 'N/A')}")
        print(f"    내용: {doc.page_content[:100]}...")
    
    print(f"\n{'='*80}")
    print("✨ 테스트 완료!")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    try:
        example_searches()
    except Exception as e:
        print(f"❌ 오류: {e}")
        import traceback
        traceback.print_exc()
