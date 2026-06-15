"""Statistics computation (spec §5.8, FR-60~63)."""
from datetime import date

from sqlmodel import Session, select

from ..models import Chapter, ChapterContent, ContentVersion, User


def book_stats(session: Session, book_id: int, current_user_id: int) -> dict:
    """Book-level statistics (spec FR-60/62/63, §5.8 example)."""
    chapters = session.exec(
        select(Chapter).where(
            Chapter.book_id == book_id, Chapter.deleted_at == None  # noqa: E711
        )
    ).all()
    chapter_ids = [c.id for c in chapters]
    chapter_count = len(chapters)
    completed_count = sum(1 for c in chapters if c.status == "done")

    total_words = 0
    contents = []
    if chapter_ids:
        contents = session.exec(
            select(ChapterContent).where(ChapterContent.chapter_id.in_(chapter_ids))
        ).all()
        total_words = sum(c.word_count for c in contents)

    progress = (completed_count / chapter_count) if chapter_count else 0.0

    # Goal rate (spec FR-62)
    from ..models import Book

    book = session.get(Book, book_id)
    goal = book.word_count_goal if book else None
    goal_rate = (total_words / goal) if goal else None

    # Contributors (spec FR-60): sum latest word_count per chapter, attributed
    # to the last editor of each chapter. (Simple attribution model for v1.0.)
    contrib: dict[int, int] = {}
    for c in contents:
        if c.updated_by:
            contrib[c.updated_by] = contrib.get(c.updated_by, 0) + c.word_count
    contributors = []
    for uid, words in sorted(contrib.items(), key=lambda x: -x[1]):
        u = session.get(User, uid)
        contributors.append({
            "user_id": uid,
            "name": u.name if u else "未知",
            "words": words,
            "ratio": round(words / total_words, 4) if total_words else 0.0,
        })

    # Today words (spec FR-63): per-person diff from today's version snapshots.
    today = date.today().isoformat()
    today_words = 0
    if chapter_ids:
        versions = session.exec(
            select(ContentVersion).where(
                ContentVersion.chapter_id.in_(chapter_ids),
                ContentVersion.editor_id == current_user_id,
            )
        ).all()
        # Group by chapter, compute (latest today) - (last before today).
        by_chapter: dict[int, list[ContentVersion]] = {}
        for v in versions:
            by_chapter.setdefault(v.chapter_id, []).append(v)
        for cid, vs in by_chapter.items():
            vs.sort(key=lambda v: v.version)
            todays = [v for v in vs if v.created_at[:10] == today]
            if not todays:
                continue
            before = [v for v in vs if v.created_at[:10] < today]
            base = before[-1].word_count if before else 0
            latest_today = todays[-1].word_count
            today_words += max(0, latest_today - base)

    return {
        "total_words": total_words,
        "chapter_count": chapter_count,
        "completed_count": completed_count,
        "progress": round(progress, 4),
        "goal": goal,
        "goal_rate": round(goal_rate, 4) if goal_rate is not None else None,
        "today_words": today_words,
        "contributors": contributors,
    }
