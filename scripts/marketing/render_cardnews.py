#!/usr/bin/env python3
"""Render premium Korean card-news PNGs for nonya marketing."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "assets" / "marketing" / "cardnews"
SRC = ROOT / "assets" / "marketing" / "imagegen"
W, H = 1080, 1350
VERSION = "v0.2.4"


@dataclass(frozen=True)
class Card:
    source: str
    eyebrow: str
    title: str
    body: str
    footer: str
    accent: tuple[int, int, int]
    focus: tuple[float, float] = (0.52, 0.5)
    title_size: int = 112


CARDS = [
    Card(
        "nonya-imagegen-cover.png",
        "AI SESSION WATCHDOG",
        "밤새 맡긴 AI,\n아침에는 끝나 있어야죠",
        "질문 하나에 멈춘 Claude/Codex 세션을\nNonya가 감시하고, 필요하면 다시 깨웁니다.",
        "멈추면 깨우고, 위험하면 멈춥니다.",
        (61, 214, 255),
        focus=(0.58, 0.55),
        title_size=92,
    ),
    Card(
        "nonya-imagegen-wake.png",
        "NONYA MODE",
        "켜두면\n조용히 챙깁니다",
        "작업 중인 세션이 멈추는 순간을 보고,\n가능한 범위에서 다시 이어갑니다.",
        "밤새 작업 흐름이 끊기지 않게.",
        (255, 191, 87),
        focus=(0.54, 0.54),
        title_size=92,
    ),
    Card(
        "nonya-imagegen-cover.png",
        "AUTONOMOUS WAIT HANDLING",
        "입력 대기가 와도\n흐름을 놓치지 않습니다",
        "로컬 지침으로 판단 가능한 질문은 답하고,\n승인이 필요한 일은 자동으로 넘기지 않습니다.",
        "자동화와 안전선을 같이 잡았습니다.",
        (90, 255, 184),
        focus=(0.38, 0.52),
        title_size=92,
    ),
    Card(
        "nonya-imagegen-cli.png",
        "NO CONTEXT LOSS",
        "대화 흐름은\n그대로 이어집니다",
        "새 세션을 만들지 않고 기존 대화와 터미널 흐름을\n기준으로 멈춘 지점부터 다시 이어갑니다.",
        "컨텍스트를 다시 설명할 필요를 줄입니다.",
        (153, 109, 255),
        focus=(0.48, 0.55),
        title_size=94,
    ),
    Card(
        "nonya-imagegen-safety.png",
        "HARD SAFETY LINE",
        "위험한 요청은\n반드시 멈춥니다",
        "비밀값, 결제, 삭제, 설치, 외부 네트워크,\n배포처럼 위험한 작업은 자동 승인하지 않습니다.",
        "자율 실행에도 안전선은 분명합니다.",
        (255, 87, 113),
        focus=(0.54, 0.5),
        title_size=86,
    ),
    Card(
        "nonya-imagegen-cli.png",
        "CLI + TMUX READY",
        "CLI 작업도\n안정적으로 이어갑니다",
        "Claude/Codex CLI와 tmux pane에 포커스를 옮기지 않아도\n정확히 전달되도록 검증했습니다.",
        "긴 작업일수록 차이가 납니다.",
        (96, 255, 116),
        focus=(0.62, 0.55),
        title_size=92,
    ),
    Card(
        "nonya-imagegen-cover.png",
        "MAC MENU BAR APP",
        "지금 상태는\n메뉴바에서 확인",
        "macOS 네이티브 앱이 감시 상태를 보여주고,\n가벼운 Python 코어가 실제 감시를 맡습니다.",
        "켜두면 조용히 보고, 필요할 때 움직입니다.",
        (84, 178, 255),
        focus=(0.7, 0.52),
        title_size=92,
    ),
    Card(
        "nonya-imagegen-release.png",
        "PUBLIC RELEASE",
        "Mac · Windows\n바로 시작하세요",
        f"GitHub 릴리스에서 macOS DMG와 Windows ZIP을 받을 수 있습니다.\nRelease {VERSION}",
        "github.com/ezBuilder/nonya",
        (215, 141, 255),
        focus=(0.88, 0.5),
        title_size=84,
    ),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for name in names:
        try:
            index = 8 if bold and name.endswith(".ttc") else 0
            return ImageFont.truetype(name, size=size, index=index)
        except OSError:
            continue
    return ImageFont.load_default()


EYEBROW = font(30, bold=True)
BODY = font(43)
FOOTER = font(28, bold=True)
NUM = font(31, bold=True)
BRAND = font(31, bold=True)
MICRO = font(23)


def cover_source(path: Path, focus: tuple[float, float], zoom: float = 1.06) -> Image.Image:
    if path.exists():
        img = Image.open(path).convert("RGB")
    else:
        img = Image.new("RGB", (W, W), (7, 11, 24))
    iw, ih = img.size
    scale = max(W / iw, H / ih) * zoom
    rw, rh = int(iw * scale), int(ih * scale)
    resized = img.resize((rw, rh), Image.Resampling.LANCZOS)
    max_x = max(0, rw - W)
    max_y = max(0, rh - H)
    left = int(max_x * min(max(focus[0], 0.0), 1.0))
    top = int(max_y * min(max(focus[1], 0.0), 1.0))
    return resized.crop((left, top, left + W, top + H)).convert("RGBA")


def draw_linear_gradient(size: tuple[int, int], stops: list[tuple[float, tuple[int, int, int, int]]]) -> Image.Image:
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = size
    for x in range(width):
        t = x / max(1, width - 1)
        for idx in range(len(stops) - 1):
            a_t, a = stops[idx]
            b_t, b = stops[idx + 1]
            if a_t <= t <= b_t:
                local = 0 if b_t == a_t else (t - a_t) / (b_t - a_t)
                color = tuple(int(a[c] * (1 - local) + b[c] * local) for c in range(4))
                draw.line((x, 0, x, height), fill=color)
                break
    return overlay


def tint_layer(accent: tuple[int, int, int], alpha: int = 60) -> Image.Image:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.ellipse((-330, -260, 450, 540), fill=(*accent, alpha))
    draw.ellipse((680, 760, 1340, 1450), fill=(*accent, max(18, alpha // 2)))
    return layer.filter(ImageFilter.GaussianBlur(64))


def wrap_pixels(draw: ImageDraw.ImageDraw, text: str, ft: ImageFont.ImageFont, max_px: int) -> str:
    out: list[str] = []
    for raw in text.splitlines():
        words = raw.split(" ")
        line = ""
        for word in words:
            cand = word if not line else f"{line} {word}"
            if draw.textbbox((0, 0), cand, font=ft)[2] <= max_px:
                line = cand
                continue
            if line:
                out.append(line)
                line = ""
            chunk = ""
            for ch in word:
                cand2 = chunk + ch
                if draw.textbbox((0, 0), cand2, font=ft)[2] > max_px and chunk:
                    out.append(chunk)
                    chunk = ch
                else:
                    chunk = cand2
            line = chunk
        if line:
            out.append(line)
    return "\n".join(out)


def line_height(ft: ImageFont.ImageFont) -> int:
    box = ft.getbbox("가나다ABC123")
    return box[3] - box[1]


def multiline_height(text: str, ft: ImageFont.ImageFont, spacing: int) -> int:
    return len(text.splitlines()) * line_height(ft) + max(0, len(text.splitlines()) - 1) * spacing


def glow_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    ft: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    spacing: int = 0,
) -> None:
    x, y = xy
    for off, alpha in [(6, 70), (3, 120)]:
        draw.multiline_text((x + off, y + off), text, font=ft, fill=(0, 0, 0, alpha), spacing=spacing)
    draw.multiline_text((x, y), text, font=ft, fill=fill, spacing=spacing)


def rounded(draw: ImageDraw.ImageDraw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def draw_chrome(draw: ImageDraw.ImageDraw, card: Card, idx: int) -> None:
    accent = card.accent
    rounded(draw, (64, 58, 207, 111), 26, (5, 10, 25, 162), (*accent, 180), 1)
    draw.text((91, 71), f"{idx + 1:02d}/08", font=NUM, fill=(245, 250, 255, 245))
    draw.text((826, 72), "NONYA", font=BRAND, fill=(245, 250, 255, 232))
    draw.line((65, 142, 65, 605), fill=(*accent, 235), width=6)
    draw.line((78, 142, 78, 343), fill=(255, 255, 255, 90), width=1)
    rounded(draw, (64, 1148, 1016, 1237), 28, (5, 10, 25, 178), (255, 255, 255, 70), 1)
    for n in range(8):
        x0 = 790 + n * 24
        alpha = 60 + n * 18
        draw.line((x0, 1195, x0, 1216 - n * 3), fill=(*accent, min(230, alpha)), width=8)


def draw_card(idx: int, card: Card) -> Path:
    img = cover_source(SRC / card.source, card.focus)
    img = Image.alpha_composite(img, tint_layer(card.accent))
    img = Image.alpha_composite(
        img,
        draw_linear_gradient(
            (W, H),
            [
                (0.0, (2, 6, 18, 236)),
                (0.48, (2, 6, 18, 186)),
                (0.82, (2, 6, 18, 76)),
                (1.0, (2, 6, 18, 34)),
            ],
        ),
    )
    top_shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(top_shadow).rectangle((0, 0, W, 230), fill=(0, 0, 0, 80))
    img = Image.alpha_composite(img, top_shadow.filter(ImageFilter.GaussianBlur(22)))

    draw = ImageDraw.Draw(img, "RGBA")
    draw_chrome(draw, card, idx)

    x = 96
    y = 184
    rounded(draw, (x, y, x + 390, y + 47), 22, (*card.accent, 48), (*card.accent, 148), 1)
    rounded(draw, (x, y, x + 390, y + 47), 22, (5, 10, 25, 158), (*card.accent, 190), 1)
    draw.text((x + 20, y + 11), card.eyebrow, font=EYEBROW, fill=(245, 250, 255, 238))
    y += 88

    title_font = font(card.title_size, bold=True)
    title = wrap_pixels(draw, card.title, title_font, 850)
    glow_text(draw, (x, y), title, title_font, (248, 252, 255, 255), spacing=10)
    y += multiline_height(title, title_font, 10) + 46

    body = wrap_pixels(draw, card.body, BODY, 805)
    glow_text(draw, (x + 2, y), body, BODY, (218, 233, 247, 238), spacing=14)

    footer = wrap_pixels(draw, card.footer, FOOTER, 710)
    draw.text((96, 1174), footer, font=FOOTER, fill=(245, 250, 255, 242))

    out = OUT / f"nonya-card-{idx + 1:02d}.png"
    img.convert("RGB").save(out, optimize=True, quality=95)
    return out


def contact_sheet(paths: list[Path]) -> Path:
    tw, th = 360, 450
    sheet = Image.new("RGB", (tw * 4, th * 2), (4, 8, 20))
    for i, path in enumerate(paths):
        thumb = Image.open(path).resize((tw, th), Image.Resampling.LANCZOS)
        sheet.paste(thumb, ((i % 4) * tw, (i // 4) * th))
    out = OUT / "nonya-cardnews-contact-sheet.png"
    sheet.save(out, optimize=True, quality=95)
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    paths = [draw_card(i, card) for i, card in enumerate(CARDS)]
    paths.append(contact_sheet(paths))
    for path in paths:
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
