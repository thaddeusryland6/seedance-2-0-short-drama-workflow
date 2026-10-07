import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
from drama.pipeline import (
    DramaError, VideoClient, body_for_scene, build, budget, cost, digest, run_phase,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def read(self):
        return self.payload
    def close(self):
        pass


class Recorder:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.requests = []
    def __call__(self, request, timeout):
        self.requests.append(request)
        return FakeResponse(self.payloads.pop(0))


class PipelineTests(unittest.TestCase):
    def plan(self):
        return build(
            1, ["小雨捡起钥匙", "她走向影院"],
            title="旧钥匙", references=["https://cdn.test/character.png"],
        )

    def test_plan_has_two_models_and_budget(self):
        plan = self.plan()
        self.assertEqual(plan["draft_model"], "seedance-2.0-mini")
        self.assertEqual(plan["final_model"], "seedance-2.0")
        self.assertEqual(cost("seedance-2.0-mini", "720p", 5), 0.285)
        self.assertEqual(budget(plan)["draft_usd"], 0.57)
        self.assertEqual(body_for_scene(plan, plan["scenes"][0], "final")["model"], "seedance-2.0")

    def test_build_rejects_adaptive_and_bad_reference(self):
        with self.assertRaises(ValueError):
            build(1, ["x"], aspect_ratio="adaptive")
        with self.assertRaises(ValueError):
            build(1, ["x"], references=["http://cdn.test/a.png"])

    def test_client_headers_and_status_contract(self):
        recorder = Recorder([b'{"request_id":"job-1"}'])
        client = VideoClient("secret", base="https://api.test/v1", opener=recorder)
        self.assertEqual(client.submit({"model": "seedance-2.0-mini", "prompt": "x"}), "job-1")
        self.assertEqual(recorder.requests[0].get_header("Authorization"), "Bearer secret")
        self.assertEqual(recorder.requests[0].get_header("User-agent"), "seedance-2-0-short-drama-workflow/1.0")
        bad = Recorder([b'{"status":"running"}'])
        with self.assertRaises(DramaError):
            VideoClient("secret", base="https://api.test/v1", opener=bad).status("job-1")

    def test_final_requires_approval_and_does_not_post(self):
        plan = self.plan()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(DramaError):
                run_phase(plan, "final", pathlib.Path(directory), VideoClient("secret", base="https://api.test/v1"))
        self.assertFalse(pathlib.Path(directory, "final-manifest.json").exists())

    def test_dry_run_cli_preserves_plan_and_budget(self):
        plan = self.plan()
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "plan.json"
            path.write_text(json.dumps(plan, ensure_ascii=False))
            loaded = json.loads(path.read_text())
            self.assertEqual(digest(body_for_scene(loaded, loaded["scenes"][0], "draft")),
                             digest(body_for_scene(plan, plan["scenes"][0], "draft")))

    def test_run_phase_persists_request_before_status(self):
        plan = self.plan()
        payloads = [b'{"request_id":"draft-1"}', b'{"status":"done"}', b'video',
                    b'{"request_id":"draft-2"}', b'{"status":"done"}', b'video']
        recorder = Recorder(payloads)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            client = VideoClient("secret", base="https://api.test/v1", opener=recorder)
            state = run_phase(plan, "draft", root, client)
            self.assertEqual(state["scenes"]["scene-01"]["request_id"], "draft-1")
            self.assertTrue((root / "draft" / "scene-01.mp4").is_file())
            self.assertEqual(recorder.requests[0].get_header("User-agent"), "seedance-2-0-short-drama-workflow/1.0")


if __name__ == "__main__":
    unittest.main()
