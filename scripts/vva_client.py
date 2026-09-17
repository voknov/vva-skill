#!/usr/bin/env python3
"""Portable VVA authoring and draft-media client (Python standard library)."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import webbrowser

RATIOS = ("16:9", "4:3", "1:1", "3:4", "9:16", "21:9")
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
ENTITY_LISTS = ("assets", "looks", "drafts", "episodes", "segments", "shots")
DEFAULT_API_URL = "https://video.pictech.top/api/v1"


class ClientError(Exception):
    pass


class AuthenticationRequired(ClientError):
    """The user must establish a VVA session before the operation can continue."""


class NoRedirect(HTTPRedirectHandler):
    """Never forward an Authorization header to another endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ClientError("API 地址不能包含凭证、查询参数或片段")
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not parsed.hostname or not (parsed.scheme == "https" or (parsed.scheme == "http" and local)):
        raise ClientError("远程 VVA 地址必须使用 HTTPS；HTTP 仅允许本机地址")
    return value.rstrip("/")


def web_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ClientError("网页地址不能包含凭证、查询参数或片段")
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not parsed.hostname or not (parsed.scheme == "https" or (parsed.scheme == "http" and local)):
        raise ClientError("远程 VVA 网页必须使用 HTTPS；HTTP 仅允许本机地址")
    return value.rstrip("/")


def default_web_url(api_url: str) -> str:
    parsed = urlsplit(endpoint(api_url))
    if parsed.hostname in {"127.0.0.1", "localhost"}:
        host = parsed.hostname
        return f"http://{host}:15173"
    path = parsed.path.rstrip("/")
    if path.endswith("/api/v1"):
        path = path[:-7]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def uuid_value(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ClientError(f"不是有效的 UUID：{value}") from exc


def load_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ClientError(f"无法读取 JSON 文件：{path}（{exc}）") from exc


def save_json(path: str, value: Any, *, private: bool = False) -> None:
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to overwrite symlinks, especially for session credentials.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        # os.fchmod is not available in Windows Python. os.open already creates
        # the file inside the current user's profile; keep the stronger 0600
        # descriptor permission on platforms that support it.
        fchmod = getattr(os, "fchmod", None)
        if private and callable(fchmod):
            fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def browser_login_command(
    api_url: str,
    web_url: str,
    token_file: str,
    *,
    unset_environment_token: bool = False,
    platform_name: str | None = None,
) -> str:
    parts = [
        sys.executable or "python3",
        str(Path(__file__).resolve()),
        "--api-url",
        endpoint(api_url),
        "--web-url",
        web_endpoint(web_url),
        "--token-file",
        str(Path(token_file).expanduser()),
        "login-web",
    ]
    if (platform_name or os.name) == "nt":
        command = subprocess.list2cmdline(parts)
        if unset_environment_token:
            command = f'set "VVA_ACCESS_TOKEN=" && {command}'
        return command
    if unset_environment_token:
        parts = ["env", "-u", "VVA_ACCESS_TOKEN", *parts]
    return shlex.join(parts)


def login_guidance(
    api_url: str,
    web_url: str,
    token_file: str,
    *,
    environment_token: bool = False,
) -> str:
    command = browser_login_command(
        api_url,
        web_url,
        token_file,
        unset_environment_token=environment_token,
    )
    source_note = "当前凭证来自 VVA_ACCESS_TOKEN；请更新该变量，或先取消它后再登录。\n" if environment_token else ""
    return (
        "VVA 尚未登录或登录已过期，当前操作已停止。\n"
        f"{source_note}请在自己的终端运行：\n{command}\n"
        "命令会尝试打开 VVA 网页；若未自动打开，请访问它打印的链接。"
        "不要在聊天中发送密码或访问令牌。网页登录并确认授权后再继续。"
    )


def browser_login(
    client: "Client",
    *,
    web_url: str,
    token_path: Path,
    open_browser: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    if not 1 <= timeout_seconds <= 900:
        raise ClientError("网页登录等待时间必须在 1～900 秒之间")
    challenge = client.request("POST", "/auth/device/start", anonymous=True)
    try:
        device_code = str(challenge["device_code"])
        user_code = str(challenge["user_code"])
        interval = max(1, int(challenge.get("interval", 2)))
    except (KeyError, TypeError, ValueError) as exc:
        raise ClientError("VVA 没有返回有效的网页登录请求") from exc
    verification_url = f"{web_endpoint(web_url)}/skill-login?{urlencode({'code': user_code})}"
    print("请在浏览器中登录 VVA 并确认本次 Skill 授权：", flush=True)
    print(verification_url, flush=True)
    if open_browser:
        try:
            opened = webbrowser.open_new_tab(verification_url)
        except (OSError, webbrowser.Error):
            opened = False
        if opened:
            print("已尝试打开浏览器，正在等待授权……", flush=True)
        else:
            print("未能自动打开浏览器，请复制上面的链接访问。正在等待授权……", flush=True)
    else:
        print("正在等待授权……", flush=True)
    deadline = time.monotonic() + timeout_seconds
    while True:
        response = client.request(
            "POST", "/auth/device/token", {"device_code": device_code}, anonymous=True
        )
        if isinstance(response, dict) and response.get("access_token"):
            save_json(
                str(token_path),
                {"api_url": client.api_url, "access_token": response["access_token"]},
                private=True,
            )
            user = response.get("user") if isinstance(response.get("user"), dict) else {}
            return {
                "ok": True,
                "user": {
                    "phone": user.get("phone"),
                },
                "token_file": str(token_path),
                "note": "网页登录成功；会话凭证已仅保存到本机文件。",
            }
        if not isinstance(response, dict) or response.get("status") != "pending":
            raise ClientError("网页登录状态异常，请重新运行 login-web")
        if time.monotonic() >= deadline:
            raise ClientError("等待网页登录超时，请重新运行 login-web")
        time.sleep(min(interval, max(0.1, deadline - time.monotonic())))


class Client:
    def __init__(self, api_url: str, token: str | None = None, *, auth_guidance: str | None = None):
        self.api_url = endpoint(api_url)
        self.token = token
        self.auth_guidance = auth_guidance or "VVA 尚未登录或登录已过期，请先运行 login 命令。"
        self.opener = build_opener(NoRedirect())

    def request(self, method: str, path: str, payload: Any = None, *, anonymous: bool = False) -> Any:
        if not anonymous and not self.token:
            raise AuthenticationRequired(self.auth_guidance)
        headers = {"Accept": "application/json"}
        if not anonymous:
            headers["Authorization"] = f"Bearer {self.token}"
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode()
        return self._send(method, path, body, headers, anonymous=anonymous)

    def request_multipart(
        self,
        path: str,
        fields: dict[str, str | int],
        *,
        file_path: Path | None = None,
        file_content_type: str | None = None,
        file_data: bytes | None = None,
    ) -> Any:
        boundary = "----vva-skill-" + uuid.uuid4().hex
        body = bytearray()
        for name, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(str(value).encode())
            body.extend(b"\r\n")
        if file_path is not None:
            filename = file_path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
            )
            body.extend(f"Content-Type: {file_content_type or 'application/octet-stream'}\r\n\r\n".encode())
            body.extend(file_data if file_data is not None else file_path.read_bytes())
            body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode())
        headers = {
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        return self._send("POST", path, bytes(body), headers, anonymous=False)

    def _send(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: dict[str, str],
        *,
        anonymous: bool,
    ) -> Any:
        if not anonymous and not self.token:
            raise AuthenticationRequired(self.auth_guidance)
        headers = dict(headers)
        if not anonymous:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.api_url + path, data=body, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=45) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            # Never print raw error bodies: validation responses can echo secrets or prompts.
            details: object = None
            try:
                details = json.loads(exc.read(32768)).get("detail")
            except (ValueError, AttributeError):
                pass
            if exc.code == 409:
                message = "内容版本或请求号冲突。保留草稿，重新 pull 后对比；不要直接覆盖。"
            elif exc.code == 401:
                if anonymous:
                    message = "登录失败，请检查手机号和密码。"
                else:
                    raise AuthenticationRequired(self.auth_guidance) from None
            elif exc.code == 403:
                message = "当前账号无权执行此操作，请核对作品权限。"
            elif exc.code == 404:
                message = "作品、资产、草稿或媒体不存在，或当前账号没有访问权限。"
            elif exc.code == 410 and anonymous:
                message = "网页登录请求已过期或已经使用，请重新运行 login-web。"
            elif exc.code == 413:
                message = "图片超过服务器允许的上传大小，请压缩后再试。"
            elif exc.code == 415:
                message = "文件不是服务器支持且可解码的 PNG、JPEG 或 WebP 图片。"
            elif exc.code == 400:
                message = "媒体上传参数有误；替换时必须且只能提供文件或已有媒体 ID。"
            elif exc.code == 503:
                message = "VVA 媒体存储暂时不可用。不要更换 request_id；先 pull 核对，再原样重试。"
            elif exc.code == 422:
                message = "请求未通过校验，请对照 schema 检查字段、引用和比例。"
                if isinstance(details, list):
                    locations = [".".join(str(part) for part in item.get("loc", []))
                                 for item in details[:8] if isinstance(item, dict)]
                    if locations:
                        message += " 字段：" + "；".join(locations)
            elif 300 <= exc.code < 400:
                message = "拒绝携带凭证跟随重定向，请配置最终的 API 地址。"
            else:
                message = "VVA 请求失败，请检查服务日志或连接设置。"
            safe_detail = ""
            if isinstance(details, str):
                safe_detail = details
            elif isinstance(details, dict) and isinstance(details.get("message"), str):
                safe_detail = details["message"]
            if safe_detail and exc.code in {400, 409, 413, 415, 422, 503}:
                safe_detail = safe_detail.replace(self.token or "\0", "[已隐藏]").replace("\n", " ")[:300]
                message += " " + safe_detail
            raise ClientError(f"HTTP {exc.code}：{message}") from None
        except (URLError, TimeoutError, OSError):
            raise ClientError("VVA 连接失败或超时。写入结果可能不确定：先读取检查，重试须保留原 request_id 和内容。") from None
        if len(data) > MAX_RESPONSE_BYTES:
            raise ClientError("服务响应过大，请缩小作品范围")
        try:
            return json.loads(data) if data else None
        except ValueError:
            raise ClientError("服务未返回 JSON，请检查 VVA_API_URL 是否指向 /api/v1") from None


def validate_media_file(path_value: str, capabilities: dict[str, Any]) -> tuple[Path, str, str, bytes]:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        raise ClientError("媒体文件必须使用绝对路径")
    try:
        info = path.lstat()
    except OSError as exc:
        raise ClientError(f"无法读取媒体文件：{path}（{exc}）") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ClientError("媒体文件必须是本机普通文件，不能是目录、设备或符号链接")
    media_capability = capabilities.get("media") if isinstance(capabilities, dict) else None
    maximum = int(media_capability.get("maximum_upload_bytes", 0)) if isinstance(media_capability, dict) else 0
    if maximum <= 0:
        raise ClientError("VVA 未公布安全的媒体上传大小限制，请升级服务端")
    if info.st_size <= 0:
        raise ClientError("不能上传空文件")
    if info.st_size > maximum:
        raise ClientError(f"媒体文件超过服务器允许的 {maximum} 字节")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ClientError(f"读取媒体文件失败：{path}（{exc}）") from exc
    if len(data) > maximum:
        raise ClientError(f"媒体文件超过服务器允许的 {maximum} 字节")
    signature = data[:16]
    if signature.startswith(b"\x89PNG\r\n\x1a\n"):
        content_type = "image/png"
    elif signature.startswith(b"\xff\xd8\xff"):
        content_type = "image/jpeg"
    elif signature.startswith(b"RIFF") and signature[8:12] == b"WEBP":
        content_type = "image/webp"
    else:
        raise ClientError("文件内容不是 PNG、JPEG 或 WebP 图片")
    accepted = (
        media_capability.get("accepted_mime_types")
        or media_capability.get("accepted_content_types", [])
        if isinstance(media_capability, dict)
        else []
    )
    if content_type not in accepted:
        raise ClientError(f"服务器不接受 {content_type} 图片")
    return path, content_type, hashlib.sha256(data).hexdigest(), data


def validate_plan(plan: Any, *, create: bool) -> None:
    """Basic offline guards; backend's strict schema is the final authority."""
    if not isinstance(plan, dict):
        raise ClientError("计划必须是 JSON 对象")
    uuid_value(plan.get("request_id"))
    project = plan.get("project", {})
    if not isinstance(project, dict):
        raise ClientError("project 必须是对象")
    if create:
        uuid_value(project.get("id"))
        if not str(project.get("title", "")).strip():
            raise ClientError("新作品必须填写 title")
        if project.get("aspect_ratio") not in RATIOS:
            raise ClientError("新作品必须先与用户确定 aspect_ratio，不能省略或使用自适应")
    elif "aspect_ratio" in project and project["aspect_ratio"] not in RATIOS:
        raise ClientError("不支持的作品比例")
    all_ids: set[str] = set()
    for name in ENTITY_LISTS:
        rows = plan.get(name, [])
        if not isinstance(rows, list):
            raise ClientError(f"{name} 必须是数组")
        for row in rows:
            if not isinstance(row, dict):
                raise ClientError(f"{name} 中必须是对象")
            entity_id = uuid_value(row.get("id"))
            if entity_id in all_ids:
                raise ClientError("计划存在重复的实体 ID")
            all_ids.add(entity_id)
            if "expected_revision" in row and (
                type(row["expected_revision"]) is not int or row["expected_revision"] < 1
            ):
                raise ClientError("expected_revision 必须是正整数")
            if name == "shots":
                document = row.get("script_document", {})
                if not isinstance(document, dict):
                    raise ClientError("script_document 必须是对象；不修改请省略字段")
                content = document.get("content", [])
                if not isinstance(content, list):
                    raise ClientError("script_document.content 必须是数组")
                for node in content:
                    if not isinstance(node, dict):
                        raise ClientError("镜头脚本节点必须是对象")
                    if node.get("type") == "asset_mention" and not node.get("asset_id"):
                        raise ClientError("资产引用必须包含 asset_id，不能只有 @名称")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.getenv("VVA_API_URL", DEFAULT_API_URL))
    parser.add_argument("--web-url", default=os.getenv("VVA_WEB_URL"))
    parser.add_argument("--token-file", default=os.getenv("VVA_TOKEN_FILE", str(Path.home() / ".config/vva/session.json")))
    commands = parser.add_subparsers(dest="command", required=True)
    login = commands.add_parser("login", help="交互式登录，不向终端打印密码或令牌")
    login.add_argument("--phone", required=True)
    login_web = commands.add_parser("login-web", help="在 VVA 网页中登录并一次性授权当前 Skill")
    login_web.add_argument("--no-browser", action="store_true", help="只打印登录链接，不尝试打开浏览器")
    login_web.add_argument("--timeout-seconds", type=int, default=600)
    for command in ("whoami", "capabilities", "schema", "projects"):
        sub = commands.add_parser(command)
        sub.add_argument("--output")
    pull = commands.add_parser("pull", help="读取完整作品快照与 revision")
    pull.add_argument("project_id")
    pull.add_argument("--output")
    media_upload = commands.add_parser(
        "media-upload",
        help="只上传本地图片，不绑定资产或草稿",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  vva_client.py media-upload PROJECT_UUID --file /absolute/image.png "
            "--request-id REQUEST_UUID --output uploaded.json"
        ),
    )
    media_upload.add_argument("project_id")
    media_upload.add_argument("--file", required=True, help="本地普通图片文件的绝对路径")
    media_upload.add_argument("--request-id", required=True)
    media_upload.add_argument("--output")
    media_replace = commands.add_parser(
        "media-replace",
        help="原子上传并替换图片草稿，或绑定已上传的项目图片",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  vva_client.py media-replace PROJECT_UUID --draft-id DRAFT_UUID "
            "--asset-id ASSET_UUID --expected-revision 3 --file /absolute/image.png "
            "--request-id REQUEST_UUID --output replaced.json"
        ),
    )
    media_replace.add_argument("project_id")
    media_replace.add_argument("--draft-id", required=True)
    media_replace.add_argument("--asset-id", required=True)
    media_replace.add_argument("--expected-revision", required=True, type=int)
    source = media_replace.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="本地普通图片文件的绝对路径")
    source.add_argument("--media-file-id", help="由 media-upload 返回的媒体 UUID")
    media_replace.add_argument("--request-id", required=True)
    media_replace.add_argument("--output")
    for command in ("create", "push", "validate"):
        sub = commands.add_parser(command)
        if command == "push":
            sub.add_argument("project_id")
        sub.add_argument("--file", required=True)
        sub.add_argument("--output")
        if command == "validate":
            sub.add_argument("--create", action="store_true")
    scaffold = commands.add_parser("new-plan", help="比例必须先由用户确认")
    scaffold.add_argument("--title", required=True)
    scaffold.add_argument("--aspect-ratio", choices=RATIOS, required=True)
    scaffold.add_argument("--output")
    ids = commands.add_parser("ids", help="同一项目/逻辑键始终产生同一 UUID")
    ids.add_argument("project_id")
    ids.add_argument("keys", nargs="+")
    args = parser.parse_args(argv)
    try:
        resolved_web_url = web_endpoint(args.web_url or default_web_url(args.api_url))
        if args.command == "new-plan":
            result = {
                "request_id": str(uuid.uuid4()),
                "project": {"id": str(uuid.uuid4()), "title": args.title, "aspect_ratio": args.aspect_ratio},
                **{name: [] for name in ENTITY_LISTS},
            }
        elif args.command == "ids":
            namespace = uuid.UUID(uuid_value(args.project_id))
            result = {key: str(uuid.uuid5(namespace, key)) for key in args.keys}
        elif args.command == "validate":
            validate_plan(load_json(args.file), create=args.create)
            result = {"ok": True, "note": "基础格式检查通过；完整字段与关系仍由服务端严格校验。"}
        else:
            token = os.getenv("VVA_ACCESS_TOKEN")
            token_path = Path(args.token_file).expanduser()
            guidance = login_guidance(
                args.api_url,
                resolved_web_url,
                str(token_path),
                environment_token=bool(token),
            )
            if not token and token_path.exists() and args.command not in {"login", "login-web"}:
                session = load_json(str(token_path))
                if not isinstance(session, dict) or not session.get("access_token"):
                    raise AuthenticationRequired(guidance)
                if session.get("api_url") != endpoint(args.api_url):
                    raise AuthenticationRequired("已保存的凭证属于另一个 VVA API 地址。\n" + guidance)
                token = session.get("access_token")
            client = Client(args.api_url, token, auth_guidance=guidance)
            if args.command == "login":
                if not sys.stdin.isatty():
                    raise ClientError(
                        "当前执行环境不支持安全的密码输入，请改用网页登录：\n"
                        + browser_login_command(args.api_url, resolved_web_url, str(token_path))
                    )
                try:
                    password = getpass.getpass("VVA 密码（不显示）：")
                except EOFError as exc:
                    raise ClientError(
                        "无法读取终端密码，请改用网页登录：\n"
                        + browser_login_command(args.api_url, resolved_web_url, str(token_path))
                    ) from exc
                response = client.request("POST", "/auth/login", {"phone": args.phone, "password": password}, anonymous=True)
                save_json(str(token_path), {"api_url": client.api_url, "access_token": response["access_token"]}, private=True)
                result = {"ok": True, "token_file": str(token_path), "note": "登录凭证已仅保存到本机文件；不要分享此文件。"}
            elif args.command == "login-web":
                result = browser_login(
                    client,
                    web_url=resolved_web_url,
                    token_path=token_path,
                    open_browser=not args.no_browser,
                    timeout_seconds=args.timeout_seconds,
                )
            elif args.command == "whoami":
                result = client.request("GET", "/auth/me")
            elif args.command == "capabilities":
                result = client.request("GET", "/authoring/capabilities")
            elif args.command == "schema":
                result = client.request("GET", "/authoring/schema")
            elif args.command == "projects":
                result = client.request("GET", "/projects")
            elif args.command == "pull":
                result = client.request("GET", f"/authoring/projects/{uuid_value(args.project_id)}")
            elif args.command == "media-upload":
                project_id = uuid_value(args.project_id)
                request_id = uuid_value(args.request_id)
                capability = client.request("GET", "/authoring/capabilities")
                path, content_type, sha256, file_data = validate_media_file(args.file, capability)
                result = client.request_multipart(
                    f"/authoring/projects/{project_id}/media-uploads",
                    {"request_id": request_id},
                    file_path=path,
                    file_content_type=content_type,
                    file_data=file_data,
                )
                if result.get("client_sha256") != sha256 or result.get("media", {}).get("sha256") != sha256:
                    raise ClientError("VVA 返回的媒体校验值与本地文件不一致；不要继续绑定")
            elif args.command == "media-replace":
                project_id = uuid_value(args.project_id)
                request_id = uuid_value(args.request_id)
                fields: dict[str, str | int] = {
                    "request_id": request_id,
                    "draft_id": uuid_value(args.draft_id),
                    "asset_id": uuid_value(args.asset_id),
                    "expected_revision": args.expected_revision,
                }
                path = None
                content_type = None
                sha256 = None
                file_data = None
                if args.file:
                    capability = client.request("GET", "/authoring/capabilities")
                    path, content_type, sha256, file_data = validate_media_file(args.file, capability)
                else:
                    fields["media_file_id"] = uuid_value(args.media_file_id)
                result = client.request_multipart(
                    f"/authoring/projects/{project_id}/media-replacements",
                    fields,
                    file_path=path,
                    file_content_type=content_type,
                    file_data=file_data,
                )
                if sha256 and result.get("new_media", {}).get("sha256") != sha256:
                    raise ClientError("VVA 替换后的媒体校验值与本地文件不一致；请 pull 核对")
            else:
                plan = load_json(args.file)
                validate_plan(plan, create=args.command == "create")
                route = "/authoring/projects" if args.command == "create" else f"/authoring/projects/{uuid_value(args.project_id)}"
                result = client.request("POST" if args.command == "create" else "PATCH", route, plan)
        output = getattr(args, "output", None)
        if output:
            save_json(output, result)
            print(f"已保存：{output}")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ClientError, OSError, KeyError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消网页登录。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
