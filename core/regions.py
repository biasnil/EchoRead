"""Keep / Ignore rectangles and how they're applied to page images and text layers."""
from __future__ import annotations

from PIL import Image, ImageDraw

from .models import Region, TextBlock


class RegionMask:
    def __init__(self):
        self.only = False  # read only the Keep regions
        self.apply_all = True  # new regions apply to every page
        self._global: list[Region] = []
        self._pages: dict[int, list[Region]] = {}
        self._history: list[tuple[int | None, Region]] = []

    # -- editing
    def add(self, kind: str, rect, page: int | None = None) -> Region:
        region = Region(kind, tuple(float(v) for v in rect))
        (self._global if page is None else self._pages.setdefault(page, [])).append(region)
        self._history.append((page, region))
        return region

    def undo(self) -> bool:
        while self._history:
            page, region = self._history.pop()
            bucket = self._global if page is None else self._pages.get(page, [])
            if region in bucket:
                bucket.remove(region)
                return True
        return False

    def clear(self) -> None:
        self._global.clear()
        self._pages.clear()
        self._history.clear()

    @property
    def is_empty(self) -> bool:
        return not self._global and not any(self._pages.values())

    def signature(self) -> str:
        """Changes whenever anything that affects extraction changes."""
        import hashlib, json

        data = {"only": self.only, "g": [(x.kind, x.rect) for x in self._global],
                "p": {str(k): [(x.kind, x.rect) for x in v] for k, v in sorted(self._pages.items())}}
        return hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def for_page(self, page: int) -> list[Region]:
        return list(self._global) + list(self._pages.get(page, []))

    # -- applying
    def apply_to_image(self, image: Image.Image, page: int) -> list[Image.Image]:
        """Blank Ignore regions; with `only`, return one crop per Keep region (in drawing order)."""
        regions = self.for_page(page)
        w, h = image.size

        def px(r: Region):
            x0, y0, x1, y1 = r.rect
            return int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)

        img = image.copy()
        draw = ImageDraw.Draw(img)
        for r in regions:
            if r.kind == "ignore":
                draw.rectangle(px(r), fill=(255, 255, 255))
        keeps = [r for r in regions if r.kind == "keep"]
        if self.only and keeps:
            return [img.crop(px(r)) for r in keeps]
        return [img]

    def filter_blocks(self, blocks: list[TextBlock], page: int) -> list[TextBlock]:
        """Same rules as apply_to_image, for a PDF's text layer."""
        regions = self.for_page(page)

        def inside(b: TextBlock, rect) -> bool:
            return rect[0] <= b.cx <= rect[2] and rect[1] <= b.cy <= rect[3]

        ignores = [r.rect for r in regions if r.kind == "ignore"]
        keeps = [r.rect for r in regions if r.kind == "keep"]
        blocks = [b for b in blocks if not any(inside(b, r) for r in ignores)]
        if self.only and keeps:
            chosen, seen = [], set()
            for rect in keeps:
                for b in blocks:
                    if inside(b, rect) and id(b) not in seen:
                        seen.add(id(b))
                        chosen.append(b)
            return chosen
        return blocks
