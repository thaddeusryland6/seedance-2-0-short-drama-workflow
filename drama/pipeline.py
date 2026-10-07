"""Two-phase Chinese short-drama planner and Seedance 2.0 runner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import math
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterable, List, Optional

BASE_URL = "https://argolink.io/v1"
USER_AGENT = "seedance-2-0-short-drama-workflow/1.0"
PRICING = json.loads((Path(__file__).resolve().parents[1] / "data/prices.json").read_text())
MODELS = {name: {"resolutions": set(row["effective"]), "rates": row["effective"]}
          for name, row in PRICING["models"].items()}
RATIOS = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
STATUSES = {"pending", "done", "failed", "expired"}


class DramaError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DramaError("API 重定向未跟随；检查 base URL，避免把 key 发送到其他主机")


def _https(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是完整 HTTPS URL")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{field} 必须是完整 HTTPS URL")
    return value


def cost(model: str, resolution: str, duration: int) -> float:
    if model not in MODELS or resolution not in MODELS[model]["resolutions"]:
        raise ValueError("模型不支持这个分辨率")
    if isinstance(duration, bool) or not isinstance(duration, int) or not 4 <= duration <= 15:
        raise ValueError("Seedance 2.0 时长必须是 4–15 秒整数")
    return round(MODELS[model]["rates"][resolution] * duration, 4)


def build(
    episode: int,
    scenes: List[str],
    *,
    title: str = "",
    references: Optional[List[str]] = None,
    resolution: str = "720p",
    aspect_ratio: str = "16:9",
    draft_model: str = "seedance-2.0-mini",
    final_model: str = "seedance-2.0",
) -> Dict[str, Any]:
    if not isinstance(episode, int) or isinstance(episode, bool) or episode < 1 or not scenes:
        raise ValueError("episode 必须是正整数，且至少有一场戏")
    if aspect_ratio not in RATIOS:
        raise ValueError("短剧流程使用六种固定比例")
    refs = references or []
    if not isinstance(refs, list) or len(refs) > 9:
        raise ValueError("references 必须是最多 9 个 HTTPS 图片 URL 的列表")
    for index, ref in enumerate(refs):
        _https(ref, f"references[{index}]")
    if draft_model not in {"seedance-2.0-mini", "seedance-2.0-fast"} or final_model != "seedance-2.0":
        raise ValueError("草稿选择 Mini/Fast，成片使用 seedance-2.0")
    if resolution not in MODELS[draft_model]["resolutions"] or resolution not in MODELS[final_model]["resolutions"]:
        raise ValueError("草稿和成片模型必须都支持所选分辨率")
    rows = []
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, str) or not scene.strip():
            raise ValueError(f"第 {index} 场没有内容")
        rows.append({
            "id": f"scene-{index:02d}",
            "prompt": scene.strip(),
            "duration": 5,
        })
    return {
        "schema_version": 1,
        "episode": episode,
        "title": title,
        "draft_model": draft_model,
        "final_model": final_model,
        "resolution": resolution,
        "draft_resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "references": refs,
        "scenes": rows,
    }


def body_for_scene(plan: Dict[str, Any], scene: Dict[str, Any], phase: str) -> Dict[str, Any]:
    if phase not in {"draft", "final"}:
        raise ValueError("phase 必须是 draft 或 final")
    model = plan["draft_model"] if phase == "draft" else plan["final_model"]
    body: Dict[str, Any] = {
        "model": model,
        "prompt": scene["prompt"],
        "duration": scene["duration"],
        "resolution": plan["draft_resolution"] if phase == "draft" else plan["resolution"],
        "aspect_ratio": plan["aspect_ratio"],
        "generate_audio": True,
    }
    if plan.get("references"):
        body["reference_images"] = [{"url": value} for value in plan["references"]]
    return body


def validate_plan(plan: dict) -> dict:
    """Validate edited plans at every entry point, before any side effect."""
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise ValueError("计划必须使用 schema_version: 1")
    if type(plan.get("episode")) is not int or plan["episode"] < 1:
        raise ValueError("episode 必须为正整数")
    if plan.get("draft_model") not in {"seedance-2.0-mini", "seedance-2.0-fast"}:
        raise ValueError("草稿模型应为 Mini 或 Fast")
    if plan.get("final_model") != "seedance-2.0" or plan.get("aspect_ratio") not in RATIOS:
        raise ValueError("成片模型/比例不符合短剧契约")
    refs = plan.get("references", [])
    if not isinstance(refs, list) or len(refs) > 9:
        raise ValueError("references 最多 9 张图片")
    for ref in refs:
        _https(ref, "references")
    if not isinstance(plan.get("scenes"), list) or not plan["scenes"]:
        raise ValueError("scenes 不能为空")
    seen = set()
    for scene in plan["scenes"]:
        if not isinstance(scene, dict) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", scene.get("id", "")):
            raise ValueError("场景 id 只使用字母、数字、下划线和连字符")
        if scene["id"] in seen:
            raise ValueError("场景 id 重复")
        seen.add(scene["id"])
        prompt = scene.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 40000:
            raise ValueError("提示词不能为空，也不能超过 40,000 UTF-8 字节")
        for phase in ("draft", "final"):
            body = body_for_scene(plan, scene, phase)
            cost(body["model"], body["resolution"], body["duration"])
    return plan


def digest(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def budget(plan: dict) -> dict:
    validate_plan(plan)
    rows = []
    for scene in plan["scenes"]:
        draft, final = (body_for_scene(plan, scene, phase) for phase in ("draft", "final"))
        rows.append({"scene_id": scene["id"], "seconds": scene["duration"],
                     "draft_usd": cost(draft["model"], draft["resolution"], draft["duration"]),
                     "final_usd": cost(final["model"], final["resolution"], final["duration"])})
    draft_total = round(sum(row["draft_usd"] for row in rows), 4)
    final_total = round(sum(row["final_usd"] for row in rows), 4)
    return {"price_checked": PRICING["checked"], "rows": rows, "draft_usd": draft_total,
            "final_usd": final_total, "combined_usd": round(draft_total + final_total, 4),
            "break_even_extra_final_passes": round(draft_total / final_total, 4)}


@contextmanager
def project_lock(directory: Path):
    """Refuse concurrent writers; a crash leaves a lock for operator inspection."""
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".render.lock"
    with lock.open("x") as handle:
        handle.write(str(os.getpid()))
    try:
        yield
    finally:
        lock.unlink()


def approve(plan: dict, directory: Path, ids: List[str], note: str) -> dict:
    validate_plan(plan)
    if not note.strip():
        raise ValueError("必须填写审核备注")
    with project_lock(directory):
        draft = json.loads((directory / "draft-manifest.json").read_text())
        file = directory / "approvals.json"
        approvals = json.loads(file.read_text()) if file.exists() else {}
        selected = {s["id"]: s for s in plan["scenes"]}
        if not ids or set(ids) - set(selected):
            raise ValueError("必须选择计划内的场景")
        for scene_id in ids:
            record = draft["scenes"].get(scene_id, {})
            scene = selected[scene_id]
            expected = digest(body_for_scene(plan, scene, "draft"))
            if record.get("fingerprint") != expected or record.get("status") != "done" or not Path(record.get("clip", "")).is_file():
                raise DramaError(f"{scene_id} 缺少与当前计划匹配的草稿成片")
            approvals[scene_id] = {"draft_request_id": record["request_id"], "draft_fingerprint": expected,
                                   "final_fingerprint": digest(body_for_scene(plan, scene, "final")), "note": note}
        _save(file, approvals)
        return approvals


class VideoClient:
    def __init__(
        self, key: Optional[str] = None, *, base: Optional[str] = None,
        opener: Optional[Callable[..., Any]] = None, sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.key = key or os.environ.get("VIDEO_API_KEY")
        self.base = (base or os.environ.get("VIDEO_API_BASE_URL") or BASE_URL).rstrip("/")
        parsed = urllib.parse.urlparse(self.base)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("base 必须为 HTTPS；仅本地测试允许 HTTP")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base 不能包含凭证、查询或 fragment")
        self._open = opener or urllib.request.build_opener(NoRedirect()).open
        self._sleep = sleep

    def call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        if not self.key:
            raise DramaError("VIDEO_API_KEY 未设置")
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self.key}", "User-Agent": USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            response = self._open(request, timeout=90)
            try:
                raw = response.read()
            finally:
                response.close()
        except urllib.error.HTTPError as error:
            detail = error.read(4096).decode("utf-8", errors="replace").replace(self.key, "[redacted]")
            raise DramaError(f"{method} {path} HTTP {error.code}: {detail[:300]}") from None
        if path.endswith("/content"):
            return raw
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DramaError(f"{method} {path} 返回无效 JSON") from error
        if not isinstance(result, dict):
            raise DramaError(f"{method} {path} 返回非对象")
        return result

    def submit(self, body: Dict[str, Any]) -> str:
        result = self.call("POST", "/videos/generations", body)
        request_id = result.get("request_id")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", request_id):
            raise DramaError("提交响应没有 request_id")
        return request_id

    def status(self, request_id: str) -> Dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", request_id):
            raise ValueError("无效 request_id")
        result = self.call("GET", f"/videos/{request_id}")
        if result.get("status") not in STATUSES:
            raise DramaError(f"未知任务状态 {result.get('status')!r}")
        return result

    def download(self, request_id: str, output: Path) -> None:
        if output.exists():
            raise FileExistsError(str(output))
        raw = self.call("GET", f"/videos/{request_id}/content")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".partial-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)


def _save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".state-", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def run_phase(plan: Dict[str, Any], phase: str, output_dir: Path, client: VideoClient, *, poll_interval: float = 10.0, timeout: float = 1800.0) -> Dict[str, Any]:
    validate_plan(plan)
    if phase not in {"draft", "final"}:
        raise ValueError("phase 必须是 draft 或 final")
    if not all(math.isfinite(v) and v > 0 for v in (poll_interval, timeout)):
        raise ValueError("轮询间隔和期限必须为正有限数")
    with project_lock(output_dir):
        if phase == "final":
            approvals = json.loads((output_dir / "approvals.json").read_text()) if (output_dir / "approvals.json").exists() else {}
            for scene in plan["scenes"]:
                row = approvals.get(scene["id"], {})
                if row.get("final_fingerprint") != digest(body_for_scene(plan, scene, "final")) or row.get("draft_fingerprint") != digest(body_for_scene(plan, scene, "draft")):
                    raise DramaError(f"{scene['id']} 未审核或计划已更改；先检查草稿")
        return _render_locked(plan, phase, output_dir, client, poll_interval, timeout)


def _render_locked(plan, phase, output_dir, client, poll_interval, timeout):
    state_path = output_dir / f"{phase}-manifest.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"phase": phase, "episode": plan["episode"], "scenes": {}}
    if state.get("episode") != plan["episode"] or state.get("phase") != phase:
        raise DramaError("输出目录已经绑定其他集数或阶段")
    scene_ids = {s["id"] for s in plan["scenes"]}
    if set(state["scenes"]) - scene_ids:
        raise DramaError("计划移除了已有任务；使用新的输出目录")
    # Validate every existing body before sending any new generation.
    for scene in plan["scenes"]:
        record = state["scenes"].get(scene["id"])
        if record and record.get("fingerprint") != digest(body_for_scene(plan, scene, phase)):
            raise DramaError(f"{scene['id']} 与历史请求不同；改用新输出目录保留旧记录")
    output_dir.mkdir(parents=True, exist_ok=True)
    for scene in plan["scenes"]:
        body = body_for_scene(plan, scene, phase)
        record = state["scenes"].setdefault(scene["id"], {"body": body, "fingerprint": digest(body)})
        clip = (output_dir / phase / f"{scene['id']}.mp4").resolve()
        if record.get("status") == "done" and record.get("clip"):
            if clip.is_file():
                if hashlib.sha256(clip.read_bytes()).hexdigest() != record.get("sha256"):
                    raise DramaError(f"{scene['id']} 的下载文件已更改")
                continue
        if record.get("status") in {"failed", "expired", "submitting"}:
            raise DramaError(f"{scene['id']} 状态为 {record['status']}；检查记录后另建运行，禁止盲目重提")
        if not record.get("request_id"):
            if clip.exists():
                raise DramaError(f"{clip} 没有对应请求记录")
            record["status"] = "submitting"
            _save(state_path, state)
            record["request_id"] = client.submit(record["body"])
            record["status"] = "pending"
            _save(state_path, state)
        start = time.monotonic()
        while True:
            provider = client.status(record["request_id"])
            record["status"] = provider["status"]
            record["provider"] = provider
            _save(state_path, state)
            if provider["status"] == "done":
                break
            if provider["status"] in {"failed", "expired"}:
                raise DramaError(f"{phase} {scene['id']} {provider['status']}: {provider.get('error', '')}")
            if time.monotonic() + poll_interval > start + timeout:
                raise TimeoutError(f"{phase} {scene['id']} 尚未完成；保留 ID 后继续")
            client._sleep(poll_interval)
        client.download(record["request_id"], clip)
        record["clip"] = str(clip)
        record["sha256"] = hashlib.sha256(clip.read_bytes()).hexdigest()
        _save(state_path, state)
    return state


def assemble(clips: Iterable[Path], output: Path) -> None:
    paths = [Path(path) for path in clips]
    if not paths or any(not path.is_file() for path in paths):
        raise DramaError("合成需要所有场景的 MP4")
    if output.exists():
        raise FileExistsError(str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".ffconcat", delete=False, encoding="utf-8") as handle:
        manifest = Path(handle.name)
        handle.write("ffconcat version 1.0\n")
        for path in paths:
            handle.write("file '" + str(path.resolve()).replace("'", "'\\\\''") + "'\n")
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(manifest),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(output)], check=True)
    finally:
        manifest.unlink(missing_ok=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Seedance 2.0 先草稿后成片短剧流程")
    parser.add_argument("--episode", type=int, required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--references", nargs="*", default=[])
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--draft-model", default="seedance-2.0-mini")
    parser.add_argument("--final-model", default="seedance-2.0")
    parser.add_argument("--out", type=Path, default=Path("episode-plan.json"))
    parser.add_argument("--phase", choices=("draft", "final"))
    parser.add_argument("--plan", type=Path, help="读取已有计划；渲染/审核时必须使用")
    parser.add_argument("--approve", nargs="*", help="批准指定场景，需配合 --note")
    parser.add_argument("--note", default="")
    parser.add_argument("--budget", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path(".drama"))
    parser.add_argument("--output", type=Path, default=Path("episode.mp4"))
    parser.add_argument("scenes", nargs="*")
    args = parser.parse_args(argv)
    if args.plan:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        validate_plan(plan)
    else:
        if not args.scenes:
            parser.error("新计划需要至少一个场景；已有计划请使用 --plan")
        plan = build(args.episode, args.scenes, title=args.title, references=args.references,
                     resolution=args.resolution, aspect_ratio=args.aspect_ratio,
                     draft_model=args.draft_model, final_model=args.final_model)
        _save(args.out, plan)
    if args.budget:
        print(json.dumps(budget(plan), ensure_ascii=False, indent=2))
        return 0
    if args.approve is not None:
        if not args.plan:
            parser.error("--approve 必须配合 --plan")
        print(json.dumps(approve(plan, args.output_dir, args.approve, args.note), ensure_ascii=False, indent=2))
        return 0
    if not args.phase:
        print(f"已写入计划 {args.out}")
        return 0
    state = run_phase(plan, args.phase, args.output_dir, VideoClient())
    if args.phase == "final":
        assemble([Path(state["scenes"][scene["id"]]["clip"]) for scene in plan["scenes"]], args.output)
        print(f"已写入成片 {args.output}")
    else:
        print(f"草稿完成，审阅后把 approved 改为 true，再运行 --phase final")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
