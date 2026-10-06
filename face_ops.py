"""
face_ops.py — [면] 오퍼레이터와 키맵.

  Ctrl+Alt+클릭  : 마지막으로 고른 루프 ~ 클릭한 루프 사이 '전부' 선택
                   (사이의 모든 버텍스·엣지·면. select_faces=True)
  Shift+Alt+클릭 : 같은 구간에서 '루프(엣지)만' 선택 (사이의 면·루프와 직각인 테두리는 제외. select_faces=False)
                   -> 둘 다 MESH_OT_mirror_loop_between (키맵의 select_faces 값만 다르다)

  면 모드: 첫 클릭은 아무것도 선택하지 않고 시작 루프만 지정한다. 지정한 루프는 색 선으로 표시되고(overlay.py,
           색/두께는 환경설정), 같은 방향의 다른 루프를 클릭하면 그 사이의 면이 선택된다. Esc 로 지정을 취소한다.

  Alt+클릭 (면 모드) : Blender 기본 면 루프 선택을 그대로 쓴다. 고른 면 줄을 기억해 두었다가
                  사이드바(N) 'Mirror Loop' 탭의 숫자칸으로 폭(양옆에 나란한 줄 수)과 길이(면 수)를 바꾼다.
                  -> MESH_OT_mirror_face_loop_select, MESH_OT_mirror_face_adjust
                  (엣지·버텍스 모드의 Alt+클릭 + 고정 패널과 같은 흐름. 선택을 직접 바꾸면 패널은 쉰다.)

  버텍스 모드에서는 어느 쪽이든 루프의 버텍스를 고르면 Blender 가 그 사이 엣지·면을 자동으로 같이 선택하므로
  결과가 같다. 차이는 엣지 모드에서 가장 분명하다.

  1) Alt+클릭으로 시작 루프 A 를 고른다. (이게 앵커가 된다)
  2) Ctrl+Alt+클릭으로 끝 루프 B 를 누른다.
  -> A 에서 B 까지의 루프를 edge_range.find_between 이 찾고,
     '면 선택' 옵션(기본 켬)이면 face_core.strip_faces 가 인접한 두 루프 사이의 면도 모은다.
     (수동으로 엣지 -> 버텍스 -> 면 모드로 바꿔서 얻던 결과를 한 번에 만든다.)
  -> 끝나면 B 가 새 앵커가 되어 C 를 Ctrl+Alt+클릭하면 B~C 가 이어서 선택된다.

  Alt+더블클릭  : 클릭한 면과 같은 모양의 면(평면 영역)을 전부 선택
                  -> MESH_OT_mirror_face_similar  (Shift+G > 모양 메뉴에서는 선택한 면을 기준으로 실행)
  기어, 나사 머리처럼 같은 평면 모양을 많이 만들 때 쓴다. 비교 방법은 face_shape.py 참고.

Blender 기본의 Ring 선택 키와 같다. 이 키맵이 우선한다.
(바꾸려면 Preferences > Keymap > Mesh 에서 Mirror Loop Between 을 수정)
끔 단계에서는 poll 이 실패해 Blender 기본 Ctrl+Alt+클릭(링 선택)이 그대로 동작한다.
"""

import math

import bpy
import bmesh
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import prefs, state
from .common import (adjust_valid, ensure_tables, face_only_mode, get_mirror_axes, mesh_counts, pick_seed,
                     redraw_3d, restore_selection, screen_mid, screen_score, similar_valid, snapshot_selection)
from .edge_range import ADJUST_LENGTH_MIN, ADJUST_STEP_MAX, find_between, selected_loop
from .face_core import order_strip, strip_faces, strip_rails, strip_region
from .face_patch import diagnose_seed, patch_seed_shapes, similar_patches
from .face_shape import flat_island, island_shape, similar_islands
from .settings import extension_enabled, geometry_options, similar_options, use_mirror_extension


def selected_face_indices(bm):
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    return {f.index for f in bm.faces if f.select}


def apply_strip(context, ob, adj, steps_up, steps_down, length_adjust):
    """
    면 줄(adj)을 폭/길이 값대로 다시 선택한다. 지난번에 우리가 고른 면(adj['added'])만 풀고 다시 고르므로
    원래 선택돼 있던 다른 면은 건드리지 않는다. adj 의 up/down/keep/added 를 갱신한다.
    """
    bm = bmesh.from_edit_mesh(ob.data)
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.index_update()
    chain = [bm.faces[i] for i in adj['chain']]
    rails = strip_rails(chain, adj['closed'], bm.edges[adj['seed']] if adj['seed'] < len(bm.edges) else None)
    if rails is None:
        return False

    total = len(chain)
    keep = max(1, total + length_adjust) if length_adjust < 0 else 0
    if keep >= total:
        keep = 0
    up_left = adj['up_is_left']
    steps_left, steps_right = (steps_up, steps_down) if up_left else (steps_down, steps_up)
    faces, eff_left, eff_right = strip_region(chain, adj['closed'], rails, adj['seed_pos'],
                                              steps_left, steps_right, keep)

    for i in adj['added']:
        if i < len(bm.faces):
            bm.faces[i].select_set(False)
    pre = adj['pre']
    adj['added'] = {f.index for f in faces if f.index not in pre}
    for f in faces:
        f.select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
    adj['up'], adj['down'] = (eff_left, eff_right) if up_left else (eff_right, eff_left)
    adj['keep'] = keep
    adj['params'].update({'steps_up': adj['up'], 'steps_down': adj['down'], 'length_adjust': length_adjust})
    return True


class MESH_OT_mirror_face_loop_select(bpy.types.Operator):
    """면 모드 Alt+클릭: Blender 기본 면 루프 선택 그대로 + N 패널에서 폭(옆 줄)·길이(면 수) 조절"""
    bl_idname = "mesh.mirror_face_loop_select"
    bl_label = "Mirror Face Loop Select"
    bl_options = {'UNDO'}

    extend: BoolProperty(name="Extend", default=False)

    @classmethod
    def poll(cls, context):
        # 면 전용 모드에서만 동작한다. (엣지/버텍스 모드는 MESH_OT_mirror_loop_select 가 맡는다)
        # 끔 단계에서는 poll 이 실패해 Blender 기본 Alt+클릭이 그대로 동작한다.
        return context.mode == 'EDIT_MESH' and extension_enabled(context) and face_only_mode(context)

    def invoke(self, context, event):
        state.reset_wheel()
        state.reset_adjust()
        objs = list(context.objects_in_mode_unique_data)
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        before = {ob: selected_face_indices(bmesh.from_edit_mesh(ob.data)) for ob in objs}

        # 선택은 Blender 기본 면 루프 선택이 그대로 만든다. 우리는 그 결과를 읽어서 N 패널이 조절할 수 있게 기억만 한다.
        result = bpy.ops.mesh.loop_select('INVOKE_DEFAULT', extend=self.extend, deselect=False, toggle=False)
        if 'FINISHED' not in result:
            return {'CANCELLED'}

        for ob in objs:
            bm = bmesh.from_edit_mesh(ob.data)
            ensure_tables(bm)
            after = selected_face_indices(bm)
            s0 = after if not self.extend else after - before[ob]
            if not s0:
                continue
            strip = order_strip([bm.faces[i] for i in s0])
            if strip is None:
                return {'FINISHED'}          # 줄이 아니면(삼각형 섞임 등) 기본 결과만 남긴다
            chain, closed = strip
            edges = {e.index for f in chain for e in f.edges}
            seed = pick_seed(context, ob, bm, edges, mouse)
            seed_pos = self._nearest_pos(context, ob, chain, seed, mouse)
            rails = strip_rails(chain, closed, seed)
            if rails is None:
                return {'FINISHED'}
            up_is_left = True
            if len(rails) > seed_pos:
                lm, rm = screen_mid(context, ob, rails[seed_pos][0]), screen_mid(context, ob, rails[seed_pos][1])
                if lm is not None and rm is not None:
                    up_is_left = screen_score(lm - rm) >= 0.0
            pre = set() if not self.extend else set(before[ob])
            state.set_adjust({
                'mode': 'FACE', 'ob': ob.name, 'counts': mesh_counts(bm), 'seed': seed.index,
                'chain': [f.index for f in chain], 'closed': closed, 'seed_pos': seed_pos,
                'up_is_left': up_is_left, 'pre': pre, 'added': {i for i in s0 if i not in pre},
                'up': 0, 'down': 0, 'keep': 0, 'total': len(chain),
                'params': {'steps_up': 0, 'steps_down': 0, 'length_adjust': 0},
            })
            break
        redraw_3d(context)
        return {'FINISHED'}

    @staticmethod
    def _nearest_pos(context, ob, chain, seed, mouse):
        """클릭한 곳에 가장 가까운 면의 줄 안 위치. 화면 좌표를 못 구하면 씨앗 엣지에 닿은 첫 면."""
        region, rv3d = context.region, context.region_data
        best, best_d = None, None
        if region is not None and rv3d is not None:
            mw = ob.matrix_world
            for i, f in enumerate(chain):
                p = view3d_utils.location_3d_to_region_2d(region, rv3d, mw @ f.calc_center_median())
                if p is not None:
                    d = (p - mouse).length
                    if best_d is None or d < best_d:
                        best, best_d = i, d
            if best is not None:
                return best
        return next((i for i, f in enumerate(chain) if seed in f.edges), 0)


class MESH_OT_mirror_face_adjust(bpy.types.Operator):
    """면 모드 고정 패널의 숫자칸: 기본 면 루프 선택 결과의 폭(양옆 줄 수)과 길이(면 수)를 바꿔서 다시 선택한다"""
    bl_idname = "mesh.mirror_face_adjust"
    bl_label = "Mirror Face Adjust"
    bl_options = {'UNDO'}

    target: EnumProperty(
        name="Target",
        items=(
            ('UP', "폭: 위쪽", "화면 위쪽으로 나란한 면 줄을 더 붙이거나 줄인다"),
            ('DOWN', "폭: 아래쪽", "화면 아래쪽으로 나란한 면 줄을 더 붙이거나 줄인다"),
            ('LENGTH', "길이", "줄을 따라 몇 칸까지 고를지"),
            ('RESET', "초기화", "Blender 기본 면 루프 선택 결과로 되돌린다"),
        ),
        default='UP',
    )
    delta: IntProperty(name="Delta", default=1, min=-ADJUST_STEP_MAX, max=ADJUST_STEP_MAX)
    value: IntProperty(name="Value", default=-1, min=-1, options={'SKIP_SAVE'})   # 0 이상이면 delta 대신 이 값으로 정한다

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH' and face_only_mode(context)
                and state.adjust is not None and state.adjust.get('mode') == 'FACE')

    def execute(self, context):
        adj = state.adjust
        if not adjust_valid(context):
            state.reset_adjust()
            self.report({'WARNING'}, "선택이 바뀌었습니다. Alt+클릭으로 면 루프를 다시 선택하세요")
            redraw_3d(context)
            return {'CANCELLED'}

        up, down, length = adj['up'], adj['down'], adj['params']['length_adjust']
        absolute = self.value >= 0
        t, d = self.target, self.delta
        if t == 'UP':
            up = min(max(self.value if absolute else up + d, 0), ADJUST_STEP_MAX)
        elif t == 'DOWN':
            down = min(max(self.value if absolute else down + d, 0), ADJUST_STEP_MAX)
        elif t == 'LENGTH':
            lowest = max(ADJUST_LENGTH_MIN, -(adj['total'] - 1))
            want = (self.value - adj['total']) if absolute else length + d
            length = min(max(want, lowest), 0)
        else:
            up = down = length = 0

        if not apply_strip(context, context.edit_object, adj, up, down, length):
            self.report({'WARNING'}, "면 줄을 읽을 수 없어 조절하지 못했습니다")
            state.reset_adjust()
            return {'CANCELLED'}
        redraw_3d(context)
        return {'FINISHED'}


class MESH_OT_mirror_loop_between(bpy.types.Operator):
    """Alt+클릭으로 고른 시작 루프와 이 키로 클릭한 끝 루프 사이를 전부(또는 루프만) 선택"""
    bl_idname = "mesh.mirror_loop_between"
    bl_label = "Mirror Loop Between"
    bl_options = {'UNDO'}      # execute 가 없어 '마지막 작업' 패널은 어차피 뜨지 않는다. 옵션은 키맵(select_faces)과 설정(N 패널)으로 준다.

    threshold: FloatProperty(
        name="Mirror Threshold",
        description="버텍스가 미러 평면 위에 있다고 보는 거리 허용 오차",
        default=1e-4, min=0.0, precision=6,
    )
    max_steps: IntProperty(
        name="Max Steps",
        description="시작 루프에서 한 방향으로 최대 몇 칸까지 찾아볼지",
        default=1000, min=1, max=100000,
    )
    select_faces: BoolProperty(
        name="Select Faces",
        description="루프와 루프 사이의 면까지 선택한다. 끄면 루프(엣지)만 선택한다",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        # 끔 단계에서는 poll 이 실패해 Blender 기본 Ctrl+Alt+클릭(링 선택)이 그대로 동작한다.
        return context.mode == 'EDIT_MESH' and extension_enabled(context)

    def invoke(self, context, event):
        state.reset_wheel()  # 휠 확장 상태는 버린다.
        state.reset_adjust()  # 선택이 바뀌므로 고정 패널의 조절 상태는 버린다.

        objs = list(context.objects_in_mode_unique_data)
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))

        # 1) 기본 루프 선택으로 '클릭한 루프'를 알아낸 뒤, 선택은 원래대로 되돌린다.
        snap = snapshot_selection(objs)
        result = bpy.ops.mesh.loop_select(
            'INVOKE_DEFAULT', extend=False, deselect=False, toggle=False)

        clicked = None
        if 'FINISHED' in result:
            for ob in objs:
                bm = bmesh.from_edit_mesh(ob.data)
                bm.edges.ensure_lookup_table()
                bm.edges.index_update()
                sel = {e.index for e in bm.edges if e.select}
                if sel:
                    clicked = (ob, pick_seed(context, ob, bm, sel, mouse).index)
                    break

        restore_selection(objs, snap)
        if clicked is None:
            return {'CANCELLED'}

        ob, seed_b_idx = clicked
        bm = bmesh.from_edit_mesh(ob.data)
        ensure_tables(bm)

        max_angle, dih = geometry_options(context)      # 최대 꺾임 각도/다이헤드럴은 N 패널 설정
        cos_limit = math.cos(max_angle)
        if dih:
            bm.normal_update()   # 다이헤드럴 계산에 쓰는 면 법선을 최신으로

        # 1단계에서는 미러 반대편을 확장하지 않는다.
        axes = get_mirror_axes(ob) if use_mirror_extension(context) else []
        seed_b = bm.edges[seed_b_idx]
        # 클릭한 루프는 Alt+클릭(루프 선택)과 같은 규칙으로 정한다. (쪼개진 면의 테두리 확정, 미러 확장 포함: edge_range.selected_loop)
        sl = selected_loop(bm, seed_b, cos_limit, dih, 0, axes, self.threshold)
        loop_b, mirror_b = sl['loop'], sl['mirror']
        target = {e.index for e in loop_b} | {e.index for e in mirror_b}

        # 2) 시작 루프(앵커) 확인
        counts = mesh_counts(bm)
        anchor = state.anchor
        valid = (anchor is not None and anchor['ob'] == ob.name and anchor['counts'] == counts
                 and anchor['seed'] < len(bm.edges) and not bm.edges[anchor['seed']].hide)
        # 선택이 하나도 없으면(Alt+A 로 지운 뒤 등) 이전 시작 루프는 버리고 새로 시작한다.
        # (이전 루프가 남아 있으면 첫 클릭이 엉뚱한 루프와의 '사이'로 처리되어 경고가 뜨거나 먼 곳이 선택된다.)
        # 단, 면 모드의 첫 클릭처럼 아무것도 선택하지 않고 '대기'만 하는 시작 루프는 선택이 없어도 유효하다.
        if valid and anchor.get('selected', True) and not any(snap[ob]):
            valid = False
        # 대기 중인 시작 루프(면 모드에서 지정)는 면 모드에서만 이어서 쓴다.
        if valid and not anchor.get('selected', True) and not face_only_mode(context):
            valid = False

        # 면 모드에서는 엣지를 고르는 게 의미가 없다. 사이의 면만 고르고, 면을 못 찾으면 아무것도 고르지 않는다.
        face_only = face_only_mode(context)
        selected = set() if face_only else target
        faces = set()
        if not valid:
            self.report({'INFO'}, "시작 루프를 지정했습니다 (색 선으로 표시). 같은 방향의 다른 루프를 클릭하면 그 사이가 선택됩니다. Esc = 취소")
        else:
            # 3) 시작 루프에서 걸어가며 끝 루프와 만나는 곳까지 채운다.
            loops, steps = find_between(
                bm, bm.edges[anchor['seed']], target, axes, self.threshold,
                cos_limit, dih, self.max_steps)
            if loops is None:
                self.report({'WARNING'},
                            "두 루프 사이를 잇는 경로를 찾지 못했습니다 (삼각형/N-gon 으로 끊기거나 다른 덩어리일 수 있음). %s"
                            % ("선택하지 않습니다." if face_only else "클릭한 루프만 선택합니다."))
            else:
                selected = set() if face_only else set(target)
                for s in loops:
                    if not face_only:
                        selected |= s
                if self.select_faces or face_only:
                    faces = strip_faces(bm, loops)
                if faces:
                    self.report({'INFO'}, "루프 %d개, 면 %d개 선택" % (steps + 1, len(faces)))
                else:
                    self.report({'INFO'}, "루프 %d개 선택" % (steps + 1))

        for i in selected:
            bm.edges[i].select_set(True)
        for f in faces:
            f.select_set(True)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)

        # 끝 루프를 새 앵커로 -> 이어서 Ctrl+Alt+클릭(또는 Shift+Alt+클릭)하면 B~C 구간이 선택된다.
        pending = not (selected or faces)       # 면 모드 첫 클릭처럼 아무것도 선택하지 않고 대기만 하는 경우
        state.set_anchor(ob.name, seed_b.index, counts, selected=not pending, loop=target if pending else None)
        redraw_3d(context)
        return {'FINISHED'}


class MESH_OT_mirror_face_similar(bpy.types.Operator):
    """클릭한(또는 선택한) 면과 같은 모양의 평평한 면 영역을 전부 선택 (회전·거울·이동 무관)"""
    bl_idname = "mesh.mirror_face_similar"
    bl_label = "Select Similar Shape"
    bl_options = {'UNDO'}      # 옵션(크기 무시, 허용 오차 등)은 사이드바(N) 'Mirror Loop' 탭의 '같은 모양 면 선택' 섹션에 있다.

    pick: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)
    seed_object: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})     # 마우스로 고른 면
    seed_face: IntProperty(default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    refresh: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)   # 패널 옵션이 바뀌어 같은 기준 면으로 다시 찾기

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and extension_enabled(context)

    def _pick_face(self, context, event, objs):
        """마우스 아래 가장 가까운 면. (오브젝트 이름, 면 인덱스) 또는 None."""
        region, rv3d = context.region, context.region_data
        if region is None or rv3d is None:
            return None
        coord = (event.mouse_region_x, event.mouse_region_y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        vec = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        best = None
        for ob in objs:
            bm = bmesh.from_edit_mesh(ob.data)
            bm.faces.ensure_lookup_table()
            inv = ob.matrix_world.inverted()
            o = inv @ origin
            d = (inv.to_3x3() @ vec).normalized()
            hit = BVHTree.FromBMesh(bm).ray_cast(o, d)
            if hit[0] is None or bm.faces[hit[2]].hide:
                continue
            dist = (ob.matrix_world @ hit[0] - origin).length
            if best is None or dist < best[0]:
                best = (dist, ob.name, hit[2])
        return None if best is None else (best[1], best[2])

    def invoke(self, context, event):
        if self.pick:
            hit = self._pick_face(context, event, list(context.objects_in_mode_unique_data))
            if hit is None:
                return {'PASS_THROUGH'}      # 면이 없는 곳이면 Blender 기본 동작에 넘긴다
            self.seed_object, self.seed_face = hit
        return self.execute(context)

    def execute(self, context):
        opt = similar_options(context)
        objs = list(context.objects_in_mode_unique_data)

        # 0) 옵션이 바뀌어 다시 찾는 경우: 처음 찾기 전 선택으로 돌아가 같은 기준 면에서 다시 시작한다.
        prev = state.similar if self.refresh else None
        if self.refresh:
            if prev is None or not similar_valid(context):
                state.reset_similar()
                return {'CANCELLED'}
            restore_selection(objs, prev['snap'])
            snap = prev['snap']
        else:
            snap = snapshot_selection(objs)

        # 1) 기준이 되는 면: 다시 찾기면 처음 기준 면, 아니면 마우스로 고른 면, 없으면 선택한 면
        #    (bm 래퍼를 들고 있어야 한다. 변수를 덮어써서 래퍼가 해제되면 이미 모은 면 참조도 무효가 된다.)
        bms = [bmesh.from_edit_mesh(ob.data) for ob in objs]
        seed_faces, seed_idx = [], {}
        for ob, bm in zip(objs, bms):
            bm.faces.ensure_lookup_table()
            if prev is not None:
                picked = [bm.faces[i] for i in prev['seeds'].get(ob.name, ()) if i < len(bm.faces)]
            elif self.seed_face >= 0:
                picked = ([bm.faces[self.seed_face]]
                          if ob.name == self.seed_object and self.seed_face < len(bm.faces) else [])
            else:
                picked = [f for f in bm.faces if f.select and not f.hide]
            seed_faces.extend(picked)
            seed_idx[ob.name] = [f.index for f in picked]
        if not seed_faces:
            self.report({'WARNING'}, "기준이 될 면을 선택하거나 Alt+더블클릭으로 면을 가리키세요")
            return {'CANCELLED'}

        # 2) 기준 면의 모양
        patch_mode = opt.mode == 'PATCH'
        shapes = []
        if not patch_mode:
            # 평평한 영역 외곽선: 같은 영역에 속한 면은 한 번만
            seen = set()
            for f in seed_faces:
                if f in seen:
                    continue
                isl = flat_island(f, opt.flat_angle) if opt.use_island else {f}
                seen |= isl
                shp = island_shape(isl, opt.scale_invariant)
                if shp is not None:
                    shapes.append(shp)
            if not shapes:
                self.report({'WARNING'}, "이 면의 외곽선을 읽을 수 없습니다 (점으로만 맞닿는 면이거나 길이가 0인 변)")
                return {'CANCELLED'}
            del seen
        if patch_mode:
            # 경계 엣지 루프 방식: 기준 면이 속한 덩어리의 경계 곡선을 읽는다.
            shapes = []
            for ob, bm in zip(objs, bms):
                idx = [i for i in seed_idx.get(ob.name, ()) if i < len(bm.faces)]
                if idx:
                    shapes.extend(patch_seed_shapes(bm, [bm.faces[i] for i in idx], opt.patch_angle))
            if not shapes:
                why = None
                for ob, bm in zip(objs, bms):
                    idx = [i for i in seed_idx.get(ob.name, ()) if i < len(bm.faces)]
                    if idx:
                        why = diagnose_seed(bm, [bm.faces[i] for i in idx], opt.patch_angle)
                        if why:
                            break
                self.report({'WARNING'}, "이 면의 경계 루프를 읽을 수 없습니다. " + (why or "경계가 한 줄로 이어지지 않거나 크기가 0 입니다"))
                return {'CANCELLED'}

        # 3) 모든 편집 중인 오브젝트에서 같은 모양 찾기
        del seed_faces
        if not opt.extend:
            bpy.ops.mesh.select_all(action='DESELECT')
        regions = faces_total = 0
        found_idx, counts = {}, {}
        for ob in objs:
            bm = bmesh.from_edit_mesh(ob.data)
            bm.faces.ensure_lookup_table()
            bm.normal_update()
            if patch_mode:
                found = similar_patches(bm, shapes, opt.patch_angle, opt.curve_tolerance, opt.scale_invariant)
            else:
                found = similar_islands(bm, shapes, opt.flat_angle, opt.length_tolerance,
                                        opt.angle_tolerance, opt.scale_invariant, opt.use_island)
            idx = set()
            for isl in found:
                for f in isl:
                    f.select_set(True)
                    idx.add(f.index)
                regions += 1
                faces_total += len(isl)
            found_idx[ob.name] = idx
            counts[ob.name] = mesh_counts(bm)
            bm.select_flush_mode()
            bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)

        state.set_similar({'seeds': seed_idx, 'snap': snap, 'found': found_idx, 'counts': counts,
                           'regions': regions, 'faces': faces_total})
        self.report({'INFO'}, "같은 모양 %d곳 (면 %d개) 선택" % (regions, faces_total))
        redraw_3d(context)
        return {'FINISHED'}


class MESH_OT_mirror_pending_cancel(bpy.types.Operator):
    """(내부용) 면 모드 사이 선택에서 대기 중인 시작 루프를 Esc 로 취소"""
    bl_idname = "mesh.mirror_pending_cancel"
    bl_label = "Cancel Pending Start Loop"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        # 대기 중이 아닐 때는 poll 이 실패해서 Esc 가 평소대로 동작한다.
        a = state.anchor
        return (context.mode == 'EDIT_MESH' and face_only_mode(context)
                and a is not None and not a.get('selected', True))

    def execute(self, context):
        state.anchor = None
        redraw_3d(context)
        self.report({'INFO'}, "시작 루프 지정을 취소했습니다")
        return {'FINISHED'}


def draw_settings(layout, settings):
    """패널/메뉴에 끼워 넣는 면 모드 사이 선택 설정: 시작 루프 표시 색/두께."""
    p = prefs.get_prefs()
    if p is None:
        return
    col = layout.column(align=True)
    col.prop(p, "anchor_color")
    col.prop(p, "anchor_width")


# (idname, 키, 값, 수식키, 오퍼레이터 속성, 환경설정에 보일 제목, 한 줄 설명)
KEYMAPS = (
    # 면 모드 Alt+클릭: Blender 기본 면 루프 선택(결과는 그대로) + N 패널 조절. 엣지/버텍스 모드의 Alt+클릭(edge_ops)과 짝이다.
    (MESH_OT_mirror_face_loop_select.bl_idname, 'LEFTMOUSE', 'PRESS', {'alt': True}, {},
     "루프 선택 (면 모드)",
     "Blender 기본 면 루프 선택 + 고정 패널에서 폭·길이 조절"),
    (MESH_OT_mirror_loop_between.bl_idname, 'LEFTMOUSE', 'PRESS', {'ctrl': True, 'alt': True},
     {'select_faces': True},
     "사이 전부 선택",
     "시작 루프~클릭한 루프 사이의 모든 버텍스·엣지·면"),
    (MESH_OT_mirror_loop_between.bl_idname, 'LEFTMOUSE', 'PRESS', {'shift': True, 'alt': True},
     {'select_faces': False},
     "사이 루프만 선택",
     "시작 루프~클릭한 루프 사이의 루프(엣지)만, 면은 제외"),
    (MESH_OT_mirror_face_similar.bl_idname, 'LEFTMOUSE', 'DOUBLE_CLICK', {'alt': True},
     {'pick': True},
     "같은 모양 면 선택",
     "클릭한 면과 같은 모양(회전·거울 무관)의 평면을 전부 선택"),
    # 내부용(제목 없음 = 목록에 안 보임): 면 모드에서 대기 중인 시작 루프를 Esc 로 취소
    (MESH_OT_mirror_pending_cancel.bl_idname, 'ESC', 'PRESS', {}, {}, None, None),
)

classes = (
    MESH_OT_mirror_face_loop_select,
    MESH_OT_mirror_face_adjust,
    MESH_OT_mirror_loop_between,
    MESH_OT_mirror_face_similar,
    MESH_OT_mirror_pending_cancel,
)
