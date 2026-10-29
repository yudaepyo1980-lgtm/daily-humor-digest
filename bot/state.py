"""data/state.json 읽기/쓰기. 중복 방지와 시간당 스케줄을 여기에 보관한다."""

import json
import os
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 비공개 상태 폴더. Actions 에서는 비공개 저장소를 내려받은 곳을 가리킨다.
STATE_DIR = Path(os.environ.get("STATE_DIR") or ROOT / "data")
STATE_PATH = STATE_DIR / "state.json"
NICK_PATH = STATE_DIR / "nicknames.json"

DEFAULT = {
    "seen": [],        # "todayhumor:483994" 형태. 올렸든 건너뛰었든 다시 보지 않는다.
    "titles": [],      # 원글 제목 (같은 제목이 다른 번호로 올라오는 경우 방지)
    "posted": [],      # {"key","admin_id","nick","title","at"}
    "queue": [],       # 아직 안 쓴 후보 (추천수 순)
    "queue_at": "",    # 후보를 마지막으로 읽은 시각
    "last_run": "",    # 마지막 스케줄 실행 시각
    "carry": 0.0,      # 편수 계산에서 남은 소수점
    "recent_nicks": [],
    "fails": {},       # 글별 기술 실패 횟수
    "fail_stats": {},  # 실패 이유별 누적 횟수
    "skip_stats": {},  # 건너뛴 이유별 누적 횟수
}


def load():
    if STATE_PATH.exists():
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return {**DEFAULT, **data}
    return json.loads(json.dumps(DEFAULT))


def save(state):
    state["seen"] = state["seen"][-3000:]
    state["titles"] = state["titles"][-3000:]
    state["posted"] = state["posted"][-1000:]
    state["fails"] = dict(list(state.get("fails", {}).items())[-300:])
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def pick_nick(state):
    """최근에 쓴 10개를 빼고 랜덤으로 뽑는다."""
    nicks = json.loads(NICK_PATH.read_text(encoding="utf-8"))
    recent = set(state["recent_nicks"][-10:])
    pool = [n for n in nicks if n not in recent] or nicks
    nick = random.choice(pool)
    state["recent_nicks"] = (state["recent_nicks"] + [nick])[-20:]
    return nick
