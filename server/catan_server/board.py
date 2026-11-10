"""Coordinate-based base/5–6 boards. No screen coordinates or gameplay dependencies."""

import math
import random

RESOURCES = ("wood", "brick", "wool", "wheat", "ore")


def make_board(expanded: bool, rng: random.Random) -> dict:
    rows = [3, 4, 5, 6, 5, 4, 3] if expanded else [3, 4, 5, 4, 3]
    terrain = [
        r
        for r, n in zip(RESOURCES, (6, 5, 6, 6, 5) if expanded else (4, 3, 4, 4, 3), strict=True)
        for _ in range(n)
    ]
    terrain += ["desert"] * (2 if expanded else 1)
    rng.shuffle(terrain)
    tiles, vertices, edges = {}, {}, {}
    locations, edge_keys = {}, {}
    for row, length in enumerate(rows):
        r = row - len(rows) // 2
        start = math.ceil(-r / 2 - (length - 1) / 2)
        for q in range(start, start + length):
            tid = f"t{len(tiles)}"
            x, y = math.sqrt(3) * (q + r / 2), 1.5 * r
            corners = []
            for i in range(6):
                angle = math.radians(30 + i * 60)
                vx, vy = x + math.cos(angle), y + math.sin(angle)
                key = (round(vx, 5), round(vy, 5))
                if key not in locations:
                    vid = f"v{len(vertices)}"
                    locations[key] = vid
                    vertices[vid] = {
                        "x": key[0],
                        "y": key[1],
                        "tiles": [],
                        "edges": [],
                        "building": None,
                    }
                vid = locations[key]
                vertices[vid]["tiles"].append(tid)
                corners.append(vid)
            for i, a in enumerate(corners):
                b = corners[(i + 1) % 6]
                key = tuple(sorted((a, b)))
                if key not in edge_keys:
                    eid = f"e{len(edges)}"
                    edge_keys[key] = eid
                    edges[eid] = {"a": a, "b": b, "owner": None, "tiles": [], "port": None}
                    vertices[a]["edges"].append(eid)
                    vertices[b]["edges"].append(eid)
                edges[edge_keys[key]]["tiles"].append(tid)
            tiles[tid] = {
                "q": q,
                "r": r,
                "x": x,
                "y": y,
                "vertices": corners,
                "resource": terrain.pop(),
                "number": 0,
            }
    numbers = (
        [2, 12] * 2 + [n for n in (3, 4, 5, 6, 8, 9, 10, 11) for _ in range(3)]
        if expanded
        else [2, 12] + [n for n in (3, 4, 5, 6, 8, 9, 10, 11) for _ in range(2)]
    )
    productive = [t for t in tiles if tiles[t]["resource"] != "desert"]
    for _ in range(20000):
        rng.shuffle(numbers)
        for tid, number in zip(productive, numbers, strict=True):
            tiles[tid]["number"] = number
        if not any(
            len(e["tiles"]) == 2 and all(tiles[t]["number"] in (6, 8) for t in e["tiles"])
            for e in edges.values()
        ):
            break
    else:
        raise RuntimeError("Unable to generate balanced number tokens")
    # Walk the coast so ports are spaced on non-overlapping coastal edges.
    coast = [eid for eid, e in edges.items() if len(e["tiles"]) == 1]
    ordered = [coast[0]]
    current = edges[coast[0]]["b"]
    while len(ordered) < len(coast):
        eid = next(e for e in vertices[current]["edges"] if e in coast and e not in ordered)
        ordered.append(eid)
        e = edges[eid]
        current = e["b"] if e["a"] == current else e["a"]
    specialized = list(RESOURCES) + (["wool"] if expanded else [])
    rng.shuffle(specialized)
    # Odd port counts imply one specialized/specialized seam; otherwise alternate rates.
    ports = [
        value
        for i, resource in enumerate(specialized)
        for value in ([resource, "any"] if i < len(specialized) - 1 else [resource])
    ]
    for i, port in enumerate(ports):
        edges[ordered[i * len(ordered) // len(ports)]]["port"] = port
    return {"tiles": tiles, "vertices": vertices, "edges": edges}
