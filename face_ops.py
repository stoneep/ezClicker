"""
face_ops.py — [사이 선택] 오퍼레이터와 키맵. (파일 이름은 face 지만 엣지 모드 동작도 여기서 처리한다.)

  Ctrl+Alt+클릭 : 마지막으로 고른 루프 ~ 클릭한 루프 사이 선택  -> MESH_OT_mirror_loop_between
  Alt+휠 / Ctrl+휠 (Ctrl+Alt+클릭 직후) : 선택 범위의 바깥/안쪽 루프 추가/제거
                                          -> MESH_OT_mirror_face_step

선택 모드에 따라 '무엇이 선택되는지'가 다르다.

  엣지 모드 : 루프만 선택한다.
              시작 루프 A ~ 끝 루프 B 사이의 '루프 엣지'만 선택되고,
              루프를 잇는 가로대 엣지나 사이의 면은 선택하지 않는다.
              휠로 바깥/안쪽 루프를 한 줄씩 더하거나 뺀다. (역시 루프만)

  면 모드   : 루프 사이의 면까지 전부 선택한다.
              A ~ B 사이의 모든 면(= 사이를 잇는 모든 버텍스/엣지 포함)이 선택된다.
              휠로 바깥/안쪽 면 띠를 한 줄씩 더하거나 뺀다.

  (버텍스 모드는 엣지 모드와 같게 루프 엣지만 고른다. 단, 버텍스 모드에서는 Blender 가
   양 끝 버텍스가 선택된 엣지를 자동으로 선택하므로 가로대 엣지가 같이 켜진다.)

  1) Alt+클릭으로 시작 루프 A 를 고른다. (이게 앵커가 된다)
  2) Ctrl+Alt+클릭으로 끝 루프 B 를 누른다.
  -> A 에서 B 까지의 루프를 edge_range.find_between_chains 가 찾는다.
     면 모드이고 '면 선택' 옵션(기본 켬)이면 인접한 두 루프 사이의 면(face_range.new_face_wheel)도 모은다.
  -> 끝나면 B 가 새 앵커가 되어 C 를 Ctrl+Alt+클릭하면 B~C 가 이어서 선택된다.

  - 앵커가 없거나 사이를 못 찾으면
      면 모드   : 클릭한 루프 바로 옆 면 루프(마우스에 가까운 쪽)를 선택한다.
      엣지 모드 : 클릭한 루프만 선택한다. (이 루프에서 휠로 넓힐 수 있다)

  - 선택한 범위는 클릭 직후 휠로 바깥/안쪽으로 넓힐 수 있다. (face_range.py 참고)
      Alt+휠 업/다운   : 바깥·안쪽 루프를 동시에 추가/제거
      Ctrl+휠 업       : 바깥으로 추가 (안쪽이 늘어나 있으면 안쪽부터 제거)
      Ctrl+휠 다운     : 안쪽으로 추가 (바깥이 늘어나 있으면 바깥부터 제거)
    휠은 이 클릭 직후에만 poll 이 통과하므로, 그 외에는 기본 Alt+휠/Ctrl+휠 동작이 유지된다.
    (Ctrl+Alt 를 둘 다 누른 채로는 키맵이 맞지 않으니 Alt 를 떼고 Ctrl 만 누른 채로 돌린다.)

Blender 기본의 Ring 선택 키와 같다. 이 키맵이 우선한다.
(바꾸려면 Preferences > Keymap > Mesh 에서 Mirror Loop Between 을 수정)
끔 단계에서는 poll 이 실패해 Blender 기본 Ctrl+Alt+클릭(링 선택)이 그대로 동작한다.
"""

import math

import bpy
import bmesh
from bpy.props import BoolProperty, FloatProperty, IntProperty
from mathutils import Vector

from . import state
from .common import (ensure_tables, get_mirror_axes, is_face_mode, mesh_counts, pick_seed,
                     restore_selection, snapshot_selection)
from .edge_core import find_mirror_edges, walk_loop
from .edge_range import find_between_chains
from .face_range import (apply_faces, click_strip, face_state_valid, new_face_wheel,
                         single_loop, step_faces_both, step_faces_one)
from .settings import extension_enabled, get_settings, use_mirror_extension


class MESH_OT_mirror_loop_between(bpy.types.Operator):
    """Alt+클릭으로 고른 시작 루프와 이 키로 클릭한 끝 루프 사이를 선택 (엣지 모드: 루프만 / 면 모드: 사이의 면까지)"""
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
        description="면 모드에서 루프와 루프 사이의 면까지 선택한다. 끄면 휠 확장도 쓰지 않는다. "
                    "(엣지 모드에서는 이 옵션과 상관없이 항상 루프만 선택한다)",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        # 끔 단계에서는 poll 이 실패해 Blender 기본 Ctrl+Alt+클릭(링 선택)이 그대로 동작한다.
        return context.mode == 'EDIT_MESH' and extension_enabled(context)

    def invoke(self, context, event):
        state.reset_wheel()  # 휠 확장 상태(엣지/면)는 버린다.

        objs = list(context.objects_in_mode_unique_data)
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        settings = get_settings(context)
        use_wheel = settings is None or settings.use_wheel
        face_mode = is_face_mode(context)
        # 면은 면 모드에서만 고른다. 엣지/버텍스 모드는 항상 루프만.
        use_faces = face_mode and self.select_faces

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

        selected = target
        faces = set()
        face_st = None     # 휠 확장용 상태 (엣지 모드/면 모드 공통)
        if not valid:
            if face_mode:
                self.report({'INFO'}, "클릭한 면 루프를 시작 루프로 지정했습니다. 다른 루프를 Ctrl+Alt+클릭하면 사이의 면이 선택됩니다.")
            else:
                self.report({'INFO'}, "먼저 Alt+클릭으로 시작 루프를 선택하세요. 지금 클릭한 루프를 시작 루프로 지정했습니다.")
        else:
            # 3) 시작 루프에서 걸어가며 끝 루프와 만나는 곳까지 채운다.
            res = find_between_chains(
                bm, bm.edges[anchor['seed']], target, axes, self.threshold,
                cos_limit, dih, self.max_steps)
            if res is None:
                self.report({'WARNING'},
                            "두 루프 사이를 잇는 경로를 찾지 못했습니다 (삼각형/N-gon 으로 끊기거나 다른 덩어리일 수 있음). 클릭한 루프만 선택합니다.")
            else:
                loops, steps = res['loops'], res['steps']
                selected = set(target)
                for s in loops:
                    selected |= s
                # 엣지 모드: 항상 루프만 선택하는 휠 상태를 만든다. (면은 계산/선택하지 않는다)
                # 면 모드   : '면 선택' 옵션이 켜져 있을 때만 면 띠로 휠 상태를 만든다.
                if steps >= 1 and (use_faces or not face_mode):
                    lo, hi = (0, steps) if res['sign'] > 0 else (-steps, 0)
                    face_st, idxs = new_face_wheel(context, ob, bm, res['chains'], lo, hi, dih,
                                                   allow_empty=not face_mode)
                    faces = {bm.faces[i] for i in idxs}
                if faces:
                    self.report({'INFO'}, "루프 %d개, 면 %d개 선택" % (steps + 1, len(faces)))
                else:
                    self.report({'INFO'}, "루프 %d개 선택" % (steps + 1))

        # 사이 선택이 없었으면(앵커 없음/경로 없음/같은 루프) 클릭한 루프에서 시작해 휠로 넓힐 수 있게 한다.
        if not faces:
            if face_mode:
                # 면 모드: 클릭한 루프 옆 면 루프를 고른다.
                if use_faces:
                    strip = click_strip(context, ob, bm, seed_b, axes, self.threshold,
                                        cos_limit, dih, mouse)
                    if strip is not None:
                        chains, lo, hi = strip
                        face_st, idxs = new_face_wheel(context, ob, bm, chains, lo, hi, dih)
                        faces = {bm.faces[i] for i in idxs}
            elif face_st is None:
                # 엣지 모드: 클릭한 루프만 선택하고, 휠로 옆 루프를 한 줄씩 늘린다.
                chains, lo, hi = single_loop(context, ob, bm, seed_b, axes, self.threshold,
                                             cos_limit, dih)
                face_st, _ = new_face_wheel(context, ob, bm, chains, lo, hi, dih, allow_empty=True)

        for i in selected:
            bm.edges[i].select_set(True)
        for f in faces:
            f.select_set(True)
        bm.select_flush_mode()

        # 지금 선택된 면/엣지를 기준으로 두고 휠 확장 상태를 저장한다.
        if face_st is not None and use_wheel:
            face_st['base_faces'] = {f.index for f in bm.faces if f.select}
            face_st['base_edges'] = {e.index for e in bm.edges if e.select}
            state.set_face_wheel(face_st)

        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)

        # 끝 루프를 새 앵커로 -> 이어서 Ctrl+Alt+클릭하면 B~C 구간이 선택된다.
        state.set_anchor(ob.name, seed_b.index, counts)
        return {'FINISHED'}


class MESH_OT_mirror_face_step(bpy.types.Operator):
    """Ctrl+Alt+클릭 직후 휠: 바깥/안쪽 루프를 추가하거나 제거 (엣지 모드: 루프만 / 면 모드: 면 띠 포함, Alt = 바깥·안쪽 동시, Ctrl = 한 방향)"""
    bl_idname = "mesh.mirror_face_step"
    bl_label = "Mirror Loop Between Step"
    bl_options = {'UNDO'}  # execute 가 없어서 REGISTER(리두 패널)는 쓰지 않는다

    direction: IntProperty(name="Direction", default=1, min=-1, max=1)
    both_sides: BoolProperty(
        name="Both Sides",
        description="바깥·안쪽 루프를 동시에 늘리거나 줄인다",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        ob = context.edit_object
        st = state.face_wheel
        # 상태를 만들 때와 같은 선택 모드(면 모드인지 아닌지)일 때만 통과한다.
        return (context.mode == 'EDIT_MESH' and st is not None
                and ob is not None and ob.name == st['ob']
                and is_face_mode(context) == st['face_mode'])

    def invoke(self, context, event):
        ob = context.edit_object
        bm = bmesh.from_edit_mesh(ob.data)
        ensure_tables(bm)

        st = state.face_wheel
        if not face_state_valid(ob, bm, st):
            # 선택이 바뀌었거나 메시가 편집됨 -> 기본 휠 동작으로 넘긴다.
            state.reset_wheel()
            return {'CANCELLED', 'PASS_THROUGH'}

        if st['dih']:
            bm.normal_update()   # 다이헤드럴 계산에 쓰는 면 법선을 최신으로

        if self.both_sides:
            changed = step_faces_both(bm, st, self.direction)
        else:
            changed = step_faces_one(bm, st, self.direction)

        if not changed:
            if self.direction > 0:
                self.report({'INFO'}, "이어서 선택할 옆 루프가 없습니다")
            return {'CANCELLED'}

        apply_faces(bm, st)
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
        return {'FINISHED'}


# (idname, 키, 값, 수식키, 오퍼레이터 속성)
KEYMAPS = (
    (MESH_OT_mirror_loop_between.bl_idname, 'LEFTMOUSE', 'PRESS', {'ctrl': True, 'alt': True}, {}),

    # 휠: Alt+휠 = 바깥·안쪽 동시 / Ctrl+휠 = 한 방향
    # (엣지 휠과 같은 키다. 엣지 휠은 state.wheel 이 있을 때만, 이쪽은 state.face_wheel 이 있을 때만 통과한다.)
    (MESH_OT_mirror_face_step.bl_idname, 'WHEELUPMOUSE', 'PRESS',
     {'alt': True}, {'direction': 1, 'both_sides': True}),
    (MESH_OT_mirror_face_step.bl_idname, 'WHEELUPMOUSE', 'PRESS',
     {'ctrl': True}, {'direction': 1, 'both_sides': False}),
    (MESH_OT_mirror_face_step.bl_idname, 'WHEELDOWNMOUSE', 'PRESS',
     {'alt': True}, {'direction': -1, 'both_sides': True}),
    (MESH_OT_mirror_face_step.bl_idname, 'WHEELDOWNMOUSE', 'PRESS',
     {'ctrl': True}, {'direction': -1, 'both_sides': False}),
)

classes = (
    MESH_OT_mirror_loop_between,
    MESH_OT_mirror_face_step,
)
