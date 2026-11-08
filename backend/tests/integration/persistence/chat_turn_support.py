"""The curator's chat turn row, written as chat writes it before a turn runs (api/chat_common.py).

A chat review asks the reviewer the question of the turn that extracted a result,
read from this row. Tests that build a ChatReview or run the runner directly,
without the chat API, write it first.
"""
from src.lib.chat_history_repository import ASSISTANT_CHAT_KIND, ChatHistoryRepository
from src.models.sql.database import SessionLocal


def record_user_turn(*, owner_id, session_id, turn_id, question):
    with SessionLocal() as db:
        repository = ChatHistoryRepository(db)
        repository.get_or_create_session(session_id=session_id, user_auth_sub=owner_id,
                                         chat_kind=ASSISTANT_CHAT_KIND)
        repository.append_message(session_id=session_id, user_auth_sub=owner_id, chat_kind=ASSISTANT_CHAT_KIND,
                                  role="user", content=question, turn_id=turn_id)
        db.commit()
