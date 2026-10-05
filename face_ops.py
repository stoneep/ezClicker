"""
face_ops.py — [면] 오퍼레이터와 키맵.

  Ctrl+Alt+클릭 : 마지막으로 고른 루프 ~ 클릭한 루프 사이 전부 선택 (사이의 면 포함)
                  -> MESH_OT_mirror_loop_between

  1) Alt+클릭으로 시작 루프 A 를 고른다. (이게 앵커가 된다)
  2) Ctrl+Alt+클릭으로 끝 루프 B 를 누른다.
  -> A 에서 B 까지의 루프를 edge_range.find_between 이 찾고,
     '면 선택' 옵션(기본 켬)이면 face_core.strip_faces 가 인접한 두 루프 사이의 면도 모은다.
     (수동으로 엣지 -> 버텍스 -> 면 모드로 바꿔서 얻던 결과를 한 번에 만든다.)
  -> 끝나면 B 가 새 앵커가 되어 C 를 Ctrl+Alt+클릭하면 B~C 가 이어서 선택된다.

옵션(패널): 엣지 전용 모드에서 고른 루프가 가로(또는 세로)이면 반대 방향의 선택 엣지를 해제한다.
            사이 면의 테두리가 여기에 해당하며, 해제하면 그 사이 면도 함께 선택 해제되어 루프만 남는다.

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
from .common import (ensure_tables, get_mirror_axes, make_projector, mesh_counts,
                     pick_seed, restore_selection, snapshot_selection)
from .edge_core import deselect_cross_orientation, find_mirror_edges, walk_loop
from .edge_range import find_between
from .face_core import strip_faces
from .settings import extension_enabled, get_settings, use_mirror_extension


class MESH_OT_mirror_loop_between(bpy.types.Operator):
    """Alt+클릭으로 고른 시작 루프와 이 키로 클릭한 끝 루프 사이의 루프(와 면)를 전부 선택"""
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

        selected = target
        faces = set()
        if not valid:
            self.report({'INFO'}, "먼저 Alt+클릭으로 시작 루프를 선택하세요. 지금 클릭한 루프를 시작 루프로 지정했습니다.")
        else:
            # 3) 시작 루프에서 걸어가며 끝 루프와 만나는 곳까지 채운다.
            loops, steps = find_between(
                bm, bm.edges[anchor['seed']], target, axes, self.threshold,
                cos_limit, dih, self.max_steps)
            if loops is None:
                self.report({'WARNING'},
                            "두 루프 사이를 잇는 경로를 찾지 못했습니다 (삼각형/N-gon 으로 끊기거나 다른 덩어리일 수 있음). 클릭한 루프만 선택합니다.")
            else:
                selected = set(target)
                for s in loops:
                    selected |= s
                if self.select_faces:
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

        # 엣지 전용 모드에서는 면을 고르면 면의 테두리(루프와 직각인 선)까지 같이 선택된다.
        # 옵션이 켜져 있으면 고른 루프와 반대 방향(화면 기준)인 선택 엣지를 해제한다.
        # 사이 면은 테두리가 풀리므로 함께 선택 해제된다.
        s = get_settings(context)
        edge_only = tuple(context.tool_settings.mesh_select_mode) == (False, True, False)
        if s is not None and edge_only and (s.deselect_vertical or s.deselect_horizontal):
            n = deselect_cross_orientation(
                [e for e in bm.edges if e.select],
                {bm.edges[i] for i in selected},
                s.deselect_vertical, s.deselect_horizontal,
                make_projector(context, ob), cos_limit, dih)
            if n:
                bm.select_flush_mode()
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)

        # 끝 루프를 새 앵커로 -> 이어서 Ctrl+Alt+클릭하면 B~C 구간이 선택된다.
        state.set_anchor(ob.name, seed_b.index, counts)
        return {'FINISHED'}


def draw_settings(layout, settings):
    """패널/메뉴에 끼워 넣는 사이 선택 설정. (ui.draw_panel_body 가 호출한다)"""
    col = layout.column(align=True)
    col.prop(settings, "deselect_vertical")
    col.prop(settings, "deselect_horizontal")


# (idname, 키, 값, 수식키, 오퍼레이터 속성)
KEYMAPS = (
    (MESH_OT_mirror_loop_between.bl_idname, 'LEFTMOUSE', 'PRESS', {'ctrl': True, 'alt': True}, {}),
)

classes = (
    MESH_OT_mirror_loop_between,
)
