import math, subprocess, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import imageio_ffmpeg

W, H, FPS = 1080, 1920, 30
IMG = "img/"
F_BLACK = "fonts/black.ttf"
F_XB = "fonts/xb.ttf"
YELLOW = (255, 230, 0)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
OUT = sys.argv[1] if len(sys.argv) > 1 else "reel.mp4"

_font_cache = {}
def font(path, size):
    k = (path, size)
    if k not in _font_cache:
        _font_cache[k] = ImageFont.truetype(path, size)
    return _font_cache[k]

def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)

# ---------- text rendering (白文字＋黒フチ、テロップ風) ----------
_text_cache = {}
def text_layer(lines, size, fill=WHITE, stroke=12, fpath=F_BLACK, spacing=14, shadow=True):
    """lines: list of str or list of [(str, color), ...]"""
    key = (repr(lines), size, fill, stroke, fpath, spacing, shadow)
    if key in _text_cache:
        return _text_cache[key]
    f = font(fpath, size)
    runs_lines = [[(l, fill)] if isinstance(l, str) else l for l in lines]
    widths, heights = [], []
    for runs in runs_lines:
        w = sum(f.getlength(t) for t, _ in runs)
        widths.append(w)
        heights.append(size)
    tw = int(max(widths)) + stroke * 2 + 20
    th = int(sum(heights) + spacing * (len(lines) - 1)) + stroke * 2 + 30
    im = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    y = stroke + 6
    for runs, w in zip(runs_lines, widths):
        x = (tw - w) / 2
        for t, col in runs:
            d.text((x, y), t, font=f, fill=col, stroke_width=stroke, stroke_fill=BLACK)
            x += f.getlength(t)
        y += size + spacing
    if shadow:
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        alpha = im.split()[3].filter(ImageFilter.GaussianBlur(8))
        sh.putalpha(alpha.point(lambda a: int(a * 0.55)))
        base = Image.new("RGBA", (tw + 8, th + 8), (0, 0, 0, 0))
        base.alpha_composite(sh, (8, 8))
        base.alpha_composite(im, (0, 0))
        im = base
    _text_cache[key] = im
    return im

def paste_center(canvas, layer, cx, cy, scale=1.0, alpha=1.0):
    if scale != 1.0:
        layer = layer.resize((max(1, int(layer.width * scale)), max(1, int(layer.height * scale))), Image.BILINEAR)
    if alpha < 1.0:
        layer = layer.copy()
        a = layer.split()[3].point(lambda v: int(v * alpha))
        layer.putalpha(a)
    canvas.alpha_composite(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)))

def pop(t_local, start=0.0, dur=0.22):
    """ポップイン用 (scale, alpha)"""
    p = (t_local - start) / dur
    if p <= 0:
        return 0, 0
    if p >= 1:
        return 1.0, 1.0
    # overshoot
    s = 1.35 - 0.35 * ease(p) + 0.08 * math.sin(p * math.pi)
    return s, min(1.0, p * 2)

# ---------- backgrounds ----------
_img_cache = {}
def load(name):
    if name not in _img_cache:
        im = Image.open(IMG + name).convert("RGB")
        im = ImageEnhance.Color(im).enhance(1.08)
        _img_cache[name] = im
    return _img_cache[name]

_bg_cache = {}
def blurred_bg(name):
    if name not in _bg_cache:
        im = load(name)
        s = max(W / im.width, H / im.height)
        b = im.resize((int(im.width * s) + 2, int(im.height * s) + 2))
        b = b.crop(((b.width - W) // 2, (b.height - H) // 2, (b.width - W) // 2 + W, (b.height - H) // 2 + H))
        b = b.filter(ImageFilter.GaussianBlur(40))
        b = ImageEnhance.Brightness(b).enhance(0.55)
        _bg_cache[name] = b.convert("RGBA")
    return _bg_cache[name]

_big_cache = {}
def big(name, h):
    k = (name, h)
    if k not in _big_cache:
        im = load(name)
        s = h / im.height
        r = im.resize((int(im.width * s), h), Image.LANCZOS)
        r = r.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
        _big_cache[k] = r
    return _big_cache[k]

def photo_frame(name, p, top=330, h=1180, pan=(0.0, 1.0), zoom=(1.0, 1.06), full=False, crop=None):
    """写真をパン＋ズームで表示。p=0..1 のシーン進行度"""
    canvas = blurred_bg(name).copy()
    im = load(name) if crop is None else load(name).crop(crop)
    if full:
        top, h = 0, H
    z = zoom[0] + (zoom[1] - zoom[0]) * p
    ih = int(h * z)
    s = ih / im.height
    iw = int(im.width * s)
    if iw < W:  # 縦長素材（間取り等）
        s = W * z / im.width
        iw, ih = int(im.width * s), int(im.height * s)
    r = im.resize((iw, ih), Image.BILINEAR)
    px = pan[0] + (pan[1] - pan[0]) * ease(p)
    x0 = int((iw - W) * px)
    y0 = int((ih - h) / 2) if ih >= h else 0
    view = r.crop((x0, y0, x0 + W, y0 + min(h, ih)))
    canvas.paste(view, (0, top + max(0, (h - ih) // 2) if ih < h else top))
    return canvas

# ---------- シーン定義 ----------
# あなたの理想不動産ショートの型：
#  ・冒頭にフック大テロップ「え⁉︎〜がヤバすぎた…」
#  ・部屋ごとに「-リビング-」＋黄色で広さ/設備
#  ・下部に解説者のゆるいツッコミ字幕（〜っすね）
#  ・最後に価格発表
SUB_Y = 1600  # 字幕の中心（リールUIに隠れない高さ）

def label(canvas, tl, name, value, y=430):
    s, a = pop(tl, 0.0)
    if a:
        paste_center(canvas, text_layer([f"-{name}-"], 70, stroke=10), W / 2, y, s, a)
    s, a = pop(tl, 0.18)
    if a and value:
        paste_center(canvas, text_layer([value], 104, fill=YELLOW, stroke=13), W / 2, y + 100, s, a)

def subtitle(canvas, tl, lines, start=0.35, y=SUB_Y, size=62):
    if tl < start:
        return
    a = min(1.0, (tl - start) / 0.12)
    paste_center(canvas, text_layer(lines, size, stroke=11, fpath=F_XB), W / 2, y, 1.0, a)

def progress_bar(canvas, t, total):
    d = ImageDraw.Draw(canvas)
    d.rectangle((0, 0, int(W * t / total), 10), fill=YELLOW)

scenes = []
def scene(dur):
    def deco(fn):
        scenes.append((dur, fn))
        return fn
    return deco

@scene(3.0)
def s_hook(t, p):
    c = photo_frame("0057.jpg", p, full=True, pan=(0.25, 0.75), zoom=(1.12, 1.0))
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 70))
    c.alpha_composite(ov)
    s, a = pop(t, 0.0, 0.25)
    if a:
        paste_center(c, text_layer(["え⁉︎", "駅徒歩9分の新築", [("名古屋で", WHITE), ("ガチ優良", YELLOW)], "物件見つけた…"], 112, stroke=14), W / 2, 820, s, a)
    s, a = pop(t, 0.9, 0.2)
    if a:
        paste_center(c, text_layer([[("名古屋市瑞穂区", WHITE)]], 60, stroke=10, fpath=F_XB), W / 2, 1380, s, a)
    return c

@scene(3.4)
def s_exterior(t, p):
    c = photo_frame("0052.jpg", p, pan=(0.0, 0.8))
    label(c, t, "外観", "新築2棟だけの分譲")
    subtitle(c, t, ["白と黒の並び、", "映えすぎっすね"])
    return c

@scene(3.0)
def s_parking(t, p):
    c = photo_frame("0055.jpg", p, pan=(0.9, 0.2), zoom=(1.05, 1.12))
    label(c, t, "駐車場", "2台分 完備")
    subtitle(c, t, ["前の道路も広いんで", "車の出し入れ ラクっすよ"])
    return c

@scene(4.4)
def s_living(t, p):
    c = photo_frame("0059.jpg", p, pan=(0.15, 0.85), zoom=(1.0, 1.1))
    label(c, t, "リビング", "LDK 16.1帖")
    if t < 2.3:
        subtitle(c, t, [[("天井高 ", WHITE), ("2.72m", YELLOW)], "開放感えぐいっすね"])
    else:
        subtitle(c, t - 2.3, [[("床暖房", YELLOW), ("付き", WHITE)], "冬の朝これ最強っす"], start=0.0)
    return c

@scene(3.2)
def s_kitchen(t, p):
    c = photo_frame("0060.jpg", p, pan=(0.1, 0.9))
    label(c, t, "キッチン", "食洗機付き")
    subtitle(c, t, ["料理しながら", "リビング見渡せるやつ"])
    return c

@scene(3.0)
def s_bath(t, p):
    c = photo_frame("0061.jpg", p, pan=(0.6, 0.3), zoom=(1.0, 1.12))
    label(c, t, "浴室", "浴室乾燥機付き")
    subtitle(c, t, ["雨の日の洗濯", "これで勝ちっす"])
    return c

@scene(4.0)
def s_plan(t, p):
    # B号地 間取り図の2F→1Fをスクロール
    im = load("0058.jpg")
    crop = (170, 330, 750, 1060)
    c = blurred_bg("0059.jpg").copy()
    fp = im.crop(crop)
    s = 1000 / fp.width
    fp = fp.resize((1000, int(fp.height * s)), Image.LANCZOS).filter(ImageFilter.UnsharpMask(2, 50, 2))
    card = Image.new("RGBA", (1040, 1060), (255, 255, 255, 255))
    viewh = 1020
    y0 = int((fp.height - viewh) * ease(p))
    card.paste(fp.crop((0, y0, 1000, y0 + viewh)), (20, 20))
    c.alpha_composite(card, (20, 330))
    label(c, t, "間取り", "3LDK＋WIC", y=260)
    subtitle(c, t, [[("主寝室に", WHITE), ("3帖のWIC", YELLOW)], "全部屋に収納あるっす"])
    return c

@scene(3.2)
def s_garden(t, p):
    c = photo_frame("0062.jpg", p, pan=(0.2, 0.8))
    label(c, t, "設備", "太陽光＋エネファーム")
    subtitle(c, t, ["停電のときも", "安心なやつっすね"])
    return c

@scene(4.0)
def s_area(t, p):
    c = blurred_bg("0057.jpg").copy()
    items = [("0021.jpg", "駅", "670m"), ("0023.jpg", "小学校", "380m"),
             ("0026.jpg", "スーパー", "460m"), ("0027.jpg", "病院", "350m")]
    tw, th = 500, 375
    for i, (f, nm, dist) in enumerate(items):
        st = 0.15 + i * 0.25
        s, a = pop(t, st, 0.2)
        if not a:
            continue
        x = 30 + (i % 2) * 520
        y = 400 + (i // 2) * 440
        tile = load(f).resize((tw, th), Image.LANCZOS).convert("RGBA")
        d = ImageDraw.Draw(tile)
        d.rectangle((0, th - 92, tw, th), fill=(0, 0, 0, 170))
        ff = font(F_BLACK, 50)
        d.text((22, th - 80), nm, font=ff, fill=WHITE)
        d.text((tw - 22 - ff.getlength(dist), th - 80), dist, font=ff, fill=YELLOW)
        paste_center(c, tile, x + tw / 2, y + th / 2, s, a)
    s, a = pop(t, 0.0)
    if a:
        paste_center(c, text_layer(["-周辺環境-"], 70, stroke=10), W / 2, 290, s, a)
    subtitle(c, t, ["生活に要るもの", "ぜんぶ徒歩圏っす"], start=1.3, y=1420)
    return c

@scene(5.0)
def s_price(t, p):
    c = photo_frame("0052.jpg", p, full=True, pan=(0.3, 0.6), zoom=(1.0, 1.08))
    c.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 150)))
    s, a = pop(t, 0.0)
    if a:
        paste_center(c, text_layer(["で、気になる", "お値段は…"], 96, stroke=12), W / 2, 620, s, a)
    s, a = pop(t, 1.4, 0.3)
    if a:
        paste_center(c, text_layer(["7,450万円〜"], 150, fill=YELLOW, stroke=16), W / 2, 960, s, a)
    s, a = pop(t, 2.0)
    if a:
        paste_center(c, text_layer(["（全2区画 7,450万〜7,600万円）"], 42, stroke=8, fpath=F_XB), W / 2, 1090, s, a)
    s, a = pop(t, 2.5)
    if a:
        paste_center(c, text_layer(["3LDK・4LDK / 即入居OK", "地下鉄桜通線「瑞穂区役所」徒歩9分"], 48, stroke=9, fpath=F_XB, spacing=20), W / 2, 1290, s, a)
    return c

@scene(3.0)
def s_cta(t, p):
    c = photo_frame("0059.jpg", p, full=True, pan=(0.4, 0.6), zoom=(1.05, 1.0))
    c.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 140)))
    s, a = pop(t, 0.0)
    if a:
        paste_center(c, text_layer(["あなたの理想の家、", [("見つかりました", YELLOW), ("？", WHITE)]], 90, stroke=12), W / 2, 760, s, a)
    s, a = pop(t, 0.7)
    if a:
        paste_center(c, text_layer(["見返せるように保存してね", "内見予約はプロフのリンクから"], 54, stroke=9, fpath=F_XB, spacing=22), W / 2, 1130, s, a)
    return c

# ---------- render ----------
total = sum(d for d, _ in scenes)
XF = 0.18  # クロスフェードではなく「白フラッシュ」風の短いトランジション
ff = imageio_ffmpeg.get_ffmpeg_exe()
cmd = [ff, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
       "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
       "-shortest", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
       "-profile:v", "high", "-movflags", "+faststart", "-c:a", "aac", "-b:a", "128k", OUT]
proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
nframes = int(round(total * FPS))
t0 = 0.0
bounds = []
for d, fn in scenes:
    bounds.append((t0, d, fn)); t0 += d
for i in range(nframes):
    t = i / FPS
    for st, d, fn in bounds:
        if st <= t < st + d or (fn is bounds[-1][2] and t >= st):
            tl = t - st
            frame = fn(tl, min(1.0, tl / d))
            # シーン頭のフラッシュ（テンポ感）
            if tl < 0.1 and st > 0:
                a = int(255 * 0.45 * (1 - tl / 0.1))
                frame.alpha_composite(Image.new("RGBA", (W, H), (255, 255, 255, a)))
            break
    progress_bar(frame, t, total)
    proc.stdin.write(frame.convert("RGB").tobytes())
    if i % 150 == 0:
        print(f"{i}/{nframes}", flush=True)
proc.stdin.close(); proc.wait()
print("done", OUT, total, "sec")
