"""
face_range.py — Ctrl+Alt+클릭 직후 휠로 루프를 바깥/안쪽으로 넓히고 줄이는 로직.
(엣지 모드/면 모드 모두 쓰지만 '무엇을 선택하는지'가 다르다.)

bmesh 만 다루고 오퍼레이터/UI 는 모른다. (의존 방향: face_range -> edge_range -> face_core -> common)

모델
  루프 오프셋: 시작 루프(앵커 A)가 0, 한쪽으로 한 칸씩 1, 2, ... / 반대쪽으로 -1, -2, ...
               (edge_range.chain_state / build_offset 이 만든다. k>0 은 chains[1], k<0 은 chains[-1])
  면 띠       : j번째 루프와 j+1번째 루프 사이의 면 = strips[j]   (면 모드에서만 쓴다)
  선택 범위   : 루프 lo ~ hi. 처음 선택한 범위가 lo0 ~ hi0 이고
               휠로 이 범위를 양쪽 끝에서 넓히거나(바깥/안쪽 루프 추가) 처음 범위까지 줄인다.
               루프 하나만 골랐다면 lo0 = hi0 = 0 이다.

선택 모드별 차이 (st['face_mode'])
  엣지(·버텍스) 모드 : '루프 엣지만' 선택한다. 루프 사이의 가로대 엣지/면은 선택하지 않는다.
                       (면 띠는 계산하지 않는다. 옆 루프 사이에 면이 없어도 루프는 계속 늘린다.)
  면 모드             : 루프 사이의 면 띠를 선택한다. (루프를 잇는 모든 면 = 모든 정점이 같이 선택)
                       면만 켜고 끄면 엣지/버텍스는 Blender 가 맞춰 준다.

바깥/안쪽 정하기 (decide_outer)
  범위 양 끝 루프의 '크기'(루프 중심에서 버텍스까지 평균 거리)를 비교해 더 큰 쪽을 바깥으로 본다.
  (동심원 모양 루프, 구의 위도 링 등)
  크기가 비슷하면(원통을 따라 쌓인 링, 평면 격자 등) 화면에서 위쪽(거의 수평이면 오른쪽)을 바깥으로 본다.
  루프 하나만 골랐을 때는 비교할 대상이 없으므로 화면 위쪽(거의 수평이면 오른쪽)을 바깥으로 본다.

  Alt  + 휠 업/다운 : 바깥·안쪽 루프를 동시에 한 칸씩 추가/제거
  Ctrl + 휠 업      : 안쪽이 늘어나 있으면 안쪽부터 줄이고, 아니면 바깥으로 한 칸 추가
  Ctrl + 휠 다운    : 바깥이 늘어나 있으면 바깥부터 줄이고, 아니면 안쪽으로 한 칸 추가
"""

from mathutils import Vector

from .common import is_face_mode, mesh_counts, screen_point, screen_score
from .edge_range import build_offset, chain_state, fork_chain
from .face_core import init_faces, strip_faces

OUTER_SIZE_TOL = 0.02   # 양 끝 루프 크기 차이가 큰 쪽 기준 이 비율보다 작으면 '비슷하다'고 본다


# ---------------------------------------------------------------------------
# 상태 만들기
# ---------------------------------------------------------------------------

def loop_at(st, m):
    """오프셋 m 의 루프(엣지 인덱스 집합)."""
    return st['chains'][1 if m > 0 else -1]['loops'][m]


def strip_at(bm, st, j):
    """j번째~j+1번째 루프 사이의 면 인덱스 집합을 (필요하면 계산해서) 기록한다. 비었으면 False."""
    idxs = st['strips'].get(j)
    if idxs is None:
        idxs = {f.index for f in strip_faces(bm, [loop_at(st, j), loop_at(st, j + 1)])}
        st['strips'][j] = idxs
    return bool(idxs)


def loop_extent(bm, idxs):
    """루프의 (중심, 중심에서 버텍스까지 평균 거리). 루프가 비었으면 (None, 0)."""
    verts = set()
    for i in idxs:
        verts.update(bm.edges[i].verts)
    if not verts:
        return None, 0.0
    center = Vector((0.0, 0.0, 0.0))
    for v in verts:
        center += v.co
    center /= len(verts)
    radius = sum((v.co - center).length for v in verts) / len(verts)
    return center, radius


def decide_outer(context, ob, bm, st):
    """오프셋이 커지는 쪽(hi)이 바깥이면 +1, 작아지는 쪽(lo)이 바깥이면 -1."""
    c_lo, r_lo = loop_extent(bm, loop_at(st, st['lo']))
    c_hi, r_hi = loop_extent(bm, loop_at(st, st['hi']))
    if c_lo is None or c_hi is None:
        return 1

    big = max(r_lo, r_hi)
    if big > 0.0 and abs(r_hi - r_lo) > big * OUTER_SIZE_TOL:
        return 1 if r_hi > r_lo else -1

    p_hi = screen_point(context, ob, c_hi)
    p_lo = screen_point(context, ob, c_lo)
    if p_hi is not None and p_lo is not None:
        return 1 if screen_score(p_hi - p_lo) >= 0.0 else -1
    return 1


def new_face_wheel(context, ob, bm, chains, lo, hi, dih, allow_empty=False):
    """
    루프 오프셋 lo ~ hi 로 휠 확장 상태를 만든다.
    chains      : {+1: 체인, -1: 체인} (edge_range 의 chain_state / find_between_chains 가 만든 것)
    allow_empty : True 면 면이 하나도 없어도(루프 하나만 고른 경우 등) 상태를 만든다.
    반환: (상태, 처음 선택할 면 인덱스 집합)
      - 면 모드   : 루프 사이의 면 띠를 모은다. 면이 없고 allow_empty 가 False 면 (None, set()).
      - 엣지 모드 : 면은 선택하지 않는다. 항상 (상태, set()) 이다.
    호출하는 쪽에서 bm 의 인덱스(ensure_tables)가 최신인 상태여야 한다.
    선택을 끝낸 뒤 st['base_faces'], st['base_edges'] 에 '그 시점에 선택된 면/엣지 인덱스'를 넣어 줄 것.
    """
    st = {
        'ob': ob.name,
        'counts': mesh_counts(bm),
        'face_mode': is_face_mode(context),
        'dih': dih,
        'chains': chains,
        'lo': lo, 'hi': hi,
        'lo0': lo, 'hi0': hi,
        'strips': {},
        'base_faces': set(),
        'base_edges': set(),
        'outer': 1,
    }
    faces = set()
    if st['face_mode']:
        for j in range(lo, hi):
            strip_at(bm, st, j)
            faces |= st['strips'][j]
        if not faces and not allow_empty:
            return None, set()
    if lo != hi:
        st['outer'] = decide_outer(context, ob, bm, st)
    return st, faces


def single_loop(context, ob, bm, seed, axes, threshold, cos_limit, dih):
    """
    seed 루프 하나만 고른 상태로 휠 확장을 시작하기 위한 (chains, lo, hi).
    오프셋 +1 이 화면 위(거의 수평이면 오른쪽)가 되도록 양쪽 면의 방향을 정한다.
    """
    cache = {}
    base = chain_state(bm, seed, axes, threshold, cos_limit, dih, cache)
    base['faces'][0] = init_faces(context, ob, seed)     # (뒤쪽 면, 앞쪽 면) = (오프셋 -1, +1 방향)
    chains = {1: base, -1: fork_chain(base)}
    return chains, 0, 0


def click_strip(context, ob, bm, seed, axes, threshold, cos_limit, dih, mouse):
    """
    seed 루프 바로 옆의 면 띠(= 루프 하나와 그 옆 루프 사이)를 고른다. (면 모드 전용)
    seed 엣지의 양쪽 면 중 마우스에 가까운 쪽을 먼저 쓰고, 막혀 있으면 반대쪽을 쓴다.
    반환: (chains, lo, hi) 또는 못 찾으면 None.
    """
    cache = {}
    base = chain_state(bm, seed, axes, threshold, cos_limit, dih, cache)
    chains = {1: base, -1: fork_chain(base)}

    # chain_state 와 같은 순서: faces[0] -> 오프셋 -1 쪽, faces[1] -> 오프셋 +1 쪽
    faces = [f for f in seed.link_faces if not f.hide][:2]
    order = [-1, 1]
    if len(faces) == 2 and mouse is not None:
        dist = []
        for f in faces:
            p = screen_point(context, ob, f.calc_center_median())
            dist.append((p - mouse).length if p is not None else float('inf'))
        if dist[1] < dist[0]:
            order = [1, -1]

    for sign in order:
        if build_offset(bm, chains[sign], sign):
            return (chains, 0, 1) if sign > 0 else (chains, -1, 0)
    return None


# ---------------------------------------------------------------------------
# 선택 상태 확인 / 적용
# ---------------------------------------------------------------------------

def desired_faces(st, lo, hi):
    out = set()
    for j in range(lo, hi):
        out |= st['strips'].get(j, set())
    return out


def desired_edges(st, lo, hi):
    """루프 lo ~ hi 의 엣지 인덱스 집합."""
    out = set()
    for m in range(lo, hi + 1):
        out |= loop_at(st, m)
    return out


def face_state_valid(ob, bm, st):
    """메시/선택이 마지막으로 우리가 만든 상태 그대로인지 확인한다."""
    if st is None or ob.name != st['ob']:
        return False
    if mesh_counts(bm) != st['counts']:
        return False

    lo, hi = st['lo'], st['hi']
    if st['face_mode']:
        desired = desired_faces(st, lo, hi)
        for i in desired:
            if not bm.faces[i].select:
                return False
        for j, idxs in st['strips'].items():
            if lo <= j < hi:
                continue
            for i in idxs:
                if i not in desired and i not in st['base_faces'] and bm.faces[i].select:
                    return False
    else:
        # 엣지/버텍스 모드: 루프 엣지가 선택돼 있어야 한다. (면은 보지 않는다)
        for i in desired_edges(st, lo, hi):
            if not bm.edges[i].select:
                return False
    return True


def apply_faces(bm, st):
    """st 의 lo ~ hi 범위에 맞게 선택을 갱신한다.
      면 모드             : 루프 사이의 면
      엣지/버텍스 모드     : 루프 엣지만
    처음 선택 시점의 면/엣지는 해제하지 않는다."""
    lo, hi = st['lo'], st['hi']

    if st['face_mode']:
        desired = desired_faces(st, lo, hi)
        keep = desired | st['base_faces']
        for idxs in st['strips'].values():
            for i in idxs:
                if i not in keep:
                    bm.faces[i].select_set(False)
        for i in desired:
            bm.faces[i].select_set(True)
    else:
        want = desired_edges(st, lo, hi) | st['base_edges']
        for chain in st['chains'].values():
            for idxs in chain['loops'].values():
                for i in idxs:
                    if i not in want:
                        bm.edges[i].select_set(False)
        for i in want:
            bm.edges[i].select_set(True)

    bm.select_flush_mode()


# ---------------------------------------------------------------------------
# 한 칸 확장 / 축소 (성공하면 st 의 lo/hi 를 바꾸고 True)
#   side: +1 = 오프셋이 커지는 쪽(hi), -1 = 작아지는 쪽(lo)
# ---------------------------------------------------------------------------

def extend_side(bm, st, side):
    k = st['hi'] + 1 if side > 0 else st['lo'] - 1
    if not build_offset(bm, st['chains'][1 if k > 0 else -1], k):
        return False
    # 면 모드는 새 루프와 기존 끝 루프 사이에 면이 없으면 선택할 게 없으므로 멈춘다.
    # 엣지 모드는 면과 상관없이 루프만 계속 늘린다.
    if st['face_mode']:
        j = k - 1 if k > 0 else k            # 새 루프와 기존 끝 루프 사이의 띠
        if not strip_at(bm, st, j):
            return False
    if side > 0:
        st['hi'] = k
    else:
        st['lo'] = k
    return True


def shrink_side(st, side):
    if side > 0:
        if st['hi'] <= st['hi0']:
            return False
        st['hi'] -= 1
    else:
        if st['lo'] >= st['lo0']:
            return False
        st['lo'] += 1
    return True


def step_faces_one(bm, st, direction):
    """Ctrl+휠: 한 방향 확장/축소. 바뀌었으면 True."""
    outer = st['outer']
    inner = -outer
    if direction > 0:      # 휠 업: 안쪽이 늘어나 있으면 줄이고, 아니면 바깥으로 확장
        return shrink_side(st, inner) or extend_side(bm, st, outer)
    # 휠 다운: 바깥이 늘어나 있으면 줄이고, 아니면 안쪽으로 확장
    return shrink_side(st, outer) or extend_side(bm, st, inner)


def step_faces_both(bm, st, direction):
    """Alt+휠: 바깥·안쪽 동시 확장/축소. 바뀌었으면 True."""
    outer = st['outer']
    inner = -outer
    if direction > 0:
        # 한쪽이 막혀 있어도(경계, 삼각형 등) 되는 쪽은 계속 늘린다.
        a = extend_side(bm, st, outer)
        b = extend_side(bm, st, inner)
        return a or b
    a = shrink_side(st, outer)
    b = shrink_side(st, inner)
    return a or b
