"""
edge_ops.py — [엣지] 오퍼레이터와 키맵.

  Alt+클릭            : 루프 선택 (기본 루프 선택과 동일)        -> MESH_OT_mirror_loop_select
  Ctrl+Shift+Alt+클릭 : 루프 선택 추가/해제 (토글)                 -> MESH_OT_mirror_loop_select
  Alt+휠                  : 위·아래 루프 동시 확장/축소          -> MESH_OT_mirror_loop_step
  Ctrl+휠                 : 한 방향 확장/축소                    -> MESH_OT_mirror_loop_step

왼쪽 아래 '마지막 작업' 패널은 쓰지 않는다. 폭/길이/링과 최대 꺾임 각도·다이헤드럴은 모두 N 패널에 있다.
(루프 따라가기 옵션은 설정(settings.MLS_Settings)에 두어, 다른 작업의 옵션과 섞이거나 충돌하지 않는다.)

사이드바(N)의 'Mirror Loop' 탭(고정 패널, 임시 키 Alt+1)에서도 같은 폭/길이/링 값을 +/- 로 조절한다.
패널은 닫기 전까지 열려 있고 휠 확장과 숫자가 서로 맞는다.     -> MESH_OT_mirror_loop_adjust, MESH_OT_mirror_loop_panel
(값이 바뀔 때마다 클릭 전 선택에서 다시 시작해 run_selection 으로 처음부터 계산한다.)

엣지 모드에서 루프를 고른 뒤 버텍스 모드로 넘어가도 같은 방식으로 이어서 조절된다. (휠, 고정 패널 모두)
선택 모드는 휠/패널을 막는 조건이 아니다. 면 전용 모드만 막는다. 그 밖의 조건은 '마지막으로 만든 선택이 그대로인지'뿐이다.
버텍스 모드에서는 Blender 규칙대로 나란한 루프 사이의 엣지도 함께 선택되고, 줄이면 함께 풀린다.

휠은 루프 선택 직후에만 poll 이 통과하므로, 그 외에는 기본 Alt+휠(프레임 이동)/Ctrl+휠 동작이 유지된다.
끔 단계에서는 poll 이 실패해 Blender 기본 Alt+클릭(루프 선택)이 그대로 동작한다.
"""

import math
from types import SimpleNamespace

import numpy as np
import bpy
import bmesh
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from mathutils import Vector

from . import state
from .common import (adjust_valid, similar_edge_valid, ensure_tables, face_only_mode, get_mirror_axes, mesh_counts, pick_seed, redraw_3d,
                     restore_selection, snapshot_selection)
from .edge_core import CREASE_SIMILAR, crease_angle
from .edge_range import (ADJUST_LENGTH_MIN, ADJUST_STEP_MAX, apply_range, build_sides, new_wheel_state,
                         ring_edges, selected_loop, state_valid, step_both_sides, step_one_side)
from .face_core import init_faces
from .edge_shape import selected_chains, seed_shapes, similar_loops
from .settings import (edge_similar_options, extension_enabled, geometry_options, get_settings,
                       use_mirror_extension)


def expand_sides(context, ob, bm, seed, st, steps_up, steps_down):
    """위/아래 값만큼 옆 루프를 더 선택한다. 휠 확장과 같은 상태(lo/hi)로 남겨서 휠을 이어 쓸 수 있다."""
    if not (steps_up or steps_down):
        return
    st['faces'][0] = init_faces(context, ob, seed)
    lo, hi = build_sides(bm, st, steps_up, steps_down)
    if (lo, hi) != (0, 0):
        apply_range(bm, st, lo, hi)
        st['lo'], st['hi'] = lo, hi


def run_selection(context, p):
    """
    루프 선택의 본체. 오퍼레이터(execute)와 고정 패널(MESH_OT_mirror_loop_adjust)이 같이 쓴다.

    p : 값을 담은 객체. 필요한 속성 — seed_object, seed_edge, default_edges, do_select, replace, use_ring,
        steps_up, steps_down, length_adjust, threshold
        (최대 꺾임 각도와 다이헤드럴은 설정(N 패널)에서 읽는다)
    반환: ('FINISHED' 또는 'CANCELLED', 경고 메시지 또는 None)
    선택에 성공하면 앵커, 휠 확장 상태, 고정 패널용 조절 상태(state.adjust)를 함께 갱신한다.
    """
    state.reset_wheel()
    state.reset_adjust()
    ob = next((o for o in context.objects_in_mode_unique_data if o.name == p.seed_object), None)
    if ob is None or p.seed_edge < 0:
        return 'CANCELLED', "Alt+클릭으로 루프를 선택해야 이 패널을 쓸 수 있습니다"

    # 이 선택을 적용하기 전의 선택. 고정 패널이 값을 바꿀 때마다 여기서 다시 시작한다.
    # 교체 선택(replace)이면 이전 선택을 버리므로 기록하지 않는다. (큰 메시에서 가장 비싼 부분)
    snap = None if p.replace else snapshot_selection([ob])
    if p.replace:
        bpy.ops.mesh.select_all(action='DESELECT')
    bm = bmesh.from_edit_mesh(ob.data)    # 위 연산 뒤에는 다시 받는다
    ensure_tables(bm)
    if p.seed_edge >= len(bm.edges):
        return 'CANCELLED', None

    settings = get_settings(context)
    use_wheel = settings is None or settings.use_wheel
    max_angle, dih = geometry_options(context)
    cos_limit = math.cos(max_angle)
    if dih:
        bm.normal_update()   # 다이헤드럴 계산에 쓰는 면 법선을 최신으로

    seed = bm.edges[p.seed_edge]
    params = {
        'seed_object': p.seed_object, 'seed_edge': p.seed_edge, 'default_edges': p.default_edges,
        'do_select': p.do_select, 'replace': p.replace, 'use_ring': p.use_ring,
        'steps_up': p.steps_up, 'steps_down': p.steps_down, 'length_adjust': p.length_adjust,
        'threshold': p.threshold,
    }

    # 링: 루프가 아니라 클릭한 엣지와 나란히 쌓인 엣지 한 줄. 폭/길이/미러/기본 루프 결과는 쓰지 않는다.
    if p.do_select and p.use_ring:
        ring = ring_edges(seed)
        for e in ring:
            e.select_set(True)
        bm.select_flush_mode()
        state.set_anchor(ob.name, seed.index, mesh_counts(bm))
        state.set_adjust({'ob': ob.name, 'params': params, 'snap': snap, 'counts': mesh_counts(bm),
                          'core': {e.index for e in ring}, 'up': 0, 'down': 0, 'keep': 0, 'total': 0})
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
        return 'FINISHED', None

    # 씨앗에서 직접 끝까지 걸어 루프를 완성하고 미러 반대편까지 확장 (1단계에서는 미러 반대편을 확장하지 않는다)
    # 길이 줄이기: 클릭한 엣지를 가운데로 엣지를 keep 개만 남긴다.
    # 줄이는 동안에는 미러 반대편과 '기본 루프가 고른 엣지'(루프 전체)를 쓰지 않는다.
    axes = get_mirror_axes(ob) if use_mirror_extension(context) else []
    sl = selected_loop(bm, seed, cos_limit, dih, p.length_adjust if p.do_select else 0, axes, p.threshold)
    loop, mirror, keep, ref_dir, total, full_idxs, axes = (
        sl['loop'], sl['mirror'], sl['keep'], sl['ref_dir'], sl['total'], sl['full'], sl['axes'])
    for e in loop:
        e.select_set(p.do_select)
    for e in mirror:
        e.select_set(p.do_select)
    if not keep and not sl['outline']:
        # Blender 기본 루프가 더 간 엣지도 함께 고른다(불리언 교차선 등). 단, 쪼개진 면에서 기본 루프가 안쪽 쪼갠 선으로 샌 것까지
        # 섞이지 않도록, 우리 루프와 꺾임이 비슷한 엣지만 받는다. (교차선·테두리는 날카롭고 안쪽 쪼갠 선은 평평하다)
        angles = sorted(crease_angle(e) for e in loop)
        ref = angles[len(angles) // 2] if angles else 0.0
        for i in (int(s) for s in p.default_edges.split(",") if s):
            if i < len(bm.edges) and abs(crease_angle(bm.edges[i]) - ref) <= CREASE_SIMILAR:
                bm.edges[i].select_set(p.do_select)
    bm.select_flush_mode()

    # 선택한 경우에만 '사이 채우기' 앵커, 휠 확장 상태, 고정 패널용 조절 상태를 저장한다.
    if p.do_select:
        state.set_anchor(ob.name, seed.index, mesh_counts(bm))
        core = {e.index for e in loop} | {e.index for e in mirror}
        st = new_wheel_state(ob, bm, seed, core, axes, p.threshold, cos_limit, dih, full_idxs)
        st['keep'], st['ref_dir'] = keep, ref_dir
        expand_sides(context, ob, bm, seed, st, p.steps_up, p.steps_down)
        if use_wheel:
            state.set_wheel(st)
        # 실제로 늘어난 폭을 기억해서(막혀서 덜 늘어났을 수 있다) 패널의 숫자와 +/- 가 맞게 한다.
        params['steps_up'], params['steps_down'] = st['hi'], -st['lo']
        state.set_adjust({'ob': ob.name, 'params': params, 'snap': snap, 'counts': mesh_counts(bm),
                          'core': core, 'up': st['hi'], 'down': -st['lo'],
                          'keep': keep, 'total': total})

    bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
    return 'FINISHED', None


class MESH_OT_mirror_loop_select(bpy.types.Operator):
    """루프 선택 + 극점에서 멈추지 않고 끝까지 + 미러 축에서 끊긴 반대편까지"""
    bl_idname = "mesh.mirror_loop_select"
    bl_label = "Mirror Loop Select"
    bl_options = {'UNDO'}      # REGISTER 를 쓰지 않아 왼쪽 아래 '마지막 작업' 패널이 뜨지 않는다. 옵션은 모두 N 패널에 있다.

    extend: BoolProperty(name="Extend", default=False)
    deselect: BoolProperty(name="Deselect", default=False)
    toggle: BoolProperty(name="Toggle", default=False)
    threshold: FloatProperty(
        name="Mirror Threshold",
        description="버텍스가 미러 평면 위에 있다고 보는 거리 허용 오차",
        default=1e-4, min=0.0, precision=6,
    )
    # 폭/길이/링은 사이드바(N) 고정 패널에서 조절한다. (값은 mesh.mirror_loop_adjust 가 바꾼다)
    # 아래 속성은 프로그램(테스트, 매크로)에서 직접 부를 때의 입력이다. 왼쪽 아래 '마지막 작업' 패널은 쓰지 않는다.
    use_ring: BoolProperty(
        name="링 (Ring)",
        description="루프 대신 링을 선택한다: 클릭한 엣지와 나란히 쌓인 엣지 한 줄(사각형 면을 가로질러 맞은편 엣지를 계속 따라감). "
                    "Blender 기본 링 선택과 같다. 폭/길이 값은 쓰지 않는다",
        default=False, options={'SKIP_SAVE'},
    )
    steps_up: IntProperty(
        name="폭: 위쪽 루프 (+/-)",
        description="클릭한 루프에서 화면 위쪽으로 나란한 루프를 몇 줄 더 선택할지 (루프의 폭)",
        default=0, min=0, max=50, options={'SKIP_SAVE'},   # soft_max 를 따로 두지 않아야 +/- 화살표도 끝까지(50) 올라간다
    )
    steps_down: IntProperty(
        name="폭: 아래쪽 루프 (+/-)",
        description="클릭한 루프에서 화면 아래쪽으로 나란한 루프를 몇 줄 더 선택할지 (루프의 폭)",
        default=0, min=0, max=50, options={'SKIP_SAVE'},   # soft_max 를 따로 두지 않아야 +/- 화살표도 끝까지(50) 올라간다
    )
    length_adjust: IntProperty(
        name="길이: 엣지 (-/+)",
        description="루프를 따라가는 길이. 클릭한 엣지를 가운데로 엣지를 한 칸씩 줄이고(-) 다시 늘린다(+). 0 이면 루프 전체. "
                    "폭을 늘렸다면 나란한 루프도 같은 길이로 잘린다. 줄이는 동안은 미러 반대편 확장을 쓰지 않는다",
        default=0, min=-200, max=0, options={'SKIP_SAVE'},
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
        # 면 모드에서도 마찬가지다. (Blender 기본 Alt+클릭은 면 루프를 고른다. 우리 연산은 엣지를 고르므로 맞지 않는다.)
        return (context.mode == 'EDIT_MESH' and extension_enabled(context)
                and not face_only_mode(context))

    def _find_seed(self, context, objs, before, plain, mouse):
        """기본 루프 선택이 바꾼 엣지 중 '클릭한 엣지'를 찾는다: (오브젝트, 씨앗 엣지 인덱스, 선택 여부, 바뀐 엣지들) 또는 None."""
        dih = geometry_options(context)[1]
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

        # 일반 클릭(교체 선택)은 Alt+A 로 비운 뒤 클릭한 것과 같게 취급한다.
        # (이미 선택돼 있던 엣지를 다시 클릭해도 '해제'로 오인하지 않도록)
        plain = not (self.extend or self.toggle or self.deselect)

        # 1) 기존 선택 상태 기록. 교체 선택은 이전 선택을 버리므로 기록할 필요가 없다.
        #    (메시가 크면 모든 버텍스·엣지·면을 훑는 이 기록이 클릭 시간의 대부분이다)
        snap, before = None, {}
        if not plain:
            snap = snapshot_selection(objs)
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

        hit = self._find_seed(context, objs, before, plain, mouse)
        if hit is None:
            return {'FINISHED'}

        # 3) 기본 루프가 바꾼 선택은 되돌리고, 씨앗과 기본 루프가 고른 엣지를 기억해서 execute 가 모든 걸 다시 계산하게 한다.
        #    기본 루프가 우리 루프보다 더 가는 곳(불리언 교차선 등)도 예전처럼 함께 선택한다.
        #    (패널에서 값을 바꿔 다시 실행할 때와 처음 실행이 같은 결과를 내도록)
        if snap is not None:
            restore_selection(objs, snap)
        ob, self.seed_edge, self.do_select, default_edges = hit
        self.seed_object = ob.name
        self.default_edges = ",".join(str(i) for i in sorted(default_edges))
        self.replace = plain
        return self.execute(context)

    def execute(self, context):
        status, msg = run_selection(context, self)
        if msg:
            self.report({'WARNING'}, msg)
        return {status}


class MESH_OT_mirror_edge_similar(bpy.types.Operator):
    """선택한 엣지 루프와 같은 모양의 엣지 루프를 전부 선택 (톱니바퀴 림처럼 뾰족한 윤곽, 베벨·버텍스 수·회전·크기 달라도)"""
    bl_idname = "mesh.mirror_edge_similar"
    bl_label = "Select Similar Edge Loops"
    bl_options = {'UNDO'}      # 옵션은 사이드바(N) 'Mirror Loop' 탭의 '같은 모양 엣지 루프' 섹션에 있다.

    refresh: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)    # 옵션이 바뀌어 같은 씨앗으로 다시 찾기

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and extension_enabled(context) and not face_only_mode(context)

    def execute(self, context):
        opt = edge_similar_options(context)
        objs = list(context.objects_in_mode_unique_data)
        prev = state.similar_edge if self.refresh else None
        if self.refresh:
            if prev is None or not similar_edge_valid(context):
                state.reset_similar_edge()
                return {'CANCELLED'}
            restore_selection(objs, prev['snap'])
            snap = prev['snap']
        else:
            snap = snapshot_selection(objs)

        # 1) 씨앗: 다시 찾기면 처음 씨앗 엣지, 아니면 지금 선택한 엣지. 월드 좌표로 읽어 오브젝트 크기 차이를 맞춘다.
        bms = [bmesh.from_edit_mesh(ob.data) for ob in objs]
        seed_idx, chains = {}, []
        for ob, bm in zip(objs, bms):
            ensure_tables(bm)
            if prev is not None:
                edges = [bm.edges[i] for i in prev['seeds'].get(ob.name, ()) if i < len(bm.edges)]
            else:
                edges = [e for e in bm.edges if e.select and not e.hide]
            seed_idx[ob.name] = {e.index for e in edges}
            chains.extend(selected_chains(edges, np.array(ob.matrix_world)))
        if not any(seed_idx.values()):
            self.report({'WARNING'}, "기준이 될 엣지 루프를 선택하세요 (Alt+클릭으로 루프를 고른 뒤 실행)")
            return {'CANCELLED'}
        shapes = seed_shapes(chains)
        if not shapes:
            self.report({'WARNING'}, "선택한 엣지가 한 줄로 이어진 루프가 아닙니다 (갈라지거나 길이가 0). 루프 하나를 골라 주세요")
            return {'CANCELLED'}

        # 2) 모든 편집 중인 오브젝트에서 같은 모양 루프 찾기
        max_angle, dih = geometry_options(context)
        cos_limit = math.cos(max_angle)
        if not opt.extend:
            bpy.ops.mesh.select_all(action='DESELECT')
        found_idx, counts, total = {}, {}, 0
        for ob in objs:
            bm = bmesh.from_edit_mesh(ob.data)
            ensure_tables(bm)
            if dih:
                bm.normal_update()
            loops = similar_loops(bm, shapes, cos_limit, dih, opt.shape_tol, opt.size_tol, opt.scale_invariant,
                                  np.array(ob.matrix_world))
            idx = set()
            for chain in loops:
                for e in chain:
                    e.select_set(True)
                    idx.add(e.index)
            total += len(loops)
            found_idx[ob.name] = idx
            counts[ob.name] = mesh_counts(bm)
            bm.select_flush_mode()
            bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)

        state.set_similar_edge({'seeds': seed_idx, 'snap': snap, 'found': found_idx, 'counts': counts, 'loops': total})
        self.report({'INFO'}, "같은 모양 엣지 루프 %d개 선택" % total)
        redraw_3d(context)
        return {'FINISHED'}


class MESH_OT_mirror_loop_step(bpy.types.Operator):
    """루프 선택 후 휠: 옆 루프까지 선택 범위를 늘리거나 줄인다 (Alt = 위·아래 동시, Ctrl = 한 방향)"""
    bl_idname = "mesh.mirror_loop_step"
    bl_label = "Mirror Loop Step"
    bl_options = {'UNDO'}  # 다시 실행(redo)할 값이 없어서 REGISTER(리두 패널)는 쓰지 않는다

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
        return (context.mode == 'EDIT_MESH' and st is not None and not face_only_mode(context)
                and ob is not None and ob.name == st['ob'])

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
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
        adj = state.adjust
        if adj is not None and adj['ob'] == ob.name:     # 고정 패널의 숫자와 다시 계산할 값도 맞춘다
            adj['up'], adj['down'] = new_hi, -new_lo
            adj['params']['steps_up'], adj['params']['steps_down'] = new_hi, -new_lo
        redraw_3d(context)
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
        return {'FINISHED'}


class MESH_OT_mirror_loop_adjust(bpy.types.Operator):
    """고정 패널의 +/- 버튼: 마지막 루프 선택의 폭(위/아래), 길이, 링을 바꿔서 다시 선택한다"""
    bl_idname = "mesh.mirror_loop_adjust"
    bl_label = "Mirror Loop Adjust"
    bl_options = {'UNDO'}      # 값은 패널에 보이므로 왼쪽 아래 '마지막 작업' 패널(REGISTER)은 쓰지 않는다

    target: EnumProperty(
        name="Target",
        items=(
            ('UP', "폭: 위쪽", "화면 위쪽으로 나란한 루프를 늘리거나 줄인다"),
            ('DOWN', "폭: 아래쪽", "화면 아래쪽으로 나란한 루프를 늘리거나 줄인다"),
            ('LENGTH', "길이", "루프를 따라가는 엣지 수를 줄이거나(-) 되돌린다(+)"),
            ('RING', "링", "루프 대신 링(나란히 쌓인 엣지 한 줄)으로 바꾸거나 되돌린다"),
            ('RESET', "초기화", "폭과 길이를 처음 클릭한 상태(루프 전체)로 되돌린다"),
            ('REFRESH', "다시 계산", "값은 그대로 두고 현재 설정(최대 꺾임 각도 등)으로 다시 고른다"),
        ),
        default='UP',
    )
    delta: IntProperty(name="Delta", default=1, min=-ADJUST_STEP_MAX, max=ADJUST_STEP_MAX)
    # 0 이상이면 delta 대신 이 값으로 바로 정한다. (패널의 숫자칸 드래그/입력용)
    # 폭: 줄 수 / 길이: 남길 엣지 수(1~전체)
    value: IntProperty(name="Value", default=-1, min=-1, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH' and state.adjust is not None
                and not face_only_mode(context))

    def execute(self, context):
        adj = state.adjust
        if not adjust_valid(context):
            state.reset_adjust()
            self.report({'WARNING'}, "선택이 바뀌었습니다. Alt+클릭으로 루프를 다시 선택하세요")
            redraw_3d(context)
            return {'CANCELLED'}

        p = SimpleNamespace(**adj['params'])
        t, d = self.target, self.delta
        absolute = self.value >= 0
        if t == 'UP':
            p.steps_up = min(max(self.value if absolute else p.steps_up + d, 0), ADJUST_STEP_MAX)
        elif t == 'DOWN':
            p.steps_down = min(max(self.value if absolute else p.steps_down + d, 0), ADJUST_STEP_MAX)
        elif t == 'LENGTH':
            lowest = max(ADJUST_LENGTH_MIN, -(adj['total'] - 1)) if adj['total'] else 0
            want = (self.value - adj['total']) if absolute else p.length_adjust + d
            p.length_adjust = min(max(want, lowest), 0)
        elif t == 'RING':
            p.use_ring = not p.use_ring
        elif t == 'RESET':
            p.steps_up = p.steps_down = p.length_adjust = 0
        if p.use_ring and t not in ('RING', 'REFRESH'):
            p.use_ring = False        # 링 상태에서 폭/길이를 만지면 루프로 돌아와서 적용한다

        ob = context.edit_object
        if adj['snap'] is not None:
            restore_selection([ob], adj['snap'])
        status, msg = run_selection(context, p)
        if msg:
            self.report({'WARNING'}, msg)
        redraw_3d(context)
        return {status}


class MESH_OT_mirror_loop_panel(bpy.types.Operator):
    """오른쪽 사이드바(N)의 Mirror Loop 탭을 열고 닫는다. 열어 둔 동안은 휠을 돌리거나 다른 작업을 해도 닫히지 않는다"""
    bl_idname = "mesh.mirror_loop_panel"
    bl_label = "Mirror Loop Panel"

    CATEGORY = "Mirror Loop"

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH' and context.area is not None
                and context.area.type == 'VIEW_3D')

    def execute(self, context):
        space = context.space_data
        region = next((r for r in context.area.regions if r.type == 'UI'), None)
        if space is None or space.type != 'VIEW_3D':
            return {'CANCELLED'}
        showing = space.show_region_ui
        if showing and region is not None and region.active_panel_category == self.CATEGORY:
            space.show_region_ui = False            # 이미 보이고 있으면 닫는다
            return {'FINISHED'}
        space.show_region_ui = True
        if region is not None:
            try:
                region.active_panel_category = self.CATEGORY     # 이 탭을 앞으로 가져온다
            except (TypeError, ValueError):
                pass                                             # 탭이 아직 그려지기 전이면 사이드바만 연다
        context.area.tag_redraw()
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
    # 고정 패널: 사이드바(N)의 Mirror Loop 탭을 열고 닫는다. 임시 키 Alt+1, 환경설정에서 바꾼다.
    (MESH_OT_mirror_loop_panel.bl_idname, 'ONE', 'PRESS', {'alt': True}, {},
     "고정 패널 열기/닫기",
     "사이드바(N)의 Mirror Loop 탭: 폭·길이·링을 +/- 로 조절, 열어 두면 닫히지 않음"),

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
    MESH_OT_mirror_loop_adjust,
    MESH_OT_mirror_loop_panel,
    MESH_OT_mirror_edge_similar,
)
