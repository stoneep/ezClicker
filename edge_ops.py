"""
edge_ops.py — [엣지] 오퍼레이터와 키맵.

  Alt+클릭            : 루프 선택 (기본 루프 선택과 동일)        -> MESH_OT_mirror_loop_select
  Ctrl+Shift+Alt+클릭 : 루프 선택 추가/해제 (토글)                 -> MESH_OT_mirror_loop_select
  Alt+휠                  : 위·아래 루프 동시 확장/축소          -> MESH_OT_mirror_loop_step
  Ctrl+휠                 : 한 방향 확장/축소                    -> MESH_OT_mirror_loop_step

루프 선택 직후 왼쪽 아래에 Blender 의 '마지막 작업 조정' 패널이 뜬다. 거기서 '위쪽/아래쪽 루프'를 +/- 로 조절하면
휠 확장과 같은 결과를 얻는다. (Alt+휠을 안 쓰는 사람이 마우스만으로 범위를 조절하는 용도)

휠은 루프 선택 직후에만 poll 이 통과하므로, 그 외에는 기본 Alt+휠(프레임 이동)/Ctrl+휠 동작이 유지된다.
끔 단계에서는 poll 이 실패해 Blender 기본 Alt+클릭(루프 선택)이 그대로 동작한다.
"""

import math

import bpy
import bmesh
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from mathutils import Vector

from . import state
from .common import (ensure_tables, get_mirror_axes, mesh_counts, pick_seed,
                     restore_selection, snapshot_selection)
from .edge_core import find_mirror_edges, walk_loop
from .edge_range import (apply_range, build_offset, new_wheel_state, state_valid,
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
    # 선택 직후 왼쪽 아래에 뜨는 '마지막 작업 조정' 패널에서 +/- 로 조절한다. (휠 확장과 같은 동작)
    steps_up: IntProperty(
        name="위쪽 루프 (+/-)",
        description="클릭한 루프에서 화면 위쪽으로 루프를 몇 줄 더 선택할지",
        default=0, min=0, max=100000, options={'SKIP_SAVE'},   # soft_max 를 따로 두지 않아야 +/- 화살표도 끝까지 올라간다
    )
    steps_down: IntProperty(
        name="아래쪽 루프 (+/-)",
        description="클릭한 루프에서 화면 아래쪽으로 루프를 몇 줄 더 선택할지",
        default=0, min=0, max=100000, options={'SKIP_SAVE'},   # soft_max 를 따로 두지 않아야 +/- 화살표도 끝까지 올라간다
    )
    # 패널에서 값을 바꾸면 Blender 가 실행 취소 후 execute 를 다시 부른다. 그래서 invoke 에서 정한 것을 기억해 둔다.
    seed_object: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    seed_edge: IntProperty(default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    default_edges: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})    # Blender 기본 루프 선택이 바꾼 엣지 인덱스 ("3,4,5")
    do_select: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})    # False: 클릭한 루프를 해제
    replace: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})     # True: 기존 선택을 지우고 교체

    @classmethod
    def poll(cls, context):
        # 끔 단계에서는 poll 이 실패해 Blender 기본 Alt+클릭(루프 선택)이 그대로 동작한다.
        return context.mode == 'EDIT_MESH' and extension_enabled(context)

    def draw(self, context):
        layout = self.layout
        if self.do_select:
            col = layout.column(align=True)
            col.prop(self, "steps_up")
            col.prop(self, "steps_down")
            layout.separator()
        layout.prop(self, "max_angle")
        layout.prop(self, "use_dihedral")

    def _find_seed(self, context, objs, before, plain, mouse):
        """기본 루프 선택이 바꾼 엣지 중 '클릭한 엣지'를 찾는다: (오브젝트, 씨앗 엣지 인덱스, 선택 여부, 바뀐 엣지들) 또는 None."""
        dih = self.use_dihedral
        for ob in objs:
            bm = bmesh.from_edit_mesh(ob.data)
            bm.edges.ensure_lookup_table()
            bm.edges.index_update()
            after = {e.index for e in bm.edges if e.select}
            if plain:
                added, removed = after, set()
            else:
                added = after - before[ob]
                removed = before[ob] - after
            if added:
                return ob, pick_seed(context, ob, bm, added, mouse).index, True, added
            if removed:        # Ctrl+Shift+Alt 토글로 해제된 경우
                return ob, pick_seed(context, ob, bm, removed, mouse).index, False, removed
        return None

    def invoke(self, context, event):
        state.reset_wheel()  # 새 클릭이면 이전 휠 상태는 버린다.

        objs = list(context.objects_in_mode_unique_data)
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))

        # 1) 기존 선택 상태 기록
        snap = snapshot_selection(objs)
        before = {}
        for ob in objs:
            bm = bmesh.from_edit_mesh(ob.data)
            bm.edges.index_update()
            before[ob] = {e.index for e in bm.edges if e.select}

        # 2) 기본 루프 선택 실행 (클릭 위치 판정은 Blender 기본 기능 사용)
        #    기본 루프가 바꾼 엣지 중 '클릭한 엣지'를 씨앗으로 쓴다.
        result = bpy.ops.mesh.loop_select(
            'INVOKE_DEFAULT',
            extend=self.extend, deselect=self.deselect, toggle=self.toggle,
        )
        if 'FINISHED' not in result:
            return {'CANCELLED'}

        # 일반 클릭(교체 선택)은 Alt+A 로 비운 뒤 클릭한 것과 같게 취급한다.
        # (이미 선택돼 있던 엣지를 다시 클릭해도 '해제'로 오인하지 않도록)
        plain = not (self.extend or self.toggle or self.deselect)
        hit = self._find_seed(context, objs, before, plain, mouse)
        if hit is None:
            return {'FINISHED'}

        # 3) 기본 루프가 바꾼 선택은 되돌리고, 씨앗과 기본 루프가 고른 엣지를 기억해서 execute 가 모든 걸 다시 계산하게 한다.
        #    기본 루프가 우리 루프보다 더 가는 곳(불리언 교차선 등)도 예전처럼 함께 선택한다.
        #    (패널에서 값을 바꿔 다시 실행할 때와 처음 실행이 같은 결과를 내도록)
        restore_selection(objs, snap)
        ob, self.seed_edge, self.do_select, default_edges = hit
        self.seed_object = ob.name
        self.default_edges = ",".join(str(i) for i in sorted(default_edges))
        self.replace = plain
        return self.execute(context)

    def execute(self, context):
        state.reset_wheel()
        ob = next((o for o in context.objects_in_mode_unique_data if o.name == self.seed_object), None)
        if ob is None or self.seed_edge < 0:
            self.report({'WARNING'}, "Alt+클릭으로 루프를 선택해야 이 패널을 쓸 수 있습니다")
            return {'CANCELLED'}

        if self.replace:
            bpy.ops.mesh.select_all(action='DESELECT')
        bm = bmesh.from_edit_mesh(ob.data)    # 위 연산 뒤에는 다시 받는다
        ensure_tables(bm)
        if self.seed_edge >= len(bm.edges):
            return {'CANCELLED'}

        settings = get_settings(context)
        use_wheel = settings is None or settings.use_wheel
        cos_limit = math.cos(self.max_angle)
        dih = self.use_dihedral
        if dih:
            bm.normal_update()   # 다이헤드럴 계산에 쓰는 면 법선을 최신으로

        # 씨앗에서 직접 끝까지 걸어 루프를 완성하고 미러 반대편까지 확장
        seed = bm.edges[self.seed_edge]
        loop = walk_loop(seed, cos_limit, dih)
        # 1단계에서는 미러 반대편을 확장하지 않는다.
        axes = get_mirror_axes(ob) if use_mirror_extension(context) else []
        mirror = find_mirror_edges(bm, loop, axes, self.threshold, cos_limit, dih) if axes else []
        for e in loop:
            e.select_set(self.do_select)
        for e in mirror:
            e.select_set(self.do_select)
        for i in (int(s) for s in self.default_edges.split(",") if s):
            if i < len(bm.edges):
                bm.edges[i].select_set(self.do_select)
        bm.select_flush_mode()

        # 선택한 경우에만 '사이 채우기' 앵커와 휠 확장용 상태를 저장한다.
        if self.do_select:
            state.set_anchor(ob.name, seed.index, mesh_counts(bm))
            if use_wheel:
                st = new_wheel_state(
                    ob, bm, seed,
                    {e.index for e in loop} | {e.index for e in mirror},
                    axes, self.threshold, cos_limit, dih)
                self._expand(context, ob, bm, seed, st)
                state.set_wheel(st)

        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
        return {'FINISHED'}

    def _expand(self, context, ob, bm, seed, st):
        """패널의 위/아래 값만큼 옆 루프를 더 선택한다. 휠 확장과 같은 상태(lo/hi)로 남겨서 휠을 이어 쓸 수 있다."""
        if not (self.steps_up or self.steps_down):
            return
        st['faces'][0] = init_faces(context, ob, seed)
        lo = hi = 0
        for k in range(1, self.steps_up + 1):
            if not build_offset(bm, st, k):
                break
            hi = k
        for k in range(1, self.steps_down + 1):
            if not build_offset(bm, st, -k):
                break
            lo = -k
        if (lo, hi) != (0, 0):
            apply_range(bm, st, lo, hi)
            st['lo'], st['hi'] = lo, hi


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


# (idname, 키, 값, 수식키, 오퍼레이터 속성, 환경설정에 보일 제목, 한 줄 설명)
KEYMAPS = (
    (MESH_OT_mirror_loop_select.bl_idname, 'LEFTMOUSE', 'PRESS', {'alt': True}, {},
     "루프 선택",
     "극점·삼각형을 지나 끝까지, 미러 반대편까지"),
    (MESH_OT_mirror_loop_select.bl_idname, 'LEFTMOUSE', 'PRESS',
     {'ctrl': True, 'shift': True, 'alt': True}, {'toggle': True},
     "루프 선택 추가/해제",
     "선택한 루프는 해제, 아니면 추가"),

    # Alt+휠: 위·아래 동시 / Ctrl+휠: 한 방향
    (MESH_OT_mirror_loop_step.bl_idname, 'WHEELUPMOUSE', 'PRESS',
     {'alt': True}, {'direction': 1, 'both_sides': True},
     "위·아래 동시 확장 · 휠 업",
     "루프 선택 직후 위·아래 옆 루프를 함께 늘림"),
    (MESH_OT_mirror_loop_step.bl_idname, 'WHEELUPMOUSE', 'PRESS',
     {'ctrl': True}, {'direction': 1, 'both_sides': False},
     "한 방향 확장 · 휠 업",
     "루프 선택 직후 한쪽 옆 루프를 늘림"),
    (MESH_OT_mirror_loop_step.bl_idname, 'WHEELDOWNMOUSE', 'PRESS',
     {'alt': True}, {'direction': -1, 'both_sides': True},
     "위·아래 동시 축소 · 휠 다운",
     "늘린 범위를 위·아래에서 함께 줄임"),
    (MESH_OT_mirror_loop_step.bl_idname, 'WHEELDOWNMOUSE', 'PRESS',
     {'ctrl': True}, {'direction': -1, 'both_sides': False},
     "한 방향 축소 · 휠 다운",
     "늘린 범위를 한쪽에서 줄임"),
)

classes = (
    MESH_OT_mirror_loop_select,
    MESH_OT_mirror_loop_step,
)
