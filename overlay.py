"""
overlay.py — 면 모드 '사이 선택'의 시작 루프(대기 중) 임시 표시.

면 모드에서 첫 클릭은 아무것도 선택하지 않고 시작 루프만 지정한다. 지정했다는 걸 알 수 없으면 쓰기 어려워서,
그 루프의 엣지를 색 선으로 3D 뷰에 겹쳐 그린다. (선택이 아니라 화면 표시일 뿐이라 선택 상태에는 영향이 없다.)

색과 두께는 환경설정(또는 팝업의 옵션)에서 바꾼다. 같은 방향의 다른 루프를 클릭하면 그 사이 면이 선택되고 표시는 사라진다.
Esc 로 대기를 해제한다. (face_ops.MESH_OT_mirror_pending_cancel)

그리는 코드(_draw)는 GPU 가 필요해서 Blender 창 안에서만 돈다. 어떤 선분을 그릴지 정하는 pending_segments 는
GPU 없이도 돌아가므로 따로 테스트할 수 있다.
"""

import bpy
import bmesh

from . import prefs, state
from .common import face_only_mode, mesh_counts

_handle = None
_shader = None


def pending_anchor(context):
    """지금 표시해야 할 '대기 중 시작 루프'. 없으면 None."""
    a = state.anchor
    if a is None or a.get('selected', True) or not a.get('loop'):
        return None
    if context.mode != 'EDIT_MESH' or not face_only_mode(context):
        return None
    ob = context.edit_object
    if ob is None or ob.name != a['ob']:
        return None
    return a


def pending_segments(ob, anchor):
    """대기 중 시작 루프의 엣지 선분 좌표(월드 좌표) 리스트: [시작0, 끝0, 시작1, 끝1, ...]. 메시가 바뀌었으면 []."""
    bm = bmesh.from_edit_mesh(ob.data)
    if mesh_counts(bm) != anchor['counts']:
        return []
    bm.edges.ensure_lookup_table()
    mw, n, pts = ob.matrix_world, len(bm.edges), []
    for i in anchor['loop']:
        if i >= n:
            continue
        e = bm.edges[i]
        if e.hide:
            continue
        pts.append(mw @ e.verts[0].co)
        pts.append(mw @ e.verts[1].co)
    return pts


def _draw():
    import gpu
    from gpu_extras.batch import batch_for_shader

    context = bpy.context
    a = pending_anchor(context)
    if a is None:
        return
    pts = pending_segments(context.edit_object, a)
    if not pts:
        return
    p = prefs.get_prefs(context)
    color = tuple(p.anchor_color) if p else (1.0, 0.25, 0.1, 1.0)
    width = p.anchor_width if p else 4.0

    global _shader
    if _shader is None:
        _shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    region = context.region
    batch = batch_for_shader(_shader, 'LINES', {"pos": [tuple(v) for v in pts]})
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')       # 뒤쪽 면에 가려져도 보이게 한다
    _shader.bind()
    _shader.uniform_float("viewportSize", (region.width, region.height))
    _shader.uniform_float("lineWidth", width)
    _shader.uniform_float("color", color)
    batch.draw(_shader)
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('NONE')


def _safe_draw():
    try:
        _draw()
    except Exception:           # 그리기 오류가 매 프레임 콘솔을 도배하지 않게 한다
        pass


def register():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_safe_draw, (), 'WINDOW', 'POST_VIEW')


def unregister():
    global _handle, _shader
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    _shader = None
