"""
tests/test_notifications.py — Mixtape

Regression test for Issue #4: rating a song should notify the sharer,
the same way adding it to a playlist does.
"""

import pytest
from app import create_app, db
from models import User, Song
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def sharer_and_rater(app):
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        rater = User(username="rater", email="rater@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(title="Test Song", artist="Test Artist", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        yield {"sharer": sharer, "rater": rater, "song": song}


def test_rating_notifies_song_sharer(app, sharer_and_rater):
    """Rating a song should create a notification for the original sharer."""
    with app.app_context():
        sharer_id = sharer_and_rater["sharer"].id
        rater_id = sharer_and_rater["rater"].id
        song_id = sharer_and_rater["song"].id

        assert get_notifications(sharer_id) == []

        rate_song(rater_id, song_id, 5)

        notifications = get_notifications(sharer_id)
        assert len(notifications) == 1
        assert notifications[0]["type"] == "song_rated"


def test_rating_own_song_does_not_notify(app, sharer_and_rater):
    """A user rating their own song should not receive a self-notification."""
    with app.app_context():
        sharer_id = sharer_and_rater["sharer"].id
        song_id = sharer_and_rater["song"].id

        rate_song(sharer_id, song_id, 4)

        assert get_notifications(sharer_id) == []