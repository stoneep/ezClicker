"""
settings.py — 확장 단계 설정.

  끔     : Blender 기본 동작 그대로 (Alt+클릭 = 기본 루프 선택, Ctrl+Alt+클릭 = 기본 링 선택,
           휠 확장 없음). 여러 루프를 클릭한 뒤 1 -> 3 키로 변환할 때처럼
           '딱 클릭한 만큼만' 선택되길 원할 때 쓴다.
  1단계  : 극점/삼각형/능선을 통과해 루프를 끝까지 잇는다. 미러 반대편은 건드리지 않는다.
  2단계  : 1단계 + 미러 축에서 끊긴 반대편 루프까지 선택한다. (기존 동작)

설정은 WindowManager 에 둔다. (블렌더를 다시 켜면 2단계로 돌아온다)
단계를 바꾸면 휠 확장 상태는 버린다. (이전 단계 규칙으로 만든 상태이므로)

엣지/면 기능이 쓰는 옵션을 더 늘릴 때는 MLS_Settings 에 속성을 추가하고,
그 옵션을 패널에 그리는 코드는 해당 기능 모듈(edge_ops / face_ops)의 draw_settings 에 둔다.
"""

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty

from . import state


LEVEL_ITEMS = (
    ('OFF', "끔 (기본 루프 선택)",
     "Blender 기본 루프/링 선택 그대로 쓴다. 극점 통과, 미러 확장, 휠 확장, 사이 선택이 모두 꺼진다",
     'X', 0),
    ('LOOP', "1단계: 루프 이어가기",
     "극점/삼각형/능선을 지나 루프를 끝까지 선택한다. 미러 반대편은 선택하지 않는다",
     'EDGESEL', 1),
    ('MIRROR', "2단계: 이어가기 + 미러",
     "1단계에 더해, 미러 축에서 끊긴 반대편 루프까지 함께 선택한다",
     'MOD_MIRROR', 2),
)

LEVEL_SHORT = {'OFF': "루프: 끔", 'LOOP': "루프: 1단계", 'MIRROR': "루프: 2단계"}
LEVEL_ICON = {'OFF': 'X', 'LOOP': 'EDGESEL', 'MIRROR': 'MOD_MIRROR'}


def _on_settings_update(self, context):
    state.reset_wheel()


# 고정 패널(사이드바)의 숫자칸. 값은 state.adjust 에서 읽고(휠 확장과 항상 일치), 바꾸면 조절 오퍼레이터를 부른다.
# 일반 숫자칸이라 클릭 드래그, 좌우 화살표, 직접 입력이 모두 된다.
def _adjust_getter(key):
    def get(self):
        adj = state.adjust
        if adj is None:
            return 1 if key == 'keep' else 0
        if key == 'keep':
            return adj['keep'] or adj['total'] or 1
        return adj[key]
    return get


def _adjust_setter(target, key):
    def set_(self, value):
        adj = state.adjust
        if adj is None or value == _adjust_getter(key)(self):
            return
        try:
            op = bpy.ops.mesh.mirror_face_adjust if adj.get('mode') == 'FACE' else bpy.ops.mesh.mirror_loop_adjust
            op('EXEC_DEFAULT', target=target, value=value)
        except RuntimeError:        # 편집 모드가 아니거나 선택이 바뀌어 조절할 수 없는 상태
            pass
    return set_


class MLS_Settings(bpy.types.PropertyGroup):
    adjust_up: IntProperty(
        name="폭: 위쪽 루프",
        description="클릭한 루프에서 화면 위쪽으로 나란한 루프를 몇 줄 더 선택할지 (드래그/화살표/입력)",
        min=0, max=50,
        get=_adjust_getter('up'), set=_adjust_setter('UP', 'up'),
    )
    adjust_down: IntProperty(
        name="폭: 아래쪽 루프",
        description="클릭한 루프에서 화면 아래쪽으로 나란한 루프를 몇 줄 더 선택할지 (드래그/화살표/입력)",
        min=0, max=50,
        get=_adjust_getter('down'), set=_adjust_setter('DOWN', 'down'),
    )
    adjust_length: IntProperty(
        name="길이: 엣지 수",
        description="루프를 따라 선택할 엣지 수. 클릭한 엣지를 가운데로 줄어든다. 루프 전체 길이보다 크면 전체로 맞춘다",
        min=1, max=100000, soft_max=500,
        get=_adjust_getter('keep'), set=_adjust_setter('LENGTH', 'keep'),
    )
    level: EnumProperty(
        name="확장 단계",
        description="루프 선택 확장 기능을 어디까지 쓸지 정한다",
        items=LEVEL_ITEMS,
        default='MIRROR',
        update=_on_settings_update,
    )
    use_wheel: BoolProperty(
        name="휠 확장 사용",
        description="루프 선택 직후 Alt+휠 / Ctrl+휠로 옆 루프까지 확장/축소한다",
        default=True,
        update=_on_settings_update,
    )


def get_settings(context):
    return getattr(context.window_manager, "mls_settings", None)


def extension_enabled(context):
    """끔 단계가 아니면 True. (설정이 아직 없으면 켜진 것으로 본다.)"""
    s = get_settings(context)
    return s is None or s.level != 'OFF'


def use_mirror_extension(context):
    s = get_settings(context)
    return s is None or s.level == 'MIRROR'


classes = (MLS_Settings,)
