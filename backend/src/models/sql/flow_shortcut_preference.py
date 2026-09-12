"""Personal main-chat flow shortcuts, independent of flow lifecycle and routing."""
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class FlowShortcutPreference(Base):
    __tablename__ = "flow_shortcut_preferences"
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    flow_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
