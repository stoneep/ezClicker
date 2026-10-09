"""
face_core.py — [면] 면 위상 헬퍼와 '루프 사이의 면' 모으기.

bmesh 만 다루고 오퍼레이터/UI 는 모른다. 엣지 알고리즘(edge_core)에는 의존하지 않는다.
(edge_range 가 이쪽의 opposite_edge 를 가져다 쓴다. 의존 방향: edge -> face -> common)

  - opposite_edge : 사각형 면에서 맞은편 엣지 (루프에서 옆 루프로 넘어갈 때 쓴다)
  - init_faces    : 씨앗 엣지의 양쪽 면 중 어느 쪽이 화면 '위'인지 정한다 (예전 방식)
  - init_faces_oriented / strip_cw_is_left : 폭의 '시계 방향 쪽'을 화면·글로벌·로컬 기준으로 정한다
  - strip_faces   : 순서대로 놓인 루프들 사이의 면을 모은다
  - order_strip / strip_region : Blender 기본 면 루프 선택이 고른 면 줄을 순서대로 읽고,
                    폭(옆 줄 수)과 길이(면 수)를 바꾼 면 집합을 다시 계산한다
"""

from bpy_extras import view3d_utils
from mathutils import Vector

from .common import screen_mid, screen_score


def opposite_edge(face, edge):
    """사각형 면에서 edge 의 맞은편 엣지. 사각형이 아니면 None."""
    if len(face.verts) != 4:
        return None
    ev = set(edge.verts)
    for e in face.edges:
        if e is not edge and not (set(e.verts) & ev):
            return e
    return None


def init_faces(context, ob, seed):
    """
    씨앗 엣지의 (뒤쪽 면, 앞쪽 면) 인덱스. 휠 업이 화면 위(또는 오른쪽)로 가도록 정한다.
    경계라서 한쪽 면만 있으면 반대쪽은 None.
    """
    faces = [f for f in seed.link_faces if not f.hide][:2]
    if not faces:
        return (None, None)

    base = screen_mid(context, ob, seed)
    scores = []
    for f in faces:
        opp = opposite_edge(f, seed)
        p = screen_mid(context, ob, opp) if opp is not None else None
        scores.append(screen_score(p - base) if (p is not None and base is not None) else 0.0)

    if len(faces) == 1:
        return (None, faces[0].index) if scores[0] >= 0.0 else (faces[0].index, None)

    if scores[0] >= scores[1]:
        fwd, back = faces[0], faces[1]
    else:
        fwd, back = faces[1], faces[0]
    return (back.index, fwd.index)


# ---------------------------------------------------------------------------
# 폭의 방향: 시계 방향 쪽 / 반시계 방향 쪽
#
# 루프(또는 면 줄)가 진행하는 방향 d 와 표면의 바깥 법선 n 이 있으면, d 를 n 둘레로 시계 방향으로 돌린 쪽(d x n)이
# '시계 방향 쪽', 반대가 '반시계 방향 쪽'이다. d 의 부호(어느 쪽으로 진행하는 걸로 볼지)는 기준에 따라 정한다.
#   화면(SCREEN) : 화면에서 오른쪽(세로에 가까우면 위쪽)으로 가는 쪽. 클릭한 순간의 화면이다.
#   글로벌(GLOBAL): 월드 축 중 가장 많이 가리키는 축의 + 방향.
#   로컬(LOCAL)   : 오브젝트 로컬 축 중 가장 많이 가리키는 축의 + 방향.
# ---------------------------------------------------------------------------

SIDE_REFERENCES = ('SCREEN', 'GLOBAL', 'LOCAL')


def has_screen(context):
    """3D 뷰의 화면 정보(영역, 뷰 행렬)가 있는지. 사이드바 패널 안에서는 없다."""
    return context.region is not None and context.region_data is not None


def _positive_sign(vec):
    """vec 의 가장 큰 성분이 양수면 1, 아니면 -1."""
    i = max(range(3), key=lambda k: abs(vec[k]))
    return 1.0 if vec[i] >= 0.0 else -1.0


def travel_direction(context, ob, vec_local, p0_local, p1_local, mode):
    """진행 방향(로컬 좌표). vec_local 은 진행선을 대표하는 벡터, p0/p1 은 화면 부호를 정할 때 투영할 양 끝점."""
    if mode == 'SCREEN' and has_screen(context):
        a = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, ob.matrix_world @ p0_local)
        b = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, ob.matrix_world @ p1_local)
        if a is not None and b is not None and (b - a).length > 1e-6:
            return vec_local if screen_score(b - a) >= 0.0 else -vec_local
    if mode == 'LOCAL':
        return vec_local * _positive_sign(vec_local)
    world = ob.matrix_world.to_3x3() @ vec_local          # 글로벌 (화면 정보가 없을 때의 대체이기도 하다)
    return vec_local * _positive_sign(world)


def clockwise_vector(ob, d, n):
    """진행 방향 d, 표면 법선 n 에서 '시계 방향 쪽'을 가리키는 벡터. 음수 스케일(거울) 오브젝트는 손잡이가 뒤집힌다."""
    cw = d.cross(n)
    return -cw if ob.matrix_world.determinant() < 0.0 else cw


def init_faces_oriented(context, ob, seed, mode):
    """
    init_faces 와 같은 (뒤쪽 면, 앞쪽 면) 인덱스. 다만 앞쪽(+쪽, 폭 '시계 방향')을 화면 위가 아니라
    '진행 방향 기준 시계 방향 쪽 면'으로 정한다. 진행 방향의 부호는 mode(화면/글로벌/로컬)가 정한다.
    """
    faces = [f for f in seed.link_faces if not f.hide][:2]
    if not faces:
        return (None, None)
    a, b = seed.verts
    d = travel_direction(context, ob, b.co - a.co, a.co, b.co, mode).normalized()
    n = sum((f.normal for f in faces), Vector())
    n = faces[0].normal.copy() if n.length < 1e-9 else n.normalized()
    cw = clockwise_vector(ob, d, n)
    mid = (a.co + b.co) * 0.5
    scores = []
    for f in faces:
        opp = opposite_edge(f, seed)
        v = ((opp.verts[0].co + opp.verts[1].co) * 0.5 - mid) if opp is not None else (f.calc_center_median() - mid)
        scores.append(v.dot(cw))
    if len(faces) == 1:
        return (None, faces[0].index) if scores[0] >= 0.0 else (faces[0].index, None)
    if scores[0] >= scores[1]:
        fwd, back = faces[0], faces[1]
    else:
        fwd, back = faces[1], faces[0]
    return (back.index, fwd.index)


def strip_cw_is_left(context, ob, chain, rails, pos, mode):
    """면 줄에서 '시계 방향 쪽'이 왼쪽 레일 쪽이면 True. 줄 진행 방향의 부호는 mode 가 정한다."""
    n = len(chain)
    f = chain[pos]
    c0 = f.calc_center_median()
    if n >= 2:
        j = pos + 1 if pos + 1 < n else pos - 1
        other = chain[j].calc_center_median()
        t = (other - c0) if j > pos else (c0 - other)
        p0, p1 = (c0, other) if j > pos else (other, c0)
    else:
        left_mid = (rails[pos][0].verts[0].co + rails[pos][0].verts[1].co) * 0.5
        right_mid = (rails[pos][1].verts[0].co + rails[pos][1].verts[1].co) * 0.5
        side = left_mid - right_mid
        # 면이 하나뿐이면 레일에 직각인 방향을 줄 방향으로 본다
        t = f.normal.cross(side)
        p0, p1 = c0, c0 + t
    if t.length < 1e-9:
        return True
    d = travel_direction(context, ob, t, p0, p1, mode).normalized()
    cw = clockwise_vector(ob, d, f.normal)
    left_mid = (rails[pos][0].verts[0].co + rails[pos][0].verts[1].co) * 0.5
    return (left_mid - c0).dot(cw) >= 0.0


def strip_faces(bm, loop_sets):
    """
    순서대로 놓인 루프들(엣지 인덱스 집합 리스트)에서, 인접한 두 루프 사이의 면을 모은다.

    한 면이 j번째~j+1번째 루프 '사이'라는 건 다음 둘을 모두 만족한다는 뜻이다.
      - 면의 모든 버텍스가 그 두 루프의 버텍스 안에 있다.
      - 면에 두 루프의 엣지가 아닌 엣지(= 두 루프를 잇는 가로대)가 하나 이상 있다.
        (루프 하나가 통째로 테두리인 캡 면이 딸려 오지 않게 한다.)
    전체 루프의 버텍스를 한꺼번에 보지 않고 인접한 쌍끼리만 보므로,
    링이 한 바퀴 돌아 반대편에서 다시 맞닿는 곳의 면이 잘못 섞여 들어가지 않는다.
    미러 반대편 엣지도 루프 집합에 들어 있어서 이음매를 건너는 면도 같이 잡힌다.
    """
    bm.edges.ensure_lookup_table()
    edge_verts = []
    for s in loop_sets:
        vs = set()
        for i in s:
            vs.update(bm.edges[i].verts)
        edge_verts.append(vs)

    faces = set()
    for j in range(len(loop_sets) - 1):
        verts = edge_verts[j] | edge_verts[j + 1]
        eset = loop_sets[j] | loop_sets[j + 1]
        for i in loop_sets[j]:
            for f in bm.edges[i].link_faces:
                if f.hide or f in faces:
                    continue
                if not all(v in verts for v in f.verts):
                    continue
                if all(e.index in eset for e in f.edges):
                    continue
                faces.add(f)
    return faces


# ---------------------------------------------------------------------------
# 면 줄(face strip): Blender 기본 면 모드 Alt+클릭이 고르는 사각형 면의 한 줄
#
#   줄의 방향으로 이어진 면들이 chain, 이웃한 두 면이 공유하는 엣지가 '가로대(rung)',
#   줄의 양 옆을 따라 달리는 엣지가 '레일(rail)'. 폭을 늘리면 레일 너머로 나란한 면 줄이 붙는다.
# ---------------------------------------------------------------------------

def window_indices(n, pos, closed, keep):
    """0..n-1 중 pos 를 가운데로 keep 개의 인덱스. 닫힌 고리는 돌아서 잇고 열린 줄은 끝에서 멈춘다.
    (edge_core.trim_window 와 같은 규칙. face_core 는 edge_core 에 의존하지 않으므로 따로 둔다.)"""
    if not keep or keep >= n:
        return list(range(n))
    keep = max(1, keep)
    before = (keep - 1) // 2
    if closed:
        s = pos - before
        return [(s + i) % n for i in range(keep)]
    s = max(0, min(pos - before, n - keep))
    return list(range(s, s + keep))


def order_strip(faces):
    """
    면 집합이 사각형 면 한 줄이면 (순서대로 놓인 면 리스트, 닫힌 고리 여부), 아니면 None.
    줄이 갈라지거나(한 면에 이웃 3개 이상) 삼각형/N-gon 이 섞여 있으면 None.
    """
    faces = set(faces)
    if not faces or any(len(f.verts) != 4 for f in faces):
        return None
    nbrs = {}
    for f in faces:
        found = []
        for e in f.edges:
            for g in e.link_faces:
                if g is not f and g in faces and g not in found:
                    found.append(g)
        if len(found) > 2:
            return None
        nbrs[f] = found
    if len(faces) == 1:
        return list(faces), False
    ends = [f for f in faces if len(nbrs[f]) <= 1]
    closed = not ends
    if not closed and len(ends) != 2:
        return None
    start = min(ends if ends else faces, key=lambda f: f.index)
    chain, prev, cur = [start], None, start
    while len(chain) < len(faces):
        nxt = [g for g in nbrs[cur] if g is not prev and g not in chain]
        if not nxt:
            return None
        prev, cur = cur, nxt[0]
        chain.append(cur)
    return chain, closed


def _shared(f, g):
    """두 면이 공유하는 엣지 (여러 개면 첫 번째)."""
    for e in f.edges:
        if g in e.link_faces:
            return e
    return None


def strip_rails(chain, closed, seed=None):
    """
    줄의 면마다 (왼쪽 레일, 오른쪽 레일). 왼/오른쪽은 줄 전체에서 같은 쪽이다. 읽을 수 없으면 None.
    seed: 면이 하나뿐일 때 가로대로 쓸 엣지 (줄 방향을 정하는 데 필요하다).
    """
    n = len(chain)
    rungs = [_shared(chain[i], chain[(i + 1) % n]) for i in range(n if closed else n - 1)]
    if any(r is None for r in rungs):
        return None
    prev_r, next_r = [], []
    for i, f in enumerate(chain):
        if closed:
            p, q = rungs[i - 1], rungs[i]
        elif n == 1:
            p = seed if seed in f.edges else f.edges[0]
            q = opposite_edge(f, p)
        else:
            p = rungs[i - 1] if i > 0 else None
            q = rungs[i] if i < n - 1 else None
            if p is None:
                p = opposite_edge(f, q)
            if q is None:
                q = opposite_edge(f, p)
        if p is None or q is None:
            return None
        prev_r.append(p)
        next_r.append(q)

    lv = min(prev_r[0].verts, key=lambda v: v.index)
    out = []
    for i, f in enumerate(chain):
        rails = [e for e in f.edges if e is not prev_r[i] and e is not next_r[i]]
        if len(rails) != 2:
            return None
        left = next((e for e in rails if lv in e.verts), None)
        if left is None:
            return None
        right = rails[1] if rails[0] is left else rails[0]
        out.append((left, right))
        lv = left.other_vert(lv)
    return out


def walk_across(face, edge, limit):
    """face 에서 edge 를 건너 바깥쪽으로 나란한 면을 limit 개까지 모은다. (맞은편 엣지를 따라 한 줄로)"""
    out, seen = [], {face}
    f, e = face, edge
    while len(out) < limit:
        others = [g for g in e.link_faces if g is not f and not g.hide]
        if len(others) != 1:
            break
        g = others[0]
        if g in seen or len(g.verts) != 4:
            break
        out.append(g)
        seen.add(g)
        e = opposite_edge(g, e)
        if e is None or e.hide:
            break
        f = g
    return out


def strip_region(chain, closed, rails, seed_pos, steps_left, steps_right, keep):
    """
    줄을 keep 개 면으로 줄이고(0 이면 전체, 씨앗 위치를 가운데로) 양옆으로 나란한 줄을 steps 개씩 붙인 면 집합.
    막혀서 못 붙이는 곳이 있으면 모든 면이 같은 폭이 되도록 가장 짧은 쪽에 맞춘다.
    반환: (면 집합, 실제 왼쪽 줄 수, 실제 오른쪽 줄 수)
    """
    win = window_indices(len(chain), seed_pos, closed, keep)
    faces = {chain[i] for i in win}
    eff = []
    for side, steps in ((0, steps_left), (1, steps_right)):
        cols = [walk_across(chain[i], rails[i][side], steps) for i in win] if steps > 0 else []
        k = min((len(c) for c in cols), default=0)
        for c in cols:
            faces.update(c[:k])
        eff.append(k)
    return faces, eff[0], eff[1]
