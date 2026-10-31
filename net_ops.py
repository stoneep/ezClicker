"""
net_ops.py — [메시] 버텍스에서 한 칸씩 이어 붙여 선택 (Select More 와 같은 원리, Seam / Sharp 마크에서 멈춤).

  Alt+우클릭 : 클릭한 버텍스(엣지·면 모드에서는 가장 가까운 꼭짓점)에서 퍼뜨린다.   -> MESH_OT_mirror_net_spread
  Alt+휠 업/다운 : 퍼진 선택을 한 칸 더 / 한 칸 덜 (N 패널의 '단계' 숫자와 같다)     -> MESH_OT_mirror_net_step
  N 패널 '퍼뜨리기' 섹션의 버튼: 지금 선택한 버텍스에서 퍼뜨린다.

퍼지는 규칙은 net_spread.py 참고. 옵션(단계, 끝까지, Seam/Sharp 에서 멈춤 등)은 N 패널에 있고, 바꾸면 같은 시작점으로 바로 다시 퍼뜨린다.
"""

import bpy
import bmesh
from bpy.props import BoolProperty, IntProperty
from mathutils import Vector

from . import state
from .common import (ensure_tables, mesh_counts, pick_vertex, redraw_3d, restore_selection, snapshot_selection,
                     spread_valid)
from .net_spread import grow
from .settings import extension_enabled, get_settings, spread_options


class MESH_OT_mirror_net_spread(bpy.types.Operator):
    """버텍스에서 한 칸씩 이어 붙여 선택한다(Select More 와 같은 원리). Seam / Sharp 로 마크한 엣지를 만나면 거기까지만 선택한다"""
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
        state.reset_wheel()          # 루프 휠(Alt+휠)이 아니라 퍼뜨리기 휠이 받도록
        state.reset_adjust()

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
        n_faces = n_edges = n_verts = n_blocked = levels = 0
        for ob in objs:
            bm = bmesh.from_edit_mesh(ob.data)
            ensure_tables(bm)
            seed_verts = [bm.verts[i] for i in seeds.get(ob.name, ()) if i < len(bm.verts)]
            if not seed_verts:
                counts[ob.name] = mesh_counts(bm)
                found[ob.name] = set()
                continue
            verts, edges, faces, blocked, lv = grow(bm, seed_verts, opt.steps, opt.stop_seam, opt.stop_sharp, opt.face_step)
            levels = max(levels, lv)
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
                          'faces': n_faces, 'edges': n_edges, 'verts': n_verts, 'blocked': n_blocked,
                          'levels': levels, 'steps': opt.steps})
        self.report({'INFO'}, "퍼뜨리기 %d단계: 버텍스 %d, 엣지 %d, 면 %d 선택 (마크에서 멈춘 엣지 %d)" % (levels, n_verts, n_edges, n_faces, n_blocked))
        redraw_3d(context)
        return {'FINISHED'}


class MESH_OT_mirror_net_step(bpy.types.Operator):
    """퍼뜨린 직후 휠: 퍼진 단계를 한 칸 늘리거나 줄인다 (N 패널의 '단계' 숫자와 같다)"""
    bl_idname = "mesh.mirror_net_step"
    bl_label = "Spread Step"
    bl_options = {'UNDO'}

    direction: IntProperty(name="Direction", default=1, min=-1, max=1)

    @classmethod
    def poll(cls, context):
        info = state.spread
        return (context.mode == 'EDIT_MESH' and info is not None and not info.get('pending')
                and spread_valid(context))

    def execute(self, context):
        s = get_settings(context)
        if s is None or s.net_unlimited:
            return {'PASS_THROUGH'}          # 끝까지 모드에서는 단계가 의미가 없다. 기본 Alt+휠(프레임 이동)로 넘긴다
        new = min(max(s.net_steps + self.direction, s.bl_rna.properties['net_steps'].hard_min),
                  s.bl_rna.properties['net_steps'].hard_max)
        if new == s.net_steps:
            return {'CANCELLED'}
        s.net_steps = new                     # 바뀐 값으로 같은 시작점에서 바로 다시 퍼뜨린다 (settings 의 update)
        return {'FINISHED'}


# (idname, 키, 값, 수식키, 오퍼레이터 속성, 환경설정에 보일 제목, 한 줄 설명)
KEYMAPS = (
    (MESH_OT_mirror_net_spread.bl_idname, 'RIGHTMOUSE', 'PRESS', {'alt': True}, {'pick': True},
     "버텍스에서 퍼뜨리기",
     "클릭한 버텍스에서 한 칸씩 이어 붙여 선택(Select More), Seam/Sharp 마크 엣지까지만"),
    (MESH_OT_mirror_net_step.bl_idname, 'WHEELUPMOUSE', 'PRESS', {'alt': True}, {'direction': 1},
     "퍼뜨리기 한 칸 더 · 휠 업",
     "퍼뜨린 직후 단계를 한 칸 늘림 (루프 휠 확장과 같은 키, 퍼뜨린 직후에만 동작)"),
    (MESH_OT_mirror_net_step.bl_idname, 'WHEELDOWNMOUSE', 'PRESS', {'alt': True}, {'direction': -1},
     "퍼뜨리기 한 칸 덜 · 휠 다운",
     "퍼뜨린 직후 단계를 한 칸 줄임"),
)

classes = (MESH_OT_mirror_net_spread, MESH_OT_mirror_net_step)
