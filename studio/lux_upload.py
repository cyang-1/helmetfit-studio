"""Upload local images to official Aholo Asset/OUS using its OpenAPI v2 protocol."""
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

from .lux import GATEWAYS, LuxError, NetworkError, _provider_error, _read_json


def _request(host, token, route, *, method="GET", body=None, content_type=None):
    headers = {"ous-token-v2": token, "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    result = _read_json(Request(host + "/ous/api/v2/" + route, data=body, method=method, headers=headers))
    if str(result.get("c")) != "0":
        raise _provider_error(200, result)
    return result.get("d")


def _multipart(host, token, route, raw, name, field, value):
    boundary = "helmetfit" + secrets.token_hex(18)
    prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"\r\n\r\n{value}\r\n"
              f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
              "Content-Type: application/octet-stream\r\n\r\n").encode("ascii")
    _request(host, token, route, method="POST", content_type="multipart/form-data; boundary=" + boundary,
             body=prefix + raw + f"\r\n--{boundary}--\r\n".encode("ascii"))


def _remaining_parts(state, count):
    missing = state.get("lackBlocks")
    if state.get("deduplicated"):
        return []
    if missing is None:
        return range(1, count + 1)
    if not isinstance(missing, list):
        raise LuxError("Aholo 返回无效的待传分片列表。")
    parts = set()
    for entry in missing:
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", str(entry))
        if not match:
            raise LuxError("Aholo 返回无效的待传分片编号。")
        start, end = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= end <= count:
            raise LuxError("Aholo 返回的待传分片超出输入文件范围。")
        parts.update(range(start, end + 1))
    return sorted(parts)


def upload_image(key, region, raw, mime):
    # Token is scoped to one upload. The long-lived key only reaches the gateway.
    token_info = _read_json(Request(GATEWAYS[region] + "/asset/v1/token",
                                    headers={"Authorization": key, "Accept": "application/json"}))
    if "c" in token_info:
        if str(token_info.get("c")) != "0":
            raise _provider_error(200, token_info)
        token_info = token_info.get("d")
    if not isinstance(token_info, dict):
        raise LuxError("未取得 Aholo 图片上传凭证。")
    host = token_info.get("globalDomain")
    parsed = urlsplit(host if isinstance(host, str) else "")
    token, block = token_info.get("ousToken"), token_info.get("blockSize")
    if (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".kujiale.com")
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username
            or parsed.password or not isinstance(token, str) or not token or not isinstance(block, int)
            or block < 1):
        raise LuxError("Aholo 返回的图片上传配置无效。")
    host = host.rstrip("/")
    name = "helmet." + {"image/png":"png", "image/jpeg":"jpg", "image/webp":"webp"}[mime]
    md5 = hashlib.md5(raw, usedforsecurity=False).hexdigest()  # OUS file integrity field.
    if len(raw) <= block:
        _multipart(host, token, "single/upload", raw, name, "md5", md5)
    else:
        count = (len(raw) + block - 1) // block
        query = urlencode({"md5":md5, "blocks":count, "size":len(raw), "name":name})
        state = _request(host, token, "block/upload/init?" + query, method="POST", body=b"")
        if not isinstance(state, dict):
            raise LuxError("Aholo 未返回分片上传任务。")
        for part in _remaining_parts(state, count):
            _multipart(host, token, "block/upload/part", raw[(part-1)*block:part*block],
                       f"{name}.part{part}", "block", part)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = _request(host, token, "upload/status")
        if not isinstance(state, dict):
            raise LuxError("Aholo 返回无效的图片上传状态。")
        if state.get("status") == 5:
            url = state.get("url")
            if isinstance(url, str) and url.startswith("https://"):
                return url
            raise LuxError("图片上传完成但缺少公开 HTTPS URL。")
        if state.get("status") in (6, 8):
            raise LuxError("Aholo 未接收参考图，请检查图片内容或重试上传。")
        time.sleep(0.5)
    raise NetworkError("Aholo 图片上传尚未完成，请稍后重试。")
