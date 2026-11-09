"""Aholo Lux3D image and text generation using the 1.9 OpenAPI contract and Python stdlib."""
import hashlib
import http.client
import json
import os
import re
import struct
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
KEY_FILE = "Aholo Lux3D APIkey.env"
GATEWAYS = {"com": "https://api.aholo.ai/global", "cn": "https://api.aholo3d.cn"}
IMAGE_LIMIT = 16 * 1024**2
TOTAL_IMAGE_LIMIT = 64 * 1024**2
GLB_LIMIT = 300 * 1024**2
POLL_SECONDS = 12


class LuxError(RuntimeError):
    """Safe, user-visible provider error. No secret, response body or signed URL."""


class NetworkError(LuxError):
    """Temporary gateway/CDN failure. A submitted task can be resumed."""


def _settings():
    key = os.environ.get("AHOLO_API_KEY") or os.environ.get("LUX3D_API_KEY")
    region = os.environ.get("AHOLO_REGION")
    if not key:
        override = os.environ.get("HELMETFIT_AHOLO_KEY_FILE")
        locations = [Path(override)] if override else [
            ROOT / KEY_FILE, ROOT.parent / KEY_FILE,
            ROOT.parent / "HelmetFit-Windows-Portable-v1.0" / KEY_FILE,
        ]
        file = next((p for p in locations if p.is_file()), None)
        if file:
            try:
                lines = file.read_text(encoding="utf-8-sig").splitlines()
            except (OSError, UnicodeError):
                raise LuxError("密钥文件无法读取，请使用 UTF-8 文本并检查权限。") from None
            for line in lines:
                line = line.strip().removeprefix("export ").strip()
                if not line or line.startswith("#"):
                    continue
                assignment = re.fullmatch(r"(AHOLO_API_KEY|LUX3D_API_KEY|AHOLO_REGION)\s*=\s*(.+)", line)
                if assignment:
                    name, value = assignment.groups()
                    value = value.strip().strip("\"'")
                    if name == "AHOLO_REGION":
                        region = region or value
                    else:
                        key = value
                elif len(lines) == 1:
                    key = line.strip("\"'")
    region = (region or "com").lower()
    if region not in GATEWAYS:
        raise LuxError("AHOLO_REGION 需填写 com（国际站）或 cn（国内站）。")
    if not key or not re.fullmatch(r"[!-~]{8,512}", key.strip()):
        raise LuxError("请在应用目录放置 Aholo Lux3D APIkey.env，填写有效的 Aholo API Key。")
    return key.strip(), region


def configured():
    try:
        _settings()
        return True
    except (LuxError, OSError):
        return False


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if (req.has_header("Authorization") or req.has_header("Ous-token-v2")
                or parsed.scheme != "https" or parsed.username or parsed.password):
            raise LuxError("Aholo 返回了异常跳转，请检查服务地址。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _provider_error(status, body=None):
    message = str((body or {}).get("m", "")).lower()
    if status in (401, 403) or "invalid appkey" in message:
        return LuxError("Aholo 拒绝密钥，请核对开放平台 API Key 和账号区域（AHOLO_REGION：com 或 cn），更新后重试。")
    if status == 429:
        return LuxError("Aholo 请求过于频繁，请稍后再试。")
    if status == 402 or any(word in message for word in ("credit", "balance", "quota", "积分", "余额")):
        return LuxError("Aholo 额度不足，请在对应区域的开放平台核对积分和套餐。")
    if status >= 500:
        return NetworkError("Aholo 服务暂时不可用。")
    code = str((body or {}).get("c", ""))
    suffix = f"，业务码 {code}" if re.fullmatch(r"-?\d{1,9}", code) else ""
    return LuxError(f"Aholo 拒绝了请求（HTTP {status}{suffix}），请核对输入内容和账号权限。")


def _read_json(request):
    try:
        with build_opener(_SafeRedirect()).open(request, timeout=90) as response:
            result = json.loads(response.read(2 * 1024**2))
    except HTTPError as exc:
        try:
            parsed = json.loads(exc.read(16384))
        except (ValueError, OSError):
            parsed = {}
        raise _provider_error(exc.code, parsed if isinstance(parsed, dict) else {}) from None
    except (URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError):
        raise NetworkError("无法连接 Aholo，请检查网络后继续。") from None
    except (ValueError, UnicodeError):
        raise NetworkError("Aholo 返回了无法识别的响应。") from None
    if not isinstance(result, dict):
        raise NetworkError("Aholo 返回了无法识别的响应。")
    return result


def _gateway(key, region, route, payload=None):
    data = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    request = Request(GATEWAYS[region] + "/lux3d/v1/" + route, data=data,
                      headers={"Authorization": key, "Content-Type": "application/json",
                               "Accept": "application/json", "User-Agent": "HelmetFitStudio/1.0"})
    result = _read_json(request)
    if "c" not in result:
        raise NetworkError("Aholo 响应缺少任务状态。")
    if str(result.get("c")) != "0":
        raise _provider_error(200, result)
    if result.get("d") is None:
        raise NetworkError("Aholo 响应缺少任务数据。")
    return result["d"]


def _prepare(images, upload):
    if not 1 <= len(images) <= 32:
        raise LuxError("请选择 1–32 张同一头盔的参考图。")
    if sum(p.stat().st_size for p in images) > TOTAL_IMAGE_LIMIT:
        raise LuxError("参考图总大小请控制在 64 MB 以内。")
    urls, hashes = [], []
    for p in images:
        if not 0 < p.stat().st_size <= IMAGE_LIMIT:
            raise LuxError("单张参考图请控制在 16 MB 以内。")
        raw = p.read_bytes()
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif raw.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            raise LuxError("参考图需为 PNG、JPEG 或 WebP 格式。")
        urls.append(upload(raw, mime))
        hashes.append(hashlib.sha256(raw).hexdigest())
    body = {"version": "G1", "faceCount": 100000, "outputFormat": ["glb"], "aiPredictSize": False}
    if len(urls) == 1:
        body["img"] = urls[0]
    else:
        body["imgs"] = urls
    return body, hashes


def _save(path, obj):
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _verify_glb(path):
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(20)
        if len(header) != 20:
            raise LuxError("生成文件内容不完整，请重新下载该任务。")
        magic, version, length, json_size, chunk = struct.unpack("<4sIII4s", header)
        if (magic != b"glTF" or version != 2 or length != size or chunk != b"JSON"
                or not 0 < json_size <= min(size - 20, 32 * 1024**2)):
            raise LuxError("下载文件不是完整的 GLB 2.0，请重新下载该任务。")
        try:
            doc = json.loads(stream.read(json_size))
        except (ValueError, UnicodeError):
            raise LuxError("GLB 的模型描述损坏，请重新下载该任务。") from None
        if not isinstance(doc, dict) or not isinstance(doc.get("meshes"), list) or not doc["meshes"]:
            raise LuxError("Aholo 生成的 GLB 没有可用网格。")
        for name in ("images", "buffers"):
            resources = doc.get(name, [])
            if not isinstance(resources, list):
                raise LuxError("GLB 资源描述损坏，请重新下载该任务。")
            for entry in resources:
                uri = entry.get("uri", "") if isinstance(entry, dict) else None
                if not isinstance(uri, str) or (uri and not uri.startswith("data:")):
                    raise LuxError("生成文件引用无效或外部资源，无法作为独立 GLB 交付。")


def _download(url, path):
    link = urlsplit(url)
    if link.scheme != "https" or not link.hostname or link.username or link.password:
        raise LuxError("Aholo 返回了无效下载地址。")
    temp = path.with_suffix(".partial")
    try:
        # CDN receives only the signed result URL. Never forward the gateway API key.
        with build_opener(_SafeRedirect()).open(Request(url), timeout=60) as response, temp.open("wb") as out:
            total = 0
            while chunk := response.read(256 * 1024):
                total += len(chunk)
                if total > GLB_LIMIT:
                    raise LuxError("生成模型超出本机单文件 300 MB 限制。")
                out.write(chunk)
        _verify_glb(temp)
        os.replace(temp, path)
    except (URLError, TimeoutError, ConnectionError, http.client.HTTPException):
        raise NetworkError("GLB 下载失败，稍后可继续同一任务。") from None
    finally:
        temp.unlink(missing_ok=True)


def read_journal(path):
    """Absent means no create attempt; unreadable means a create may have occurred."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {"state": "submitting"}
    if not isinstance(record, dict) or not (record.get("taskId") or record.get("state")):
        return {"state": "submitting"}
    return record


def generate(images, directory, on_status, resume_id=None, *, before_create=None):
    def prepare(key, region):
        from .lux_upload import upload_image

        def upload(raw, mime):
            on_status("正在上传参考图至 Aholo", None)
            return upload_image(key, region, raw, mime)

        body, hashes = _prepare([Path(p) for p in images], upload)
        return body, {"inputs": hashes}

    return _generate("img-to-3d", prepare, directory, on_status, resume_id, before_create)


def generate_text(prompt_spec, directory, on_status, resume_id=None, *, before_create=None):
    def prepare(_key, _region):
        # Use the exact brief persisted when the user created this project;
        # future revisions of the templates must not change pending jobs.
        from .prompts import STYLES
        if not isinstance(prompt_spec, dict):
            raise LuxError("项目缺少文字生成设计方案。")
        prompt = prompt_spec.get("prompt")
        style = prompt_spec.get("style")
        if not isinstance(prompt, str) or not prompt.strip() or style not in STYLES.values():
            raise LuxError("文字生成方案缺少有效提示词或风格，请重新创建作品。")
        body = {"prompt": prompt, "style": style, "version": "G1", "faceCount": 100000,
                "outputFormat": ["glb"], "aiPredictSize": False}
        return body, {"prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "style": style}

    return _generate("text-to-3d", prepare, directory, on_status, resume_id, before_create)


def _generate(operation, prepare, directory, on_status, resume_id, before_create=None):
    """Create or resume one paid operation in an exclusively owned job directory."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path, journal = directory / "original.glb", directory / "lux-task.json"
    record = read_journal(journal)
    if record.get("operation") and record["operation"] != operation:
        raise LuxError("源任务类型与当前作品不一致，请核对项目目录。")
    corrupt_cache = False
    if path.is_file():
        try:
            _verify_glb(path)
            return path
        except LuxError:
            corrupt_cache = True
    key, configured_region = _settings()
    region = configured_region if record.get("state") == "rejected" else record.get("region", configured_region)
    if region not in GATEWAYS:
        raise LuxError("原任务的 Aholo 区域记录无效。")
    task_id = record.get("taskId") or resume_id
    if not task_id:
        if corrupt_cache:
            _save(journal, {"region": region, "state": "submitting"})
            raise LuxError("本地 GLB 损坏，请核对 Aholo 历史任务号以重新下载。")
        if record.get("state") == "submitting":
            raise LuxError("前次创建结果尚未确认。请到 Aholo 任务历史核对，避免重复消耗积分。")
        body, metadata = prepare(key, region)
        record = {"region": region, "model": "G1", "operation": operation, "state": "submitting", **metadata}
        on_status("正在将设计提交至 Aholo Lux3D", None)
        if before_create is not None:
            before_create()
        _save(journal, record)
        try:
            task_id = _gateway(key, region, f"generate/{operation}/task/create", body)
        except NetworkError:
            raise LuxError("网络中断，Aholo 是否收到本次创建请求尚未确认，请在任务历史中核对。") from None
        except LuxError:
            record["state"] = "rejected"
            _save(journal, record)
            raise
    if not re.fullmatch(r"\d{1,30}", str(task_id)):
        raise LuxError("未取得有效 Aholo 任务号，请在任务历史中核对。")
    task_id = str(task_id)
    record.update(region=region, taskId=task_id, state="queued")
    _save(journal, record)
    on_status("Aholo 已接收生成任务", task_id)
    end = time.monotonic() + 30 * 60
    query = "generate/task/get?" + urlencode({"taskid": task_id})
    while time.monotonic() < end:
        try:
            result = _gateway(key, region, query)
        except NetworkError:
            on_status("连接暂时中断，正在恢复同一任务", task_id)
            time.sleep(POLL_SECONDS)
            continue
        if not isinstance(result, dict) or str(result.get("taskId")) != task_id:
            raise LuxError("查询结果与原 Aholo 任务号不一致。")
        status = result.get("status")
        if status == 3:
            # G1 always produces ZIP in slot 0 and GLB in slot 1. Never pick the
            # first download blindly; signed CDN paths need not carry an extension.
            output_list = result.get("outputs")
            outputs = [o.get("content") if isinstance(o, dict) else None for o in
                       (output_list if isinstance(output_list, list) else [])]
            url = next((u for u in outputs if isinstance(u, str) and u.startswith("https://")
                        and urlsplit(u).path.lower().endswith(".glb")), None)
            if not url and len(outputs) >= 2 and isinstance(outputs[1], str) and outputs[1].startswith("https://"):
                url = outputs[1]
            if not url:
                raise LuxError("任务已完成，但没有返回可下载的 GLB。")
            on_status("正在下载并校验 GLB", task_id)
            _download(url, path)
            record["state"] = "complete"
            _save(journal, record)
            return path
        if status in (4, 6):
            record["state"] = "failed" if status == 4 else "canceled"
            _save(journal, record)
            raise LuxError("Aholo 任务失败或被取消，请核对素材，调整后创建新任务。")
        if status not in (0, 1):
            raise LuxError("Aholo 任务状态未知，请稍后继续原任务。")
        on_status("正在排队" if status == 0 else "Lux3D 正在生成头盔外形", task_id)
        time.sleep(POLL_SECONDS)
    raise LuxError("等待超过 30 分钟，可稍后继续查询同一任务。")
