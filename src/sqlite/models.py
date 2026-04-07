from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()


class User(Base):
    """사용자 정보 테이블"""
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), nullable=False, unique=True)
    mbti = Column(String(10), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # 관계 설정
    user_cards = relationship("UserCard", back_populates="user", cascade="all, delete-orphan")
    chat_histories = relationship("ChatHistory", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(user_id={self.user_id}, username='{self.username}', mbti='{self.mbti}')>"


class UserCard(Base):
    """사용자 카드 정보 테이블"""
    __tablename__ = "user_cards"

    card_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    card_name = Column(String(100), nullable=False)
    company = Column(String(100), nullable=False)

    # 관계 설정
    user = relationship("User", back_populates="user_cards")

    def __repr__(self):
        return f"<UserCard(card_id={self.card_id}, card_name='{self.card_name}', company='{self.company}')>"


class ChatHistory(Base):
    """채팅 히스토리 테이블"""
    __tablename__ = "chat_history"

    chat_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    role = Column(String(50), nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # 관계 설정
    user = relationship("User", back_populates="chat_histories")

    def __repr__(self):
        return f"<ChatHistory(chat_id={self.chat_id}, role='{self.role}', created_at={self.created_at})>"
