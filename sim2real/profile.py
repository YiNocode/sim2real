import math
import re
from pathlib import Path

import yaml


SOURCES = {'vio', 'flow_range', 'gnss', 'rtk', 'external', 'none'}
STATUS = {'specification', 'estimated', 'measured'}
EXAMPLE = Path(__file__).resolve().parents[1] / 'profiles' / 'example.yaml'


def number(value, label, minimum=0, maximum=math.inf, *, positive=False):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError(f'{label} 必须是有限数值')
    if not minimum <= value <= maximum or (positive and value <= 0):
        raise ValueError(f'{label} 超出允许范围')
    return value


def vector(value, label, size=3, minimum=-math.inf):
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f'{label} 必须有 {size} 个数值')
    return [number(item, label, minimum) for item in value]


def validate(data):
    try:
        if not isinstance(data, dict) or not re.fullmatch('[a-z][a-z0-9-]{1,47}', data['id']):
            raise ValueError('机型 ID 需以小写字母开头，只用小写字母、数字或连字符')
        if type(data['revision']) is not int or not 1 <= data['revision'] <= 999999:
            raise ValueError('版本必须是 1–999999 的整数')
        geometry = data['geometry']
        wheelbase = number(geometry['wheelbase_m'], '轴距', positive=True)
        mass = number(geometry['takeoff_mass_kg'], '起飞重量', positive=True)
        vector(geometry['com_flu_m'], '质心')
        inertia = vector(geometry['inertia_kg_m2'], '惯量', minimum=0)
        if min(inertia) <= 0 or 2 * max(inertia) > sum(inertia):
            raise ValueError('惯量需为正且满足惯量三角不等式')
        rotors = geometry['rotors']
        if len(rotors) != 4 or [r['output'] for r in rotors] != [1, 2, 3, 4]:
            raise ValueError('首版支持四旋翼，按 PX4 电机输出 1–4 排列')
        positions = [vector(r['xyz_flu_m'], '旋翼坐标') for r in rotors]
        if any(abs(p[2] - positions[0][2]) > 0.0001 for p in positions):
            raise ValueError('首版要求四个旋翼共面')
        if len(set(map(tuple, positions))) != 4 or not math.isclose(
                max(math.dist(a, b) for a in positions for b in positions), wheelbase, rel_tol=.03):
            raise ValueError('轴距应等于最远的两个电机中心间距（容差 3%）')
        if sorted(r['direction'] for r in rotors) != ['ccw', 'ccw', 'cw', 'cw']:
            raise ValueError('需要两个 CW 和两个 CCW 旋翼')
        for field in ('motor', 'propeller', 'esc', 'flight_controller', 'companion', 'firmware'):
            if not isinstance(data['hardware'][field], str) or not data['hardware'][field].strip():
                raise ValueError(f'请填写 {field}')
        battery = data['battery']
        if type(battery['cells']) is not int:
            raise ValueError('电池串数必须为整数')
        number(battery['cells'], '电池串数', 1, 24)
        number(battery['capacity_mah'], '电池容量', positive=True)
        low, high = vector(battery['voltage_window_v'], '电压窗口', 2, 0)
        if not 0 < low < high:
            raise ValueError('电压窗口应为递增的正数')
        propulsion = data['propulsion']
        for field in ('thrust_coefficient', 'torque_coefficient', 'max_rad_s', 'rise_s', 'fall_s'):
            number(propulsion[field], field, positive=True)
        if 4 * propulsion['thrust_coefficient'] * propulsion['max_rad_s'] ** 2 <= mass * 9.81:
            raise ValueError('按当前推进参数，四个电机的最大总推力不足以悬停')
        localization = data['localization']
        if localization['source'] not in SOURCES:
            raise ValueError('请选择定位来源')
        vector(localization['mounting_xyz_flu_m'], '定位传感器安装位置')
        vector(localization['mounting_rpy_rad'], '定位传感器安装角')
        number(localization['rate_hz'], '定位更新率', .1, 1000)
        number(localization['delay_s'], '定位延迟', 0, 5)
        number(localization['position_noise_std_m'], '位置噪声')
        number(localization['velocity_noise_std_mps'], '速度噪声')
        for field in ('geometry', 'hardware', 'battery', 'propulsion', 'localization'):
            entry = data['provenance'][field]
            if entry['status'] not in STATUS or not isinstance(entry['source'], str) or not entry['source'].strip():
                raise ValueError(f'请补齐 {field} 的参数来源')
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f'机型档案字段缺失或格式错误：{exc}') from exc
    return data


def load(path):
    return validate(yaml.safe_load(Path(path).read_text(encoding='utf-8')))


def key(profile):
    return f"{profile['id']}-r{profile['revision']}"


def encode(profile):
    return yaml.safe_dump(profile, allow_unicode=True, sort_keys=False)
