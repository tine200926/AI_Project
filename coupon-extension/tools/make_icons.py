"""Draw the toolbar/store icons (a coupon ticket with a clock) with Pillow.

    python3 tools/make_icons.py
"""
import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), '..', 'icons')
S = 1024  # draw large, then downscale for clean edges
ORANGE = (232, 113, 10, 255)
WHITE = (255, 255, 255, 255)
CLEAR = (0, 0, 0, 0)


def draw():
    img = Image.new('RGBA', (S, S), CLEAR)
    d = ImageDraw.Draw(img)
    # Ticket body with two half-circle notches.
    d.rounded_rectangle([60, 230, S - 60, S - 230], radius=90, fill=ORANGE)
    d.ellipse([60 - 90, S // 2 - 90, 60 + 90, S // 2 + 90], fill=CLEAR)
    d.ellipse([S - 60 - 90, S // 2 - 90, S - 60 + 90, S // 2 + 90], fill=CLEAR)
    # Perforation.
    for y in range(290, S - 290, 70):
        d.rounded_rectangle([340, y, 370, y + 36], radius=12, fill=WHITE)
    # Clock face.
    cx, cy, r = 650, S // 2, 190
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE)
    d.rounded_rectangle([cx - 18, cy - 130, cx + 18, cy + 10], radius=18, fill=ORANGE)
    d.line([cx, cy, cx + 95, cy + 70], fill=ORANGE, width=36)
    d.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], fill=ORANGE)
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    big = draw()
    for size in (16, 32, 48, 128):
        big.resize((size, size), Image.LANCZOS).save(os.path.join(OUT, f'icon{size}.png'))
    print('icons written to', os.path.abspath(OUT))


if __name__ == '__main__':
    main()
