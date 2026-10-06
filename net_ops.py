"""
net_ops.py — [메시] 버텍스에서 그물망처럼 퍼뜨려 선택 (Seam / Sharp 마크까지만).

  Alt+우클릭 : 클릭한 버텍스(엣지·면 모드에서는 가장 가까운 꼭짓점)에서 퍼뜨린다.   -> MESH_OT_mirror_net_spread
  N 패널 '퍼뜨리기' 섹션의 버튼: 지금 선택한 버텍스에서 퍼뜨린다.

퍼지는 규칙은 net_spread.py 참고. 옵션(Seam/Sharp 에서 멈춤, 기존 선택에 추가)은 N 패널에 있고, 바꾸면 같은 시작점으로 바로 다시 퍼뜨린다.
"""

import bpy
import bmesh
from bpy.props import BoolProperty
from mathutils import Vector

from . import state
from .common import (ensure_tables, mesh_counts, pick_vertex, redraw_3d, restore_selection, snapshot_selection,
                     spread_valid)
from .net_spread import spread
from .settings import extension_enabled, spread_options


class MESH_OT_mirror_net_spread(bpy.types.Operator):
    """버텍스에서 그물망처럼 퍼뜨려 선택한다. Seam / Sharp 로 마크한 엣지까지만 선택하고 그 너머로는 퍼지지 않는다"""
    bl_idname = "mesh.mirror_net_spread"
    bl_label = "Spread Select"
    bl_options = {'UNDO'}      # 옵션은 사이드바(N) 'Mirror Loop' 탭의 '퍼뜨리기' 섹션에 있다.

    pick: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)       # 마우스로 클릭한 버텍스에서 시작
    refresh: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)    # 옵션이 바뀌어 같은 시작점으로 다시 퍼뜨리기

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and extension_enabled(context)

    def invoke(self, context, event):
        if not self.pick:
            return self.execute(context)

        # 클릭한 곳의 버텍스는 Blender 기본 선택(가려진 쪽 제외, 선택 모드에 맞는 판정)에게 맡기고 결과만 읽는다.
        objs = list(context.objects_in_mode_unique_data)
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        snap = snapshot_selection(objs)
        result = bpy.ops.view3d.select('INVOKE_DEFAULT', extend=False, deselect=False, toggle=False)
        hit = None
        if 'FINISHED' in result:
            for ob in objs:
                bm = bmesh.from_edit_mesh(ob.data)
                ensure_tables(bm)
                picked = [v for v in bm.verts if v.select]
                if picked:
                    hit = (ob, pick_vertex(context, ob, picked, mouse).index)
                    break
        restore_selection(objs, snap)
        if hit is None:
            return {'PASS_THROUGH'}          # 아무것도 못 집었으면 Blender 기본 동작에 넘긴다
        ob, vi = hit
        state.set_spread({'seeds': {ob.name: [vi]}, 'snap': snap, 'found': {}, 'counts': {},
                          'faces': 0, 'edges': 0, 'verts': 0, 'blocked': 0, 'pending': True})
        return self.execute(context)

    def execute(self, context):
        opt = spread_options(context)
        objs = list(context.objects_in_mode_unique_data)
        prev = state.spread

        if self.refresh:
            if prev is None or prev.get('pending') or not spread_valid(context):
                state.reset_spread()
                return {'CANCELLED'}
            restore_selection(objs, prev['snap'])
            snap, seeds = prev['snap'], prev['seeds']
        elif prev is not None and prev.get('pending'):
            snap, seeds = prev['snap'], prev['seeds']            # invoke 가 방금 정한 시작 버텍스
        else:
            snap = snapshot_selection(objs)
            seeds = {}
            for ob in objs:
                bm = bmesh.from_edit_mesh(ob.data)
                ensure_tables(bm)
                seeds[ob.name] = [v.index for v in bm.verts if v.select and not v.hide]
        if not any(seeds.values()):
            self.report({'WARNING'}, "시작할 버텍스를 선택하거나 Alt+우클릭으로 버텍스를 가리키세요")
            state.reset_spread()
            return {'CANCELLED'}

        if not opt.extend:
            bpy.ops.mesh.select_all(action='DESELECT')
        found, counts = {}, {}
        n_faces = n_edges = n_verts = n_blocked = 0
        for ob in objs:
            bm = bmesh.from_edit_mesh(ob.data)
            ensure_tables(bm)
            seed_verts = [bm.verts[i] for i in seeds.get(ob.name, ()) if i < len(bm.verts)]
            if not seed_verts:
                counts[ob.name] = mesh_counts(bm)
                found[ob.name] = set()
                continue
            faces, edges, verts, blocked = spread(bm, seed_verts, opt.stop_seam, opt.stop_sharp)
            for f in faces:
                f.select_set(True)
            for e in edges:
                e.select_set(True)
            for v in verts:
                v.select_set(True)
            bm.select_flush_mode()
            bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
            found[ob.name] = {v.index for v in verts}
            counts[ob.name] = mesh_counts(bm)
            n_faces += len(faces)
            n_edges += len(edges)
            n_verts += len(verts)
            n_blocked += len(blocked)

        state.set_spread({'seeds': seeds, 'snap': snap, 'found': found, 'counts': counts,
                          'faces': n_faces, 'edges': n_edges, 'verts': n_verts, 'blocked': n_blocked})
        self.report({'INFO'}, "퍼뜨리기: 버텍스 %d, 엣지 %d, 면 %d 선택 (마크에서 멈춘 엣지 %d)" % (n_verts, n_edges, n_faces, n_blocked))
        redraw_3d(context)
        return {'FINISHED'}


# (idname, 키, 값, 수식키, 오퍼레이터 속성, 환경설정에 보일 제목, 한 줄 설명)
KEYMAPS = (
    (MESH_OT_mirror_net_spread.bl_idname, 'RIGHTMOUSE', 'PRESS', {'alt': True}, {'pick': True},
     "버텍스에서 퍼뜨리기",
     "클릭한 버텍스에서 그물망처럼 퍼뜨려 선택, Seam/Sharp 마크 엣지까지만"),
)

classes = (MESH_OT_mirror_net_spread,)
