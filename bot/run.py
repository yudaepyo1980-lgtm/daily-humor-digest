"""커뮤니티 베스트(기본 루리웹 유머) -> 재작성 -> 어드민 자유게시판 등록.

    python -m bot.run                 스케줄대로 (GitHub Actions 가 30분마다 호출)
    python -m bot.run --dry-run       읽고 재작성만 하고 out/ 에 미리보기를 만든다 (등록 안 함)
    python -m bot.run --dry-run --count 2
    python -m bot.run --count 1       스케줄 무시하고 지금 1편 등록

스케줄: 마지막 실행 이후 흐른 시간 x (시간당 2~3편) 만큼 올린다. 실행이 늦게 와도 밀린 만큼
따라잡고, 편 사이는 랜덤 간격을 둔다.
"""

import argparse
import html
import mimetypes
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import importlib

from bot import admin, filters, imaging, rewrite, state as st

# 소스는 SOURCE 환경변수로 고른다 (bot/source_<이름>.py). 기본은 ruliweb.
src = importlib.import_module("bot.source_" + os.environ.get("SOURCE", "ruliweb"))

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

POSTS_PER_HOUR = (2, 3)
MAX_PER_RUN = 8
QUEUE_REFRESH_MIN = 55
MAX_IMAGES = 10
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MIN_OKNOK = 5
MAX_FAILS_PER_ITEM = 3     # 같은 글이 기술적 이유로 이 횟수 실패하면 포기한다
MAX_CONSECUTIVE_FAILS = 4  # 연속으로 이만큼 실패하면 시스템 문제로 보고 실행을 실패 처리한다
_consecutive_fails = 0


def now_kst():
    return datetime.now(KST)


IN_CI = os.environ.get("GITHUB_ACTIONS") == "true"


def log(msg):
    print(f"[{now_kst():%H:%M:%S}] {msg}", flush=True)


def plog(msg):
    """글 제목/닉네임이 들어간 로그. 공개 Actions 로그에는 남기지 않는다."""
    if not IN_CI:
        log(msg)


# ---------------------------------------------------------------- 후보

def refresh_queue(state, force=False):
    last = state.get("queue_at")
    fresh = False
    if last and not force:
        age = (now_kst() - datetime.fromisoformat(last)).total_seconds() / 60
        fresh = age < QUEUE_REFRESH_MIN and len(state["queue"]) >= 3
    if fresh:
        return
    log("후보 목록 읽는 중")
    seen, titles = set(state["seen"]), set(state["titles"])
    cands = []
    for it in src.fetch_candidates(pages=3):
        key = f"{it['src']}:{it['no']}"
        if key in seen or it["title"] in titles or it["oknok"] < MIN_OKNOK:
            continue
        if filters.is_blocked(it["title"]):
            continue
        it["key"] = key
        cands.append(it)
    cands.sort(key=lambda x: (x["oknok"], x["hits"]), reverse=True)
    state["queue"] = cands[:40]
    state["queue_at"] = now_kst().isoformat()
    log(f"후보 {len(cands)}개 중 상위 {len(state['queue'])}개 대기열")


# ---------------------------------------------------------------- 한 편 처리

def to_html(body, image_urls):
    parts = []
    for line in body:
        m = re.fullmatch(r"\s*\[IMG(\d+)\]\s*", line)
        if m:
            url = image_urls.get(int(m.group(1)))
            if url:
                parts.append(f'<p><img src="{url}" style="max-width:100%;height:auto;" /></p>')
            continue
        parts.append("<p>" + html.escape(line).replace("\n", "<br>") + "</p>")
    return "\n".join(parts)


def tech_fail(state, item, what, dry):
    """읽기/재작성 같은 기술적 실패. 글 문제가 아니므로 '본 글' 로 치지 않고 다시 시도하게 둔다.
    같은 글이 여러 번 실패하면 포기하고, 연속 실패가 많으면 실행 전체를 실패로 끝내 눈에 띄게 한다."""
    global _consecutive_fails
    _consecutive_fails += 1
    key = item["key"]
    fails = state.setdefault("fails", {})
    fails[key] = fails.get(key, 0) + 1
    log(f"  {what} (이 글 {fails[key]}번째, 연속 {_consecutive_fails}번째)")
    if not dry and fails[key] < MAX_FAILS_PER_ITEM:
        if key in state["seen"]:
            state["seen"].remove(key)
        if item["title"] in state["titles"]:
            state["titles"].remove(item["title"])
    if _consecutive_fails >= MAX_CONSECUTIVE_FAILS:
        raise RuntimeError(f"연속 {_consecutive_fails}번 실패: {what}. 시스템 문제로 보고 중단한다.")


def process_one(state, token, dry):
    """대기열에서 쓸 만한 글 하나를 골라 올린다. 올렸으면 True."""
    global _consecutive_fails
    while state["queue"]:
        item = state["queue"].pop(0)
        key = item["key"]
        if not dry:
            state["seen"].append(key)
            state["titles"].append(item["title"])
        plog(f"선택: [{item['oknok']}추] {item['title']} ({key})")
        log("글 처리 시작")

        try:
            post = src.fetch_post(item["no"], **({"board": item["board"]} if "board" in item else {}))
        except Exception as exc:  # noqa: BLE001
            tech_fail(state, item, f"본문 읽기 실패 ({type(exc).__name__})", dry)
            continue
        _consecutive_fails = 0
        blocks = post["blocks"]
        imgs = [b for b in blocks if b["t"] == "img"]
        text_len = sum(len(b["v"]) for b in blocks if b["t"] == "text")
        if post["has_embed"]:
            log("  영상/임베드 포함 -> 건너뜀")
            continue
        if not blocks or (text_len < 15 and not imgs) or len(imgs) > MAX_IMAGES:
            log("  내용이 비었거나 이미지가 너무 많음 -> 건너뜀")
            continue

        try:
            data, reason = rewrite.rewrite(item["title"], blocks)
        except Exception as exc:  # noqa: BLE001
            tech_fail(state, item, f"재작성 실패 ({type(exc).__name__}: {str(exc)[:80]})", dry)
            continue
        _consecutive_fails = 0
        if data is None:
            log("  재작성 단계에서 제외")
            plog(f"    이유: {reason}")
            continue

        image_urls, files = {}, {}
        for n, b in enumerate(imgs, start=1):
            try:
                raw = src.download_image(b["src"])
                if len(raw) > MAX_IMAGE_BYTES:
                    raise RuntimeError("이미지가 너무 큼")
                ctype = mimetypes.guess_type(b["src"].split("?")[0])[0] or "image/jpeg"
                name = f"{item['no']}_{n}" + (mimetypes.guess_extension(ctype) or ".jpg")
                if dry:
                    OUT.mkdir(exist_ok=True)
                    (OUT / name).write_bytes(raw)
                    image_urls[n] = name
                else:
                    raw, new_ctype, name = imaging.enhance(raw, name)
                    image_urls[n] = admin.upload_image(token, name, raw, new_ctype or ctype)
            except Exception as exc:  # noqa: BLE001
                log(f"  이미지 {n} 실패")
        if imgs and not image_urls:
            tech_fail(state, item, "이미지가 하나도 안 올라감", dry)
            continue

        content = to_html(data["body"], image_urls)
        thumb = image_urls.get(1) or next(iter(image_urls.values()), "")
        if dry:
            OUT.mkdir(exist_ok=True)
            path = OUT / f"{item['no']}.html"
            path.write_text(
                f'<meta charset="utf-8"><body style="max-width:720px;margin:20px auto;'
                f'font:16px/1.7 sans-serif"><h2>{html.escape(data["title"])}</h2>'
                f'<p style="color:#888">원글: {html.escape(item["title"])} · 추천 '
                f'{item["oknok"]} · {post["url"]}</p>'
                f"{content}</body>", encoding="utf-8")
            log("  미리보기 생성 (등록 안 함)")
            plog(f"  미리보기 저장: {path}")
            return True

        nick = st.pick_nick(state)
        admin_id = admin.create_post(token, data["title"], content, nick, thumb)
        state["posted"].append({
            "key": key, "admin_id": admin_id, "nick": nick,
            "title": data["title"], "at": now_kst().isoformat(),
        })
        _consecutive_fails = 0
        state.get("fails", {}).pop(key, None)
        log("  등록 완료")
        plog(f"    id={admin_id} [{nick}] {data['title']}")
        return True
    return False


# ---------------------------------------------------------------- 스케줄
#
# GitHub 예약 실행은 늦게, 불규칙하게 온다 (몇 시간 간격일 수도 있다).
# 그래서 "몇 시에 몇 편" 이 아니라 "마지막 실행 이후 흐른 시간 x 시간당 편수" 로 계산해서
# 밀린 만큼 한꺼번에 따라잡는다. 소수점은 carry 에 남겨 다음 실행으로 넘긴다.

MAX_CATCHUP_HOURS = 4      # 아무리 오래 쉬었어도 이만큼만 따라잡는다
MAX_PER_DAY = 72           # 하루 상한 (시간당 3편 x 24)


def plan_count(state):
    """이번 실행에서 올릴 편수를 정한다."""
    now = now_kst()
    last = state.get("last_run")
    hours = 1.0
    if last:
        hours = (now - datetime.fromisoformat(last)).total_seconds() / 3600
    hours = max(0.0, min(hours, MAX_CATCHUP_HOURS))

    budget = float(state.get("carry", 0.0)) + hours * random.uniform(*POSTS_PER_HOUR)
    n = int(budget)
    state["carry"] = round(budget - n, 3)

    day_ago = now - timedelta(hours=24)
    last_day = sum(1 for p in state["posted"] if datetime.fromisoformat(p["at"]) >= day_ago)
    n = max(0, min(n, MAX_PER_RUN, MAX_PER_DAY - last_day))
    log(f"마지막 실행 후 {hours:.1f}시간 -> 이번 계획 {n}편 (최근 24시간 {last_day}편)")
    return n


MAX_RUN_MINUTES = 150      # 한 번 실행에서 편 사이 대기까지 포함해 쓸 수 있는 최대 시간
MIN_GAP_SEC = 45
MAX_GAP_SEC = 45 * 60


def random_gap():
    """편 사이 대기 시간. 지수분포라서 짧게 연달아 올리기도, 길게 쉬기도 한다."""
    mean = 3600 / sum(POSTS_PER_HOUR) * 2          # 시간당 평균 편수의 간격 (약 24분)
    return max(MIN_GAP_SEC, min(MAX_GAP_SEC, random.expovariate(1 / mean)))


def run_schedule(state, token, dry):
    n = plan_count(state)
    state["last_run"] = now_kst().isoformat()
    st.save(state)
    if n <= 0:
        return
    refresh_queue(state)
    st.save(state)
    started = time.monotonic()
    time.sleep(random.randint(0, 120))  # 항상 같은 초에 올라가지 않게
    for k in range(n):
        if k:
            gap = random_gap()
            if (time.monotonic() - started + gap) / 60 > MAX_RUN_MINUTES:
                # 시간이 모자라면 남은 편수는 다음 실행으로 넘긴다
                state["carry"] = round(float(state.get("carry", 0.0)) + (n - k), 3)
                log(f"실행 시간 한도로 {n - k}편은 다음 실행으로 넘긴다")
                break
            log(f"다음 글까지 {gap / 60:.1f}분 대기")
            time.sleep(gap)
        if not process_one(state, token, dry):
            log("대기열이 비었다. 다음 실행 때 다시 읽는다.")
            state["queue_at"] = ""
            break
        state["last_run"] = now_kst().isoformat()
        st.save(state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--count", type=int, default=0)
    args = ap.parse_args()

    state = st.load()
    token = None if args.dry_run else admin.login()

    if args.count:
        refresh_queue(state, force=True)
        for i in range(args.count):
            if i and not args.dry_run:
                time.sleep(random.randint(10, 30))
            if not process_one(state, token, args.dry_run):
                break
        if not args.dry_run:
            st.save(state)
        return

    if args.dry_run:
        refresh_queue(state, force=True)
        process_one(state, token, True)
        return
    try:
        run_schedule(state, token, False)
    finally:
        st.save(state)


if __name__ == "__main__":
    sys.exit(main())
