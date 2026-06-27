#!/usr/bin/env python3
"""Render Korean card-news PNGs for nonya marketing."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "assets" / "marketing" / "cardnews"
W, H = 1080, 1350
VERSION = "v0.2.1"


CARDS = [
    ("밤새 맡긴 AI,\n아침에 보니 멈춰 있었다면?", "질문 하나, rate limit 하나 때문에\n밤샘 작업이 통째로 놀고 있던 그 상황."),
    ("노냐?", "Claude / Codex / Antigravity 세션이\n자는지 감시하는 AI session watchdog."),
    ("새 작업을 만들지 않습니다", "쓰던 대화, 구독 표면, 컨텍스트를 유지하고\n멈춘 그 창 또는 tmux pane을 다시 밀어줍니다."),
    ("자율 모드 입력대기 처리", "로컬 지침에 답이 있으면 답하고,\n없으면 안전한 기본값으로 계속 진행합니다."),
    ("위험한 승인은 안 합니다", "secrets, billing, 삭제, 설치, 외부 네트워크,\nproduction/deploy/publish는 자동 승인하지 않습니다."),
    ("CLI + tmux가 강합니다", "포커스가 없어도 Claude/Codex CLI pane에\n정확히 send-keys 전달을 검증했습니다."),
    ("메뉴바에서 바로 봅니다", "macOS 네이티브 앱이 상태를 보여주고,\n코어는 Python stdlib 기반으로 가볍게 움직입니다."),
    ("지금 받아보세요", "github.com/ezBuilder/nonya\nRelease " + VERSION + "\n밤새 AI가 놀지 않게."),
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
            return ImageFont.truetype(name, size=size, index=8 if bold and name.endswith(".ttc") else 0)
        except OSError:
            continue
    return ImageFont.load_default()


TITLE = font(82, bold=True)
BODY = font(43)
SMALL = font(30)
NUM = font(36, bold=True)


def wrap_text(text: str, width: int) -> str:
    lines = []
    for raw in text.splitlines():
        if len(raw) <= width:
            lines.append(raw)
        else:
            cur = ""
            for ch in raw:
                if len(cur) >= width:
                    lines.append(cur)
                    cur = ch
                else:
                    cur += ch
            if cur:
                lines.append(cur)
    return "\n".join(lines)


def wrap_pixels(draw: ImageDraw.ImageDraw, text: str, ft: ImageFont.ImageFont, max_px: int) -> str:
    out = []
    for raw in text.splitlines():
        words = raw.split(" ")
        line = ""
        for word in words:
            cand = word if not line else line + " " + word
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


def rounded(draw: ImageDraw.ImageDraw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def make_bg(i: int) -> Image.Image:
    colors = [
        ((7, 42, 48), (15, 118, 110)),
        ((18, 24, 38), (37, 99, 235)),
        ((19, 47, 76), (20, 184, 166)),
        ((26, 38, 54), (245, 158, 11)),
        ((37, 32, 20), (220, 38, 38)),
        ((15, 23, 42), (34, 197, 94)),
        ((24, 38, 61), (168, 85, 247)),
        ((5, 46, 42), (14, 165, 233)),
    ]
    a, b = colors[i % len(colors)]
    img = Image.new("RGB", (W, H), a)
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        for x in range(W):
            s = (x / (W - 1)) * 0.25 + t * 0.75
            px[x, y] = tuple(int(a[c] * (1 - s) + b[c] * s) for c in range(3))
    return img.filter(ImageFilter.GaussianBlur(0.2))


def draw_card(idx: int, title: str, body: str):
    img = make_bg(idx)
    d = ImageDraw.Draw(img)

    d.ellipse((700, -80, 1240, 460), fill=(255, 255, 255, 28))
    d.ellipse((-180, 880, 400, 1460), fill=(255, 255, 255, 20))
    rounded(d, (74, 92, 1006, 1258), 34, (255, 255, 255), (226, 232, 240), 2)
    rounded(d, (112, 130, 968, 1220), 26, (248, 250, 252), (203, 213, 225), 2)

    rounded(d, (128, 150, 282, 208), 29, (15, 118, 110), None)
    d.text((158, 160), f"{idx + 1:02d}/08", font=NUM, fill=(255, 255, 255))
    d.text((718, 160), "nonya", font=SMALL, fill=(51, 65, 85))

    y = 312
    d.multiline_text((132, y), wrap_pixels(d, title, TITLE, 780), font=TITLE, fill=(15, 23, 42), spacing=18)
    y += 290
    d.multiline_text((136, y), wrap_pixels(d, body, BODY, 780), font=BODY, fill=(51, 65, 85), spacing=18)

    rounded(d, (136, 1080, 944, 1146), 22, (15, 23, 42), None)
    footer = "멈추면 깨우고, 위험하면 승인하지 않는다"
    if idx == len(CARDS) - 1:
        footer = "github.com/ezBuilder/nonya"
    d.text((170, 1096), footer, font=SMALL, fill=(255, 255, 255))

    out = OUT / f"nonya-card-{idx + 1:02d}.png"
    img.save(out, optimize=True)
    return out


def contact_sheet(paths):
    thumbs = [Image.open(p).resize((270, 338)) for p in paths]
    sheet = Image.new("RGB", (1080, 676), (15, 23, 42))
    for i, t in enumerate(thumbs):
        x = (i % 4) * 270
        y = (i // 4) * 338
        sheet.paste(t, (x, y))
    out = OUT / "nonya-cardnews-contact-sheet.png"
    sheet.save(out, optimize=True)
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    paths = [draw_card(i, title, body) for i, (title, body) in enumerate(CARDS)]
    paths.append(contact_sheet(paths))
    for path in paths:
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
