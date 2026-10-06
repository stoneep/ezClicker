"""
net_spread.py — [메시] 버텍스에서 그물망처럼 퍼뜨려 선택하되, 마크한 엣지(Seam / Sharp)에서 멈추기.

bmesh 만 다루고 오퍼레이터/UI 는 모른다.

Blender 의 '연결된 것 선택'(L, Delimit)과 같은 규칙으로, 시작 버텍스에 닿은 면에서 출발해 '막지 않는 엣지'를 건너 이웃 면으로
퍼진다. 막는 엣지(Seam, Sharp 로 마크한 엣지)는 건너지 않지만 그 엣지 자체와 양끝 버텍스는 선택된다. (마크까지만 선택, 그 너머는 안 함)
버텍스 하나씩 그래프를 따라가지 않고 면을 따라가는 이유: 마크 선 위의 버텍스는 양쪽 영역에 모두 속해서, 버텍스 기준으로 퍼지면
마크 선을 타고 반대편 영역으로 새기 때문이다. 면 기준이면 새지 않는다.

면이 없는 엣지(와이어, 느슨한 엣지)는 같은 규칙으로 버텍스를 따라 퍼진다.
"""


def is_blocking(e, stop_seam, stop_sharp):
    """이 엣지를 건너지 않아야 하는지. (Seam 으로 마크했거나, Sharp 로 마크한 엣지)"""
    return (stop_seam and e.seam) or (stop_sharp and not e.smooth)


def spread(bm, seed_verts, stop_seam=True, stop_sharp=True):
    """
    seed_verts 에서 퍼진 (면 집합, 엣지 집합, 버텍스 집합, 막힌 엣지 집합).
    막힌 엣지: 퍼지는 도중 만났지만 건너지 않은 마크 엣지. (선택에는 포함된다)
    """
    seed_verts = [v for v in seed_verts if not v.hide]
    faces, stack = set(), []
    for v in seed_verts:
        for f in v.link_faces:
            if not f.hide and f not in faces:
                faces.add(f)
                stack.append(f)
    blocked = set()
    while stack:
        f = stack.pop()
        for e in f.edges:
            if e.hide:
                continue
            if is_blocking(e, stop_seam, stop_sharp):
                blocked.add(e)
                continue
            for g in e.link_faces:
                if not g.hide and g not in faces:
                    faces.add(g)
                    stack.append(g)

    edges = {e for f in faces for e in f.edges if not e.hide}
    verts = {v for f in faces for v in f.verts if not v.hide}
    verts.update(seed_verts)

    # 면이 없는 엣지(와이어): 시작 버텍스와 퍼진 영역의 버텍스에서 같은 규칙으로 따라간다.
    vstack = list(verts)
    seen_v = set(verts)
    while vstack:
        v = vstack.pop()
        for e in v.link_edges:
            if e.hide or e.link_faces:
                continue
            if is_blocking(e, stop_seam, stop_sharp):
                blocked.add(e)
                edges.add(e)
                verts.add(e.other_vert(v))      # 마크 엣지는 선택하되 그 너머로는 가지 않는다
                continue
            edges.add(e)
            w = e.other_vert(v)
            verts.add(w)
            if w not in seen_v:
                seen_v.add(w)
                vstack.append(w)
    return faces, edges, verts, blocked
