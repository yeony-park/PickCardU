import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from db.models import Base

# 현재 파일의 디렉토리 (db 폴더)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# src 폴더
SRC_DIR = os.path.dirname(CURRENT_DIR)

# 프로젝트 루트 (src 폴더의 한 단계 위)
PROJECT_ROOT = os.path.dirname(SRC_DIR)

# 데이터베이스 경로 설정
DB_DIR = os.path.join(PROJECT_ROOT, "sqlite_db")
DB_PATH = os.path.join(DB_DIR, "app.db")

# 디렉토리 생성 (없으면)
os.makedirs(DB_DIR, exist_ok=True)

# SQLite 데이터베이스 연결 (상대 경로 사용)
# 절대 경로를 사용할 때 주의: sqlite:/// + 절대경로 (슬래시 4개)
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})

# 세션 설정
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """데이터베이스 테이블 생성"""
    Base.metadata.create_all(bind=engine)
    print(f"✅ 데이터베이스 초기화 완료: {DB_PATH}")


def get_db() -> Session:
    """데이터베이스 세션 반환"""
    db = SessionLocal()
    try:
        return db
    except Exception as e:
        db.close()
        raise e


def close_db(db: Session):
    """데이터베이스 세션 종료"""
    if db:
        db.close()
