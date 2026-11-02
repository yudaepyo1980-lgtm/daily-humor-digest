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


# ---- 새 사이트용: 서버가 사진을 다시 줄이지 않는다. 올리기 전에 화질은 지키면서 용량을 최대한 줄인다 ----
# 규칙(안내서): 사진 5MB, gif 4MB, 영상(mp4/webm) 20MB. 저장공간 무료 10GB 라 줄일수록 오래 간다.
#  - 사진 -> WebP. 기본은 손실 82. 색이 적은 이미지(캡처/글씨/도표)는 무손실과 비교해 더 작은 쪽을 쓴다.
#  - 가로 1600px 넘는 것만 줄이고, 메타정보(EXIF 등)는 버린다. 폰 사진은 방향을 바로잡는다.
#  - GIF 움짤 -> MP4 (보통 5~10배 작고 화질 같음. 자동재생/반복은 서버가 처리).
import os
import shutil
import subprocess
import tempfile

NUDE_MAX_WIDTH = 1600
NUDE_MAX_HEIGHT = 16000          # WebP 한 변 한계(16383) 아래로
NUDE_MAX_PHOTO = 5 * 1024 * 1024
NUDE_MAX_GIF = 4 * 1024 * 1024
NUDE_MAX_VIDEO = 20 * 1024 * 1024
VIDEO_MAX_WIDTH = 720


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg")


def gif_to_mp4(raw):
    """GIF 바이트를 MP4 바이트로 바꾼다. 실패하면 None."""
    exe = _ffmpeg()
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.gif"), os.path.join(d, "out.mp4")
        with open(src, "wb") as f:
            f.write(raw)
        for crf, width in ((26, VIDEO_MAX_WIDTH), (30, 540), (34, 400)):   # 20MB 를 넘으면 더 줄여 다시 시도
            cmd = [exe, "-y", "-loglevel", "error", "-i", src, "-movflags", "+faststart", "-pix_fmt", "yuv420p",
                   "-vf", f"scale=trunc(min(iw\\,{width})/2)*2:-2", "-c:v", "libx264", "-preset", "slow",
                   "-crf", str(crf), "-an", dst]
            try:
                subprocess.run(cmd, check=True, timeout=180, capture_output=True)
                data = open(dst, "rb").read()
            except Exception:  # noqa: BLE001
                return None
            if 0 < len(data) <= NUDE_MAX_VIDEO:
                return data
    return None


def _webp_bytes(im, **kw):
    buf = io.BytesIO()
    im.save(buf, "WEBP", method=6, **kw)
    return buf.getvalue()


def for_nude(raw, name):
    """(바이트, content_type, 파일명) 을 돌려준다. 올릴 수 없는 파일이면 None."""
    stem = name.rsplit(".", 1)[0]
    if Image is None:
        return None
    try:
        im = Image.open(io.BytesIO(raw))
        if getattr(im, "is_animated", False):
            if im.format == "GIF":
                mp4 = gif_to_mp4(raw)                       # 움짤은 mp4 가 훨씬 작다
                if mp4 is not None and len(mp4) < len(raw):
                    return mp4, "video/mp4", stem + ".mp4"
                if len(raw) <= NUDE_MAX_GIF:                # 변환이 안 되거나 더 크면 gif 그대로
                    return raw, "image/gif", stem + ".gif"
            return None
        from PIL import ImageOps
        im = ImageOps.exif_transpose(im)                    # 폰 사진 방향 보정 (메타정보는 저장하지 않는다)
        im.load()
        has_alpha = im.mode in ("RGBA", "LA") or "transparency" in im.info
        im = im.convert("RGBA" if has_alpha else "RGB")
        w, h = im.size
        scale = min(1.0, NUDE_MAX_WIDTH / w, NUDE_MAX_HEIGHT / h)
        if scale < 1.0:
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        best = None
        for quality in (82, 74, 66, 58):
            data = _webp_bytes(im, quality=quality)
            if len(data) <= NUDE_MAX_PHOTO:
                best = data
                break
        # 색이 적은 이미지(캡처/글씨/도표)는 무손실이 화질도 완벽하고 더 작은 경우가 많다
        try:
            few_colors = im.convert("RGB").getcolors(maxcolors=65536) is not None
        except Exception:  # noqa: BLE001
            few_colors = False
        if few_colors:
            lossless = _webp_bytes(im, lossless=True)
            if len(lossless) <= NUDE_MAX_PHOTO and (best is None or len(lossless) < len(best)):
                best = lossless
        if best is None:
            return None
        return best, "image/webp", stem + ".webp"
    except Exception:  # noqa: BLE001
        return None
