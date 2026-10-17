"""오늘의유머 베스트 목록과 글 본문을 읽는다.

읽기 전용이다. 로그인하지 않고, 공개된 목록/본문 페이지만 가져온다.
요청 사이에 간격을 두고, 정체를 밝히는 User-Agent 를 쓴다.
"""

import html
import re
import time
import urllib.request
from html.parser import HTMLParser

BASE = "https://www.todayhumor.co.kr"
UA = "Mozilla/5.0 (compatible; ContentResearchBot/1.0)"
TABLE = "bestofbest"
REQUEST_GAP_SEC = 2.0


def http_get(url, referer=None, binary=False, timeout=30):
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    time.sleep(REQUEST_GAP_SEC)
    return data if binary else data.decode("utf-8", "replace")


ROW = re.compile(r'<tr class="view[^"]*".*?</tr>', re.S)


def parse_list(page_html):
    items = []
    for row in ROW.findall(page_html):
        no = re.search(r'<td class="no"><a[^>]*>(\d+)</a>', row)
        subj = re.search(r'<td class="subject"><a href="[^"]+"[^>]*>(.*?)</a>', row, re.S)
        ok = re.search(r'<td class="oknok">\s*(-?\d+)', row)
        hits = re.search(r'<td class="hits">\s*([\d,]+)', row)
        memo = re.search(r"list_memo_count_span'>\s*\[(\d+)\]", row)
        date = re.search(r'<td class="date">\s*([^<]+)', row)
        if not (no and subj and ok):
            continue
        items.append({
            "src": "todayhumor",
            "no": no.group(1),
            "title": html.unescape(re.sub(r"<[^>]+>", "", subj.group(1))).strip(),
            "oknok": int(ok.group(1)),
            "hits": int((hits.group(1) if hits else "0").replace(",", "")),
            "comments": int(memo.group(1)) if memo else 0,
            "date": date.group(1).strip() if date else "",
        })
    return items


def fetch_candidates(pages=3):
    """베스트 목록 1~pages 페이지를 읽어 후보를 돌려준다."""
    out = []
    for page in range(1, pages + 1):
        url = f"{BASE}/board/list.php?table={TABLE}&page={page}"
        try:
            out.extend(parse_list(http_get(url)))
        except Exception as exc:  # noqa: BLE001
            print(f"목록 {page}페이지 실패: {exc}")
            break
    return out


class _Body(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.buf = []
        self.has_embed = False

    def _flush(self):
        text = re.sub(r"[ \t ]+", " ", "".join(self.buf)).strip()
        if text:
            self.blocks.append({"t": "text", "v": text})
        self.buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "img":
            src = a.get("src") or ""
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("http") and "list_icon" not in src:
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


def fetch_post(no):
    """본문을 텍스트/이미지 블록 목록으로 돌려준다."""
    url = f"{BASE}/board/view.php?table={TABLE}&no={no}"
    page = http_get(url)
    m = re.search(r'<div class="viewContent">(.*?)</div><!--viewContent-->', page, re.S)
    if not m:
        raise RuntimeError("본문 영역을 찾지 못했다")
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", m.group(1), flags=re.S)
    parser = _Body()
    parser.feed(body)
    parser._flush()
    return {"url": url, "blocks": parser.blocks, "has_embed": parser.has_embed}


def download_image(src):
    return http_get(src, referer=BASE + "/", binary=True, timeout=60)
