"""
데이터베이스 초기화 스크립트
(처음 한 번만 실행하면 됨)

Usage:
    python src/db/init_db.py 또는 src 폴더에서 python -m db.init_db
"""

import sys
import os

# sys.path에 src 디렉토리 추가 (부모 폴더)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import init_db, get_db, SessionLocal
from db.models import User, UserCard, ChatHistory


def initialize_database():
    """데이터베이스 테이블 생성"""
    init_db()


def add_sample_data():
    """테스트용 샘플 데이터 추가"""
    db = SessionLocal()
    try:
        # 기존 데이터 확인
        user_count = db.query(User).count()
        if user_count > 0:
            print("⚠️  이미 데이터가 있습니다. 샘플 데이터 추가를 건너뜁니다.")
            return

        # 샘플 사용자 생성
        user1 = User(username="john_doe", mbti="INFP")
        user2 = User(username="jane_smith", mbti="ESTJ")

        db.add(user1)
        db.add(user2)
        db.commit()

        # 샘플 카드 정보 추가
        card1 = UserCard(user_id=user1.user_id, card_name="BC 바로클리어 플러스", company="BC카드")
        card2 = UserCard(user_id=user2.user_id, card_name="국민 체크카드", company="국민은행")

        db.add(card1)
        db.add(card2)
        db.commit()

        # 샘플 채팅 기록 추가
        chat1 = ChatHistory(user_id=user1.user_id, role="user", content="카드 정보를 알려줘")
        chat2 = ChatHistory(user_id=user1.user_id, role="assistant", content="BC 바로클리어 플러스는...")

        db.add(chat1)
        db.add(chat2)
        db.commit()

        print("✅ 샘플 데이터 추가 완료")

    except Exception as e:
        db.rollback()
        print(f"❌ 오류 발생: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    print("데이터베이스 초기화 중...")
    initialize_database()
    print("\n샘플 데이터 추가 중...")
    add_sample_data()
    print("\n✅ 모든 작업 완료!")
