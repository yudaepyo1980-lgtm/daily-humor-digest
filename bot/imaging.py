"""올리기 전에 이미지를 손봐서, 사이트의 webp 재압축 후에도 선명하게 보이게 한다.

사이트는 어떤 형식으로 올려도 같은 화질(약 40dB)의 webp 로 다시 압축한다. 화질 옵션도 없다.
대신 이미지를 2배로 키워 올리면, 화면에서 본문 폭에 맞춰 줄여 보일 때 압축 흔적이 줄어들어
더 선명해 보인다 (시험에서 약 +2dB). 너무 큰 이미지는 키우지 않는다.
Pillow 가 없으면 원본 그대로 올린다.
"""

import io

try:
    from PIL import Image, ImageFilter
except ImportError:  # pragma: no cover
    Image = None

SCALE = 2
MAX_SIDE = 4200          # 키운 뒤 한 변이 이 값을 넘으면 키우지 않는다
MAX_PIXELS = 12_000_000  # 키운 뒤 총 화소 상한
MAX_BYTES = 8 * 1024 * 1024


def enhance(raw, name):
    """(바이트, content_type, 파일명) 을 돌려준다. 실패하면 원본을 그대로 돌려준다."""
    if Image is None:
        return raw, None, name
    try:
        im = Image.open(io.BytesIO(raw))
        if getattr(im, "is_animated", False):   # 움짤은 건드리지 않는다
            return raw, None, name
        im.load()
        w, h = im.size
        nw, nh = w * SCALE, h * SCALE
        if max(nw, nh) > MAX_SIDE or nw * nh > MAX_PIXELS:
            return raw, None, name
        has_alpha = im.mode in ("RGBA", "LA") or "transparency" in im.info
        im = im.convert("RGBA" if has_alpha else "RGB")
        big = im.resize((nw, nh), Image.LANCZOS)
        big = big.filter(ImageFilter.UnsharpMask(radius=1.2, percent=45, threshold=3))
        stem = name.rsplit(".", 1)[0]
        buf = io.BytesIO()
        big.save(buf, "PNG", optimize=False)
        if buf.tell() <= MAX_BYTES:
            return buf.getvalue(), "image/png", stem + ".png"
        buf = io.BytesIO()
        big.convert("RGB").save(buf, "JPEG", quality=95, subsampling=0)
        return buf.getvalue(), "image/jpeg", stem + ".jpg"
    except Exception:  # noqa: BLE001
        return raw, None, name
