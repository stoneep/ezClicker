"""
face_core.py — [면] 면 위상 헬퍼와 '루프 사이의 면' 모으기.

bmesh 만 다루고 오퍼레이터/UI 는 모른다. 엣지 알고리즘(edge_core)에는 의존하지 않는다.
(edge_range 가 이쪽의 opposite_edge 를 가져다 쓴다. 의존 방향: edge -> face -> common)

  - opposite_edge : 사각형 면에서 맞은편 엣지 (루프에서 옆 루프로 넘어갈 때 쓴다)
  - init_faces    : 씨앗 엣지의 양쪽 면 중 어느 쪽이 화면 '위'인지 정한다
  - strip_faces   : 순서대로 놓인 루프들 사이의 면을 모은다
"""

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
