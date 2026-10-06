"""
face_patch.py — [면] 해상도가 달라도 같은 모양인 '매끈한 덩어리'(패치) 찾기.

face_shape.py 는 '평평한 영역의 외곽선'을 읽는다. 그래서 베벨이나 둥근 면이 섞이면(예: 원을 inset·extrude 한 뒤 모서리에 베벨을 준
나사 머리) 덩어리가 쪼개지고, 원을 몇 조각으로 나눴는지(세그먼트 수)가 다르면 다른 모양이 된다.
여기서는 다음 방식으로 그 둘을 해결한다. bmesh 만 다루고 오퍼레이터/UI 는 모른다.

  1) 덩어리: 이웃한 면과의 각도가 delimit_angle 이하면 이어 붙인 덩어리. (베벨 조각들은 한 덩어리가 되고,
     바닥과 만나는 날카로운 모서리에서 끊긴다.)
  2) 비교: 덩어리의 경계 엣지 루프를 호 길이 기준으로 같은 간격 점으로 다시 찍어(재샘플링) 읽는다.
     - 중심에서 점까지의 거리 곡선 + 일정 호 길이만큼 떨어진 두 점 사이 거리 곡선
       (회전·이동·거울·읽기 시작점에 무관, 원을 16조각으로 나눴든 64조각으로 나눴든 같은 곡선)
     - 덩어리의 높이(경계 평면에서 얼마나 솟았는지)와 면적. 내부 면 개수는 보지 않는다.
     - 크기를 무시하면 평균 반지름으로 나눠서 비교한다.
  한계: 곡선의 허용 오차(curve_tolerance)가 이 방식의 핵심 값이다. 기어 톱니처럼 뾰족한 모서리가 많은 외곽선은
  재샘플링 위치가 어긋나면 오차가 커지므로 face_shape.py 의 방식이 더 정확하다.
"""

import math

from mathutils import Vector

from .face_shape import boundary_loops

SAMPLES = 32            # 경계 루프를 다시 찍는 점 개수
DEFAULT_DELIMIT = math.radians(60.0)
DEFAULT_CURVE_TOL = 0.04


def smooth_patches(bm, delimit_angle):
    """보이는 모든 면을 '이웃한 면과 각도가 delimit_angle 이하'로 이어진 덩어리(face 집합)로 나눈다."""
    patches, seen = [], set()
    for f in bm.faces:
        if f.hide or f in seen:
            continue
        patch, stack = {f}, [f]
        seen.add(f)
        while stack:
            cur = stack.pop()
            for e in cur.edges:
                if e.hide:
                    continue
                links = [g for g in e.link_faces if not g.hide]
                if len(links) != 2:
                    continue
                g = links[0] if links[1] is cur else links[1]
                if g in seen or e.calc_face_angle(0.0) > delimit_angle:
                    continue
                seen.add(g)
                patch.add(g)
                stack.append(g)
        patches.append(patch)
    return patches


def _resample(points, m):
    """닫힌 폴리라인을 호 길이 기준으로 m 개의 같은 간격 점으로 다시 찍는다. (점 리스트, 전체 길이)"""
    n = len(points)
    seg = [(points[(i + 1) % n] - points[i]).length for i in range(n)]
    total = sum(seg)
    out, i, acc = [], 0, 0.0
    for k in range(m):
        t = total * k / m
        while i < n - 1 and acc + seg[i] < t:
            acc += seg[i]
            i += 1
        u = (t - acc) / seg[i] if seg[i] > 0.0 else 0.0
        out.append(points[i].lerp(points[(i + 1) % n], min(max(u, 0.0), 1.0)))
    return out, total


class PatchShape:
    """덩어리 하나의 모양 서명. 값싼 스칼라는 바로 계산하고 곡선(프로파일)은 필요할 때만 만든다."""
    __slots__ = ("loops", "scalars", "_points", "_profiles")

    def __init__(self, loops, scalars, points):
        self.loops = loops          # 경계 루프 수
        self.scalars = scalars      # dict: length, radius, height, area
        self._points = points       # 루프마다 버텍스 좌표 (바깥 큰 루프가 앞)
        self._profiles = None

    def profiles(self):
        """루프마다 (거리 곡선, 현 길이 곡선): 평균 반지름으로 나눈 값."""
        if self._profiles is None:
            out = []
            for pts in self._points:
                res, total = _resample(pts, SAMPLES)
                c = sum(res, Vector()) / len(res)
                r = [(p - c).length for p in res]
                mr = sum(r) / len(r) or 1.0
                k = max(1, SAMPLES // 8)
                ch = [(res[(i + k) % SAMPLES] - res[i]).length for i in range(SAMPLES)]
                out.append(([x / mr for x in r], [x / mr for x in ch]))
            self._profiles = out
        return self._profiles


def patch_shape(patch):
    """덩어리의 PatchShape. 경계를 한 줄로 읽을 수 없거나 퇴화했으면 None."""
    loops = boundary_loops(patch)
    if not loops:
        return None
    points = []
    for lp in loops:
        pts = [v.co.copy() for v in lp]
        if len(pts) < 3:
            return None
        points.append(pts)
    points.sort(key=lambda pts: -sum((pts[(i + 1) % len(pts)] - pts[i]).length for i in range(len(pts))))

    outer = points[0]
    n = len(outer)
    seg = [(outer[(i + 1) % n] - outer[i]).length for i in range(n)]
    total = sum(seg)
    if total <= 1e-12:
        return None
    mids = [(outer[i] + outer[(i + 1) % n]) * 0.5 for i in range(n)]
    c = sum((m * s for m, s in zip(mids, seg)), Vector()) / total
    radius = sum((m - c).length * s for m, s in zip(mids, seg)) / total
    if radius <= 1e-12:
        return None

    # Newell 법선: 경계는 면의 감는 방향을 따르므로 법선이 덩어리가 솟은 쪽을 가리킨다.
    nrm = Vector()
    for i in range(n):
        a, b = outer[i], outer[(i + 1) % n]
        nrm.x += (a.y - b.y) * (a.z + b.z)
        nrm.y += (a.z - b.z) * (a.x + b.x)
        nrm.z += (a.x - b.x) * (a.y + b.y)
    if nrm.length <= 1e-12:
        height = 0.0
    else:
        nrm.normalize()
        hs = [(v.co - c).dot(nrm) for f in patch for v in f.verts]
        height = max(hs, key=abs)
    area = sum(f.calc_area() for f in patch)
    return PatchShape(len(points), {'length': total, 'radius': radius, 'height': height, 'area': area}, points)


def _profile_match(a, b, tol):
    """두 프로파일이 읽기 시작점과 방향(거울)만 다른 같은 곡선인지."""
    ra, ca = a
    rb, cb = b
    m = len(ra)
    for rev in (False, True):
        if rev:
            rb2, cb2 = rb[::-1], cb[::-1]
        else:
            rb2, cb2 = rb, cb
        for s in range(m):
            ok = True
            for i in range(m):
                j = (i + s) % m
                if abs(ra[i] - rb2[j]) > tol or abs(ca[i] - cb2[j]) > tol:
                    ok = False
                    break
            if ok:
                return True
    return False


def patches_match(a, b, tol=DEFAULT_CURVE_TOL, scale_invariant=False):
    """a, b 가 같은 모양의 덩어리인지."""
    if a.loops != b.loops:
        return False
    sa, sb = a.scalars, b.scalars
    ra, rb = sa['radius'], sb['radius']
    if not scale_invariant and abs(ra - rb) > tol * max(ra, rb):
        return False
    # 크기 무관 비율로 값싼 검사부터
    if abs(sa['length'] / ra - sb['length'] / rb) > 6.0 * tol * max(sa['length'] / ra, sb['length'] / rb):
        return False
    if abs(sa['height'] / ra - sb['height'] / rb) > 2.0 * tol:
        return False
    aa, ab = sa['area'] / (ra * ra), sb['area'] / (rb * rb)
    if abs(aa - ab) > 4.0 * tol * max(aa, ab):
        return False
    # 경계 곡선: 바깥 루프는 모두 맞아야 하고, 구멍은 서로 짝이 맞으면 된다(구멍 위치는 보지 않는다).
    pa, pb = a.profiles(), b.profiles()
    used = set()
    for x in pa:
        hit = next((j for j, y in enumerate(pb) if j not in used and _profile_match(x, y, tol)), None)
        if hit is None:
            return False
        used.add(hit)
    return True


def patch_seed_shapes(bm, seed_faces, delimit_angle=DEFAULT_DELIMIT):
    """seed_faces 가 속한 덩어리들의 PatchShape 리스트. (같은 덩어리는 한 번만, 읽을 수 없는 건 뺀다)"""
    bm.normal_update()
    wanted = set(seed_faces)
    out = []
    for patch in smooth_patches(bm, delimit_angle):
        if patch & wanted:
            shp = patch_shape(patch)
            if shp is not None:
                out.append(shp)
    return out


def similar_patches(bm, shapes, delimit_angle=DEFAULT_DELIMIT, tol=DEFAULT_CURVE_TOL, scale_invariant=False):
    """bm 에서 shapes 중 하나와 같은 모양의 덩어리들을 face 집합 리스트로 돌려준다. (씨앗 자신의 덩어리도 포함된다.)"""
    bm.normal_update()
    found = []
    for patch in smooth_patches(bm, delimit_angle):
        shp = patch_shape(patch)
        if shp is not None and any(patches_match(sd, shp, tol, scale_invariant) for sd in shapes):
            found.append(patch)
    return found
