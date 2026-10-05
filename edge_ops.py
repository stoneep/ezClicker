"""
edge_ops.py — [엣지] 오퍼레이터와 키맵.

  Alt+클릭, Shift+Alt+클릭 : 루프 선택 (기본 루프 선택과 동일)   -> MESH_OT_mirror_loop_select
  Alt+휠                  : 위·아래 루프 동시 확장/축소          -> MESH_OT_mirror_loop_step
  Ctrl+휠                 : 한 방향 확장/축소                    -> MESH_OT_mirror_loop_step

휠은 루프 선택 직후에만 poll 이 통과하므로, 그 외에는 기본 Alt+휠(프레임 이동)/Ctrl+휠 동작이 유지된다.
끔 단계에서는 poll 이 실패해 Blender 기본 Alt+클릭(루프 선택)이 그대로 동작한다.
"""

import math

import bpy
import bmesh
from bpy.props import BoolProperty, FloatProperty, IntProperty
from mathutils import Vector

from . import state
from .common import ensure_tables, get_mirror_axes, make_projector, mesh_counts, pick_seed
from .edge_core import (cross_orientation_edges, find_mirror_edges,
                        screen_orientation, walk_loop)
from .edge_range import (apply_range, new_wheel_state, state_valid,
                         step_both_sides, step_one_side)
from .face_core import init_faces
from .settings import extension_enabled, get_settings, use_mirror_extension


class MESH_OT_mirror_loop_select(bpy.types.Operator):
    """루프 선택 + 극점에서 멈추지 않고 끝까지 + 미러 축에서 끊긴 반대편까지"""
    bl_idname = "mesh.mirror_loop_select"
    bl_label = "Mirror Loop Select"
    bl_options = {'REGISTER', 'UNDO'}

    extend: BoolProperty(name="Extend", default=False)
    deselect: BoolProperty(name="Deselect", default=False)
    toggle: BoolProperty(name="Toggle", default=False)
    max_angle: FloatProperty(
        name="Max Turn Angle",
        description="극점/삼각형에서 루프가 꺾여도 계속 진행할 최대 각도 (클수록 더 멀리 감)",
        default=math.radians(60.0), min=0.0, max=math.radians(120.0),
        subtype='ANGLE',
    )
    threshold: FloatProperty(
        name="Mirror Threshold",
        description="버텍스가 미러 평면 위에 있다고 보는 거리 허용 오차",
        default=1e-4, min=0.0, precision=6,
    )
    use_dihedral: BoolProperty(
        name="Use Dihedral",
        description="면 사이 각도(다이헤드럴)를 보조 기준으로 써서 능선·로우폴리에서 루프가 끊기거나 새는 것을 줄인다",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        # 끔 단계에서는 poll 이 실패해 Blender 기본 Alt+클릭(루프 선택)이 그대로 동작한다.
        return context.mode == 'EDIT_MESH' and extension_enabled(context)

    def invoke(self, context, event):
        state.reset_wheel()  # 새 클릭이면 이전 휠 상태는 버린다.

        objs = list(context.objects_in_mode_unique_data)
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        settings = get_settings(context)
        use_wheel = settings is None or settings.use_wheel
        use_mirror = use_mirror_extension(context)

        # 1) 기존 선택 상태 기록
        before = {}
        for ob in objs:
            bm = bmesh.from_edit_mesh(ob.data)
            bm.edges.index_update()
            before[ob] = {e.index for e in bm.edges if e.select}

        # 2) 기본 루프 선택 실행 (클릭 위치 판정은 Blender 기본 기능 사용)
        #    기본 루프가 바꾼 엣지 중 '클릭한 엣지'를 찾아 씨앗으로 쓴다.
        result = bpy.ops.mesh.loop_select(
            'INVOKE_DEFAULT',
            extend=self.extend, deselect=self.deselect, toggle=self.toggle,
        )
        if 'FINISHED' not in result:
            return {'CANCELLED'}

        cos_limit = math.cos(self.max_angle)
        dih = self.use_dihedral

        # 일반 클릭(교체 선택)은 Alt+A 로 비운 뒤 클릭한 것과 같게 취급한다.
        # (이미 선택돼 있던 엣지를 다시 클릭해도 '해제'로 오인하지 않도록)
        plain = not (self.extend or self.toggle or self.deselect)

        # 반대 방향 해제 옵션은 엣지 전용 모드에서만 쓴다.
        edge_only = tuple(context.tool_settings.mesh_select_mode) == (False, True, False)
        deselect_v = settings is not None and settings.deselect_vertical
        deselect_h = settings is not None and settings.deselect_horizontal

        # 3) 씨앗에서 직접 끝까지 걸어 루프를 완성하고 미러 반대편까지 확장
        for ob in objs:
            bm = bmesh.from_edit_mesh(ob.data)
            bm.edges.ensure_lookup_table()
            bm.edges.index_update()
            if dih:
                bm.normal_update()   # 다이헤드럴 계산에 쓰는 면 법선을 최신으로
            after = {e.index for e in bm.edges if e.select}
            if plain:
                added, removed = after, set()
            else:
                added = after - before[ob]
                removed = before[ob] - after

            if added:
                select = True
                seed = pick_seed(context, ob, bm, added, mouse)
            elif removed:        # Shift+Alt 토글로 해제된 경우
                select = False
                seed = pick_seed(context, ob, bm, removed, mouse)
            else:
                continue

            loop = walk_loop(seed, cos_limit, dih)
            for e in loop:
                e.select_set(select)

            # 1단계에서는 미러 반대편을 확장하지 않는다.
            axes = get_mirror_axes(ob) if use_mirror else []
            mirror = find_mirror_edges(bm, loop, axes, self.threshold, cos_limit, dih) if axes else []
            for e in mirror:
                e.select_set(select)

            # 엣지 전용 모드에서 가로/세로 루프를 고르면 반대 방향 루프는 해제한다. (옵션)
            #   엣지 전용 모드에서만 한다: 이 모드의 select_set(False)는 다른 선택 엣지가 쓰는
            #   버텍스를 건드리지 않아서, 교차하는 루프가 서로를 깨뜨리지 않는다.
            if select and edge_only and (deselect_v or deselect_h):
                prev = [bm.edges[i] for i in before[ob]] if not plain else []
                if prev:
                    project = make_projector(context, ob)
                    ori = screen_orientation(loop, project)
                    if (ori == 'H' and deselect_v) or (ori == 'V' and deselect_h):
                        keep = loop | set(mirror)
                        for e in cross_orientation_edges(prev, keep, 'V' if ori == 'H' else 'H',
                                                         project, cos_limit, dih):
                            e.select_set(False)

            bm.select_flush_mode()

            # 4) 선택한 경우에만 '사이 채우기' 앵커와 휠 확장용 상태를 저장한다.
            if select:
                ensure_tables(bm)
                state.set_anchor(ob.name, seed.index, mesh_counts(bm))
                if use_wheel:
                    state.set_wheel(new_wheel_state(
                        ob, bm, seed,
                        {e.index for e in loop} | {e.index for e in mirror},
                        axes, self.threshold, cos_limit, dih))

            bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)

        return {'FINISHED'}


class MESH_OT_mirror_loop_step(bpy.types.Operator):
    """루프 선택 후 휠: 옆 루프까지 선택 범위를 늘리거나 줄인다 (Alt = 위·아래 동시, Ctrl = 한 방향)"""
    bl_idname = "mesh.mirror_loop_step"
    bl_label = "Mirror Loop Step"
    bl_options = {'UNDO'}  # execute 가 없어서 REGISTER(리두 패널)는 쓰지 않는다

    direction: IntProperty(name="Direction", default=1, min=-1, max=1)
    both_sides: BoolProperty(
        name="Both Sides",
        description="위·아래 루프를 동시에 늘리거나 줄인다",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        ob = context.edit_object
        st = state.wheel
        return (context.mode == 'EDIT_MESH' and st is not None
                and ob is not None and ob.name == st['ob'])

    def invoke(self, context, event):
        ob = context.edit_object
        bm = bmesh.from_edit_mesh(ob.data)
        ensure_tables(bm)

        st = state.wheel
        if not state_valid(ob, bm, st):
            # 선택이 바뀌었거나 메시가 편집됨 -> 기본 휠 동작으로 넘긴다.
            state.reset_wheel()
            return {'CANCELLED', 'PASS_THROUGH'}

        if st['dih']:
            bm.normal_update()   # 다이헤드럴 계산에 쓰는 면 법선을 최신으로

        if 0 not in st['faces']:
            st['faces'][0] = init_faces(context, ob, bm.edges[st['seeds'][0]])

        lo, hi = st['lo'], st['hi']
        if self.both_sides:
            result = step_both_sides(bm, st, lo, hi, self.direction)
        else:
            result = step_one_side(bm, st, lo, hi, self.direction)

        if result is None:
            if self.direction > 0:
                self.report({'INFO'}, "이어서 선택할 옆 루프가 없습니다")
            return {'CANCELLED'}

        new_lo, new_hi = result
        apply_range(bm, st, new_lo, new_hi)
        st['lo'], st['hi'] = new_lo, new_hi
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
        return {'FINISHED'}


def draw_settings(layout, settings):
    """패널/메뉴에 끼워 넣는 엣지 기능 설정. (ui.draw_panel_body 가 호출한다)"""
    layout.prop(settings, "use_wheel")
    col = layout.column(align=True)
    col.prop(settings, "deselect_vertical")
    col.prop(settings, "deselect_horizontal")


# (idname, 키, 값, 수식키, 오퍼레이터 속성)
KEYMAPS = (
    (MESH_OT_mirror_loop_select.bl_idname, 'LEFTMOUSE', 'PRESS', {'alt': True}, {}),
    (MESH_OT_mirror_loop_select.bl_idname, 'LEFTMOUSE', 'PRESS',
     {'shift': True, 'alt': True}, {'toggle': True}),

    # Alt+휠: 위·아래 동시 / Ctrl+휠: 한 방향
    (MESH_OT_mirror_loop_step.bl_idname, 'WHEELUPMOUSE', 'PRESS',
     {'alt': True}, {'direction': 1, 'both_sides': True}),
    (MESH_OT_mirror_loop_step.bl_idname, 'WHEELUPMOUSE', 'PRESS',
     {'ctrl': True}, {'direction': 1, 'both_sides': False}),
    (MESH_OT_mirror_loop_step.bl_idname, 'WHEELDOWNMOUSE', 'PRESS',
     {'alt': True}, {'direction': -1, 'both_sides': True}),
    (MESH_OT_mirror_loop_step.bl_idname, 'WHEELDOWNMOUSE', 'PRESS',
     {'ctrl': True}, {'direction': -1, 'both_sides': False}),
)

classes = (
    MESH_OT_mirror_loop_select,
    MESH_OT_mirror_loop_step,
)
