"""게시 대상 사이트 API: 로그인, 이미지 업로드, 글 등록.

두 가지 방식을 지원한다.
  1) 열쇠 방식(새 사이트): 환경변수 NUDE_API_KEY(ntk_ 로 시작하는 열쇠)와 설정의 nude_api_base 가 있으면 쓴다.
     로그인 없이 Authorization: Bearer 열쇠 로 글/사진을 올린다.
  2) 로그인 방식(기존 사이트): 위가 없으면 SITE_LOGIN_ID / SITE_PASSWORD 로 로그인해서 올린다.

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
import re
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
    if key_backend():                      # 열쇠 방식은 로그인이 필요 없다
        return os.environ["NUDE_API_KEY"].strip()
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
    """이미지를 올리고 사이트가 쓰는 주소를 돌려준다."""
    if key_backend():
        return key_upload_image(filename, data, content_type)
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


def create_post(token, title, content_html, nickname, thumbnail="", board=None, source_key=None):
    if key_backend():
        return key_create_post(title, content_html, nickname, board or _config().get("nude_board", "free"), source_key)
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


# ---------------------------------------------------------------- 열쇠 방식 (새 사이트)

class NickRejected(Exception):
    """작성자 닉네임이 거절됨(실제 회원 닉네임과 같거나 운영자처럼 보임 등)."""


def key_backend():
    """열쇠 방식을 쓰는지. 열쇠와 새 사이트 주소가 모두 있어야 한다."""
    if not os.environ.get("NUDE_API_KEY", "").strip():
        return False
    try:
        return bool(_config().get("nude_api_base"))
    except RuntimeError:
        return False


def _krequest(path, method="GET", payload=None, raw=None, ctype=None):
    """새 사이트 API 호출. 성공하면 (HTTP 상태, 응답 JSON), 실패하면 RuntimeError(상태, 까닭)."""
    base = _config()["nude_api_base"].rstrip("/")
    key = os.environ["NUDE_API_KEY"].strip()
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    else:
        data = raw
        if ctype:
            headers["Content-Type"] = ctype
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        try:
            info = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            info = {}
        raise RuntimeError(f"{exc.code}|{str(info.get('까닭', ''))[:120]}") from exc


def key_upload_image(filename, data, content_type):
    """사진을 올리고 본문에 넣을 주소(/media/...)를 돌려준다. 파일 자체를 몸으로 보낸다."""
    _, res = _krequest("/api/admin/media", "POST", raw=data, ctype=content_type)
    if not res.get("됨") or not res.get("주소"):
        raise RuntimeError("업로드 응답에 주소가 없다")
    return res["주소"]


def key_create_post(title, body_html, nickname, board, source_key=None):
    """글을 올리고 글 id 를 돌려준다. 같은 sourceKey 가 이미 있으면 그 글 id 를 돌려준다."""
    payload = {"board": board, "title": title[:120], "author": nickname, "body": body_html, "visible": True}
    if source_key and re.fullmatch(r"[A-Za-z0-9:_./-]{1,100}", source_key):
        payload["sourceKey"] = source_key
    try:
        status, res = _krequest("/api/admin/posts", "POST", payload=payload)
    except RuntimeError as exc:
        code, _, why = str(exc).partition("|")
        if code == "400" and any(w in why for w in ("작성자", "닉네임", "author", "운영자", "회원")):
            raise NickRejected(why) from exc
        raise
    if not res.get("됨"):
        raise RuntimeError(f"등록 실패: {str(res.get('까닭', ''))[:80]}")
    return (res.get("글") or {}).get("id")
