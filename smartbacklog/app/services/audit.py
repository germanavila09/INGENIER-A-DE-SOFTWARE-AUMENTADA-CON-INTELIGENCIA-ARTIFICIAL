from sqlalchemy.orm import Session

from app.models import StoryEvent, UserStory
from app.services.workflow import Actor


def record(
    db: Session,
    story: UserStory,
    actor: Actor,
    event_type: str,
    from_status: str | None = None,
    to_status: str | None = None,
    diff: dict | None = None,
) -> None:
    db.add(
        StoryEvent(
            story=story,
            actor_type=actor.kind,
            actor_name=actor.name,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            diff=diff,
        )
    )
