"""루리웹 유머 베스트 목록과 글 본문을 읽는다.

읽기 전용이다. 로그인하지 않고, 공개된 목록/본문 페이지만 가져온다.
source_todayhumor 와 같은 모양(fetch_candidates / fetch_post / download_image)이라
bot.run 이 SOURCE 환경변수로 소스를 고른다.
"""

import html
import re
from html.parser import HTMLParser

from bot.source_todayhumor import http_get  # 요청 간격/UA 는 같은 규칙을 쓴다

BASE = "https://bbs.ruliweb.com"
BOARD = "300143"          # 유머 베스트 게시판 번호
TABLE = "best/humor"
LIST_URL = BASE + "/best/humor/now?page={page}"

ROW = re.compile(r'<tr class="table_body[^"]*".*?</tr>', re.S)


def parse_list(page_html):
    items = []
    for row in ROW.findall(page_html):
        href = re.search(r'href="/best/board/(\d+)/read/(\d+)', row)
        # 상단 고정 줄은 <strong>, 일반 줄은 <span> 에 제목이 들어 있다.
        title = re.search(r'<(?:strong|span) class="text_over">(.*?)</(?:strong|span)>', row, re.S)
        rec = re.search(r'<td class="recomd">\s*(-?\d+)', row)
        hit = re.search(r'<td class="hit">\s*([\d,]+)', row)
        reply = re.search(r'num_reply[^>]*>\s*\((\d+)\)', row)
        if not (href and title and rec):
            continue
        items.append({
            "src": "ruliweb",
            "board": href.group(1),
            "no": href.group(2),
            "title": html.unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip(),
            "oknok": int(rec.group(1)),
            "hits": int((hit.group(1) if hit else "0").replace(",", "")),
            "comments": int(reply.group(1)) if reply else 0,
        })
    return items


def fetch_candidates(pages=3):
    out, seen = [], set()
    for page in range(1, pages + 1):
        try:
            for it in parse_list(http_get(LIST_URL.format(page=page))):
                if it["no"] not in seen:     # 상단 고정 줄이 페이지마다 겹친다
                    seen.add(it["no"])
                    out.append(it)
        except Exception as exc:  # noqa: BLE001
            print(f"목록 {page}페이지 실패: {exc}")
            break
    return out


class _Body(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.buf, self.has_embed = [], [], False

    def _flush(self):
        text = re.sub(r"[ \t ]+", " ", "".join(self.buf)).strip()
        if text:
            self.blocks.append({"t": "text", "v": text})
        self.buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "img":
            src = a.get("data-original") or a.get("data-src") or a.get("src") or ""
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("http") and "emoticon" not in src and "/icon" not in src:
                self._flush()
                self.blocks.append({"t": "img", "src": src})
        elif tag in ("iframe", "video", "embed", "object"):
            self.has_embed = True
        elif tag in ("br", "p", "div", "li", "hr"):
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("p", "div", "li"):
            self._flush()

    def handle_data(self, data):
        self.buf.append(data)


def fetch_post(no, board=BOARD):
    url = f"{BASE}/best/board/{board}/read/{no}"
    page = http_get(url)
    m = re.search(r'class="view_content[^"]*"[^>]*>(.*?)</article>', page, re.S)
    if not m:
        raise RuntimeError("본문 영역을 찾지 못했다")
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", m.group(1), flags=re.S)
    parser = _Body()
    parser.feed(body)
    parser._flush()
    return {"url": url, "blocks": parser.blocks, "has_embed": parser.has_embed}


def download_image(src):
    return http_get(src, referer=BASE + "/", binary=True, timeout=60)
