"""
net_spread.py — [메시] 버텍스에서 한 칸씩 이어 붙여 선택하기 (Select More 와 같은 원리) + Seam / Sharp 마크에서 멈추기.

bmesh 만 다루고 오퍼레이터/UI 는 모른다.

Blender 의 Select More 처럼 시작 버텍스에서 한 단계마다 이웃 버텍스를 더한다.
  - 면 단위(face_step, 기본): 선택한 버텍스에 닿은 면의 모든 버텍스를 더한다. (대각선 포함, Blender 기본 Select More 와 같다)
  - 엣지 단위: 엣지로 바로 이어진 버텍스만 더한다.
Seam 이나 Sharp 로 마크한 엣지에 닿은 버텍스(마크 선 위의 버텍스)는 모두 '끝점'이다. 끝점까지는 선택하지만 거기서 다음 단계로
퍼지지 않으므로 마크 선을 타고 반대편 영역으로 새지 않는다. 영역을 둘러싼 마크 선의 엣지는 양 끝 버텍스가 모두 선택되므로 엣지째
선택된다. (시작 버텍스 자신은 마크 선 위에 있어도 퍼진다.)

steps = 0 이면 더 이상 늘지 않을 때까지(마크에 막히거나 메시 끝까지) 퍼진다. 면이 없는 엣지(와이어)도 같은 규칙이다.
"""


def is_blocking(e, stop_seam, stop_sharp):
    """이 엣지가 마크 엣지(퍼짐을 멈추게 하는 엣지)인지. Seam 으로 마크했거나 Sharp 로 마크한 엣지."""
    return (stop_seam and e.seam) or (stop_sharp and not e.smooth)


def grow(bm, seed_verts, steps=1, stop_seam=True, stop_sharp=True, face_step=True):
    """
    seed_verts 에서 steps 단계만큼 퍼진 결과: (버텍스 집합, 엣지 집합, 면 집합, 막힌 엣지 집합, 실제로 퍼진 단계 수)
    steps = 0 이면 끝까지. 막힌 엣지: 선택에 포함된 마크 엣지(퍼짐이 멈춘 경계).
    """
    seeds = [v for v in seed_verts if not v.hide]
    selected = set(seeds)
    frontier = set(seeds)
    blocked = set()
    end_cache = {}

    def is_end(v):
        """마크 엣지에 닿은 버텍스(퍼짐의 끝점)."""
        if v not in end_cache:
            end_cache[v] = any(is_blocking(e, stop_seam, stop_sharp) for e in v.link_edges if not e.hide)
        return end_cache[v]

    level = 0
    while frontier and (steps == 0 or level < steps):
        reached = set()
        for v in frontier:
            for e in v.link_edges:
                if e.hide:
                    continue
                if is_blocking(e, stop_seam, stop_sharp):
                    blocked.add(e)
                reached.add(e.other_vert(v))
            if face_step:
                for f in v.link_faces:
                    if not f.hide:
                        reached.update(f.verts)
        new = {u for u in reached if not u.hide} - selected
        if not new:
            break
        selected |= new
        frontier = {u for u in new if not is_end(u)}
        level += 1

    # 선택한 버텍스로 이루어진 엣지와 면. (Blender 가 버텍스 모드에서 선택을 이어 주는 것과 같다)
    edges, faces = set(), set()
    for v in selected:
        for e in v.link_edges:
            if not e.hide and e.other_vert(v) in selected:
                edges.add(e)
        for f in v.link_faces:
            if not f.hide and f not in faces and all(x in selected for x in f.verts):
                faces.add(f)
    # 마크 엣지 중 선택에 포함된 것(퍼짐이 멈춘 경계). 끝점은 퍼지지 않으므로 퍼지는 도중이 아니라 결과에서 센다.
    blocked |= {e for e in edges if is_blocking(e, stop_seam, stop_sharp)}
    return selected, edges, faces, blocked, level
