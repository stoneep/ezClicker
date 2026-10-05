"""
edge_range.py — [엣지] 옆 루프로 선택 범위를 넓히고 줄이는 로직.

  - 휠 확장/축소 (Alt+휠 = 위·아래 동시, Ctrl+휠 = 한 방향)
  - 시작 루프 ~ 끝 루프 사이 탐색 (face_ops 의 사이 선택이 쓴다)

모델: 클릭한 루프가 오프셋 0.
      선택 = 오프셋 lo ~ hi 사이의 모든 루프. (lo <= 0 <= hi)
      hi 쪽이 '화면 위(또는 오른쪽)', lo 쪽이 '화면 아래(또는 왼쪽)'.

  Alt  + 휠 업/다운 : 위·아래를 동시에 한 칸씩 늘리기/줄이기
  Ctrl + 휠 업/다운 : 한 방향으로 늘리기/줄이기
                      (아래쪽이 늘어나 있으면 먼저 그쪽부터 줄이고, 0을 넘어가면 반대쪽으로 확장)

옆 루프로 넘어갈 때는 사각형 면의 맞은편 엣지를 따라가므로 face_core.opposite_edge 를 쓴다.
"""

from .common import mesh_counts
from .edge_core import find_mirror_edges, trimmed_loop, walk_loop
from .face_core import opposite_edge


def new_wheel_state(ob, bm, seed, loop_idxs, axes, threshold, cos_limit, dih, full_idxs=None):
    """
    Alt+클릭으로 루프를 고른 직후의 휠 확장 상태를 만든다. (state.set_wheel 에 넘긴다)

    loop_idxs : 시작 루프(+미러 반대편)의 엣지 인덱스 집합. 오프셋 0.
    호출하는 쪽에서 bm 의 인덱스(ensure_tables)가 최신인 상태여야 한다.

    full_idxs : 길이를 줄이기 전의 전체 루프 엣지 인덱스. (줄이지 않았으면 loop_idxs 와 같다)
                '한 바퀴 돌아 이미 선택한 루프로 돌아왔는지' 판정에 쓴다.
    상태에 keep(루프마다 남길 엣지 수)과 ref_dir 을 넣으면 build_offset 이 나란한 루프도 같은 길이로 자른다.
    """
    return {
        'ob': ob.name,
        'counts': mesh_counts(bm),
        'axes': axes,
        'threshold': threshold,
        'cos_limit': cos_limit,
        'dih': dih,
        'base_sel': {e.index for e in bm.edges if e.select},
        'lo': 0,       # 아래쪽(휠 다운 방향)으로 확장된 오프셋 (<= 0)
        'hi': 0,       # 위쪽(휠 업 방향)으로 확장된 오프셋 (>= 0)
        'seeds': {0: seed.index},
        'faces': {},   # 오프셋 -> (뒤쪽 면 인덱스, 앞쪽 면 인덱스). 0번은 첫 휠에서 결정
        'loops': {0: loop_idxs},
        'full': {0: set(full_idxs if full_idxs is not None else loop_idxs)},
        'keep': 0,
        'ref_dir': None,
    }


def build_offset(bm, st, k):
    """오프셋 k 의 루프를 계산해 상태에 기록한다. 이어갈 루프가 없으면 False."""
    if k in st['loops']:
        return True

    base = k - 1 if k > 0 else k + 1
    back_i, fwd_i = st['faces'][base]
    face_i = fwd_i if k > 0 else back_i
    if face_i is None:
        return False

    face = bm.faces[face_i]
    new = opposite_edge(face, bm.edges[st['seeds'][base]])
    if new is None or new.hide:
        return False
    # 한 바퀴 돌아 이미 선택한 루프로 돌아온 경우 (길이를 줄였어도 전체 루프 기준으로 본다)
    if any(new.index in idxs for idxs in st.get('full', st['loops']).values()):
        return False

    others = [f for f in new.link_faces if f is not face and not f.hide]
    other_i = others[0].index if others else None
    st['faces'][k] = (face_i, other_i) if k > 0 else (other_i, face_i)
    st['seeds'][k] = new.index

    loop = walk_loop(new, st['cos_limit'], st['dih'])
    full = {e.index for e in loop}
    if st.get('keep'):
        # 길이를 줄인 상태: 나란한 루프도 씨앗(맞은편 엣지)을 가운데로 같은 개수만 남긴다.
        # (미러 반대편은 자른 구간과 대응시키기 어려워 쓰지 않는다)
        window, _n, _d = trimmed_loop(new, st['cos_limit'], st['dih'], st['keep'], st.get('ref_dir'))
        idxs = {e.index for e in window}
    else:
        idxs = set(full)
        if st['axes']:
            idxs.update(e.index for e in find_mirror_edges(
                bm, loop, st['axes'], st['threshold'], st['cos_limit'], st['dih'], st.get('cache')))
    if 'full' in st:
        st['full'][k] = full
    st['loops'][k] = idxs
    return True


def ring_edges(seed):
    """
    seed 의 링(ring): 사각형 면을 가로질러 맞은편 엣지를 양쪽으로 계속 따라간 엣지 집합.
    루프가 '진행 방향'으로 이어지는 엣지라면, 링은 그와 직각으로 나란히 쌓인 엣지 한 줄이다.
    삼각형/N-gon, 경계, 숨긴 엣지를 만나면 그쪽에서 멈추고, 한 바퀴 돌아오면 거기서 끝난다.
    (폭 최대 + 길이 1 과 같은 결과를 한 줄씩 루프를 걷지 않고 얻는다. 제한이 없고 빠르다.)
    """
    seen = {seed}
    for start in [f for f in seed.link_faces if not f.hide][:2]:
        e, f = seed, start
        while f is not None:
            new = opposite_edge(f, e)
            if new is None or new.hide or new in seen:
                break
            seen.add(new)
            nxt = [g for g in new.link_faces if g is not f and not g.hide]
            e, f = new, (nxt[0] if nxt else None)
    return seen


def desired_for(st, lo, hi):
    desired = set()
    for k in range(lo, hi + 1):
        desired |= st['loops'][k]
    return desired


def state_valid(ob, bm, st):
    """메시/선택이 마지막으로 우리가 만든 상태 그대로인지 확인한다."""
    if st is None or ob.name != st['ob']:
        return False
    if mesh_counts(bm) != st['counts']:
        return False

    lo, hi = st['lo'], st['hi']
    desired = desired_for(st, lo, hi)
    for i in desired:
        if not bm.edges[i].select:
            return False
    for k, seed in st['seeds'].items():
        if (k < lo or k > hi) and seed not in desired and seed not in st['base_sel']:
            if bm.edges[seed].select:
                return False
    return True


def apply_range(bm, st, lo, hi):
    desired = desired_for(st, lo, hi)
    keep = desired | st['base_sel']
    for idxs in st['loops'].values():
        for i in idxs:
            if i not in keep:
                bm.edges[i].select_set(False)
    for i in desired:
        bm.edges[i].select_set(True)
    bm.select_flush_mode()


def step_one_side(bm, st, lo, hi, direction):
    """Ctrl+휠: 한 방향 확장/축소. (새 lo, 새 hi) 또는 불가능하면 None."""
    if direction > 0:                      # 휠 업: 아래쪽이 늘어나 있으면 줄이고, 아니면 위로 확장
        if lo < 0:
            return lo + 1, hi
        return (lo, hi + 1) if build_offset(bm, st, hi + 1) else None
    if hi > 0:                             # 휠 다운: 위쪽이 늘어나 있으면 줄이고, 아니면 아래로 확장
        return lo, hi - 1
    return (lo - 1, hi) if build_offset(bm, st, lo - 1) else None


def step_both_sides(bm, st, lo, hi, direction):
    """Alt+휠: 위·아래 동시 확장/축소. (새 lo, 새 hi) 또는 불가능하면 None."""
    if direction > 0:
        # 한쪽이 막혀 있어도(경계, 삼각형 등) 되는 쪽은 계속 늘린다.
        new_hi = hi + 1 if build_offset(bm, st, hi + 1) else hi
        new_lo = lo - 1 if build_offset(bm, st, lo - 1) else lo
        if (new_lo, new_hi) == (lo, hi):
            return None
        return new_lo, new_hi
    new_lo = lo + 1 if lo < 0 else lo
    new_hi = hi - 1 if hi > 0 else hi
    if (new_lo, new_hi) == (lo, hi):
        return None
    return new_lo, new_hi


# ---------------------------------------------------------------------------
# 시작 루프 ~ 끝 루프 사이 탐색
#
# 시작 루프 A 에서 맞은편 엣지를 따라 양쪽 방향으로 한 칸씩 걸어가며 루프를 하나씩 만들고,
# 끝 루프 B 와 만나는 쪽(= 더 짧은 쪽)의 루프를 순서대로 돌려준다.
# 각 단계의 루프는 기존 루프 워커 + 미러 확장을 그대로 쓰므로 극점/미러에서도 같은 규칙이다.
# 이 루프들 사이의 '면'을 모으는 일은 face_core.strip_faces 가 한다.
# ---------------------------------------------------------------------------

def chain_state(bm, seed, axes, threshold, cos_limit, dih, cache):
    """seed 루프를 오프셋 0 으로 하는 build_offset 용 상태를 만든다."""
    loop = walk_loop(seed, cos_limit, dih)
    idxs = {e.index for e in loop}
    if axes:
        idxs.update(e.index for e in find_mirror_edges(
            bm, loop, axes, threshold, cos_limit, dih, cache))
    faces = [f for f in seed.link_faces if not f.hide][:2]
    back = faces[0].index if faces else None
    fwd = faces[1].index if len(faces) > 1 else None
    return {
        'axes': axes, 'threshold': threshold, 'cos_limit': cos_limit, 'dih': dih,
        'cache': cache,
        'seeds': {0: seed.index},
        'faces': {0: (back, fwd)},     # k>0 은 faces[1] 쪽, k<0 은 faces[0] 쪽으로 걸어간다
        'loops': {0: idxs},
    }


def find_between(bm, seed_a, target, axes, threshold, cos_limit, dih, max_steps):
    """
    seed_a 의 루프에서 target(엣지 인덱스 집합)과 만날 때까지 양쪽으로 한 칸씩 걸어간다.
    반환: (A 에서 바깥으로 순서대로 늘어놓은 루프 엣지 인덱스 집합 리스트, 걸어간 칸 수)
          또는 못 찾으면 (None, 0).
    """
    cache = {}
    base = chain_state(bm, seed_a, axes, threshold, cos_limit, dih, cache)
    if base['loops'][0] & target:
        return [set(base['loops'][0])], 0

    chains = {
        1: base,
        -1: {**base, 'seeds': dict(base['seeds']), 'faces': dict(base['faces']),
             'loops': dict(base['loops'])},
    }
    pos = {1: 0, -1: 0}
    alive = {1: True, -1: True}

    for _ in range(max_steps):
        moved = False
        for sign in (1, -1):           # 두 방향을 번갈아 한 칸씩 -> 먼저 만나는(짧은) 쪽이 이긴다
            if not alive[sign]:
                continue
            st = chains[sign]
            k = pos[sign] + sign
            if not build_offset(bm, st, k):
                alive[sign] = False
                continue
            pos[sign] = k
            moved = True
            if st['loops'][k] & target:
                return [st['loops'][j] for j in range(0, k + sign, sign)], abs(k)
        if not moved:
            break
    return None, 0
