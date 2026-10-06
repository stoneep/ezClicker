"""
mark_ops.py — [엣지] Seam / Sharp 로 마크한 엣지만 골라 선택하기 (N 패널 토글 버튼).

A 로 전체 선택한 뒤 버튼을 누르면 선택이 '마크한 엣지만' 으로 줄어든다. 같은 버튼을 한 번 더 누르면 누르기 전 선택으로 돌아간다. (토글)
  - Seam 만 / Sharp 만 / 둘 다 (Seam 이거나 Sharp)
  - 지금 선택한 엣지 안에서 고른다. 선택한 엣지가 하나도 없으면 보이는 모든 엣지에서 고른다.
  - Seam 을 켠 채 Sharp 를 누르면 누르기 전 선택으로 되돌린 뒤 거기서 Sharp 만 고른다. (필터가 겹쳐서 줄어들지 않는다)
  - 엣지 모드에서 쓴다. 버텍스나 면 모드에서는 마크 엣지만 따로 선택해도 이웃 엣지가 따라 선택돼 의미가 없다.
"""

import bpy
import bmesh
from bpy.props import EnumProperty

from . import state
from .common import ensure_tables, mesh_counts, redraw_3d, restore_selection, snapshot_selection
from .settings import extension_enabled

KINDS = (
    ('SEAM', "Seam", "Seam(심)으로 마크한 엣지만 선택한다"),
    ('SHARP', "Sharp", "Sharp(샤프)로 마크한 엣지만 선택한다"),
    ('BOTH', "Seam + Sharp", "Seam 이거나 Sharp 로 마크한 엣지를 선택한다"),
)
KIND_TEXT = {'SEAM': "Seam", 'SHARP': "Sharp", 'BOTH': "Seam + Sharp"}


def is_marked(e, kind):
    if kind == 'SEAM':
        return e.seam
    if kind == 'SHARP':
        return not e.smooth
    return e.seam or not e.smooth


def edge_mode(context):
    """엣지 선택 모드가 켜져 있는지."""
    return bool(context.tool_settings.mesh_select_mode[1])


EXACT_LIMIT = 60000      # 패널을 그릴 때 선택이 '정확히' 같은지 확인하는 엣지 수 한계 (그보다 큰 메시는 가볍게만 확인)


def marked_valid(context, exact=True):
    """
    마지막 '마크 엣지만 선택' 결과가 그대로인지: 편집 중이고, 메시가 같고, 고른 엣지가 아직 선택돼 있다.
    exact 이면 고른 엣지 말고 다른 엣지가 더 선택돼 있지 않은지도 본다. (A 로 다시 전체 선택하면 켜진 상태가 아니다)
    """
    info = state.marked
    if info is None or context.mode != 'EDIT_MESH':
        return False
    for ob in context.objects_in_mode_unique_data:
        if ob.name not in info['counts']:
            return False
        bm = bmesh.from_edit_mesh(ob.data)
        if mesh_counts(bm) != info['counts'][ob.name]:
            return False
        bm.edges.ensure_lookup_table()
        edges = bm.edges
        found = info['found'].get(ob.name, ())
        if not all(edges[i].select for i in found):
            return False
        if exact and len(edges) <= EXACT_LIMIT and sum(1 for e in edges if e.select) != len(found):
            return False
    return True


class MESH_OT_mirror_select_marked(bpy.types.Operator):
    """선택한 엣지(없으면 전체) 중 Seam / Sharp 로 마크한 엣지만 남긴다. 같은 버튼을 한 번 더 누르면 원래 선택으로 돌아간다"""
    bl_idname = "mesh.mirror_select_marked"
    bl_label = "Select Marked Edges"
    bl_options = {'UNDO'}

    kind: EnumProperty(name="Kind", items=KINDS, default='SEAM')

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and extension_enabled(context) and edge_mode(context)

    def execute(self, context):
        objs = list(context.objects_in_mode_unique_data)
        info = state.marked
        active = info is not None and marked_valid(context, exact=True)

        if active and info['kind'] == self.kind:            # 켜져 있던 같은 버튼: 끄기 = 원래 선택으로
            restore_selection(objs, info['snap'])
            state.reset_marked()
            self.report({'INFO'}, "%s 만 선택을 껐습니다 (원래 선택으로)" % KIND_TEXT[self.kind])
            redraw_3d(context)
            return {'FINISHED'}

        if active:                                          # 다른 종류가 켜져 있으면 원래 선택에서 다시 시작
            restore_selection(objs, info['snap'])
            snap = info['snap']
        else:
            snap = snapshot_selection(objs)

        pools, total = {}, 0          # 오브젝트별 고른 엣지의 인덱스 (BMEdge 는 아래 select_all 뒤에 무효가 된다)
        any_selected = False
        for ob in objs:
            bm = bmesh.from_edit_mesh(ob.data)
            ensure_tables(bm)
            if any(e.select and not e.hide for e in bm.edges):
                any_selected = True
        found, counts = {}, {}
        for ob in objs:
            bm = bmesh.from_edit_mesh(ob.data)
            ensure_tables(bm)
            pool = [e for e in bm.edges if not e.hide and (e.select or not any_selected)]
            keep = [e.index for e in pool if is_marked(e, self.kind)]
            pools[ob] = keep
            total += len(keep)
            counts[ob.name] = mesh_counts(bm)
        if total == 0:
            if active:
                state.reset_marked()
            self.report({'WARNING'}, "%s 로 마크한 엣지가 %s 없습니다" % (KIND_TEXT[self.kind], "선택한 엣지 중에" if any_selected else ""))
            return {'CANCELLED'}

        bpy.ops.mesh.select_all(action='DESELECT')
        for ob in objs:
            bm = bmesh.from_edit_mesh(ob.data)
            ensure_tables(bm)
            idx = set()
            for i in pools[ob]:
                bm.edges[i].select_set(True)
                idx.add(i)
            bm.select_flush_mode()
            bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
            found[ob.name] = idx

        state.set_marked({'kind': self.kind, 'snap': snap, 'found': found, 'counts': counts, 'n': total,
                          'whole': not any_selected})
        self.report({'INFO'}, "%s 마크 엣지 %d개만 선택했습니다 (%s)" % (
            KIND_TEXT[self.kind], total, "선택한 엣지 중" if any_selected else "전체에서"))
        redraw_3d(context)
        return {'FINISHED'}


classes = (MESH_OT_mirror_select_marked,)
