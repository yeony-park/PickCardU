from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session

from db.database import SessionLocal, init_db
from db.models import User, UserCard, ChatHistory

def ensure_db():
    init_db()

def get_or_create_user(username: str, mbti: Optional[str] = None) -> User:
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            user = User(username=username, mbti=mbti)
            db.add(user)
            db.commit()
            db.refresh(user)
        return user
    finally:
        db.close()

def update_user_mbti(user_id: int, mbti: str) -> None:
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user:
            user.mbti = mbti
            db.commit()
    finally:
        db.close()

def update_username(user_id: int, new_name: str) -> None:
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user:
            user.username = new_name
            db.commit()
    finally:
        db.close()

# User card CRUD
def get_user_cards(user_id: int) -> list[dict]:
    """return [{card_id, card_name, company}, ...]"""
    db: Session = SessionLocal()
    try:
        cards = db.query(UserCard).filter(UserCard.user_id == user_id).all()
        return [
            {"card_id": c.card_id, "card_name": c.card_name, "company": c.company}
            for c in cards
        ]
    finally:
        db.close()

def add_user_card(user_id: int, card_name: str, company: str) -> None:
    db: Session = SessionLocal()
    try:
        exists = (
            db.query(UserCard)
            .filter(
                UserCard.user_id == user_id,
                UserCard.card_name == card_name,
            )
            .first()
        )
        if not exists:
            db.add(UserCard(user_id=user_id, card_name=card_name, company=company))
            db.commit()
    finally:
        db.close()

def remove_user_card(card_id: int) -> None:
    db: Session = SessionLocal()
    try:
        card = db.query(UserCard).filter(UserCard.card_id == card_id).first()
        if card:
            db.delete(card)
            db.commit()
    finally:
        db.close()


def remove_user_card_by_name(user_id: int, card_name: str) -> None:
    """user_id + card_name 으로 카드 삭제"""
    db: Session = SessionLocal()
    try:
        card = (
            db.query(UserCard)
            .filter(UserCard.user_id == user_id, UserCard.card_name == card_name)
            .first()
        )
        if card:
            db.delete(card)
            db.commit()
    finally:
        db.close()


# Chat history CRUD
def get_chat_history(user_id: int, limit: int = 100) -> list[dict]:
    """return [{chat_id, role, content, created_at}, ...]"""
    db: Session = SessionLocal()
    try:
        rows = (
            db.query(ChatHistory)
            .filter(ChatHistory.user_id == user_id)
            .order_by(ChatHistory.created_at.asc())
            .limit(limit)
            .all()
        )
        return [
            {
                "chat_id": r.chat_id,
                "role": r.role,
                "content": r.content,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()

def save_chat_message(user_id: int, role: str, content: str) -> None:
    db: Session = SessionLocal()
    try:
        db.add(ChatHistory(user_id=user_id, role=role, content=content))
        db.commit()
    finally:
        db.close()

def clear_chat_history(user_id: int) -> None:
    db: Session = SessionLocal()
    try:
        db.query(ChatHistory).filter(ChatHistory.user_id == user_id).delete()
        db.commit()
    finally:
        db.close()

def extract_company_from_display(display_name: str) -> str:
    """Extract company from display name (e.g., 'KB국민카드 쿠팡와우' → 'KB국민카드')"""
    parts = display_name.split(" ", 1)
    return parts[0] if parts else display_name
