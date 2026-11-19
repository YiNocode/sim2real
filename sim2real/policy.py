import math
from dataclasses import dataclass

import numpy as np


OBSERVATIONS = ['x', 'y', 'z', 'vx', 'vy', 'vz', 'sin_yaw', 'cos_yaw', 'ref_dx', 'ref_dy', 'ref_dz']
ACTIONS = ['vx', 'vy', 'vz']
DEFAULTS = dict(position_scale_m=2.0, speed_limit_mps=0.6, heading_enu_rad=0.,
                policy_rate_hz=10., setpoint_rate_hz=50., timeout_s=.35)


def observation(position, velocity, yaw, reference, config):
    pos = np.asarray(position)
    return np.concatenate((pos / config['position_scale_m'],
                           np.asarray(velocity) / config['speed_limit_mps'],
                           np.stack((np.sin(yaw), np.cos(yaw)), axis=-1),
                           (np.asarray(reference) - pos) / config['position_scale_m']), axis=-1).astype(np.float32)


def limit(action, speed):
    values = np.asarray(action, dtype=np.float32)
    if values.shape != (3,) or not np.all(np.isfinite(values)):
        raise ValueError('策略必须输出三个有限的速度分量')
    return values * min(1., speed / max(1e-9, float(np.linalg.norm(values.astype(np.float64)))))


def enu_ned(values):
    x, y, z = values
    return [float(y), float(x), -float(z)]


def heading_flip(yaw):
    angle = math.pi / 2 - yaw
    return math.atan2(math.sin(angle), math.cos(angle))


@dataclass
class Sample:
    position: list
    velocity: list
    yaw: float
    received: float
    reset_counters: tuple


def local_position(msg, now):
    if not (msg.xy_valid and msg.z_valid and msg.v_xy_valid and msg.v_z_valid and msg.heading_good_for_control):
        return None
    values = [msg.x, msg.y, msg.z, msg.vx, msg.vy, msg.vz, msg.heading]
    if not all(map(math.isfinite, values)):
        return None
    resets = tuple(getattr(msg, key) for key in ('xy_reset_counter', 'z_reset_counter', 'heading_reset_counter'))
    return Sample(enu_ned(values[:3]), enu_ned(values[3:6]), heading_flip(values[6]), now, resets)


def fresh(sample, now, timeout):
    return sample is not None and 0 <= now - sample.received <= timeout
