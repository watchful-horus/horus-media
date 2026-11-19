"""Playback problems: what the clients read, what the poller records, what the pages get."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media import maintenance, problems
from horus_media.clients.emby import EmbyClient
from horus_media.clients.plex import PlexClient
from horus_media.crypto import TokenCipher
from horus_media.db import connect, migrate
from horus_media.main import create_app
from horus_media.poller import close_stale, poll_once
from horus_media.store import ServerStore

from .conftest import TOKEN

T0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def plex_item(state="playing", offset=60_000, transcode=None, user="alice", key="7"):
    item = {"type": "movie", "title": "A Film", "sessionKey": key, "duration": 6_000_000,
            "viewOffset": offset, "User": {"title": user},
            "Player": {"title": "TV", "platform": "Roku", "state": state, "address": "10.0.0.9"},
            "Session": {"id": "abc", "bandwidth": 8000, "location": "lan"}}
    if transcode is not None:
        item["TranscodeSession"] = {"videoDecision": "transcode", "audioDecision": "copy",
                                    **transcode}
    return item


def emby_item(position=600_000_000, paused=False, info=None, method="DirectPlay"):
    s = {"Id": "s1", "UserName": "carol", "DeviceName": "Tablet", "Client": "Emby Web",
         "NowPlayingItem": {"Id": "i1", "Name": "Film B", "Type": "Movie",
                            "RunTimeTicks": 60_000_000_000,
                            "MediaStreams": [{"Type": "Video", "RealFrameRate": 24.0}]},
         "PlayState": {"PlayMethod": method, "PositionTicks": position, "IsPaused": paused}}
    if info is not None:
        s["TranscodingInfo"] = info
    return s


class Env:
    def __init__(self, tmp_path, kind):
        self.conn = connect(tmp_path / "h.db")
        migrate(self.conn)
        self.cipher = TokenCipher.for_db(tmp_path / "h.db")
        ServerStore(self.conn, self.cipher).add(kind, "Box", "http://box", TOKEN)
        self.sessions: list = []
        self.sent: list = []

        def answer(request: httpx.Request) -> httpx.Response:
            if request.url.host == "hook":
                self.sent.append(request)
                return httpx.Response(200)
            body = ({"MediaContainer": {"Metadata": self.sessions}} if kind == "plex"
                    else self.sessions)
            return httpx.Response(200, json=body)

        self.transport = httpx.MockTransport(answer)

    def poll(self, seconds):
        poll_once(self.conn, self.cipher, self.transport, now=T0 + timedelta(seconds=seconds))

    def rows(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM playback_problems ORDER BY id")]


@pytest.fixture
def plex(tmp_path):
    return Env(tmp_path, "plex")


@pytest.fixture
def emby(tmp_path):
    return Env(tmp_path, "emby")


# ---- what the clients read ----

def _plex_session(item):
    client = PlexClient("Box", "http://box", TOKEN, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"MediaContainer": {"Metadata": [item]}})))
    return client.get_sessions()[0]


def _emby_session(item):
    client = EmbyClient("Box", "http://box", TOKEN, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=[item])))
    return client.get_sessions()[0]


def test_plex_reads_buffering_and_transcode_speed():
    s = _plex_session(plex_item(state="buffering", transcode={"speed": 0.5, "throttled": False}))
    assert s.state == "buffering" and s.transcode_speed == 0.5 and not s.transcode_throttled
    assert _plex_session(plex_item(state="something new")).state == "playing"
    done = _plex_session(plex_item(transcode={"speed": 0, "complete": True}))
    assert done.transcode_speed is None  # the whole file is converted: speed says nothing
    assert _plex_session(plex_item(transcode={})).transcode_speed is None  # server gave none


def test_emby_reads_reasons_and_speed():
    s = _emby_session(emby_item(method="Transcode", info={
        "IsVideoDirect": False, "Framerate": 12.0, "CompletionPercentage": 20.0,
        "TranscodeReasons": ["ContainerBitrateExceedsLimit", "SomethingBrandNew"]}))
    assert s.transcode_speed == 0.5
    assert s.transcode_reason == "Bitrate over the player's limit, Something brand new"
    assert not s.direct_play_failed
    failed = _emby_session(emby_item(method="Transcode", info={
        "IsVideoDirect": True, "Framerate": 5.0, "TranscodeReasons": "DirectPlayError"}))
    assert failed.direct_play_failed and failed.transcode_reason == "Direct play failed"
    assert failed.transcode_speed is None  # only audio is converted: frame rate means nothing
    plain = _emby_session(emby_item())
    assert plain.transcode_speed is None and plain.transcode_reason is None


def test_internal_flags_stay_out_of_what_is_sent_out():
    dumped = _plex_session(plex_item(transcode={"speed": 2, "throttled": True})).model_dump()
    assert "direct_play_failed" not in dumped and "native_id" not in dumped
    assert dumped["problems"] == []


# ---- what the poller records ----

def test_buffering_counts_on_the_second_poll_in_a_row(plex):
    plex.sessions.append(plex_item(state="buffering"))
    plex.poll(0)
    assert plex.rows() == []  # once could be a seek or the start of the play
    plex.poll(15)
    plex.poll(30)
    (row,) = plex.rows()
    assert row["kind"] == "buffering" and row["ended_at"] is None
    assert row["started_at"] == T0.isoformat()  # from when it was first seen
    assert row["location"] == "LAN" and "10.0.0.9" not in str(row)  # never the address

    plex.sessions[0] = plex_item(offset=75_000)
    plex.poll(45)
    (row,) = plex.rows()
    assert row["ended_at"] == (T0 + timedelta(seconds=30)).isoformat()


def test_one_buffering_poll_is_ignored(plex):
    plex.sessions.append(plex_item(state="buffering"))
    plex.poll(0)
    plex.sessions[0] = plex_item(offset=70_000)
    plex.poll(15)
    plex.sessions[0] = plex_item(state="buffering", offset=70_000)
    plex.poll(30)
    assert plex.rows() == []


def test_stalled_needs_the_position_to_stay_put_for_a_while(emby):
    emby.sessions.append(emby_item(position=600_000_000))
    for t in (0, 15, 30):
        emby.poll(t)
    assert emby.rows() == []  # players report their position only every 10 to 20 seconds
    emby.poll(45)
    (row,) = emby.rows()
    assert row["kind"] == "stalled" and row["detail"] == "Position stayed at 1:00"
    assert row["started_at"] == T0.isoformat()
    emby.sessions[0] = emby_item(position=700_000_000)
    emby.poll(60)
    assert emby.rows()[0]["ended_at"] is not None


def test_a_paused_stream_is_not_stalled(emby):
    emby.sessions.append(emby_item(paused=True))
    for t in range(0, 300, 15):
        emby.poll(t)
    assert emby.rows() == []
    emby.sessions[0] = emby_item()  # resumed at the same spot: the count starts now
    emby.poll(300)
    emby.poll(315)
    assert emby.rows() == []


def test_a_stream_without_a_position_is_not_stalled(emby):
    item = emby_item()
    del item["PlayState"]["PositionTicks"]  # live TV
    emby.sessions.append(item)
    for t in range(0, 120, 15):
        emby.poll(t)
    assert emby.rows() == []


def test_slow_transcode_but_not_when_the_server_is_only_pausing(plex):
    plex.sessions.append(plex_item(transcode={"speed": 0.6, "throttled": False}))
    plex.poll(0)
    plex.sessions[0] = plex_item(offset=70_000, transcode={"speed": 0.7, "throttled": False})
    plex.poll(15)
    (row,) = plex.rows()
    assert row["kind"] == "slow_transcode" and row["detail"] == "Converting at 0.7x playback speed"

    problems.forget()
    plex.conn.execute("DELETE FROM playback_problems")
    for i, t in enumerate((100, 115, 130)):
        plex.sessions[0] = plex_item(offset=200_000 + i * 15_000, key="8",
                                     transcode={"speed": 0.0, "throttled": True})
        plex.poll(t)
    assert plex.rows() == []


def test_emby_direct_play_error_is_recorded_once_per_play(emby):
    info = {"IsVideoDirect": True, "TranscodeReasons": ["DirectPlayError"]}
    for i, t in enumerate((0, 15, 30)):
        emby.sessions[:] = [emby_item(position=600_000_000 + i * 150_000_000, method="Transcode",
                                      info=info)]
        emby.poll(t)
    (row,) = emby.rows()
    assert row["kind"] == "direct_play_error" and row["detail"] == "Direct play failed"


def test_problems_close_when_the_play_ends_and_after_a_restart(plex):
    plex.sessions.append(plex_item(state="buffering"))
    plex.poll(0)
    plex.poll(15)
    plex.sessions.clear()
    plex.poll(30)
    assert plex.rows()[0]["ended_at"] == (T0 + timedelta(seconds=15)).isoformat()

    plex.sessions.append(plex_item(state="buffering"))
    plex.poll(45)
    plex.poll(60)
    assert plex.rows()[1]["ended_at"] is None
    close_stale(plex.conn)  # a restart
    assert plex.rows()[1]["ended_at"] is not None


def test_removed_with_old_history(plex):
    plex.sessions.append(plex_item(state="buffering"))
    plex.poll(0)
    plex.poll(15)
    plex.sessions.clear()
    plex.poll(30)
    maintenance.prune_history(plex.conn, 30, T0 + timedelta(days=10))
    assert len(plex.rows()) == 1
    maintenance.prune_history(plex.conn, 30, T0 + timedelta(days=31))
    assert plex.rows() == []


def _buffer_n_times(env, times):
    t = 0
    for n in range(times):
        env.sessions[:] = [plex_item(state="buffering", offset=60_000 + n * 60_000)]
        env.poll(t)
        env.poll(t + 15)
        env.sessions[:] = [plex_item(offset=90_000 + n * 60_000)]
        env.poll(t + 30)
        t += 45


def test_notification_once_per_play_and_again_when_it_keeps_happening(plex):
    from horus_media import notifications

    with plex.conn:
        plex.conn.execute(
            "INSERT INTO notification_channels (name, kind, url, events, created_at) "
            "VALUES ('Hook', 'webhook', ?, '[\"playback_problem\"]', ?)",
            (plex.cipher.encrypt("http://hook/x"), T0.isoformat()))
    _buffer_n_times(plex, 4)
    notifications._pool.submit(lambda: None).result()  # let the sender's queue drain
    import json
    import time
    for _ in range(50):
        if len(plex.sent) >= 2:
            break
        time.sleep(0.02)
    titles = [json.loads(r.content)["title"] for r in plex.sent]
    assert titles == ["alice: buffering", "alice: keeps buffering"]
    assert len(plex.rows()) == 4


def test_the_event_can_be_chosen_for_a_channel():
    from horus_media import notifications

    assert notifications.validate("webhook", "https://example.org/x", ["playback_problem"]) == [
        "playback_problem"]


# ---- what the pages and the data API get ----

@pytest.fixture
def app(tmp_path, monkeypatch, plex):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "h.db"))
    return TestClient(create_app(transport=plex.transport)), plex


def test_activity_shows_what_is_wrong_right_now(app):
    client, plex = app
    plex.sessions.append(plex_item(state="buffering"))
    assert client.get("/api/activity").json()["sessions"][0]["problems"] == []
    plex.poll(0)
    plex.poll(15)
    session = client.get("/api/activity").json()["sessions"][0]
    assert session["state"] == "buffering" and session["problems"] == ["buffering"]
    assert session["transcode_throttled"] is False and "native_id" not in session


def test_a_conversion_paused_ahead_of_playback_gives_no_speed():
    """What a healthy Plex stream looks like most of the time: speed 0 and throttled. A speed
    of 0 would read as a server that cannot keep up."""
    ahead = _plex_session(plex_item(transcode={"speed": 0.0, "throttled": True}))
    assert ahead.play_method == "transcode"
    assert ahead.transcode_speed is None and ahead.transcode_throttled is True
    busy = _plex_session(plex_item(transcode={"speed": 3.4, "throttled": False}))
    assert busy.transcode_speed == 3.4 and busy.transcode_throttled is False


# ---- Emby gives no speed: it is worked out from how far the conversion moved ----

EMBY_REASON = ["ContainerBitrateExceedsLimit"]


def emby_converting(reached_s, position_s=600, throttle=None, paused=False):
    """What a real Emby sends while converting video: no frame rate, how far it has got, and
    a throttle number only while it waits."""
    return emby_item(position=position_s * 10_000_000, paused=paused, method="Transcode", info={
        "IsVideoDirect": False, "TranscodeReasons": EMBY_REASON, "CompletionPercentage": 10.0,
        "CurrentThrottle": throttle, "TranscodingPositionTicks": reached_s * 10_000_000})


def test_emby_says_when_it_is_waiting_and_how_far_it_has_got():
    waiting = _emby_session(emby_converting(1015, throttle=100))
    assert waiting.play_method == "transcode" and waiting.transcode_throttled is True
    assert waiting.transcode_speed is None and waiting.transcoded_ms == 1_015_000
    working = _emby_session(emby_converting(1020))
    assert working.transcode_throttled is False and working.transcoded_ms == 1_020_000
    assert "transcoded_ms" not in working.model_dump()
    assert _emby_session(emby_item()).transcoded_ms is None  # direct play: nothing converted


def test_an_emby_server_that_cannot_keep_up_is_a_slow_transcode(emby):
    # 9 seconds of film converted in each 15 seconds: 0.6x, and it never gets to wait.
    for i, t in enumerate((0, 15, 30)):
        emby.sessions[:] = [emby_converting(700 + 9 * i, position_s=600 + 15 * i)]
        emby.poll(t)
    (row,) = emby.rows()
    assert row["kind"] == "slow_transcode" and row["detail"] == "Converting at 0.6x playback speed"
    assert row["started_at"] == (T0 + timedelta(seconds=15)).isoformat()
    # It catches up: the problem ends.
    emby.sessions[:] = [emby_converting(718 + 40, position_s=645)]
    emby.poll(45)
    assert emby.rows()[0]["ended_at"] is not None


def test_a_healthy_emby_stream_is_never_slow(emby):
    # Mostly waiting far ahead; now and then seen working, and it then covers the whole gap.
    readings = [(720, 100), (720, 100), (735, None), (750, None), (765, 100), (765, 100),
                (780, None), (795, None), (810, None)]
    for i, (reached, throttle) in enumerate(readings):
        emby.sessions[:] = [emby_converting(reached, position_s=600 + 15 * i, throttle=throttle)]
        emby.poll(15 * i)
    assert emby.rows() == []


def test_the_first_reading_after_waiting_is_not_measured(emby):
    """Waiting at one poll and just started working at the next: the little it converted since
    says nothing about its speed."""
    for i, (reached, throttle) in enumerate([(720, 100), (721, None), (722, 100), (723, None),
                                             (724, 100), (725, None)]):
        emby.sessions[:] = [emby_converting(reached, position_s=600 + 15 * i, throttle=throttle)]
        emby.poll(15 * i)
    assert emby.rows() == []


def test_a_seek_or_a_pause_starts_the_measuring_again(emby):
    emby.sessions[:] = [emby_converting(700, position_s=600)]
    emby.poll(0)
    emby.sessions[:] = [emby_converting(2000, position_s=1900)]  # jumped 20 minutes ahead
    emby.poll(15)
    emby.sessions[:] = [emby_converting(1200, position_s=1100)]  # and back: converted "less"
    emby.poll(30)
    emby.sessions[:] = [emby_converting(1201, position_s=1100, paused=True)]
    emby.poll(45)
    emby.sessions[:] = [emby_converting(1202, position_s=1100, paused=True)]
    emby.poll(60)
    assert emby.rows() == []


def test_the_worked_out_speed_shows_on_activity_and_in_the_data_api(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_AUTH_DISABLED", "1")
    env = Env(tmp_path, "emby")
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "h.db"))
    client = TestClient(create_app(transport=env.transport))
    env.sessions[:] = [emby_converting(700, position_s=600)]
    assert client.get("/api/activity").json()["sessions"][0]["transcode_speed"] is None
    env.poll(0)
    env.sessions[:] = [emby_converting(730, position_s=615)]
    env.poll(15)
    assert client.get("/api/activity").json()["sessions"][0]["transcode_speed"] == 2.0
    assert client.get("/api/v1/activity").json()[0]["transcode_speed"] == 2.0
    # Once Emby waits, the page says so and shows no speed.
    env.sessions[:] = [emby_converting(735, position_s=630, throttle=100)]
    env.poll(30)
    waiting = client.get("/api/activity").json()["sessions"][0]
    assert waiting["transcode_speed"] is None and waiting["transcode_throttled"] is True


def test_a_speed_of_zero_is_not_a_slow_transcode():
    """Seen on a real Plex: for about a second at the start of each burst it reports speed 0
    and not throttled. That is no measurement yet, not a server that cannot keep up."""
    starting = _plex_session(plex_item(transcode={"speed": 0.0, "throttled": False}))
    assert starting.play_method == "transcode" and starting.transcode_speed is None
    slow = _plex_session(plex_item(transcode={"speed": 0.4, "throttled": False}))
    assert slow.transcode_speed == 0.4


def test_two_polls_at_speed_zero_record_no_problem(plex):
    for i, t in enumerate((0, 15, 30)):
        plex.sessions[:] = [plex_item(offset=10_000 + i * 15_000, key="9",
                                      transcode={"speed": 0.0, "throttled": False})]
        plex.poll(t)
    assert plex.rows() == []


def test_activity_and_the_data_api_say_when_a_conversion_is_ahead(app):
    client, plex = app
    plex.sessions.append(plex_item(transcode={"speed": 0.0, "throttled": True}))
    session = client.get("/api/activity").json()["sessions"][0]
    assert session["transcode_speed"] is None and session["transcode_throttled"] is True
    row = client.get("/api/v1/activity").json()[0]
    assert row["transcode_speed"] is None and row["transcode_throttled"] is True
    assert "transcode_throttled" in client.get("/api/v1/activity?format=csv").text.splitlines()[0]


def test_activity_marks_a_stream_that_keeps_buffering(app):
    client, plex = app
    _buffer_n_times(plex, 3)
    assert client.get("/api/activity").json()["sessions"][0]["problems"] == ["repeated"]


def test_problem_summary_for_the_graphs_page(app):
    client, plex = app
    _buffer_n_times(plex, 2)
    day = T0.astimezone().date().isoformat()
    data = client.get("/api/problems", params={"start": day, "end": day}).json()
    assert data["total"] == 2
    assert data["by_kind"] == [{"name": "Buffering", "count": 2}]
    assert data["by_user"] == [{"name": "alice", "count": 2}]
    assert data["by_device"] == [{"name": "TV", "count": 2}]
    assert data["by_platform"] == [{"name": "Roku", "count": 2}]
    buffering = next(s for s in data["per_day"]["series"] if s["name"] == "Buffering")
    assert buffering["data"] == [2]
    assert [r["duration_seconds"] for r in data["recent"]] == [15, 15]
    assert client.get("/api/problems", params={"start": "2026-03-02", "end": "2026-03-01"}
                      ).status_code == 422


def test_data_api_lists_problems_as_json_and_csv(app):
    client, plex = app
    _buffer_n_times(plex, 1)
    window = {"from": "2026-03-10", "to": "2026-03-11"}
    (row,) = client.get("/api/v1/problems", params=window).json()
    assert row["kind"] == "buffering" and row["user"] == "alice" and row["server"] == "Box"
    assert row["location"] == "LAN"
    text = client.get("/api/v1/problems", params={**window, "format": "csv"}).text
    assert text.splitlines()[0].startswith("id,server,kind,user,title")
    live = client.get("/api/v1/activity").json()[0]
    assert "problems" not in live and "transcode_speed" in live


def test_someone_limited_to_their_own_plays_sees_only_their_own_problems(app, monkeypatch):
    client, plex = app
    _buffer_n_times(plex, 1)
    monkeypatch.setattr("horus_media.auth.only_media_user", lambda conn, user: "bob")
    day = T0.astimezone().date().isoformat()
    assert client.get("/api/problems", params={"start": day, "end": day}).json()["total"] == 0
    assert client.get("/api/v1/problems",
                      params={"from": "2026-03-10", "to": "2026-03-11"}).json() == []
