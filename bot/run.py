"""오늘의유머 베스트 -> 재작성 -> 어드민 자유게시판 등록.

    python -m bot.run                 스케줄대로 (GitHub Actions 가 10분마다 호출)
    python -m bot.run --dry-run       읽고 재작성만 하고 out/ 에 미리보기를 만든다 (등록 안 함)
    python -m bot.run --dry-run --count 2
    python -m bot.run --count 1       스케줄 무시하고 지금 1편 등록

스케줄: 한 시간마다 5~10편을 정하고, 그 시간 안의 랜덤한 시각에 하나씩 올린다.
Actions 는 10분마다 깨어나서 "시각이 지난 슬롯"만 처리한다.
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

from bot import admin, filters, rewrite, state as st

# 소스는 SOURCE 환경변수로 고른다 (bot/source_<이름>.py). 기본은 ruliweb.
src = importlib.import_module("bot.source_" + os.environ.get("SOURCE", "ruliweb"))

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

POSTS_PER_HOUR = (2, 3)
MAX_PER_RUN = 3
QUEUE_REFRESH_MIN = 55
MAX_IMAGES = 10
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MIN_OKNOK = 5


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


def process_one(state, token, dry):
    """대기열에서 쓸 만한 글 하나를 골라 올린다. 올렸으면 True."""
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
            log(f"  본문 읽기 실패 ({type(exc).__name__})")
            continue
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
            log("  재작성 실패")
            continue
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
                    image_urls[n] = admin.upload_image(token, name, raw, ctype)
            except Exception as exc:  # noqa: BLE001
                log(f"  이미지 {n} 실패")
        if imgs and not image_urls:
            log("  이미지가 하나도 안 올라가서 건너뜀")
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
        log("  등록 완료")
        plog(f"    id={admin_id} [{nick}] {data['title']}")
        return True
    return False


# ---------------------------------------------------------------- 스케줄

def make_plan(state):
    now = now_kst()
    hour_key = now.strftime("%Y-%m-%dT%H")
    plan = state.get("plan") or {}
    if plan.get("hour") == hour_key:
        return plan
    end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    remain = max(60, int((end - now).total_seconds()))
    n = random.randint(*POSTS_PER_HOUR)
    slots = sorted(now + timedelta(seconds=random.randint(0, remain - 1)) for _ in range(n))
    plan = {"hour": hour_key, "slots": [s.isoformat() for s in slots], "done": 0}
    state["plan"] = plan
    log(f"이번 시간 계획: {n}편")
    plog("   시각 " + ", ".join(f"{s:%H:%M}" for s in slots))
    return plan


def run_schedule(state, token, dry):
    plan = make_plan(state)
    now = now_kst()
    due = [i for i, s in enumerate(plan["slots"])
           if i >= plan["done"] and datetime.fromisoformat(s) <= now]
    if not due:
        log(f"지금 올릴 슬롯 없음 (이번 시간 {plan['done']}/{len(plan['slots'])})")
        return
    refresh_queue(state)
    st.save(state)
    time.sleep(random.randint(0, 30))  # 항상 같은 초에 올라가지 않게
    for k, _ in enumerate(due[:MAX_PER_RUN]):
        if k:
            time.sleep(random.randint(15, 45))
        ok = process_one(state, token, dry)
        plan["done"] += 1
        st.save(state)
        if not ok:
            log("대기열이 비었다. 다음 실행 때 다시 읽는다.")
            state["queue_at"] = ""
            break


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
    run_schedule(state, token, False)
    st.save(state)


if __name__ == "__main__":
    sys.exit(main())
