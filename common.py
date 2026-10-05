"""
common.py — 엣지/면 양쪽이 함께 쓰는 공통 유틸.

엣지나 면 어느 한쪽 개념에 묶이지 않는 것만 둔다.
(엣지 전용은 edge_core.py, 면 전용은 face_core.py)
"""

import bmesh
from bpy_extras import view3d_utils


def get_mirror_axes(obj):
    """편집 모드 Symmetry(use_mirror_x/y/z)와 Mirror 모디파이어의 축을 모두 모은다."""
    me = obj.data
    axes = {i for i, on in enumerate((me.use_mirror_x, me.use_mirror_y, me.use_mirror_z)) if on}
    for mod in obj.modifiers:
        if mod.type == 'MIRROR':
            axes.update(i for i in range(3) if mod.use_axis[i])
    return sorted(axes)


def mesh_counts(bm):
    return (len(bm.verts), len(bm.edges), len(bm.faces))


def ensure_tables(bm):
    """버텍스/엣지/면의 인덱스 조회 테이블과 index 값을 모두 최신으로 만든다."""
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.edges.index_update()
    bm.faces.index_update()


def point_segment_dist(p, a, b):
    """2D 점 p와 선분 ab 사이의 거리."""
    ab = b - a
    denom = ab.length_squared
    t = 0.0 if denom == 0.0 else max(0.0, min(1.0, (p - a).dot(ab) / denom))
    return (p - (a + ab * t)).length


def pick_seed(context, ob, bm, indices, mouse):
    """
    기본 루프 선택이 바꾼 엣지들 중, 마우스에 가장 가까운 엣지를 씨앗으로 쓴다.
    (set 의 임의 원소를 쓰면 클릭하지 않은 엣지에서 출발할 수 있다.)
    """
    region, rv3d = context.region, context.region_data
    mw = ob.matrix_world
    best, best_d = None, None
    for i in indices:
        e = bm.edges[i]
        pts = []
        for v in e.verts:
            p = view3d_utils.location_3d_to_region_2d(region, rv3d, mw @ v.co)
            if p is None:
                break
            pts.append(p)
        else:
            d = point_segment_dist(mouse, pts[0], pts[1])
            if best_d is None or d < best_d:
                best, best_d = e, d
    return best if best is not None else bm.edges[next(iter(indices))]


def make_projector(context, ob):
    """오브젝트 로컬 좌표 -> 리전(화면) 2D 좌표 함수. 3D 뷰가 아니거나 화면 밖이면 None 을 돌려준다."""
    region, rv3d = context.region, context.region_data
    mw = ob.matrix_world

    def project(co):
        if region is None or rv3d is None:
            return None
        return view3d_utils.location_3d_to_region_2d(region, rv3d, mw @ co)
    return project


def screen_mid(context, ob, e):
    """엣지 중점의 화면(리전) 좌표. 3D 뷰가 아니면 None."""
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None:
        return None
    mid = (e.verts[0].co + e.verts[1].co) * 0.5
    return view3d_utils.location_3d_to_region_2d(region, rv3d, ob.matrix_world @ mid)


def screen_score(delta):
    """화면에서 '위쪽'일수록 큰 값. 거의 수평으로 늘어선 경우엔 오른쪽을 위로 본다."""
    return delta.y if abs(delta.y) >= abs(delta.x) else delta.x


def snapshot_selection(objs):
    snap = {}
    for ob in objs:
        bm = bmesh.from_edit_mesh(ob.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        snap[ob] = (
            [i for i, v in enumerate(bm.verts) if v.select],
            [i for i, e in enumerate(bm.edges) if e.select],
            [i for i, f in enumerate(bm.faces) if f.select],
        )
    return snap


def restore_selection(objs, snap):
    for ob in objs:
        bm = bmesh.from_edit_mesh(ob.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        for f in bm.faces:
            f.select_set(False)
        for e in bm.edges:
            e.select_set(False)
        for v in bm.verts:
            v.select_set(False)
        vs, es, fs = snap[ob]
        for i in vs:
            bm.verts[i].select_set(True)
        for i in es:
            bm.edges[i].select_set(True)
        for i in fs:
            bm.faces[i].select_set(True)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
