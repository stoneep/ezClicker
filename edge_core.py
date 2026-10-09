"""
edge_core.py — [엣지] 루프 선택의 핵심 알고리즘.

bmesh 만 다루고 오퍼레이터/UI 는 모른다.

  1) 다이헤드럴(면 사이 각도) 보조 점수
  2) 루프 워커: 극점(3/5극), 삼각형, N-gon 에서도 멈추지 않고 이어서 진행
     - 크리스 인식: 불리언 유니온 등으로 생긴 교차선에서 엣지가 다른 표면으로 새지 않게 보정
  3) 미러 확장: 미러 축에서 끊긴 반대편 루프 찾기
"""

import math

from mathutils.kdtree import KDTree


def open_ends(edges):
    """선택된 엣지 묶음에서 끝점(엣지가 1개만 붙은 버텍스)을 반환한다."""
    count = {}
    for e in edges:
        for v in e.verts:
            count[v] = count.get(v, 0) + 1
    return [v for v, c in count.items() if c == 1]


# ---------------------------------------------------------------------------
# 다이헤드럴(면 사이 각도) 기준
#
# 엣지마다 '양쪽 면의 법선 이등분 방향'과 '두 면 사이의 꺾임 각도'를 서명으로 만든다.
# 능선(ridge)이나 로우폴리처럼 진행 방향 각도만으로는 애매한 곳에서,
# 같은 능선/같은 꺾임이 이어지는 엣지를 더 높게 평가하는 보조 점수로 쓴다.
# 평평한 면에서는 모든 후보의 서명이 같아 순위에 영향을 주지 않는다.
# ---------------------------------------------------------------------------

DIH_WEIGHT = 0.6                              # 보조 점수 가중치 (방향 코사인 대비)
DIH_CREASE_RANGE = math.radians(60.0)         # 꺾임 각도 차이가 이만큼이면 유사도 0
RELAX_COS = math.cos(math.radians(75.0))      # 다이헤드럴이 잘 맞을 때 허용하는 최대 꺾임
RELAX_MATCH = 0.9                             # '잘 맞는다'고 보는 유사도 기준


def edge_signature(e):
    """(법선 이등분 단위벡터, 두 면 사이 각도) 또는 면이 없으면 None."""
    faces = [f for f in e.link_faces if not f.hide][:2]
    if not faces:
        return None
    n0 = faces[0].normal
    if len(faces) == 1:
        return (n0.copy(), 0.0)
    n1 = faces[1].normal
    angle = n0.angle(n1, 0.0)
    bis = n0 + n1
    if bis.length_squared < 1e-12:      # 완전히 접힌 엣지
        bis = n0.copy()
    else:
        bis.normalize()
    return (bis, angle)


def dihedral_match(a, b):
    """두 엣지 서명의 유사도 0~1. 정보가 없으면 중립값 0.5."""
    if a is None or b is None:
        return 0.5
    bis = max(0.0, a[0].dot(b[0]))
    crease = 1.0 - min(1.0, abs(a[1] - b[1]) / DIH_CREASE_RANGE)
    return 0.5 * bis + 0.5 * crease


# ---------------------------------------------------------------------------
# 루프 워커: 극점(3/5극), 삼각형, N-gon에서도 멈추지 않고 이어서 진행
# ---------------------------------------------------------------------------

STRAIGHT_COS = math.cos(math.radians(25.0))  # 이 안쪽이면 '일직선'으로 본다

# 크리스(꺾임) 인식: 불리언 유니온처럼 서로 다른 표면이 만나는 교차선 처리용.
#
# 교차선(seam)은 양쪽 면이 크게 꺾인 '크리스 라인'이고, 교차선에 부딪혀 끝나는 엣지(관통당한 쪽의
# 링/세로 엣지)는 훨씬 부드럽다. 위상만 보면 이런 정점도 일반 3극/4극과 구별되지 않아서,
# 부드러운 엣지가 교차선으로 꺾여 들어가거나 반대편 표면의 엣지로 건너가는 일이 생겼다.
# 기본 규칙(_next_edge_base)의 결과가 명백히 틀린 경우에만 보정한다.
CREASE_MIN = math.radians(30.0)               # 이 이상 꺾여야 '크리스'로 본다
CREASE_GAP = math.radians(25.0)               # 크리스는 부드러운 엣지보다 이만큼 더 날카로워야 한다
CREASE_SIMILAR = math.radians(35.0)           # 같은 크리스로 이어지는 엣지끼리 허용하는 꺾임 각도 차이
CREASE_LINE_COS = math.cos(math.radians(120.0))   # 두 크리스 엣지 사이 각이 이 이상이면 '한 라인'
CREASE_MATCH = 0.7                            # 크리스 이어가기에 필요한 다이헤드럴 유사도
CREASE_STRAIGHT_SLACK = 0.25                  # 크리스 쪽이 기본 선택보다 이만큼(코사인)까지 덜 직진이어도 허용


def crease_angle(e):
    """엣지 양쪽 면 사이의 꺾임 각도. 면이 없거나 한 장이면 0."""
    sig = edge_signature(e)
    return sig[1] if sig else 0.0


def has_ngon(v):
    """v 에 5각형 이상의 면이 붙어 있으면 True. (불리언 등으로 잘린 면의 잔해)

    이런 면 주변에서는 '맞은편 엣지' 규칙이 믿을 수 없다. 반대로 트라이팬 캡 같은 삼각형·사각형만 있는
    곳은 Blender 기본 규칙이 맞으므로 건드리지 않는다.
    """
    return any(len(f.verts) >= 5 for f in v.link_faces if not f.hide)


def crease_adjust(v, e_in, base, cands):
    """
    기본 규칙이 고른 base(없으면 None)를 크리스 정보로 보정해 최종 엣지를 반환한다.

    A) 크리스 장벽: 부드러운 엣지가 '한 줄의 날카로운 크리스' 위로 꺾여 들어가려 하면 거기서 끝난다.
       교차선 위의 T접합(3극)이 대표적이다. 크리스를 건너 반대편으로 이어가거나 스치듯 지나가는 링은
       정상이므로 막지 않는다. (n-gon 이 붙은 4극 이상에서도 같다. 사각형만 있는 4극은 기본 규칙을 믿는다.)
    B) 크리스 따라가기: 날카로운 엣지(교차선)를 걷는 중인데 기본 규칙이 멈췄거나 훨씬 부드러운
       엣지로 새려 하면, 같은 성격의 크리스 엣지가 정확히 하나일 때 그쪽으로 이어간다.
    """
    ang_in = crease_angle(e_in)
    d_in = (v.co - e_in.other_vert(v).co).normalized()
    dirs = {e: (e.other_vert(v).co - v.co).normalized() for e in cands}

    sharp = [(e, crease_angle(e)) for e in cands]
    sharp = [(e, a) for e, a in sharp if a >= CREASE_MIN and a >= ang_in + CREASE_GAP]
    if len(v.link_edges) == 3 or has_ngon(v):
        for i in range(len(sharp)):
            for j in range(i + 1, len(sharp)):
                (e1, a1), (e2, a2) = sharp[i], sharp[j]
                if abs(a1 - a2) > CREASE_SIMILAR or dirs[e1].dot(dirs[e2]) > CREASE_LINE_COS:
                    continue
                # 크리스 라인이 있고, 기본 규칙이 그 라인 위로 꺾어 들어가려 하면(또는 이미 멈췄으면) 끝낸다.
                # 라인을 건너 반대편으로 이어가는 경우는 막지 않는다: 같은 물체의 하드 엣지(육각·팔각 기둥,
                # 박스 모서리)를 도는 정상적인 링과 교차선 건너기를 정점 하나만 보고는 구분할 수 없고,
                # 막으면 링이 모서리에서 끊겨 일부만 선택된다. 스치기만 하는 링도 마찬가지로 막지 않는다.
                if base is None or base in (e1, e2):
                    return None
    if ang_in >= CREASE_MIN and (base is None or crease_angle(base) <= ang_in - CREASE_GAP):
        sig_in = edge_signature(e_in)
        like = [e for e in cands
                if crease_angle(e) >= CREASE_MIN
                and abs(crease_angle(e) - ang_in) <= CREASE_SIMILAR
                and dihedral_match(sig_in, edge_signature(e)) >= CREASE_MATCH]
        # 비슷한 크리스가 둘 이상이면(큐브 모서리 등) 모호하므로 건드리지 않는다.
        # 기본 규칙이 고른 엣지보다 크게 덜 직진인 쪽으로는 바꾸지 않는다.
        if len(like) == 1 and like[0] is not base:
            c = like[0]
            dot_c = dirs[c].dot(d_in)
            if dot_c > RELAX_COS and (base is None or dot_c >= dirs[base].dot(d_in) - CREASE_STRAIGHT_SLACK):
                return c
    return base


def next_edge(v, e_in, cos_limit, dih=True):
    """v에 e_in으로 들어왔을 때 이어갈 엣지를 고른다. 없으면 None."""
    cands = [e for e in v.link_edges if e is not e_in and not e.hide]
    if not cands:
        return None
    base = _next_edge_base(v, e_in, cands, cos_limit, dih)
    # 순수 사각형 4극 정점은 Blender 기본 규칙이 정답이다. 다이헤드럴을 안 쓰면 보정도 하지 않는다.
    if not dih or (len(v.link_edges) == 4 and not has_ngon(v)):
        return base
    return crease_adjust(v, e_in, base, cands)


def _next_edge_base(v, e_in, cands, cos_limit, dih):
    """크리스 보정 전의 기본 규칙. (cands: v 에 붙은 숨기지 않은 엣지 중 e_in 제외)"""
    # e_in 과 면을 하나도 공유하지 않는 엣지 = 면 기준으로 '맞은편' 엣지
    faces_in = set(e_in.link_faces)
    opposite = [e for e in cands if not (faces_in & set(e.link_faces))]

    # 1) 맞은편 엣지가 딱 하나면 그게 루프의 연속이다.
    #    - 일반 4극 버텍스
    #    - 안쪽이 뚫린(구멍) 링의 3극 버텍스  <- 예전에는 여기서 바깥 스포크로 새던 부분
    if len(opposite) == 1:
        return opposite[0]

    # 2) 캡(cap) 면 경계: e_in 과 후보 엣지가 같은 면을 공유하고, 그 면의 모든 버텍스가
    #    3극이면(안쪽이 채워진 사각형, 큐브 면 등) 그 면의 테두리를 따라 한 바퀴 돈다.
    #    이런 모서리는 직진 후보(방사형 대각선)와 테두리 후보가 모두 그럴듯해 보여서
    #    방향 휴리스틱만 쓰면 바깥 스포크로 새기 때문이다.
    #    - 후보가 정확히 하나일 때만 적용한다. (큐브처럼 양쪽이 다 캡이면 모호하므로 건너뜀)
    #    - 거의 일직선(약 25° 이내)인 후보가 있으면 그쪽이 우선이므로 적용하지 않는다.
    d_in = (v.co - e_in.other_vert(v).co).normalized()
    dots = {e: d_in.dot((e.other_vert(v).co - v.co).normalized()) for e in cands}

    if max(dots.values()) < STRAIGHT_COS:
        caps = []
        for e in cands:
            for f in faces_in & set(e.link_faces):
                if all(len(fv.link_edges) == 3 for fv in f.verts):
                    caps.append(e)
                    break
        if len(caps) == 1:
            return caps[0]

    # 3) 극점(5극 이상), 삼각형, 경계 등: 진행 방향과 가까운 엣지를 선택.
    #    단, 방향이 비슷하더라도 e_in 과 면을 공유하는(= 옆으로 꺾이는) 엣지보다
    #    면을 공유하지 않는 엣지를 우선한다.
    #
    #    dih=True 이면 방향 코사인에 다이헤드럴 유사도를 더해 순위를 매긴다.
    #    - 능선처럼 꺾임이 이어지는 엣지가 앞서고, 동점이던 경우도 갈린다.
    #    - 맞은편 엣지이면서 다이헤드럴이 거의 같으면(로우폴리의 큰 꺾임) 각도 제한을
    #      RELAX_COS 까지 풀어서 루프가 중간에 끊기지 않게 한다.
    sig_in = edge_signature(e_in) if dih else None
    scored = []
    for e in cands:
        dot = dots[e]
        match = dihedral_match(sig_in, edge_signature(e)) if dih else 0.0
        if dot <= cos_limit:
            if not (dih and e in opposite and dot > RELAX_COS and match >= RELAX_MATCH):
                continue
        scored.append((e in opposite, dot + DIH_WEIGHT * match, e))
    if not scored:
        return None
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)

    # 1·2등이 사실상 같은 점수면 어느 쪽이 맞는지 알 수 없으므로 멈춘다.
    # (예: 대각선이 3극 모서리로 들어올 때 좌우 테두리가 똑같이 45°)
    if len(scored) > 1 and scored[0][0] == scored[1][0] and abs(scored[0][1] - scored[1][1]) < 1e-3:
        return None
    return scored[0][2]


def walk_loop(seed, cos_limit, dih=True):
    """seed 엣지에서 양쪽 방향으로 끝까지(또는 한 바퀴 돌 때까지) 걸어 엣지 집합을 반환한다."""
    loop = {seed}
    for start_v in seed.verts:
        e, v = seed, start_v
        while True:
            ne = next_edge(v, e, cos_limit, dih)
            if ne is None or ne in loop:
                break
            loop.add(ne)
            v = ne.other_vert(v)
            e = ne
    return loop


def walk_loop_ordered(seed, cos_limit, dih=True, ref_dir=None):
    """
    walk_loop 와 같은 루프를 '순서대로' 걷는다: (엣지 리스트, 씨앗의 위치, 닫힌 고리 여부, 씨앗의 진행 방향).
    ref_dir 을 주면 씨앗을 그 방향과 같은 쪽으로 읽는다. (나란한 루프끼리 읽는 방향을 맞출 때 쓴다)
    """
    a, b = seed.verts
    if ref_dir is not None and (b.co - a.co).dot(ref_dir) < 0.0:
        a, b = b, a
    seen = {seed}

    def run(v, e):
        out, closed = [], False
        while True:
            ne = next_edge(v, e, cos_limit, dih)
            if ne is None:
                break
            if ne in seen:
                closed = ne is seed          # 한 바퀴 돌아 씨앗으로 돌아옴
                break
            seen.add(ne)
            out.append(ne)
            v = ne.other_vert(v)
            e = ne
        return out, closed

    fwd, closed = run(b, seed)
    bwd, _ = run(a, seed)
    return bwd[::-1] + [seed] + fwd, len(bwd), closed, (b.co - a.co)


def trim_window(chain, seed_pos, closed, keep):
    """chain 에서 씨앗(seed_pos)을 가운데로 keep 개를 고른다. 닫힌 고리는 돌아서 잇고, 열린 루프는 끝에서 멈춘다."""
    n = len(chain)
    keep = max(1, min(keep, n))
    if keep >= n:
        return list(chain)
    before = (keep - 1) // 2
    if closed:
        s = seed_pos - before
        return [chain[(s + i) % n] for i in range(keep)]
    s = max(0, min(seed_pos - before, n - keep))
    return chain[s:s + keep]


def trimmed_loop(seed, cos_limit, dih, keep, ref_dir=None):
    """씨앗을 가운데로 루프를 keep 개 엣지로 줄인다: (엣지 리스트, 전체 엣지 수, 씨앗의 진행 방향)."""
    chain, pos, closed, d = walk_loop_ordered(seed, cos_limit, dih, ref_dir)
    return trim_window(chain, pos, closed, keep), len(chain), d


# ---------------------------------------------------------------------------
# 미러 확장
# ---------------------------------------------------------------------------

DEFAULT_COS = math.cos(math.radians(60.0))

NEAR_VERT_RATIO = 0.5                         # 정확한 짝이 없을 때 반사 위치 주변 탐색 반경 (로컬 엣지 길이 비율)
MIRROR_EDGE_TOL = 0.35                        # 반사된 중점이 이 거리(자기 길이 비율) 안에 있으면 짝 후보
MIRROR_DIR_COS = math.cos(math.radians(40.0))  # 반사된 방향과 후보 엣지 방향이 이 안쪽이어야 한다


def local_len(v):
    """v 에 붙은 (숨기지 않은) 엣지 중 가장 긴 길이. 로컬 스케일 기준으로 쓴다."""
    lens = [e.calc_length() for e in v.link_edges if not e.hide]
    return max(lens) if lens else 0.0


def find_mirror_edges(bm, edges, axes, threshold, cos_limit=DEFAULT_COS, dih=True, cache=None):
    """
    열린 호의 반대편(미러) 엣지 리스트를 반환한다. (선택은 호출하는 쪽에서 한다.)

    cache 에 dict 를 넘기면 버텍스/엣지 KD 트리를 호출 사이에 재사용한다.
    (메시 형태가 바뀌지 않는 한 번의 작업 안에서 여러 번 부를 때만 넘길 것)

    좌우 위상이 달라도(한쪽에만 베벨/엣지를 넣은 경우 등) 어느 쪽을 클릭하든 같은 결과가
    나오도록 아래 순서로 찾는다. 특정 축(Z)의 높이에는 의존하지 않는다.

      1) 정확한 미러 위치의 버텍스(이음매에 겹쳐 있는 쌍둥이 버텍스도 모두 후보)가
         엣지로 이어져 있으면 그 엣지를 씨앗으로
      2) 정확한 짝 엣지가 없으면 엣지의 중점과 방향을 미러 반사해서,
         그 근처(자기 길이 기준 허용치)에서 방향이 비슷한 엣지 중 가장 잘 맞는 하나를 씨앗으로
         (능선, 로우폴리, 뺨처럼 높이가 계속 변하거나 위상이 어긋난 곳)
      3) 이렇게 찾은 반대편 엣지들을 씨앗으로 반대편에서 루프를 직접 걸어 끊긴 구간을 메운다.
    """
    ends = open_ends(edges)
    if not ends:
        return []  # 이미 닫힌 루프

    if cache is None:
        cache = {}
    if 'vkd' not in cache:
        live_verts = [v for v in bm.verts if not v.hide]
        tree = None
        if live_verts:
            tree = KDTree(len(live_verts))
            for i, v in enumerate(live_verts):
                tree.insert(v.co, i)
            tree.balance()
        cache['vkd'] = (live_verts, tree)
    live, kd = cache['vkd']
    if not live:
        return []

    def partners(v, axis):
        """v 의 미러 짝 후보 리스트 (가까운 순). 없으면 빈 리스트."""
        co = v.co.copy()
        co[axis] = -co[axis]
        exact = sorted(kd.find_range(co, threshold), key=lambda r: r[2])
        if exact:
            return [live[i] for _, i, _ in exact]

        # 정확한 짝이 없으면: 반사 위치 근처(로컬 엣지 길이의 절반 이내)의 반대편 버텍스
        side = v.co[axis]
        if abs(side) <= threshold:
            return []          # 평면 위 버텍스는 자기 자신이 짝이므로 위에서 이미 잡힌다
        reach = local_len(v) * NEAR_VERT_RATIO
        if reach <= 0.0:
            return []
        found = []
        for _, i, d in kd.find_range(co, reach):
            c = live[i]
            if c is v or c.co[axis] * side >= 0.0:        # 반대편에 있어야 한다
                continue
            found.append((d, c))
        found.sort(key=lambda r: r[0])
        return [c for _, c in found]

    # 미러 축 결정: 끝점이 모두 평면 위에 있는 축을 우선,
    # 없으면 끝점마다 (평면 위에 있거나 반대편 짝이 있는) 축
    axis = next((a for a in axes if all(abs(v.co[a]) <= threshold for v in ends)), None)
    if axis is None:
        axis = next((a for a in axes
                     if all(abs(v.co[a]) <= threshold or any(p is not v for p in partners(v, a))
                            for v in ends)), None)
    if axis is None:
        return []

    # 엣지 중점 KD 트리는 정말 필요할 때만 만든다.
    def edge_kd():
        if 'ekd' not in cache:
            es = [e for e in bm.edges if not e.hide]
            tree = KDTree(len(es))
            for i, e in enumerate(es):
                tree.insert((e.verts[0].co + e.verts[1].co) * 0.5, i)
            tree.balance()
            cache['ekd'] = (tree, es)
        return cache['ekd']

    def geometric_partner(e, loop_set):
        """엣지 e 를 미러 반사한 위치/방향에 가장 가까운 반대편 엣지. 없으면 None."""
        v0, v1 = e.verts
        mid = (v0.co + v1.co) * 0.5
        if abs(mid[axis]) <= threshold:
            return None                      # 미러 평면 위 엣지는 자기 자신이 짝
        length = (v1.co - v0.co).length
        if length <= 0.0:
            return None

        rmid = mid.copy()
        rmid[axis] = -rmid[axis]
        rdir = v1.co - v0.co
        rdir[axis] = -rdir[axis]
        rdir.normalize()

        tol = max(threshold, length * MIRROR_EDGE_TOL)
        tree, es = edge_kd()
        best, best_score = None, None
        for _, i, dist in tree.find_range(rmid, tol):
            c = es[i]
            if c is e or c in loop_set:
                continue
            a, b = c.verts
            if ((a.co[axis] + b.co[axis]) * 0.5) * mid[axis] >= 0.0:    # 반대편이어야 한다
                continue
            cd = b.co - a.co
            cl = cd.length
            if cl <= 0.0:
                continue
            d = abs(rdir.dot(cd) / cl)
            if d < MIRROR_DIR_COS:
                continue
            score = dist / tol + (1.0 - d)
            if best_score is None or score < best_score:
                best, best_score = c, score
        return best

    loop_set = set(edges)
    seeds = []
    for e in edges:
        v0, v1 = e.verts
        p0, p1 = partners(v0, axis), partners(v1, axis)

        # 1) 짝 버텍스 사이에 실제 엣지가 있으면 그걸 씨앗으로
        hit = False
        for a in p0:
            for b in p1:
                if a is b:
                    continue
                e2 = bm.edges.get((a, b))
                if e2 is not None and not e2.hide and e2 not in loop_set:
                    seeds.append(e2)
                    hit = True
        if hit:
            continue

        # 2) 정확한 짝 엣지가 없으면 반사한 중점/방향으로 찾는다.
        g = geometric_partner(e, loop_set)
        if g is not None:
            seeds.append(g)

    # 3) 반대편에서 직접 걸어서 끊긴 구간까지 메운다.
    covered = set()
    for s in seeds:
        if s in covered:
            continue
        covered |= walk_loop(s, cos_limit, dih)

    return [e for e in covered if e not in loop_set]
