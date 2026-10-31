"""
edge_shape.py — [엣지] 같은 모양의 엣지 루프 찾기.

bmesh 와 numpy 만 다루고 오퍼레이터/UI 는 모른다. 톱니바퀴의 뾰족뾰족한 바퀴(림) 윤곽선처럼 '모양이 있는 엣지 루프'를
다른 곳에서 찾는다. 베벨이 들어가 모서리가 둥글어졌거나, 직선 구간에 버텍스가 더 있어도 같은 모양으로 본다.

비교 방법 (닫힌 루프)
  1) 루프를 호 길이 기준 같은 간격의 점 SAMPLES 개로 다시 찍는다. 버텍스가 몇 개인지는 상관없다.
  2) 중심에서 점까지의 거리 곡선 r(s)와, 루프가 놓인 평면에서의 높이 곡선 h(s)를 평균 반지름으로 나눈다.
  3) 두 곡선을 푸리에 변환해 진폭만 남긴다. 회전, 이동, 거울 반전, 읽기 시작점이 달라도 진폭은 같다.
     (점을 찍는 위치가 반 칸 어긋나도 뾰족한 모서리가 흔들리지 않는다. 그래서 톱니가 많아도 안정적이다.)
  4) 진폭 차이가 기준 진폭의 shape_tol 이내면 같은 모양이다.
     - 톱니 수가 다르면 중심 진동수가 달라 크게 어긋나고, 톱니 깊이가 다르면 진폭이 달라진다.
     - 크기를 무시하지 않으면 평균 반지름도 size_tol 안에서 같아야 한다.
열린 루프는 점 곡선을 앞뒤 방향만 바꿔 직접 비교한다.
"""

import math

import numpy as np

from .edge_core import walk_loop_ordered

SAMPLES = 128           # 루프를 다시 찍는 점 개수 (톱니 수의 몇 배 이상이어야 한다)
HARMONICS = 48          # 비교하는 진동수 개수 (SAMPLES/2 이하)
FLOOR = 0.01            # 거의 원인 루프에서 진폭이 0 에 가까울 때를 위한 바닥값 (평균 반지름 대비)
OPEN_SAMPLES = 64


# ---------------------------------------------------------------------------
# 엣지 -> 순서 있는 점
# ---------------------------------------------------------------------------

def _world(points, matrix):
    """로컬 점 배열을 matrix(4x4 numpy)로 월드 좌표로. matrix 가 None 이면 그대로."""
    if matrix is None:
        return points
    return points @ matrix[:3, :3].T + matrix[:3, 3]


def chain_points(chain, closed, matrix=None):
    """순서대로 이어진 엣지 리스트를 점 좌표 배열(N x 3)로. 이어지지 않으면 None."""
    if len(chain) < (3 if closed else 1):
        return None
    if len(chain) >= 2:
        shared = set(chain[0].verts) & set(chain[1].verts)
        if len(shared) != 1:
            return None
        v = next(x for x in chain[0].verts if x not in shared)
    else:
        v = chain[0].verts[0]
    verts = [v]
    for e in chain:
        nv = e.other_vert(v)
        if nv is None:
            return None
        verts.append(nv)
        v = nv
    if closed:
        verts.pop()
    return _world(np.array([tuple(x.co) for x in verts], dtype=float), matrix)


def candidate_loops(bm, cos_limit, dih):
    """보이는 모든 엣지를 루프로 나눠 (엣지 리스트, 닫힘 여부)들을 돌려준다. 엣지마다 한 루프에만 속한다."""
    seen, out = set(), []
    for e in bm.edges:
        if e.hide or e in seen:
            continue
        chain, _pos, closed, _d = walk_loop_ordered(e, cos_limit, dih)
        seen.update(chain)
        out.append((chain, closed))
    return out


def selected_chains(edges, matrix=None):
    """
    선택한 엣지를 이어진 한 줄(열린 길 또는 닫힌 고리)마다 (점 배열, 닫힘 여부)로 돌려준다.
    갈라지는 곳이 있는 덩어리는 건너뛴다.
    """
    edges = set(edges)
    vlinks = {}
    for e in edges:
        for v in e.verts:
            vlinks.setdefault(v, []).append(e)
    seen, out = set(), []
    for e0 in edges:
        if e0 in seen:
            continue
        comp, stack = [], [e0]
        seen.add(e0)
        while stack:
            e = stack.pop()
            comp.append(e)
            for v in e.verts:
                for g in vlinks[v]:
                    if g not in seen:
                        seen.add(g)
                        stack.append(g)
        if any(len(vlinks[v]) > 2 for e in comp for v in e.verts):
            continue
        ends = [v for e in comp for v in e.verts if len(vlinks[v]) == 1]
        closed = not ends
        start = ends[0] if ends else comp[0].verts[0]
        pts, v, used = [tuple(start.co)], start, set()
        while True:
            nxt = next((g for g in vlinks[v] if g not in used), None)
            if nxt is None:
                break
            used.add(nxt)
            v = nxt.other_vert(v)
            if closed and v is start:
                break
            pts.append(tuple(v.co))
        if len(pts) >= (3 if closed else 2):
            out.append((_world(np.array(pts, dtype=float), matrix), closed))
    return out


# ---------------------------------------------------------------------------
# 서명
# ---------------------------------------------------------------------------

def _resample(points, m, closed):
    """폴리라인을 호 길이 기준 m 개의 같은 간격 점으로. (m x 3, 전체 길이)"""
    pts = np.vstack([points, points[:1]]) if closed else points
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    if total <= 1e-12:
        return None, 0.0
    t = np.linspace(0.0, total, m, endpoint=not closed)
    res = np.stack([np.interp(t, cum, pts[:, i]) for i in range(3)], axis=1)
    return res, total


class EdgeShape:
    """엣지 루프 하나의 서명. 값싼 스칼라를 먼저 만들고 푸리에 진폭은 필요할 때만 만든다."""
    __slots__ = ("closed", "length", "radius", "_points", "_spec", "_prof")

    def __init__(self, points, closed, length, radius):
        self.closed = closed
        self.length = length
        self.radius = radius
        self._points = points
        self._spec = None
        self._prof = None

    def _curves(self, m):
        res, total = _resample(self._points, m, self.closed)
        c = res.mean(axis=0)
        d = res - c
        r = np.linalg.norm(d, axis=1)
        mr = r.mean() or 1.0
        # 루프가 놓인 평면의 법선: Newell (닫힌 루프) / 특이값 분해 (열린 루프)
        if self.closed:
            a, b = res, np.roll(res, -1, axis=0)
            n = np.array([((a[:, 1] - b[:, 1]) * (a[:, 2] + b[:, 2])).sum(),
                          ((a[:, 2] - b[:, 2]) * (a[:, 0] + b[:, 0])).sum(),
                          ((a[:, 0] - b[:, 0]) * (a[:, 1] + b[:, 1])).sum()])
        else:
            n = np.linalg.svd(d, full_matrices=False)[2][-1]
        ln = np.linalg.norm(n)
        h = d @ (n / ln) if ln > 1e-12 else np.zeros(len(res))
        return r / mr, h / mr

    def spectrum(self):
        """(거리 곡선 진폭, 높이 곡선 진폭): 닫힌 루프의 진동수 1..HARMONICS."""
        if self._spec is None:
            r, h = self._curves(SAMPLES)
            amp = lambda x: np.abs(np.fft.rfft(x))[1:HARMONICS + 1] * 2.0 / len(x)
            self._spec = (amp(r), amp(h))
        return self._spec

    def profile(self):
        """열린 루프: 점 개수를 맞춘 (거리 곡선, 높이 곡선)."""
        if self._prof is None:
            self._prof = self._curves(OPEN_SAMPLES)
        return self._prof


def edge_shape(points, closed):
    """EdgeShape. 점이 너무 적거나 크기가 0 이면 None."""
    if points is None or len(points) < (3 if closed else 2):
        return None
    pts = np.vstack([points, points[:1]]) if closed else points
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    total = float(seg.sum())
    if total <= 1e-12:
        return None
    mids = (pts[:-1] + pts[1:]) * 0.5
    c = (mids * seg[:, None]).sum(axis=0) / total
    radius = float((np.linalg.norm(mids - c, axis=1) * seg).sum() / total)
    if radius <= 1e-12:
        return None
    return EdgeShape(points, closed, total, radius)


# ---------------------------------------------------------------------------
# 비교
# ---------------------------------------------------------------------------

def shapes_match(a, b, shape_tol=0.15, size_tol=0.02, scale_invariant=False):
    """
    두 EdgeShape 가 같은 모양인지.
    shape_tol : 진폭 차이의 허용 비율 (기준 진폭 대비). 베벨로 모서리가 둥글어진 정도를 허용하는 값이다.
    size_tol  : 크기를 무시하지 않을 때 평균 반지름이 같다고 볼 비율.
    """
    if a.closed != b.closed:
        return False
    if not scale_invariant and abs(a.radius - b.radius) > size_tol * max(a.radius, b.radius):
        return False
    fa, fb = a.length / a.radius, b.length / b.radius
    if abs(fa - fb) > shape_tol * max(fa, fb):
        return False
    if a.closed:
        for xa, xb in zip(a.spectrum(), b.spectrum()):
            base = max(float(np.linalg.norm(xa)), FLOOR)
            if float(np.linalg.norm(xa - xb)) > shape_tol * base:
                return False
        return True
    ra, ha = a.profile()
    rb, hb = b.profile()
    tol = max(FLOOR, 0.5 * shape_tol * max(float(ra.std()), 0.05))
    for rev in (False, True):
        r2, h2 = (rb[::-1], hb[::-1]) if rev else (rb, hb)
        if (np.abs(ra - r2).max() <= tol and
                (np.abs(ha - h2).max() <= tol or np.abs(ha + h2).max() <= tol)):
            return True
    return False


def similar_loops(bm, shapes, cos_limit, dih, shape_tol=0.15, size_tol=0.02, scale_invariant=False, matrix=None):
    """
    shapes: seed_shapes() 가 만든 EdgeShape 리스트. bm 에서 그 중 하나와 같은 모양인 루프를 찾아 엣지 리스트들로 돌려준다.
    (씨앗 자신의 루프도 포함된다.) matrix: 이 bm 오브젝트의 월드 변환(numpy 4x4), 서로 다른 오브젝트의 크기를 맞춰 비교한다.
    """
    found = []
    for chain, closed in candidate_loops(bm, cos_limit, dih):
        pts = chain_points(chain, closed, matrix)
        shp = edge_shape(pts, closed)
        if shp is not None and any(shapes_match(sd, shp, shape_tol, size_tol, scale_invariant) for sd in shapes):
            found.append(chain)
    return found


def seed_shapes(chains):
    """selected_chains() 결과를 EdgeShape 리스트로. (읽을 수 없는 건 뺀다)"""
    return [s for s in (edge_shape(p, c) for p, c in chains) if s is not None]
