"""
edge_outline.py — [엣지] 큰 평평한 면의 '테두리'로 루프를 확정하기.

bmesh 만 다루고 오퍼레이터/UI 는 모른다.

문제: 얇은 판에 원기둥을 Boolean > Difference 로 여러 개 뚫으면 윗면은 구멍을 피해 n-gon/삼각형으로 쪼개지고,
테두리 사각형의 모서리 버텍스에 쪼갠 선(브리지 엣지)이 더 붙어 버텍스가 4~8극이 된다. 위상(맞은편 엣지)과 진행 방향만 보는
루프 워커는 이런 곳에서 엉뚱한 엣지로 새거나 모서리에서 멈춰 테두리를 일부만 고른다.

해결: 클릭한 엣지가 '날카로운 엣지'이고 한쪽이 훨씬 큰 평평한 영역(윗면)이면, 그 영역의 바깥 경계를 그대로 루프로 쓴다.
경계는 면을 어떻게 쪼갰는지(내부 엣지)와 무관하게 정해지므로 쪼개짐에 흔들리지 않는다.

검증 (아래 중 하나라도 어긋나면 None 을 돌려주고, 호출한 쪽은 기존 루프 워커 결과를 그대로 쓴다)
  1) 클릭한 엣지가 면 두 장 사이의 엣지이고 두 면의 꺾임이 SHARP 이상이다. (매끈한 곳은 기존 방식이 맞다)
  2) 두 면의 평평한 영역(flat_island) 중 한쪽 면적이 다른 쪽의 DOMINANCE 배 이상이다. (큐브 모서리나 구의 극처럼
     면 크기가 비슷하면 어느 쪽 경계인지 모호하다)
  3) 그 영역의 경계를 한 줄씩 읽을 수 있다. (한 점에서 맞닿거나 갈라지면 읽을 수 없다)
  4) 클릭한 엣지가 읽은 경계 안에 있고, 경계의 모든 엣지가 영역과 영역 밖 사이의 엣지다.
  5) 기존 루프 워커 결과(walked)가 '틀렸다'는 증거가 있다. 증거 없이 바꾸면 곡면·교차선 같은 정상 결과를 망친다.
     - 새는 경우: 워커가 큰 영역 안쪽 엣지(양쪽 면이 모두 그 영역)를 지났다. 쪼갠 선(브리지)을 타고 들어간 것이다.
     - 덜 가는 경우: 워커 결과가 경계의 일부이고(부분집합) 영역이 훨씬 크다(PARTIAL_DOMINANCE 배). 모서리에서 멈춘 것이다.
     - 엉뚱한 쪽으로 도는 경우: 워커 결과가 작은 쪽 면(판의 옆면)의 경계 안에 들어 있고 영역이 훨씬 크다. 모서리에서 옆면 테두리를 따라 꺾인 것이다.
"""

import math

from .face_shape import boundary_loops, flat_island

SHARP = math.radians(30.0)        # 이 이상 꺾인 엣지만 '테두리'로 본다
FLAT = math.radians(2.0)          # 이웃한 면 법선 차이가 이 안쪽이면 같은 평면
DOMINANCE = 2.0                   # 큰 쪽 영역의 면적이 작은 쪽의 몇 배 이상이어야 하는지
PARTIAL_DOMINANCE = 4.0           # 워커가 덜 간 경우(부분집합)에 바꾸려면 필요한 배수. 더 엄격하다


def _area(island):
    return sum(f.calc_area() for f in island)


def region_outline(bm, seed, walked):
    """
    seed 가 큰 평평한 영역의 테두리이고 기존 루프 워커 결과(walked: 엣지 집합)가 틀렸다면
    (순서대로 이어진 엣지 리스트, seed 의 위치, 닫힘=True), 아니면 None.
    """
    faces = [f for f in seed.link_faces if not f.hide]
    if len(faces) != 2:
        return None
    bm.normal_update()
    if faces[0].normal.angle(faces[1].normal, 0.0) < SHARP:
        return None
    islands = [flat_island(f, FLAT) for f in faces]
    if faces[1] in islands[0]:                    # 한 영역이 양쪽을 다 덮는다(접힘 등)
        return None
    areas = [_area(i) for i in islands]
    big = 0 if areas[0] >= areas[1] else 1
    if areas[big] < DOMINANCE * areas[1 - big]:
        return None
    island = islands[big]

    # 5) 기존 결과가 틀렸는지 먼저 본다. (틀렸다는 증거가 없으면 건드리지 않는다)
    leaked = any(len(e.link_faces) == 2 and all(f in island for f in e.link_faces) for e in walked)
    strong = areas[big] >= PARTIAL_DOMINANCE * areas[1 - big]
    around_small = False
    if strong and not leaked:
        small = islands[1 - big]
        small_border = {e for f in small for e in f.edges if sum(1 for g in e.link_faces if g in small) == 1}
        around_small = walked <= small_border
    if not leaked and not strong:
        return None

    loops = boundary_loops(island)
    if not loops:
        return None
    sv = set(seed.verts)
    for lp in loops:
        n = len(lp)
        for i in range(n):
            if {lp[i], lp[(i + 1) % n]} != sv:
                continue
            chain = []
            for j in range(n):
                a, b = lp[(i + j) % n], lp[(i + j + 1) % n]
                e = next((x for x in a.link_edges
                          if x.other_vert(a) is b and not x.hide
                          and sum(1 for f in x.link_faces if f in island) == 1), None)
                if e is None:
                    return None
                chain.append(e)
            if not (leaked or around_small or walked < set(chain)):      # 틀렸다는 증거가 없으면 기존 결과를 믿는다
                return None
            return chain, 0, True
    return None
