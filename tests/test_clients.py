import httpx
import pytest

from horus_media.clients import EmbyClient, PlexClient

from .conftest import TOKEN


def test_plex_test_connection(transport):
    r = PlexClient("P", "http://plex:32400", TOKEN, transport=transport).test_connection()
    assert r.version == "1.41.0"


def test_plex_sessions_normalised(transport):
    s = PlexClient("P", "http://plex:32400", TOKEN, transport=transport).get_sessions()
    assert [x.play_method for x in s] == ["transcode", "direct_play"]
    assert s[0].title == "Some Show - Pilot"
    assert s[0].progress_percent == 25.0
    assert s[0].bandwidth_kbps == 8000
    assert s[1].state == "paused"
    assert s[1].platform == "Android"


def test_emby_test_connection(transport):
    r = EmbyClient("E", "http://emby:8096", TOKEN, transport=transport).test_connection()
    assert (r.server_name, r.version) == ("EmbyBox", "4.8.0.0")


def test_emby_sessions_only_now_playing(transport):
    s = EmbyClient("E", "http://emby:8096", TOKEN, transport=transport).get_sessions()
    assert len(s) == 1
    assert s[0].user == "carol"
    assert s[0].play_method == "transcode"
    assert s[0].bandwidth_kbps == 4000
    assert s[0].progress_percent == 25.0
    assert s[0].state == "paused"
    assert s[0].media_type == "episode"


def test_bad_token_raises(transport):
    with pytest.raises(httpx.HTTPStatusError):
        PlexClient("P", "http://plex:32400", "wrong", transport=transport).test_connection()


def test_clients_only_use_get():
    methods = []

    def handler(request):
        methods.append(request.method)
        return httpx.Response(200, json={"MediaContainer": {}})

    c = PlexClient("P", "http://x", TOKEN, transport=httpx.MockTransport(handler))
    c.test_connection()
    c.get_sessions()
    assert set(methods) == {"GET"}


def _emby_with_item(item, play_method="DirectStream"):
    sessions = [{"UserName": "u", "NowPlayingItem": {"Name": "x", "Type": "Movie", **item},
                 "PlayState": {"PlayMethod": play_method}}]
    t = httpx.MockTransport(lambda r: httpx.Response(200, json=sessions))
    return EmbyClient("E", "http://emby", TOKEN, transport=t).get_sessions()[0]


def test_emby_direct_stream_bitrate_fallbacks():
    assert _emby_with_item({"Bitrate": 6_000_000}).bandwidth_kbps == 6000
    assert _emby_with_item({"MediaSources": [{"Bitrate": 5_000_000}]}).bandwidth_kbps == 5000
    streams = {"MediaStreams": [{"BitRate": 4_000_000}, {"BitRate": 640_000}, {}]}
    assert _emby_with_item(streams).bandwidth_kbps == 4640
    assert _emby_with_item({}).bandwidth_kbps is None


def _image_transport(seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"\x89PNGfake", headers={"content-type": "image/png"})

    return httpx.MockTransport(handler)


def test_plex_poster_uses_series_art_for_episodes_and_resizes():
    from horus_media.clients.plex import PlexClient as P

    item = {"type": "episode", "title": "E", "thumb": "/library/metadata/2/thumb/9",
            "grandparentThumb": "/library/metadata/1/thumb/5"}
    sessions = {"MediaContainer": {"Metadata": [item]}}
    c = P("P", "http://x", TOKEN, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=sessions)))
    assert c.get_sessions()[0].poster == "/library/metadata/1/thumb/5"

    seen = []
    img = P("P", "http://x", TOKEN, transport=_image_transport(seen))
    assert img.get_image("/library/metadata/1/thumb/5") == (b"\x89PNGfake", "image/png")
    assert seen[0].url.path == "/photo/:/transcode"
    assert seen[0].url.params["url"] == "/library/metadata/1/thumb/5"
    assert seen[0].headers["x-plex-token"] == TOKEN
    assert TOKEN not in str(seen[0].url)  # token goes in a header, never the URL


@pytest.mark.parametrize("path", [
    "http://evil.example/x", "/library/sections", "/library/metadata/1/thumb/../../x",
    "//evil.example/library/metadata/1/thumb", "/library/metadata/abc/thumb"])
def test_plex_rejects_unexpected_image_paths(path):
    with pytest.raises(ValueError):
        PlexClient("P", "http://x", TOKEN, transport=_image_transport([])).get_image(path)


def test_emby_poster_selection_and_fetch():
    assert _emby_with_item({"SeriesId": "s1", "SeriesPrimaryImageTag": "t"}).poster == \
        "/Items/s1/Images/Primary"
    assert _emby_with_item({"Id": "i1", "ImageTags": {"Primary": "t"}}).poster == \
        "/Items/i1/Images/Primary"
    assert _emby_with_item({"Id": "i1"}).poster is None
    seen = []
    c = EmbyClient("E", "http://x", TOKEN, transport=_image_transport(seen))
    assert c.get_image("/Items/s1/Images/Primary")[1] == "image/png"
    assert seen[0].headers["x-emby-token"] == TOKEN
    with pytest.raises(ValueError):
        c.get_image("/Users/abc/Images/Primary")


def test_non_image_response_rejected():
    t = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>", headers={
        "content-type": "text/html"}))
    with pytest.raises(ValueError):
        EmbyClient("E", "http://x", TOKEN, transport=t).get_image("/Items/s1/Images/Primary")


PLEX_DIRECT = {
    "type": "movie", "title": "The Beast", "duration": 8_750_000, "viewOffset": 320_000,
    "User": {"title": "xMasterControl"},
    "Player": {"title": "Chrome", "product": "Plex Web", "platform": "Chrome",
               "address": "192.168.1.60", "local": True, "state": "playing"},
    "Session": {"bandwidth": 5200, "location": "lan"},
    "Media": [{"container": "mp4", "bitrate": 1300, "videoCodec": "h264",
               "videoResolution": "720", "Part": [{"Stream": [
                   {"streamType": 1, "codec": "h264"},
                   {"streamType": 2, "codec": "aac", "channels": 2, "selected": True},
                   {"streamType": 3, "codec": "srt", "language": "English", "selected": True},
                   {"streamType": 3, "codec": "srt", "language": "French"},
               ]}]}],
}


def _plex_one(item):
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"MediaContainer": {
        "Metadata": [item]}}))
    return PlexClient("P", "http://x", TOKEN, transport=t).get_sessions()[0]


def test_plex_card_details_direct_play():
    s = _plex_one(PLEX_DIRECT)
    assert s.product == "Plex Web"
    assert s.quality == "Original (1.3 Mbps)"
    assert s.container == "Direct Play (MP4)"
    assert s.video == "Direct Play (H264 720p)"
    assert s.audio == "Direct Play (Unknown - AAC Stereo)"
    assert s.subtitle == "Direct Play (English - SRT)"
    assert s.location == "LAN: 192.168.1.60"
    assert (s.position_ms, s.duration_ms) == (320_000, 8_750_000)


def test_plex_card_details_transcode_and_no_subtitle():
    item = {**PLEX_DIRECT, "Player": {**PLEX_DIRECT["Player"], "local": False},
            "Session": {"bandwidth": 2000, "location": "wan"},
            "TranscodeSession": {"videoDecision": "transcode", "audioDecision": "copy",
                                 "subtitleDecision": "burn", "videoCodec": "h264", "height": 480,
                                 "container": "mkv"}}
    s = _plex_one(item)
    assert s.quality == "Converted"
    assert s.video == "Transcode (H264 720p \u2192 H264 480p)"
    assert s.audio.startswith("Direct Stream")
    assert s.subtitle == "Burn (English - SRT)"
    assert s.container == "Transcode (MP4 \u2192 MKV)"
    assert s.location.startswith("WAN")
    streams = item["Media"][0]["Part"][0]["Stream"]
    no_sub = {**PLEX_DIRECT, "Media": [{**PLEX_DIRECT["Media"][0], "Part": [{"Stream": [
        x for x in streams if x["streamType"] != 3]}]}]}
    assert _plex_one(no_sub).subtitle is None


def test_episode_label_plex_and_emby():
    plex = _plex_one({**PLEX_DIRECT, "type": "episode", "title": "Power",
                      "grandparentTitle": "TYSON", "parentIndex": 1, "index": 1})
    assert (plex.title, plex.episode) == ("TYSON - Power", "S1:E1")
    assert _plex_one(PLEX_DIRECT).episode is None
    item = {"Name": "Power", "Type": "Episode", "SeriesName": "TYSON",
            "ParentIndexNumber": 1, "IndexNumber": 1}
    sessions = [{"Id": "s", "UserName": "u", "NowPlayingItem": item, "PlayState": {}}]
    t = httpx.MockTransport(lambda r: httpx.Response(200, json=sessions))
    emby = EmbyClient("E", "http://x", TOKEN, transport=t).get_sessions()[0]
    assert (emby.title, emby.episode) == ("TYSON - Power", "S1:E1")
    sessions[0]["NowPlayingItem"] = {"Name": "Film", "Type": "Movie"}
    assert EmbyClient("E", "http://x", TOKEN, transport=t).get_sessions()[0].episode is None


def test_emby_card_details():
    item = {"Name": "Film", "Type": "Movie", "Container": "mkv", "Bitrate": 8_000_000,
            "RunTimeTicks": 600_000_000_000,
            "MediaStreams": [
                {"Type": "Video", "Index": 0, "Codec": "hevc", "Height": 1080},
                {"Type": "Audio", "Index": 1, "Codec": "aac", "Channels": 2, "Language": "eng"},
                {"Type": "Audio", "Index": 2, "Codec": "ac3", "Channels": 6, "Language": "fra"},
                {"Type": "Subtitle", "Index": 3, "Codec": "srt", "Language": "eng"}]}
    sessions = [{"Id": "s", "UserName": "u", "Client": "Emby Web", "DeviceName": "Chrome",
                 "RemoteEndPoint": "192.168.1.60", "NowPlayingItem": item,
                 "PlayState": {"PlayMethod": "DirectPlay", "PositionTicks": 60_000_000_000,
                               "AudioStreamIndex": 2, "SubtitleStreamIndex": 3}}]
    t = httpx.MockTransport(lambda r: httpx.Response(200, json=sessions))
    s = EmbyClient("E", "http://x", TOKEN, transport=t).get_sessions()[0]
    assert s.product == "Emby Web"
    assert s.quality == "Original (8.0 Mbps)"
    assert s.video == "Direct Play (HEVC 1080p)"
    assert s.audio == "Direct Play (fra - AC3 5.1)"
    assert s.subtitle == "Direct Play (eng - SRT)"
    assert s.container == "Direct Play (MKV)"
    assert s.location == "LAN: 192.168.1.60"
    assert (s.position_ms, s.duration_ms) == (6_000_000, 60_000_000)
    sessions[0]["RemoteEndPoint"] = "8.8.8.8"
    sessions[0]["PlayState"]["SubtitleStreamIndex"] = -1
    s2 = EmbyClient("E", "http://x", TOKEN, transport=t).get_sessions()[0]
    assert s2.location == "WAN: 8.8.8.8" and s2.subtitle is None
