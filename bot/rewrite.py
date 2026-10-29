"""읽은 글을 커뮤니티 말투로 가볍게 다시 쓴다 (Gemini 무료 API 또는 Claude Code CLI)."""

import base64
import io
import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request

PROMPT = """\
아래는 커뮤니티 유머 게시판에 올라온 글이다. 이 글을 같은 게시판 말투로 가볍게 다시 써라.

[원글 제목]
{title}

[원글 본문]  (이미지는 [IMG1], [IMG2] ... 로 표시했다)
{body}

다시 쓰는 규칙:
- 말투는 원글과 같은 게시판 말투를 그대로 따라 한다. 반말, ~함/~했다고 함, ㅋㅋ, ㄷㄷ,
  짧은 문장, 줄바꿈 많이. 새 유행어를 억지로 넣지 마라.
- 내용에는 살을 조금 붙이거나 조금 뺀다. 가벼운 한마디 정도만 더하고, 사실은 지어내지 마라.
  원글에 없는 숫자, 이름, 사건을 만들어 넣지 마라.
- 원글 문장을 그대로 복사하지 마라. 같은 내용을 네 말로 다시 써라.
- 이미지는 의미가 이어지는 자리에 [IMG1] 형태로 그대로 둔다. 순서를 바꿔도 되지만 빼먹지 마라.
- 이미지가 글의 핵심이면(밈, 짤, 사진 모음) 글은 짧게 쓰고 이미지에 한마디씩 붙여라.
- 제목은 원글 제목을 그대로 쓰지 말고 비슷한 느낌으로 바꿔라. 말머리(싱글벙글 등)는 쓰지 마라.
- 길이는 원글과 비슷하게. 억지로 늘리지 마라.
- 다음 소재는 쓰지 말고 skip 처리해라: 선정적/성적인 내용, 정치, 종교, 사건사고, 범죄,
  질병, 죽음, 실존 인물 비방, 특정 업체 광고.
- 만화, 웹툰, 카툰, 4컷 만화, 만평, 일러스트, 팬아트, 애니/게임 캐릭터 그림처럼 그린 그림이 글의 핵심인 글도 skip 처리해라.
  글과 함께 첨부된 이미지를 직접 보고 판단해라. 이미지가 만화 컷, 애니 캡처, 캐릭터 일러스트, 팬아트면 글이 어떻든 skip 이다.
  (실제 사진, 짤방 캡처, 커뮤니티 글 캡처, 표/그래프는 만화가 아니다.)

먼저 이 글이 우리 커뮤니티(한국 스포츠 커뮤니티의 자유게시판)와 결이 맞는지 1~5점(fit)으로 엄격하게 매겨라.
우리 게시판에 올라와도 자연스러운 글만 4점 이상이다. 애매하면 낮게 줘라.
- 4~5점: 직장·학교·연애·가족·생활 공감 썰, 웃긴 상황·실수담·반전 일화, 꿀팁·정보, 음식·여행·동물·일상,
  스포츠나 게임 이야기(실제 사진·경기·플레이 중심), 누가 봐도 바로 이해되고 웃기거나 공감되는 글.
- 1~2점: 만화·애니·캐릭터 그림·서브컬처, 원작이나 특정 밈 배경지식이 있어야 이해되는 글,
  대사만 인용한 의미 불명 이미지 짤, 특정 커뮤니티 내부 유행어·말투로만 웃기는 글, 정치·젠더 갈등·혐오·논쟁,
  사건사고·질병·죽음, 광고, 글이나 맥락 없이 이미지 한 장만 던진 글, 전문 용어가 많은 글.
- 3점: 평범하지만 딱히 끌리지 않는 글.
점수와 함께 이유를 한 줄로 적어라(fit_reason).

게시판 분류(board): 글 성격을 보고 올릴 게시판을 정해라.
- "sports_talk": 스포츠(축구, 야구, 농구, 배구, 골프, 격투기, 올림픽, 선수, 경기, 감독)가 글의 중심인 경우
- "humor": 짤, 밈, 개그처럼 웃기는 것 자체가 핵심인 글 (특히 이미지 위주로 웃기는 글)
- "free": 그 밖의 일상 썰, 공감/잡담, 게임, 애니, 연예, 사회 이야기 등
헷갈리면 "free" 로 해라.

출력은 JSON 하나만, 설명 없이:
{{"skip": false, "fit": 4, "fit_reason": "한 줄 이유", "board": "free", "title": "제목", "body": ["문단 또는 줄", "[IMG1]", "문단 ..."]}}
skip 이면: {{"skip": true, "reason": "이유"}}
"""


def blocks_to_prompt(blocks):
    lines, n = [], 0
    for b in blocks:
        if b["t"] == "img":
            n += 1
            lines.append(f"[IMG{n}]")
        else:
            lines.append(b["v"])
    return "\n".join(lines), n


def _extract_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("JSON 을 찾지 못했다")
    return json.loads(m.group(0))


# ---------------------------------------------------------------- 모델 호출
#
# REWRITE_PROVIDER 로 고른다.
#   gemini : Google Gemini 무료 API (GEMINI_API_KEY 필요). Claude 사용량이 차감되지 않는다.
#   claude : Claude Code CLI (CLAUDE_CODE_OAUTH_TOKEN 필요). Claude 요금제 사용량이 차감된다.
# 지정하지 않으면 GEMINI_API_KEY 가 있을 때 gemini, 없으면 claude.

BOARDS = ("free", "humor", "sports_talk")   # 올릴 수 있는 게시판 코드
DEFAULT_BOARD = "free"
MIN_FIT = 4            # 이 점수 미만이면 올리지 않는다
MIN_FIT_SHORT = 5      # 글자가 거의 없는 이미지 위주 글은 더 엄격하게
SHORT_TEXT_CHARS = 40
GEMINI_MODELS = os.environ.get("GEMINI_MODELS") or "gemini-3.8-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_TIMEOUT = 50         # 모델 한 번 호출을 기다리는 최대 초. 넘으면 다음 모델로 넘어간다
GEMINI_MIN_INTERVAL = 7.0   # 무료 등급 분당 호출 제한(약 10회)을 넘지 않도록 호출 사이 최소 간격(초)
_last_gemini_call = 0.0


def provider():
    p = (os.environ.get("REWRITE_PROVIDER") or "").lower()
    if p in ("gemini", "claude"):
        return p
    return "gemini" if os.environ.get("GEMINI_API_KEY") else "claude"


class Blocked(Exception):
    """모델이 안전 필터로 응답을 거부한 경우. 글 문제이므로 건너뛴다."""


def _image_parts(images, max_side=768):
    """모델에 보여줄 이미지를 작은 JPEG(base64)로 줄인다. 실패한 이미지는 건너뛴다."""
    out = []
    try:
        from PIL import Image
    except ImportError:
        return out
    for raw in images or []:
        try:
            im = Image.open(io.BytesIO(raw))
            if getattr(im, "is_animated", False):
                im.seek(0)
            im = im.convert("RGB")
            im.thumbnail((max_side, max_side))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
            out.append(base64.b64encode(buf.getvalue()).decode("ascii"))
        except Exception:  # noqa: BLE001
            continue
    return out


def _call_claude(prompt, timeout):
    proc = subprocess.run(
        ["claude", "-p", "--output-format", "json"],
        input=prompt, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude 실행 실패: {proc.stderr[:200]}")
    outer = json.loads(proc.stdout)
    if outer.get("is_error"):
        raise RuntimeError(f"claude 오류: {str(outer.get('result'))[:200]}")
    return outer.get("result", "")


def _call_gemini(prompt, timeout, images=None):
    global _last_gemini_call
    key = os.environ["GEMINI_API_KEY"]
    wait = GEMINI_MIN_INTERVAL - (time.monotonic() - _last_gemini_call)
    if wait > 0:
        time.sleep(wait)
    last = None
    for model in [m.strip() for m in GEMINI_MODELS.split(",") if m.strip()]:
        parts = [{"text": prompt}] + [
            {"inline_data": {"mime_type": "image/jpeg", "data": b64}} for b64 in _image_parts(images)]
        body = json.dumps({
            "contents": [{"parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
        }).encode("utf-8")
        req = urllib.request.Request(
            GEMINI_URL.format(model=model), data=body, method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
        )
        _last_gemini_call = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 한도 초과(429)/모델 없음(404)/일시 장애(5xx)면 다음 모델로 넘어간다
            last = f"{model}: HTTP {exc.code}"
            if exc.code in (429, 404, 500, 503):
                continue
            raise RuntimeError(f"gemini 오류 {exc.code}") from exc
        except (TimeoutError, socket.timeout, urllib.error.URLError, ConnectionError) as exc:
            # 응답이 늦거나 연결이 끊기면 기다리지 말고 다음 모델로 넘어간다
            last = f"{model}: {type(exc).__name__}"
            continue
        if (out.get("promptFeedback") or {}).get("blockReason"):
            raise Blocked(out["promptFeedback"]["blockReason"])
        cands = out.get("candidates") or []
        if not cands:
            raise RuntimeError("gemini 응답에 후보가 없다")
        if cands[0].get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            raise Blocked(cands[0]["finishReason"])
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        if text.strip():
            return text
        last = f"{model}: 빈 응답"
    raise RuntimeError(f"gemini 모든 모델 실패 ({last})")


def rewrite(title, blocks, timeout=300, images=None):
    body, img_count = blocks_to_prompt(blocks)
    prompt = PROMPT.format(title=title, body=body[:6000])
    try:
        text = _call_gemini(prompt, min(timeout, GEMINI_TIMEOUT), images) if provider() == "gemini" else _call_claude(prompt, timeout)
    except Blocked as exc:
        return None, f"모델 안전 필터({exc})"
    data = _extract_json(text)
    if data.get("skip"):
        return None, data.get("reason", "")
    if not data.get("title") or not data.get("body"):
        raise ValueError("제목/본문이 비었다")
    try:
        fit = int(data.get("fit", 0))
    except (TypeError, ValueError):
        fit = 0
    text_chars = sum(len(b["v"]) for b in blocks if b["t"] == "text")
    need = MIN_FIT_SHORT if text_chars < SHORT_TEXT_CHARS else MIN_FIT
    if fit < need:   # 점수가 없거나 낮으면 올리지 않는다
        return None, f"결 불일치(fit={fit}, 기준 {need}): {str(data.get('fit_reason', ''))[:60]}"
    if data.get("board") not in BOARDS:   # 모르는 값이면 기본 게시판
        data["board"] = DEFAULT_BOARD
    if isinstance(data["body"], str):
        data["body"] = [x for x in data["body"].split("\n") if x.strip()]
    used = set(re.findall(r"\[IMG(\d+)\]", "\n".join(data["body"])))
    for i in range(1, img_count + 1):  # 빠진 이미지는 맨 뒤에 붙인다
        if str(i) not in used:
            data["body"].append(f"[IMG{i}]")
    return data, ""
