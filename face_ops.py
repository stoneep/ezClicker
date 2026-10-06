"""
face_ops.py — [면] 오퍼레이터와 키맵.

  Ctrl+Alt+클릭  : 마지막으로 고른 루프 ~ 클릭한 루프 사이 '전부' 선택
                   (사이의 모든 버텍스·엣지·면. select_faces=True)
  Shift+Alt+클릭 : 같은 구간에서 '루프(엣지)만' 선택 (사이의 면·루프와 직각인 테두리는 제외. select_faces=False)
                   -> 둘 다 MESH_OT_mirror_loop_between (키맵의 select_faces 값만 다르다)

  면 모드: 첫 클릭은 아무것도 선택하지 않고 시작 루프만 지정한다. 지정한 루프는 색 선으로 표시되고(overlay.py,
           색/두께는 환경설정), 같은 방향의 다른 루프를 클릭하면 그 사이의 면이 선택된다. Esc 로 지정을 취소한다.

  면 모드 고정 패널: 색 선(시작 루프)이 떠 있는 동안 사이드바(N) 'Mirror Loop' 탭에서 그 선을 가운데로
           위/아래 면 줄 수와 길이를 숫자칸(드래그)으로 정해 면을 고른다.   -> MESH_OT_mirror_face_adjust
           (엣지·버텍스 모드의 폭/길이와 같은 규칙, 엣지 대신 면을 고른다. 선택을 풀거나 Esc 로 취소하면 쉰다.)

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
from types import SimpleNamespace

import bpy
import bmesh
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import prefs, state
from .common import (adjust_valid, ensure_tables, face_only_mode, get_mirror_axes, mesh_counts, pick_seed,
                     redraw_3d, restore_selection, snapshot_selection)
from .edge_core import find_mirror_edges, walk_loop
from .edge_range import (ADJUST_LENGTH_MIN, ADJUST_STEP_MAX, build_sides, find_between, new_wheel_state,
                         selected_loop)
from .face_core import init_faces, strip_faces
from .face_shape import (DEFAULT_ANGLE_TOL, DEFAULT_FLAT, DEFAULT_LEN_TOL, flat_island,
                         island_shape, similar_islands)
from .settings import extension_enabled, use_mirror_extension


def run_face_strip(context, p, prev_added):
    """
    면 모드: 대기 중인 시작 루프(색 선)를 가운데로 위/아래 p.steps_up / p.steps_down 줄의 면을 고른다.
    (엣지 모드의 '폭'과 같은 규칙: 위/아래 방향, 길이 줄이기, 미러 확장을 그대로 쓴다. 다만 엣지 대신 면을 고른다.)

    p          : seed_object, seed_edge, steps_up, steps_down, length_adjust, max_angle, threshold, use_dihedral
    prev_added : 지난번에 우리가 고른 면 인덱스. 먼저 풀고 다시 고른다. (원래 선택돼 있던 면은 건드리지 않는다.)
    반환: ('FINISHED' 또는 'CANCELLED', 경고 메시지 또는 None)
    성공하면 고정 패널용 조절 상태(state.adjust, mode='FACE')를 갱신한다.
    """
    state.reset_adjust()
    ob = next((o for o in context.objects_in_mode_unique_data if o.name == p.seed_object), None)
    if ob is None or p.seed_edge < 0:
        return 'CANCELLED', None
    bm = bmesh.from_edit_mesh(ob.data)
    ensure_tables(bm)
    if p.seed_edge >= len(bm.edges):
        return 'CANCELLED', None

    cos_limit = math.cos(p.max_angle)
    dih = p.use_dihedral
    if dih:
        bm.normal_update()
    seed = bm.edges[p.seed_edge]

    for i in prev_added:
        if i < len(bm.faces):
            bm.faces[i].select_set(False)

    axes = get_mirror_axes(ob) if use_mirror_extension(context) else []
    sl = selected_loop(bm, seed, cos_limit, dih, p.length_adjust, axes, p.threshold)
    core = {e.index for e in sl['loop']} | {e.index for e in sl['mirror']}
    st = new_wheel_state(ob, bm, seed, core, sl['axes'], p.threshold, cos_limit, dih, sl['full'])
    st['keep'], st['ref_dir'] = sl['keep'], sl['ref_dir']
    st['faces'][0] = init_faces(context, ob, seed)
    lo, hi = build_sides(bm, st, p.steps_up, p.steps_down)

    faces = set()
    if hi > 0:
        faces |= strip_faces(bm, [st['loops'][k] for k in range(0, hi + 1)])
    if lo < 0:
        faces |= strip_faces(bm, [st['loops'][-k] for k in range(0, -lo + 1)])
    added = set()
    for f in faces:
        if not f.select:
            added.add(f.index)
            f.select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)

    params = {
        'seed_object': p.seed_object, 'seed_edge': p.seed_edge, 'do_select': True, 'replace': False,
        'use_ring': False, 'steps_up': hi, 'steps_down': -lo, 'length_adjust': p.length_adjust,
        'max_angle': p.max_angle, 'threshold': p.threshold, 'use_dihedral': p.use_dihedral,
    }
    state.set_adjust({'mode': 'FACE', 'ob': ob.name, 'seed': seed.index, 'params': params,
                      'counts': mesh_counts(bm), 'added': added, 'up': hi, 'down': -lo,
                      'keep': sl['keep'], 'total': sl['total']})
    return 'FINISHED', None


class MESH_OT_mirror_face_adjust(bpy.types.Operator):
    """면 모드 고정 패널의 숫자칸: 시작 루프(색 선) 위/아래로 면을 몇 줄 고를지, 길이를 바꿔서 다시 선택한다"""
    bl_idname = "mesh.mirror_face_adjust"
    bl_label = "Mirror Face Adjust"
    bl_options = {'UNDO'}

    target: EnumProperty(
        name="Target",
        items=(
            ('UP', "폭: 위쪽", "시작 루프 위쪽으로 면을 몇 줄 고를지"),
            ('DOWN', "폭: 아래쪽", "시작 루프 아래쪽으로 면을 몇 줄 고를지"),
            ('LENGTH', "길이", "루프를 따라 몇 칸까지 고를지"),
            ('RESET', "초기화", "고른 면을 풀고 시작 루프만 남긴다"),
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
            self.report({'WARNING'}, "시작 루프(색 선)가 없거나 선택이 바뀌었습니다. Ctrl+Alt+클릭으로 시작 루프를 다시 지정하세요")
            redraw_3d(context)
            return {'CANCELLED'}

        p = SimpleNamespace(**adj['params'])
        absolute = self.value >= 0
        t, d = self.target, self.delta
        if t == 'UP':
            p.steps_up = min(max(self.value if absolute else p.steps_up + d, 0), ADJUST_STEP_MAX)
        elif t == 'DOWN':
            p.steps_down = min(max(self.value if absolute else p.steps_down + d, 0), ADJUST_STEP_MAX)
        elif t == 'LENGTH':
            lowest = max(ADJUST_LENGTH_MIN, -(adj['total'] - 1)) if adj['total'] else 0
            want = (self.value - adj['total']) if absolute else p.length_adjust + d
            p.length_adjust = min(max(want, lowest), 0)
        else:
            p.steps_up = p.steps_down = p.length_adjust = 0

        status, msg = run_face_strip(context, p, adj['added'])
        if msg:
            self.report({'WARNING'}, msg)
        redraw_3d(context)
        return {status}


class MESH_OT_mirror_loop_between(bpy.types.Operator):
    """Alt+클릭으로 고른 시작 루프와 이 키로 클릭한 끝 루프 사이를 전부(또는 루프만) 선택"""
    bl_idname = "mesh.mirror_loop_between"
    bl_label = "Mirror Loop Between"
    bl_options = {'REGISTER', 'UNDO'}

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
        state.reset_adjust()  # 고정 패널의 조절 상태도 버린다. (대기 중인 시작 루프를 새로 지정하면 아래에서 다시 만든다)

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

        cos_limit = math.cos(self.max_angle)
        dih = self.use_dihedral
        if dih:
            bm.normal_update()   # 다이헤드럴 계산에 쓰는 면 법선을 최신으로

        # 1단계에서는 미러 반대편을 확장하지 않는다.
        axes = get_mirror_axes(ob) if use_mirror_extension(context) else []
        seed_b = bm.edges[seed_b_idx]
        loop_b = walk_loop(seed_b, cos_limit, dih)
        mirror_b = find_mirror_edges(bm, loop_b, axes, self.threshold, cos_limit, dih) if axes else []
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
        if pending and face_only:
            # 고정 패널에서 이 색 선을 가운데로 위/아래 면을 고를 수 있게 한다. (처음엔 고른 면 없음)
            state.set_adjust({
                'mode': 'FACE', 'ob': ob.name, 'seed': seed_b.index, 'counts': counts, 'added': set(),
                'up': 0, 'down': 0, 'keep': 0, 'total': len(loop_b),
                'params': {
                    'seed_object': ob.name, 'seed_edge': seed_b.index, 'do_select': True, 'replace': False,
                    'use_ring': False, 'steps_up': 0, 'steps_down': 0, 'length_adjust': 0,
                    'max_angle': self.max_angle, 'threshold': self.threshold, 'use_dihedral': self.use_dihedral,
                },
            })
        redraw_3d(context)
        return {'FINISHED'}


class MESH_OT_mirror_face_similar(bpy.types.Operator):
    """클릭한(또는 선택한) 면과 같은 모양의 평평한 면 영역을 전부 선택 (회전·거울·이동 무관)"""
    bl_idname = "mesh.mirror_face_similar"
    bl_label = "Select Similar Shape"
    bl_options = {'REGISTER', 'UNDO'}

    extend: BoolProperty(
        name="Extend",
        description="기존 선택을 지우지 않고 찾은 면을 추가한다",
        default=False,
    )
    scale_invariant: BoolProperty(
        name="Ignore Size",
        description="크기가 달라도 모양이 같으면(닮음) 찾는다",
        default=False,
    )
    use_island: BoolProperty(
        name="Flat Regions",
        description="이웃한 평평한 면들을 한 덩어리의 모양으로 비교한다. 끄면 면 하나씩 비교한다",
        default=True,
    )
    length_tolerance: FloatProperty(
        name="Length Tolerance",
        description="변 길이가 이 비율 이내로 다르면 같다고 본다 (0.01 = 1%)",
        default=DEFAULT_LEN_TOL, min=0.0, max=0.5, precision=3,
    )
    angle_tolerance: FloatProperty(
        name="Angle Tolerance",
        description="꺾임 각이 이 이내로 다르면 같다고 본다",
        default=DEFAULT_ANGLE_TOL, min=0.0, max=math.radians(30.0), subtype='ANGLE',
    )
    flat_angle: FloatProperty(
        name="Flat Angle",
        description="이웃한 면의 법선 차이가 이 이내면 같은 평면으로 묶는다",
        default=DEFAULT_FLAT, min=0.0, max=math.radians(30.0), subtype='ANGLE',
    )
    pick: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)
    seed_object: StringProperty(options={'HIDDEN'})     # 마우스로 고른 면 (다시 실행/리두에서 쓴다)
    seed_face: IntProperty(default=-1, options={'HIDDEN'})

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
        objs = list(context.objects_in_mode_unique_data)

        # 1) 기준이 되는 면: 마우스로 고른 면, 없으면 선택한 면
        #    (bm 래퍼를 들고 있어야 한다. 변수를 덮어써서 래퍼가 해제되면 이미 모은 면 참조도 무효가 된다.)
        bms = [bmesh.from_edit_mesh(ob.data) for ob in objs]
        seed_faces = []
        for ob, bm in zip(objs, bms):
            bm.faces.ensure_lookup_table()
            if self.seed_face >= 0:
                if ob.name == self.seed_object and self.seed_face < len(bm.faces):
                    seed_faces.append(bm.faces[self.seed_face])
            else:
                seed_faces.extend(f for f in bm.faces if f.select and not f.hide)
        if not seed_faces:
            self.report({'WARNING'}, "기준이 될 면을 선택하거나 Alt+더블클릭으로 면을 가리키세요")
            return {'CANCELLED'}

        # 2) 기준 면의 모양 (같은 영역에 속한 면은 한 번만)
        shapes, seen = [], set()
        for f in seed_faces:
            if f in seen:
                continue
            isl = flat_island(f, self.flat_angle) if self.use_island else {f}
            seen |= isl
            shp = island_shape(isl, self.scale_invariant)
            if shp is not None:
                shapes.append(shp)
        if not shapes:
            self.report({'WARNING'}, "이 면의 외곽선을 읽을 수 없습니다 (점으로만 맞닿는 면이거나 길이가 0인 변)")
            return {'CANCELLED'}

        # 3) 모든 편집 중인 오브젝트에서 같은 모양 찾기
        del seed_faces, seen
        if not self.extend:
            bpy.ops.mesh.select_all(action='DESELECT')
        regions = faces_total = 0
        for ob in objs:
            bm = bmesh.from_edit_mesh(ob.data)
            bm.faces.ensure_lookup_table()
            bm.normal_update()
            found = similar_islands(bm, shapes, self.flat_angle, self.length_tolerance,
                                    self.angle_tolerance, self.scale_invariant, self.use_island)
            for isl in found:
                for f in isl:
                    f.select_set(True)
                regions += 1
                faces_total += len(isl)
            bm.select_flush_mode()
            bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)

        self.report({'INFO'}, "같은 모양 %d곳 (면 %d개) 선택" % (regions, faces_total))
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
    MESH_OT_mirror_loop_between,
    MESH_OT_mirror_face_adjust,
    MESH_OT_mirror_face_similar,
    MESH_OT_mirror_pending_cancel,
)
