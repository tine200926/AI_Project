"""Draw the toolbar/store icons (a bell with a green dot) with Pillow.

    python3 tools/make_icons.py
"""
import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), '..', 'icons')
S = 1024  # draw large, then downscale for clean edges


def draw():
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Rounded-square background.
    d.rounded_rectangle([40, 40, S - 40, S - 40], radius=230, fill=(75, 91, 220, 255))
    white = (255, 255, 255, 255)
    cx = S // 2
    # Bell body: dome + flared skirt.
    d.ellipse([cx - 230, 230, cx + 230, 690], fill=white)
    d.rectangle([cx - 230, 460, cx + 230, 690], fill=white)
    d.polygon([(cx - 230, 600), (cx + 230, 600), (cx + 300, 730), (cx - 300, 730)], fill=white)
    d.rounded_rectangle([cx - 310, 700, cx + 310, 760], radius=30, fill=white)
    # Top knob and clapper.
    d.ellipse([cx - 50, 170, cx + 50, 270], fill=white)
    d.ellipse([cx - 80, 770, cx + 80, 880], fill=white)
    # "In stock" dot.
    d.ellipse([cx + 150, 150, cx + 370, 370], fill=(30, 190, 90, 255), outline=(75, 91, 220, 255), width=36)
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    big = draw()
    for size in (16, 32, 48, 128):
        big.resize((size, size), Image.LANCZOS).save(os.path.join(OUT, f'icon{size}.png'))
    print('icons written to', os.path.abspath(OUT))


if __name__ == '__main__':
    main()
