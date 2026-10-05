"""
face_shape.py — [면] 모양이 같은 면(평면 영역) 찾기.

bmesh 만 다루고 오퍼레이터/UI 는 모른다. 기어, 나사 머리처럼 평평한 면을 많이 만들 때
면 하나를 고르면 같은 모양의 면을 전부 찾는 데 쓴다.

비교 단위는 '평평한 영역(island)'이다.
  - 서로 이웃하고 법선이 거의 같은 면들을 한 덩어리로 본다. 안쪽을 어떻게 나눴는지(삼각분할, 사각형)는 상관없다.
  - 덩어리의 모양 = 외곽선(구멍이 있으면 구멍의 테두리까지). 외곽선을 한 바퀴 돌며
    '변 길이, 꺾임 각, 변 길이, 꺾임 각, ...' 로 읽은 값이 같으면 같은 모양이다.
  - 회전, 이동, 거울 반전은 무시한다. (읽기 시작점이 달라도, 반대 방향으로 읽어도 같으면 일치)
  - 외곽선 위의 일직선 꼭짓점은 지운다. 같은 모양인데 변을 몇 개로 쪼갰는지만 다른 경우를 잡기 위해서다.
  - 구멍이 있는 영역(예: 육각 홈이 있는 나사 머리)은 바깥 테두리와 구멍 테두리가 각각 일치해야 한다.
    (구멍의 위치는 보지 않는다.)

크기가 다른 같은 모양(닮음)도 찾으려면 scale_invariant 를 켠다.
"""

import math


DEFAULT_FLAT = math.radians(1.0)       # 이웃한 면의 법선 차이가 이 안쪽이면 같은 평면으로 본다
DEFAULT_ANGLE_TOL = math.radians(1.0)  # 꺾임 각 허용 오차
DEFAULT_LEN_TOL = 0.01                 # 변 길이 허용 오차 (긴 쪽 대비 비율)
COLLINEAR = math.radians(1.0)          # 꺾임이 이보다 작은 꼭짓점은 일직선으로 보고 지운다


# ---------------------------------------------------------------------------
# 평평한 영역(island)
# ---------------------------------------------------------------------------

def flat_island(seed, flat_angle, taken=None):
    """
    seed 면에서 시작해 이웃한 면 중 법선이 seed 와 거의 같은 면을 모두 모은다. (face 집합)
    taken 이 있으면 그 안의 면은 건드리지 않는다. 숨긴 면은 무시한다.
    """
    island = {seed}
    stack = [seed]
    n0 = seed.normal
    while stack:
        f = stack.pop()
        for e in f.edges:
            for g in e.link_faces:
                if g in island or g.hide or (taken is not None and g in taken):
                    continue
                if g.normal.angle(n0, 0.0) <= flat_angle and g.normal.angle(f.normal, 0.0) <= flat_angle:
                    island.add(g)
                    stack.append(g)
    return island


def all_islands(bm, flat_angle, use_island=True):
    """보이는 모든 면을 평평한 영역으로 나눈 리스트 (face 집합들)."""
    taken = set()
    out = []
    for f in bm.faces:
        if f.hide or f in taken:
            continue
        isl = flat_island(f, flat_angle, taken) if use_island else {f}
        taken |= isl
        out.append(isl)
    return out


# ---------------------------------------------------------------------------
# 외곽선 -> 모양 서명
# ---------------------------------------------------------------------------

def boundary_loops(island):
    """
    island(face 집합)의 외곽선을 버텍스 리스트들로 돌려준다. (면의 감는 방향을 따라, 바깥은 반시계 / 구멍은 시계)
    외곽선이 한 줄로 이어지지 않으면(점으로만 맞닿는 등) None.
    """
    nxt = {}
    for f in island:
        for l in f.loops:
            if sum(1 for g in l.edge.link_faces if g in island) != 1:
                continue
            a, b = l.vert, l.link_loop_next.vert
            if a in nxt:
                return None
            nxt[a] = b
    loops = []
    seen = set()
    for start in nxt:
        if start in seen:
            continue
        loop, v = [], start
        while v not in seen:
            seen.add(v)
            loop.append(v)
            v = nxt.get(v)
            if v is None:
                return None
        if v is not start:
            return None
        loops.append(loop)
    return loops


def _turn(d0, d1, normal):
    """d0 방향으로 오다가 d1 방향으로 꺾는 각 (normal 기준 반시계가 +)."""
    return math.atan2(normal.dot(d0.cross(d1)), d0.dot(d1))


def loop_tokens(verts, normal):
    """
    외곽선 한 줄을 [변0 길이, 꼭짓점1 꺾임, 변1 길이, 꼭짓점2 꺾임, ...] 로 읽는다.
    일직선 꼭짓점은 지운다. 쓸 수 없는 외곽선(꼭짓점 3개 미만, 길이 0)이면 None.
    """
    pts = [v.co for v in verts]
    n = len(pts)
    if n < 3:
        return None
    dirs = []
    for i in range(n):
        d = pts[(i + 1) % n] - pts[i]
        if d.length <= 1e-12:
            return None
        dirs.append(d.normalized())
    keep = [i for i in range(n) if abs(_turn(dirs[i - 1], dirs[i], normal)) >= COLLINEAR]
    if len(keep) < 3:
        return None
    kp = [pts[i] for i in keep]
    k = len(kp)
    kd = []
    for i in range(k):
        d = kp[(i + 1) % k] - kp[i]
        if d.length <= 1e-12:
            return None
        kd.append(d)
    tokens = []
    for i in range(k):
        tokens.append(kd[i].length)
        tokens.append(_turn(kd[i].normalized(), kd[(i + 1) % k].normalized(), normal))
    return tokens


def reversed_tokens(tokens):
    """같은 외곽선을 반대 방향으로 읽은 토큰. (거울 반전한 모양과 비교할 때 쓴다)"""
    rev = list(reversed(tokens))
    return rev[1:] + rev[:1]          # 다시 '길이'가 맨 앞에 오도록 한 칸 돌린다


class Shape:
    """평평한 영역 하나의 모양 서명."""
    __slots__ = ("loops", "area", "key")

    def __init__(self, loops, area):
        # loops: [(앞으로 읽은 토큰, 뒤로 읽은 토큰), ...]  바깥 테두리가 앞에 오도록 둘레 큰 순
        self.loops = loops
        self.area = area
        self.key = (len(loops), tuple(sorted(len(a) for a, _ in loops)))


def island_shape(island, scale_invariant=False):
    """island 의 Shape. 외곽선을 읽을 수 없으면 None."""
    seed = next(iter(island))
    normal = seed.normal
    loops = boundary_loops(island)
    if not loops:
        return None
    toks = []
    for lp in loops:
        t = loop_tokens(lp, normal)
        if t is None:
            return None
        toks.append(t)
    area = sum(f.calc_area() for f in island)
    if scale_invariant:
        outer = max(sum(t[0::2]) for t in toks)      # 가장 큰 외곽선 둘레로 맞춘다
        s = 1.0 / outer
        toks = [[x * s if i % 2 == 0 else x for i, x in enumerate(t)] for t in toks]
        area *= s * s
    toks.sort(key=lambda t: -sum(t[0::2]))
    return Shape([(t, reversed_tokens(t)) for t in toks], area)


# ---------------------------------------------------------------------------
# 비교
# ---------------------------------------------------------------------------

def tokens_match(a, b, len_tol, ang_tol):
    """토큰 a 와 b 가 읽기 시작점만 다른 같은 순환 열인지 (허용 오차 안에서)."""
    m = len(a)
    if m != len(b):
        return False
    for s in range(0, m, 2):           # 시작점은 '길이' 자리에만 맞춘다
        ok = True
        for i in range(m):
            x, y = a[i], b[(i + s) % m]
            if i % 2 == 0:
                if abs(x - y) > len_tol * max(x, y):
                    ok = False
                    break
            elif abs(x - y) > ang_tol:
                ok = False
                break
        if ok:
            return True
    return False


def shapes_match(a, b, len_tol=DEFAULT_LEN_TOL, ang_tol=DEFAULT_ANGLE_TOL):
    """두 Shape 가 같은 모양인지. 거울 반전은 모든 외곽선에 일관되게 적용한다."""
    if a.key != b.key:
        return False
    if abs(a.area - b.area) > 2.0 * len_tol * max(a.area, b.area):
        return False
    for mirror in (0, 1):
        used = set()
        ok = True
        for ta, _ in a.loops:
            hit = None
            for j, lb in enumerate(b.loops):
                if j not in used and tokens_match(ta, lb[mirror], len_tol, ang_tol):
                    hit = j
                    break
            if hit is None:
                ok = False
                break
            used.add(hit)
        if ok:
            return True
    return False


def similar_islands(bm, seed_shapes, flat_angle=DEFAULT_FLAT, len_tol=DEFAULT_LEN_TOL,
                    ang_tol=DEFAULT_ANGLE_TOL, scale_invariant=False, use_island=True):
    """
    bm 에서 seed_shapes 중 하나와 같은 모양인 평평한 영역들을 찾아 face 집합의 리스트로 돌려준다.
    (seed 자신의 영역도 포함된다.)
    """
    keys = {s.key for s in seed_shapes}
    found = []
    for isl in all_islands(bm, flat_angle, use_island):
        # 서명을 만든 뒤, 외곽선 수·꼭짓점 수(key)가 맞는 후보만 정밀 비교한다.
        shp = island_shape(isl, scale_invariant)
        if shp is None or shp.key not in keys:
            continue
        if any(shapes_match(sd, shp, len_tol, ang_tol) for sd in seed_shapes):
            found.append(isl)
    return found
