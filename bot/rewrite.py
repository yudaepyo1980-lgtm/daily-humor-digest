"""읽은 글을 커뮤니티 말투로 가볍게 다시 쓴다 (Claude Code CLI 사용)."""

import json
import re
import subprocess

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

출력은 JSON 하나만, 설명 없이:
{{"skip": false, "title": "제목", "body": ["문단 또는 줄", "[IMG1]", "문단 ..."]}}
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


def rewrite(title, blocks, timeout=300):
    body, img_count = blocks_to_prompt(blocks)
    prompt = PROMPT.format(title=title, body=body[:6000])
    proc = subprocess.run(
        ["claude", "-p", "--output-format", "json"],
        input=prompt, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude 실행 실패: {proc.stderr[:200]}")
    outer = json.loads(proc.stdout)
    if outer.get("is_error"):
        raise RuntimeError(f"claude 오류: {str(outer.get('result'))[:200]}")
    data = _extract_json(outer.get("result", ""))
    if data.get("skip"):
        return None, data.get("reason", "")
    if not data.get("title") or not data.get("body"):
        raise ValueError("제목/본문이 비었다")
    used = set(re.findall(r"\[IMG(\d+)\]", "\n".join(data["body"])))
    for i in range(1, img_count + 1):  # 빠진 이미지는 맨 뒤에 붙인다
        if str(i) not in used:
            data["body"].append(f"[IMG{i}]")
    return data, ""
