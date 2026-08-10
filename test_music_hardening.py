import os
import re
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("CHECKPOINT_KEY", "checkpoint-test-key")
import main


class MusicHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="checkpoint-music-test-")
        root = Path(self.tmp.name)
        self.music = root / "music"
        self.cache = root / "cache"
        self.art = root / "art"
        self.music.mkdir()
        self.cache.mkdir()
        self.art.mkdir()
        self.track = self.music / "Birocratic & G Mills - delivery pizza.flac"
        self.track.write_bytes(b"fLaC-test")
        self.old_music = main.MUSIC_DIR
        self.old_cache = main.TRANSCODED_DIR
        self.old_art = main.MUSIC_ART_DIR
        main.MUSIC_DIR = self.music
        main.TRANSCODED_DIR = self.cache
        main.MUSIC_ART_DIR = self.art
        self.client = TestClient(main.app)

    def tearDown(self):
        main.MUSIC_DIR = self.old_music
        main.TRANSCODED_DIR = self.old_cache
        main.MUSIC_ART_DIR = self.old_art
        self.tmp.cleanup()

    def test_track_ids_are_stable_and_opaque(self):
        first = main._music_id(self.track)
        second = main._music_id(self.track)
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{16}$")
        self.assertNotIn("delivery", first)

    def test_track_lookup_accepts_only_known_library_ids(self):
        track_id = main._music_id(self.track)
        self.assertEqual(main._find_music_track(track_id), self.track.resolve())
        self.assertIsNone(main._find_music_track("../etc/passwd"))
        self.assertIsNone(main._find_music_track("0" * 16))

    def test_music_api_requires_auth_and_returns_lossless_source(self):
        self.assertEqual(self.client.get("/api/music").status_code, 401)
        response = self.client.get("/api/music", headers={"x-api-key": main.API_KEY})
        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["name"], self.track.name)
        self.assertEqual(item["mime"], "audio/flac")
        self.assertTrue(item["url"].startswith("/music/"))
        self.assertRegex(item["id"], r"^[0-9a-f]{16}$")
        self.assertTrue(item["needs_compat"])

    def test_prepare_endpoint_is_authenticated_and_rejects_unknown_ids(self):
        self.assertEqual(self.client.post("/api/music/prepare/" + "0" * 16).status_code, 401)
        response = self.client.post(
            "/api/music/prepare/" + "0" * 16,
            headers={"x-api-key": main.API_KEY},
        )
        self.assertEqual(response.status_code, 404)

    def test_checkpoint_has_security_headers(self):
        response = self.client.get("/checkpoint")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(response.headers.get("x-frame-options"), "DENY")
        self.assertIn("no-referrer", response.headers.get("referrer-policy", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
