"""게시 대상 사이트 API: 로그인, 이미지 업로드, 글 등록.

사이트 주소와 API 모양은 코드에 두지 않고 비공개 설정 파일에서 읽는다.
    <STATE_DIR>/site_config.json
        {"api_base": "...", "host": "...", "login_id_field": "...",
         "upload_field": "...", "board": "..."}

환경변수:
    SITE_LOGIN_ID    로그인 아이디
    SITE_PASSWORD    로그인 비밀번호
    STATE_DIR        비공개 설정/상태 폴더 (기본 ./data)
"""

import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("STATE_DIR") or ROOT / "data")


def _config():
    path = STATE_DIR / "site_config.json"
    if not path.exists():
        raise RuntimeError(f"설정 파일이 없다: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _find_token(obj):
    if isinstance(obj, dict):
        for k in ("token", "accessToken", "access_token", "admin_token"):
            if isinstance(obj.get(k), str):
                return obj[k]
        for v in obj.values():
            t = _find_token(v)
            if t:
                return t
    return None


def _request(path, token=None, method="GET", payload=None, raw=None, ctype=None):
    cfg = _config()
    data = raw if raw is not None else (
        json.dumps(payload).encode("utf-8") if payload is not None else None)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(f"{cfg['api_base']}{path}", data=data,
                                 method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        # 응답 본문에는 사이트 정보가 섞일 수 있어 로그에 남기지 않는다.
        raise RuntimeError(f"API {method} 실패 {exc.code}") from exc


def login():
    cfg = _config()
    res = _request("/auth/login", method="POST", payload={
        cfg.get("login_id_field", "login_id"): os.environ["SITE_LOGIN_ID"],
        "password": os.environ["SITE_PASSWORD"],
    })
    token = _find_token(res)
    if not token:
        raise RuntimeError("로그인 응답에서 토큰을 찾지 못했다")
    return token


def upload_image(token, filename, data, content_type="image/jpeg"):
    """이미지를 올리고 사이트가 쓰는 전체 주소를 돌려준다."""
    cfg = _config()
    field = cfg.get("upload_field", "image")
    boundary = uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
        f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    res = _request("/upload", token, "POST", raw=body,
                   ctype=f"multipart/form-data; boundary={boundary}")
    url = res.get("imageUrl")
    if not url:
        raise RuntimeError("업로드 응답에 주소가 없다")
    return url if url.startswith("http") else cfg["host"] + url


def create_post(token, title, content_html, nickname, thumbnail="", board=None):
    cfg = _config()
    payload = {
        "board_type": board or cfg.get("board", "free"), "author_nickname": nickname,
        "is_notice": False, "is_pinned": False, "is_published": True,
        "title": title, "thumbnail": thumbnail, "content": content_html,
    }
    res = _request("/posts", token, "POST", payload=payload)
    post = res.get("post") or res.get("data") or res
    return post.get("id")


def move_post(token, post_id, board):
    """글을 다른 게시판으로 옮긴다 (본문/제목/닉네임은 그대로). 이미 그 게시판이면 아무것도 안 한다."""
    res = _request(f"/posts/{post_id}", token)
    d = res.get("post") or res.get("data") or res
    if d.get("board_type") == board:
        return False
    payload = {
        "board_type": board, "author_nickname": d.get("author_nickname", ""),
        "is_notice": d.get("is_notice", False), "is_pinned": d.get("is_pinned", False),
        "is_published": d.get("is_published", True), "title": d.get("title", ""),
        "thumbnail": d.get("thumbnail") or "", "content": d.get("content", ""),
    }
    _request(f"/posts/{post_id}", token, "PUT", payload=payload)
    return True
