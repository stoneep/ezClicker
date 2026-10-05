"""
face_ops.py — [면] 오퍼레이터와 키맵.

  Ctrl+Alt+클릭  : 마지막으로 고른 루프 ~ 클릭한 루프 사이 '전부' 선택
                   (사이의 모든 버텍스·엣지·면. select_faces=True)
  Shift+Alt+클릭 : 같은 구간에서 '루프(엣지)만' 선택 (사이의 면·루프와 직각인 테두리는 제외. select_faces=False)
                   -> 둘 다 MESH_OT_mirror_loop_between (키맵의 select_faces 값만 다르다)

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
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import state
from .common import (ensure_tables, face_only_mode, get_mirror_axes, mesh_counts, pick_seed,
                     restore_selection, snapshot_selection)
from .edge_core import find_mirror_edges, walk_loop
from .edge_range import find_between
from .face_core import strip_faces
from .face_shape import (DEFAULT_ANGLE_TOL, DEFAULT_FLAT, DEFAULT_LEN_TOL, flat_island,
                         island_shape, similar_islands)
from .settings import extension_enabled, use_mirror_extension


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

        # 면 모드에서는 엣지를 고르는 게 의미가 없다. 사이의 면만 고르고, 면을 못 찾으면 아무것도 고르지 않는다.
        face_only = face_only_mode(context)
        selected = set() if face_only else target
        faces = set()
        if not valid:
            self.report({'INFO'}, "먼저 시작 루프를 지정해야 합니다. 지금 클릭한 루프를 시작 루프로 지정했습니다. 다른 루프를 한 번 더 클릭하세요.")
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
        state.set_anchor(ob.name, seed_b.index, counts)
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
)

classes = (
    MESH_OT_mirror_loop_between,
    MESH_OT_mirror_face_similar,
)
