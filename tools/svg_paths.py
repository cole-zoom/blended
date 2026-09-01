"""Minimal SVG path parser — enough to measure and cluster paths.

potrace emits an absolute M followed by *relative* commands, so the numbers in
a path's `d` attribute are not a flat list of x,y pairs. Reading them as one is how a
seven-glyph wordmark silently clusters into a single blob.
"""

from __future__ import annotations

import re

NUM = re.compile(r'[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?')

def parse_path(d):
    """Yield every on-curve/control point as (x, y) in path space."""
    toks = re.findall(r'([MmLlHhVvCcSsQqTtAaZz])|(' + NUM.pattern + ')', d)
    cmd, nums, pts, cx, cy, start = None, [], [], 0.0, 0.0, (0.0, 0.0)
    i = 0
    flat = [(c, n) for c, n in toks]

    def take(seq, k):
        return [seq.pop(0) for _ in range(k)] if len(seq) >= k else None

    buf = []
    for c, n in flat:
        if c:
            buf.append((c, []))
        elif buf:
            buf[-1][1].append(float(n))
    for c, args in buf:
        rel = c.islower()
        C = c.upper()
        if C == "Z":
            cx, cy = start
            continue
        k = {"M":2,"L":2,"H":1,"V":1,"C":6,"S":4,"Q":4,"T":2,"A":7}[C]
        while len(args) >= k:
            a = [args.pop(0) for _ in range(k)]
            if C == "M":
                cx, cy = (cx+a[0], cy+a[1]) if rel else (a[0], a[1])
                start = (cx, cy); pts.append((cx, cy)); C = "L"
            elif C == "L":
                cx, cy = (cx+a[0], cy+a[1]) if rel else (a[0], a[1]); pts.append((cx, cy))
            elif C == "H":
                cx = cx+a[0] if rel else a[0]; pts.append((cx, cy))
            elif C == "V":
                cy = cy+a[0] if rel else a[0]; pts.append((cx, cy))
            elif C == "C":
                for j in (0, 2, 4):
                    px, py = (cx+a[j], cy+a[j+1]) if rel else (a[j], a[j+1])
                    pts.append((px, py))
                cx, cy = pts[-1]
            elif C in ("S", "Q"):
                for j in (0, 2):
                    px, py = (cx+a[j], cy+a[j+1]) if rel else (a[j], a[j+1])
                    pts.append((px, py))
                cx, cy = pts[-1]
            elif C == "T":
                cx, cy = (cx+a[0], cy+a[1]) if rel else (a[0], a[1]); pts.append((cx, cy))
            elif C == "A":
                cx, cy = (cx+a[5], cy+a[6]) if rel else (a[5], a[6]); pts.append((cx, cy))
    return pts



def path_bbox(d):
    """(min_x, max_x) of a path's control points, in path coordinates."""
    pts = parse_path(d)
    xs = [p[0] for p in pts]
    return (min(xs), max(xs)) if xs else (0.0, 0.0)
