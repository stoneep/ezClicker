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

import math

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty

from . import state
from .face_patch import DEFAULT_CURVE_TOL, DEFAULT_DELIMIT
from .face_shape import DEFAULT_ANGLE_TOL, DEFAULT_FLAT, DEFAULT_LEN_TOL


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


DEFAULT_MAX_ANGLE = math.radians(60.0)


def _on_geometry_update(self, context):
    """루프를 따라가는 규칙(최대 꺾임 각도, 다이헤드럴)이 바뀌면 방금 고른 루프를 새 규칙으로 다시 고른다."""
    from .common import adjust_valid          # common 은 settings 를 가져오지 않지만, 로드 순서를 단순하게 두려고 함수 안에서
    adj = state.adjust
    if adj is None or adj.get('mode', 'EDGE') != 'EDGE' or not adjust_valid(context):
        state.reset_wheel()
        return
    try:
        bpy.ops.mesh.mirror_loop_adjust('EXEC_DEFAULT', target='REFRESH')
    except RuntimeError:
        pass


def _on_similar_update(self, context):
    """같은 모양 면 선택의 옵션이 바뀌면, 방금 찾은 결과를 같은 기준 면으로 새 옵션에 맞춰 다시 찾는다."""
    from .common import similar_valid
    if state.similar is None or not similar_valid(context):
        return
    try:
        bpy.ops.mesh.mirror_face_similar('EXEC_DEFAULT', refresh=True)
    except RuntimeError:
        pass


def _on_edge_similar_update(self, context):
    """같은 모양 엣지 루프의 옵션이 바뀌면, 방금 찾은 결과를 같은 씨앗 루프로 새 옵션에 맞춰 다시 찾는다."""
    from .common import similar_edge_valid
    if state.similar_edge is None or not similar_edge_valid(context):
        return
    try:
        bpy.ops.mesh.mirror_edge_similar('EXEC_DEFAULT', refresh=True)
    except RuntimeError:
        pass


SIDE_REFERENCE_ITEMS = (
    ('SCREEN', "화면", "클릭한 순간의 화면에서 오른쪽(루프가 세로에 가까우면 위쪽)으로 가는 쪽을 루프의 진행 방향으로 본다. "
                      "시계 방향 쪽은 그 진행 방향을 표면 법선 둘레로 시계 방향으로 돌린 쪽이다", 'RESTRICT_VIEW_OFF', 0),
    ('GLOBAL', "글로벌", "월드 축 중 루프가 가장 많이 가리키는 축의 + 방향을 진행 방향으로 본다. 화면을 돌려도 같은 쪽이다", 'WORLD', 1),
    ('LOCAL', "로컬", "오브젝트 로컬 축 중 루프가 가장 많이 가리키는 축의 + 방향을 진행 방향으로 본다. 오브젝트를 회전해도 같은 쪽이다",
     'OBJECT_ORIGIN', 2),
)


def _on_side_reference_update(self, context):
    """방향 기준이 바뀌면 방금 고른 선택을 새 기준으로 다시 계산한다. (폭은 그대로, 시계/반시계 쪽만 바뀐다)"""
    from .common import adjust_valid
    adj = state.adjust
    if adj is None or not adjust_valid(context):
        return
    try:
        if adj.get('mode') == 'FACE':
            bpy.ops.mesh.mirror_face_adjust('EXEC_DEFAULT', target='REFRESH')
        else:
            bpy.ops.mesh.mirror_loop_adjust('EXEC_DEFAULT', target='REFRESH')
    except RuntimeError:
        pass


SIMILAR_MODES = (
    ('FLAT', "평평한 영역 외곽선",
     "이웃한 평평한 면들을 한 덩어리로 보고 외곽선 모양(변 길이, 꺾임 각)을 비교한다. 기어처럼 뾰족한 평면 모양에 정확하다", 'MESH_PLANE', 0),
    ('PATCH', "경계 엣지 루프 (둥근 모양·해상도 달라도)",
     "각도로 끊어 매끈하게 이어진 면들(베벨·둥근 면 포함)을 한 덩어리로 보고, 경계 엣지 루프를 같은 간격으로 다시 찍어 비교한다. "
     "내부 면 개수와 원을 몇 조각으로 나눴는지는 보지 않는다. 베벨 준 나사 머리처럼 크기별로 폴리 수가 다른 부품용", 'MESH_CIRCLE', 1),
)


class MLS_Settings(bpy.types.PropertyGroup):
    side_reference: EnumProperty(
        name="방향 기준",
        description="폭의 '시계 방향 쪽/반시계 방향 쪽'을 무엇을 기준으로 정할지. 화면은 클릭한 순간에 정해 기억한다",
        items=SIDE_REFERENCE_ITEMS, default='SCREEN', update=_on_side_reference_update,
    )
    edge_similar_extend: BoolProperty(
        name="기존 선택에 추가",
        description="기존 선택을 지우지 않고 찾은 루프를 더한다",
        default=False, update=_on_edge_similar_update,
    )
    edge_similar_scale_invariant: BoolProperty(
        name="크기 무시",
        description="크기가 달라도 모양이 같으면(닮음) 찾는다",
        default=False, update=_on_edge_similar_update,
    )
    edge_similar_shape_tol: FloatProperty(
        name="모양 허용 오차",
        description="윤곽 곡선의 진폭이 기준과 이 비율(%) 이내로 다르면 같은 모양으로 본다. "
                    "베벨이 많이 들어가 모서리가 둥근 쪽을 찾으려면 키운다. 화살표/Ctrl+휠은 1% 씩",
        default=15.0, min=2.0, max=60.0, soft_max=40.0, subtype='PERCENTAGE', step=100, precision=0,
        update=_on_edge_similar_update,
    )
    edge_similar_size_tol: FloatProperty(
        name="크기 허용 오차",
        description="크기를 무시하지 않을 때 평균 반지름이 이 비율(%) 이내로 같으면 같은 크기로 본다. "
                    "너무 크면 베벨로 생긴 바로 옆 평행 루프까지 같이 잡힌다. 화살표/Ctrl+휠은 0.1% 씩",
        default=2.0, min=0.1, max=30.0, soft_max=10.0, subtype='PERCENTAGE', step=10, precision=1,
        update=_on_edge_similar_update,
    )
    similar_mode: EnumProperty(
        name="비교 기준",
        description="무엇을 보고 같은 모양인지 판단할지",
        items=SIMILAR_MODES, default='FLAT', update=_on_similar_update,
    )
    similar_patch_angle: FloatProperty(
        name="덩어리 끊는 각도",
        description="이웃한 면의 각도 차이가 이보다 크면 다른 덩어리로 나눈다. "
                    "베벨 조각 하나의 꺾임보다는 크게, 바닥과 만나는 모서리보다는 작게 잡는다",
        default=DEFAULT_DELIMIT, min=math.radians(5.0), max=math.radians(89.0), subtype='ANGLE',
        # 드래그 속도는 soft 범위에 비례한다: 자주 쓰는 구간만 soft 로 잡아 느리게, 화살표/Ctrl+휠은 1° 씩, 표시는 소수 첫째 자리까지.
        soft_min=math.radians(10.0), soft_max=math.radians(75.0), step=100, precision=1,
        update=_on_similar_update,
    )
    similar_curve_tolerance: FloatProperty(
        name="곡선 허용 오차",
        description="경계 곡선을 비교할 때 허용하는 차이(평균 반지름 대비 %). 원을 적게 나눈 쪽은 다각형이라 "
                    "크면 클수록 폴리 수가 많이 다른 것도 같다고 본다. 화살표/Ctrl+휠은 0.5% 씩",
        default=DEFAULT_CURVE_TOL * 100.0, min=0.5, max=30.0, soft_max=15.0, subtype='PERCENTAGE',
        step=50, precision=1, update=_on_similar_update,
    )
    similar_extend: BoolProperty(
        name="기존 선택에 추가",
        description="기존 선택을 지우지 않고 찾은 면을 더한다",
        default=False, update=_on_similar_update,
    )
    similar_scale_invariant: BoolProperty(
        name="크기 무시",
        description="크기가 달라도 모양이 같으면(닮음) 찾는다",
        default=False, update=_on_similar_update,
    )
    similar_use_island: BoolProperty(
        name="평평한 영역으로 묶기",
        description="이웃한 평평한 면들을 한 덩어리의 모양으로 비교한다. 끄면 면 하나씩 비교한다",
        default=True, update=_on_similar_update,
    )
    similar_length_tolerance: FloatProperty(
        name="길이 허용 오차",
        description="변 길이가 이 비율(%) 이내로 다르면 같다고 본다. 화살표/Ctrl+휠은 0.1% 씩",
        default=DEFAULT_LEN_TOL * 100.0, min=0.0, max=50.0, soft_max=10.0, subtype='PERCENTAGE',
        step=10, precision=2, update=_on_similar_update,
    )
    similar_angle_tolerance: FloatProperty(
        name="각도 허용 오차",
        description="꺾임 각이 이 이내로 다르면 같다고 본다",
        default=DEFAULT_ANGLE_TOL, min=0.0, max=math.radians(30.0), subtype='ANGLE',
        soft_max=math.radians(10.0), step=10, precision=2, update=_on_similar_update,
    )
    similar_flat_angle: FloatProperty(
        name="평면 판정 각도",
        description="이웃한 면의 법선 차이가 이 이내면 같은 평면으로 묶는다",
        default=DEFAULT_FLAT, min=0.0, max=math.radians(30.0), subtype='ANGLE',
        soft_max=math.radians(10.0), step=10, precision=2, update=_on_similar_update,
    )
    max_turn_angle: FloatProperty(
        name="최대 꺾임 각도",
        description="극점/삼각형에서 루프가 꺾여도 계속 진행할 최대 각도 (클수록 더 멀리 감). "
                    "바꾸면 방금 고른 루프를 다시 계산한다",
        default=DEFAULT_MAX_ANGLE, min=0.0, max=math.radians(120.0), subtype='ANGLE',
        soft_min=math.radians(20.0), soft_max=math.radians(90.0), step=100, precision=1,
        update=_on_geometry_update,
    )
    use_dihedral: BoolProperty(
        name="다이헤드럴 사용",
        description="면 사이 각도(다이헤드럴)를 보조 기준으로 써서 능선·로우폴리에서 루프가 끊기거나 새는 것을 줄인다. "
                    "바꾸면 방금 고른 루프를 다시 계산한다",
        default=True,
        update=_on_geometry_update,
    )
    adjust_up: IntProperty(
        name="폭: 시계 방향",
        description="루프의 진행 방향에서 시계 방향 쪽으로 나란한 루프(면 모드는 면 줄)를 몇 줄 더 선택할지. 진행 방향은 '방향 기준'이 정한다",
        min=0, max=50,
        get=_adjust_getter('up'), set=_adjust_setter('UP', 'up'),
    )
    adjust_down: IntProperty(
        name="폭: 반시계 방향",
        description="루프의 진행 방향에서 반시계 방향 쪽으로 나란한 루프(면 모드는 면 줄)를 몇 줄 더 선택할지. 진행 방향은 '방향 기준'이 정한다",
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


def geometry_options(context):
    """루프 걷기 규칙: (최대 꺾임 각도, 다이헤드럴 사용). 설정이 아직 없으면 기본값."""
    s = get_settings(context)
    return (DEFAULT_MAX_ANGLE, True) if s is None else (s.max_turn_angle, s.use_dihedral)


def similar_options(context):
    """같은 모양 면 선택 옵션 (설정이 아직 없으면 기본값)."""
    from types import SimpleNamespace
    s = get_settings(context)
    if s is None:
        return SimpleNamespace(extend=False, scale_invariant=False, use_island=True, length_tolerance=DEFAULT_LEN_TOL,
                               angle_tolerance=DEFAULT_ANGLE_TOL, flat_angle=DEFAULT_FLAT, mode='FLAT',
                               patch_angle=DEFAULT_DELIMIT, curve_tolerance=DEFAULT_CURVE_TOL)
    return SimpleNamespace(
        extend=s.similar_extend, scale_invariant=s.similar_scale_invariant, use_island=s.similar_use_island,
        length_tolerance=s.similar_length_tolerance / 100.0, angle_tolerance=s.similar_angle_tolerance,
        flat_angle=s.similar_flat_angle, mode=s.similar_mode, patch_angle=s.similar_patch_angle,
        curve_tolerance=s.similar_curve_tolerance / 100.0)


def edge_similar_options(context):
    """같은 모양 엣지 루프 옵션 (설정이 아직 없으면 기본값). 허용 오차는 비율(0~1)로 돌려준다."""
    from types import SimpleNamespace
    s = get_settings(context)
    if s is None:
        return SimpleNamespace(extend=False, scale_invariant=False, shape_tol=0.15, size_tol=0.02)
    return SimpleNamespace(extend=s.edge_similar_extend, scale_invariant=s.edge_similar_scale_invariant,
                           shape_tol=s.edge_similar_shape_tol / 100.0, size_tol=s.edge_similar_size_tol / 100.0)


def side_reference(context):
    """폭의 방향 기준 ('SCREEN' / 'GLOBAL' / 'LOCAL'). 설정이 아직 없으면 화면."""
    s = get_settings(context)
    return 'SCREEN' if s is None else s.side_reference


def extension_enabled(context):
    """끔 단계가 아니면 True. (설정이 아직 없으면 켜진 것으로 본다.)"""
    s = get_settings(context)
    return s is None or s.level != 'OFF'


def use_mirror_extension(context):
    s = get_settings(context)
    return s is None or s.level == 'MIRROR'


classes = (MLS_Settings,)
