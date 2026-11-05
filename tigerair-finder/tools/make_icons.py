"""Draw the toolbar/store icons (a plane over a down arrow: "cheap flights") with Pillow.

    python3 tools/make_icons.py
"""
import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), '..', 'icons')
S = 1024  # draw large, then downscale for clean edges


def draw():
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([40, 40, S - 40, S - 40], radius=230, fill=(15, 118, 110, 255))
    white = (255, 255, 255, 255)
    # Plane pointing straight up, drawn on its own layer and turned 45 degrees
    # so the nose points up-right.
    layer = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    p = ImageDraw.Draw(layer)
    cx = S // 2
    p.rounded_rectangle([cx - 52, 150, cx + 52, 860], radius=52, fill=white)            # fuselage
    p.polygon([(cx - 40, 400), (cx + 40, 400), (cx + 340, 600), (cx + 340, 660),
               (cx, 560), (cx - 340, 660), (cx - 340, 600)], fill=white)                 # wings
    p.polygon([(cx - 30, 740), (cx + 30, 740), (cx + 150, 830), (cx + 150, 870),
               (cx, 830), (cx - 150, 870), (cx - 150, 830)], fill=white)                 # tail
    layer = layer.rotate(-45, resample=Image.BICUBIC, center=(cx, cx))
    img.alpha_composite(layer, (-40, -40))
    # Amber "low price" badge with a down arrow.
    d.ellipse([560, 560, 900, 900], fill=(245, 158, 11, 255), outline=(15, 118, 110, 255), width=34)
    d.rectangle([705, 625, 755, 770], fill=white)
    d.polygon([(650, 750), (810, 750), (730, 845)], fill=white)
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    big = draw()
    for size in (16, 32, 48, 128):
        big.resize((size, size), Image.LANCZOS).save(os.path.join(OUT, f'icon{size}.png'))
    print('icons written to', os.path.abspath(OUT))


if __name__ == '__main__':
    main()
