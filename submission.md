# Mixtape Bug Hunt — Submission

## AI Usage

I used Claude to explain the code, each services, functions, what they do, who they interacted with on a need-to-know basis. Claude was promted to act as a guide and help looking through the code and pointing it logic flaws, syntax errors and/or inconsistencies. Then I used it to work through possible solutions. There are many things here I do not know and I am learning retroactively like  SQLAlchemy
---

## Codebase Map

**Main files and their roles:**

- `app.py` — Flask application factory (`create_app`). Configures the SQLite
  DB via SQLAlchemy, registers all four blueprints with URL prefixes
  (`/songs`, `/playlists`, `/users`, `/feed`), and creates tables on startup.
- `models.py` — SQLAlchemy models: `User`, `Song`, `Tag`, `ListeningEvent`,
  `Rating`, `Playlist`, `Notification`. Three association tables:
  `friendships` (symmetric many-to-many between users), `song_tags`
  (many-to-many between songs and tags), and `playlist_entries` (many-to-many
  between playlists and songs, with an explicit `position` integer column —
  song order in a playlist is stored explicitly, not inferred from insertion
  order).
- `routes/` — one blueprint per resource (`songs.py`, `playlists.py`,
  `users.py`, `feed.py`). Routes parse the request, call a service function,
  and format the JSON response. No business logic lives here.
- `services/` — all business logic. `streak_service.py` (listening streaks),
  `feed_service.py` (friends listening now / activity feed),
  `search_service.py` (song search), `notification_service.py`
  (notifications + rating), `playlist_service.py` (playlist retrieval).

**Data flow — a user listens to a song:**

`POST /songs/<song_id>/listen` → `routes/songs.py: listen()` → 
`streak_service.record_listening_event(user_id, song_id)` → creates a new
`ListeningEvent` row with `listened_at = datetime.now(timezone.utc)`, then
calls `update_listening_streak(user, now)` to update `User.listening_streak`
and `User.last_listened_at`, then commits both changes in one transaction.

**Data flow — a friend checks "Friends Listening Now":**

`GET /feed/<user_id>/listening-now` → `routes/feed.py: listening_now()` →
`feed_service.get_friends_listening_now(user_id)` → looks up the user's
`friends`, queries `ListeningEvent` for events by those friends within
`RECENT_THRESHOLD` of now, sorts newest-first, then keeps only the single
most recent event per friend (dedup via a `seen_friends` set) → returns a
list of `{friend, song, listened_at}` dicts.

**Pattern noticed:** every route delegates immediately to a service
function and wraps the call in a `try/except ValueError` to convert
service-level validation errors into `404`/`400` JSON responses. All
business logic and all five bugs live in `services/`, exactly as the README
states.


---

## Root Cause Analysis

### Issue #1 — My listening streak keeps resetting

**How I reproduced it:** Traced `record_listening_event` →
`update_listening_streak` in `streak_service.py`. Read through the streak
rules in the docstring and compared them against the actual `if/elif/else`
branches for the `days_since_last == 1` case.

**How I found the root cause:** Read the condition
`elif days_since_last == 1 and today.weekday() != 6:` line by line and
checked what `datetime.weekday()` returns for each day of the week.

**The root cause:**
the weird sunday clause caused the code to hit a wall on actual sundays and reset the streak to 0.

**My fix and side-effect check:** Removed the `and today.weekday() != 6`
condition so the `elif days_since_last == 1:` branch increments the streak
on every day of the week, including Sunday.
I confirmed the other branches
(days_since_last == 0 → no change, and days_since_last > 1 → reset to
are untouched, so a genuinely skipped day still correctly resets the
streak. Verified via the pre-existing test suite:
test_streak_starts_at_1_for_new_user,
test_streak_increments_on_consecutive_day,
test_streak_does_not_double_count_same_day, and
test_streak_resets_after_skipped_day all still pass alongside the
now-passing test_streak_increments_on_sunday.

### Issue #2 — Friends Listening Now shows people from yesterday

**How I reproduced it:** Hit `GET /feed/<user_id>/listening-now` for `nova`
via the browser. Used `flask shell` and a throwaway script
(`repro_issue2.py`) to inspect real `ListeningEvent` rows and their ages.
Manually inserted a controlled 3-day-old event to test the boundary.

**How I found the root cause:** Initially suspected a timezone mismatch
(DB stores naive datetimes via SQLite, but `cutoff` is built with
`datetime.now(timezone.utc)`, which is timezone-aware — confirmed
`e.listened_at.tzinfo` prints `None`, and that comparing two such values
directly in Python raises `TypeError: can't compare offset-naive and
offset-aware datetimes`). Tested this against the real SQLAlchemy-filtered
query and found the 24-hour cutoff was in fact filtering correctly —
disproving that theory. Re-read the seed data comments literally
(`# Recent events (within the past 30 minutes)` vs.
`# Older events (1-14 days ago) ... should NOT appear ... after fix`) and
compared them against the `RECENT_THRESHOLD = timedelta(hours=24)` constant
in `feed_service.py`.

**The root cause:**

for that friends listening now, running a 24 hour check doesnt do what we want it to do as a function. if my friend listened to a song 23hours and 59 mins ago, that is not a song he is "listening to now".

**My fix and side-effect check:** Changed
`RECENT_THRESHOLD = timedelta(hours=24)` to
`RECENT_THRESHOLD = timedelta(minutes=30)` in `feed_service.py`. Confirmed
`get_activity_feed` (the only other function in the file) does not use
`RECENT_THRESHOLD` at all, so this change is isolated to the "listening now"
feature. Re-ran the endpoint against `nova` after the change — friends whose
events were ~1.4h and ~11.2h old (previously shown) correctly disappeared
from the feed.

---

### Issue #3 — The same song keeps showing up twice in search

**How I reproduced it:**
I was not able to observe duplicate results
directly. I wrote a script that seeded a song with 3 tags (matching the
song_data_multi_tags pattern in seed_data.py) and ran search_songs
against both the pre-fix and fixed versions of search_service.py; both
returned exactly 1 result. The starter repo's own
test_search_no_duplicates_multi_tag_song test also passed even when run
against the pre-fix commit. I'm disclosing this rather than claiming a
reproduction I didn't get — the SQLAlchemy/environment combination here may
deduplicate whole-entity ORM query results in a way that masked the
symptom.

**How I found the root cause:** Read search_songs in
search_service.py and noticed it performs
.outerjoin(song_tags, Song.id == song_tags.c.song_id) but never actually
uses tag data to filter, sort, or select anything in the query.

**The root cause:**
The query is accidentally multiplying the number of songs in the search results because it’s joining the Song table to the song_tags table for no good reason.

**My fix and side-effect check:** Removed the outerjoin against
song_tags entirely, since it was never used to filter or select anything.
Verified all 5 tests in tests/test_search.py still pass after the change,
including cases for songs with 0, 1, and 3+ tags, and that to_dict()
still returns the correct tag list for each.

---

### Issue #4 — Notified on playlist-add but not on rating

**How I reproduced it:**
I wrote a script (repro_issue4.py) that seeded
a "sharer" and a "rater" user and a song owned by the sharer, then called
get_notifications(sharer_id) before and after calling
rate_song(rater_id, song_id, 5). Before rating: []. After rating (on
the pre-fix code): still [] — confirming no notification was created,
matching the reported bug. After my fix, the same call produces a
notification: {'type': 'song_rated', 'body': "rater rated your song 'Test Song' 5/5.", ...}.

**How I found the root cause:** Compared add_to_playlist (which calls
create_notification(...) after adding the song, notifying
song.shared_by) against rate_song (which saves the Rating and returns
immediately) in notification_service.py, following the hint to compare
the working and broken code paths line by line.

**The root cause:**
The developers successfully set up the playlist feature to trigger a notification. But they completely forgot to hook up the notification system to the rating feature.

**My fix and side-effect check:** Added a create_notification call at
the end of rate_song, mirroring the pattern in add_to_playlist —
notifying song.shared_by with a "song_rated" notification, and skipping
the notification if the rater is also the person who shared the song (same
"don't notify yourself" guard add_to_playlist already uses). I wrote a
new test file, tests/test_notifications.py (no test existed for this
service before), covering both the normal case
(test_rating_notifies_song_sharer) and the self-rating edge case
(test_rating_own_song_does_not_notify) — both pass. I also confirmed the
existing "update an existing rating" path (the if existing: branch) still
works correctly and doesn't error when a user re-rates a song.


---

### Issue #5 — The last song in a playlist never shows up

**How I reproduced it:**
The starter repo's tests/test_playlists.py
includes test_playlist_returns_all_songs, which seeds a playlist with 5
songs and expects all 5 back. I checked out the pre-fix commit and ran the
test — it failed with assert 4 == 5, and the companion test
test_playlist_returns_songs_in_order failed too, showing "Track 5"
missing from the returned list. Both pass after my fix.

**How I found the root cause:** Read `get_playlist_songs` in
`playlist_service.py` and noticed the return statement slices the results
with `songs[:-1]`, despite the function's own docstring claiming it
"returns all songs in the playlist."

**The root cause:**
The app is accidentally cutting off the very last song of every single playlist because of a typo "[:-1]"

**My fix and side-effect check:** Changed
return [song.to_dict() for song in songs[:-1]] to
return [song.to_dict() for song in songs]. Verified via
tests/test_playlists.py: test_playlist_returns_all_songs (now returns
5, not 4), test_playlist_returns_songs_in_order (all 5 titles in correct
position order), and test_empty_playlist_returns_empty_list (an empty
playlist still correctly returns [] — no off-by-one issue when there's
nothing to slice).



---

Commits

See git log --oneline on bugfix/mixtape:


5 separate fix: commits, one per bug, using conventional commit format
1 test: commit adding a new regression test for Issue #4
(tests/test_notifications.py), which had no prior test coverage
1 docs: commit annotating the streak fix


Screenshot of git log --oneline attached separately per submission
requirements.