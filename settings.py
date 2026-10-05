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
from bpy.props import BoolProperty, EnumProperty

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


class MLS_Settings(bpy.types.PropertyGroup):
    level: EnumProperty(
        name="확장 단계",
        description="루프 선택 확장 기능을 어디까지 쓸지 정한다",
        items=LEVEL_ITEMS,
        default='MIRROR',
        update=_on_settings_update,
    )
    deselect_vertical: BoolProperty(
        name="가로 선택 시 세로 해제",
        description="엣지 모드에서 Ctrl+Alt+클릭으로 가로(화면 기준) 루프를 고르면, 사이 면의 세로 테두리와 이미 선택돼 있던 세로 선을 해제한다. 사이 면은 함께 선택 해제된다",
        default=False,
    )
    deselect_horizontal: BoolProperty(
        name="세로 선택 시 가로 해제",
        description="엣지 모드에서 Ctrl+Alt+클릭으로 세로(화면 기준) 루프를 고르면, 사이 면의 가로 테두리와 이미 선택돼 있던 가로 선을 해제한다. 사이 면은 함께 선택 해제된다",
        default=False,
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
