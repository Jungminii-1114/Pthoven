"""수학적으로 만든 손: 카메라·사람·접촉 정답 데이터가 아니다."""
import math
from .geometry import FINGERS


def analytic_hand(lifts=None):
    lifts = lifts or {}; p = [[0., 0., 0.] for _ in range(21)]; p[0] = [0., -1., 0.]
    roots = {"thumb": [-1., -.35, 0.], "index": [-.7, .15, 0.], "middle": [-.25, .3, 0.], "ring": [.2, .22, 0.], "pinky": [.65, .03, 0.]}
    for finger, ids in FINGERS.items():
        p[ids[0]] = roots[finger][:]
        for j, length in enumerate([.42, .28, .21]):
            theta = [.1, .2, .3][j] + lifts.get(finger, 0) * [.8, .5, .1][j]
            before = p[ids[j]]; p[ids[j + 1]] = [before[0], before[1] + length * math.cos(theta), before[2] + length * math.sin(theta)]
    return p


def analytic_cycle(finger="index", start=0):
    return [{"t_ms": start + i * 40, "world_landmarks": analytic_hand({finger: lift})}
            for i, lift in enumerate([0, 0, .2, .6, 1, 1, 1, .6, .2, 0, 0, 0])]
