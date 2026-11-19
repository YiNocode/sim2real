import csv
import json
from pathlib import Path

import numpy as np


COLUMNS = 't_s x y z vx vy vz ref_x ref_y ref_z cmd_vx cmd_vy cmd_vz voltage_v'.split()
AXES = {'step-x': 0, 'step-y': 1, 'step-z': 2}


def csv_data(path, columns):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        rows = csv.DictReader(stream)
        if not set(columns).issubset(rows.fieldnames or []):
            raise ValueError(f'CSV 缺少列：{", ".join(sorted(set(columns) - set(rows.fieldnames or [])))}')
        values = np.array([[float(row[key]) for key in columns] for row in rows])
    if len(values) < 10 or not np.isfinite(values).all() or np.any(np.diff(values[:, 0]) <= 0):
        raise ValueError('记录至少需有 10 个有限样本，时间戳须严格递增')
    return values


def normalize_ulog(path, command_path, output, start, end, command_offset=0.):
    from pyulog import ULog
    from .bundle import sha256

    log = ULog(str(path), ['vehicle_local_position', 'battery_status'])
    state = log.get_dataset('vehicle_local_position').data
    battery = log.get_dataset('battery_status').data
    time = state['timestamp'] / 1e6
    commands = csv_data(command_path, ['t_s', *COLUMNS[7:13]])
    commands[:, 0] += command_offset
    select = (time >= start) & (time <= end)
    if select.sum() < 10 or time[select][0] < commands[0, 0] or time[select][-1] > commands[-1, 0]:
        raise ValueError('标定窗口必须有足够的 PX4 样本，并完全落在机载指令覆盖范围内')
    for field in ('xy_valid', 'z_valid', 'v_xy_valid', 'v_z_valid'):
        if not state[field][select].all():
            raise ValueError(f'窗口内定位无效：{field}')
    for field in ('xy_reset_counter', 'z_reset_counter', 'heading_reset_counter'):
        if np.ptp(state[field][select]):
            raise ValueError(f'窗口内检测到 PX4 估计器重置：{field}')
    t = time[select]
    voltage_time = battery['timestamp'] / 1e6
    if t[0] < voltage_time[0] or t[-1] > voltage_time[-1]:
        raise ValueError('电池电压记录未覆盖完整窗口')
    i = np.searchsorted(commands[:, 0], t, side='right') - 1
    x, y, z, vx, vy, vz = [state[key][select] for key in ('x', 'y', 'z', 'vx', 'vy', 'vz')]
    voltages = np.interp(t, voltage_time, battery['voltage_v'])
    values = np.column_stack((t, y, x, -z, vy, vx, -vz, commands[i, 1:], voltages))
    if not np.isfinite(values).all() or np.any(voltages <= 0):
        raise ValueError('日志含无效数值或电压')
    np.savetxt(output, values, delimiter=',', header=','.join(COLUMNS), comments='', fmt='%.9f')
    write_report(metadata_path(output), {'ulog_sha256': sha256(path), 'command_sha256': sha256(command_path),
                                        'csv_sha256': sha256(output), 'start_px4_s': start, 'end_px4_s': end,
                                        'command_offset_s': command_offset, 'position_basis': 'px4_estimate'})


def metadata_path(path):
    path = Path(path)
    return path.with_name(path.name + '.meta.json')


def trial_digest(path):
    from .bundle import sha256

    digest = sha256(path)
    metadata = metadata_path(path)
    if not metadata.is_file():
        return digest
    record = json.loads(metadata.read_text(encoding='utf-8'))
    if record['csv_sha256'] != digest:
        raise ValueError(f'{path} 与日志来源文件的校验值不一致')
    return record['ulog_sha256']


def fit_axis(time, command, velocity):
    """Grid-search effective delay and time constant, least-squares gain and offset."""
    if np.ptp(command) < .05:
        raise ValueError('速度指令阶跃幅度至少为 0.05 m/s')
    dt = float(np.median(np.diff(time)))
    if dt > .15 or np.max(np.diff(time)) > max(.15, 3 * dt):
        raise ValueError('速度样本频率过低或存在较大间隙')
    grid = np.arange(time[0], time[-1], dt)
    measured = np.interp(grid, time, velocity)
    best = None
    for delay in np.arange(0, .501, max(.025, dt)):
        indices = np.searchsorted(time, grid - delay, side='right') - 1
        reference = command[indices.clip(0)]
        for tau in np.geomspace(max(dt, .025), 2, 35):
            alpha = 1 - np.exp(-dt/tau)
            filtered = np.empty_like(reference)
            filtered[0] = reference[0]
            for i in range(1, len(reference)):
                filtered[i] = filtered[i-1] + alpha * (reference[i-1] - filtered[i-1])
            matrix = np.column_stack((filtered, np.ones(len(grid))))
            gain, bias = np.linalg.lstsq(matrix, measured, rcond=None)[0]
            if not .05 <= gain <= 2.:
                continue
            rmse = float(np.sqrt(np.mean((matrix @ [gain, bias] - measured) ** 2)))
            if best is None or rmse < best['rmse_mps']:
                best = dict(gain=float(gain), tau_s=float(tau), delay_s=float(delay), bias_mps=float(bias), rmse_mps=rmse)
    if best is None:
        raise ValueError('未找到可信的正向闭环响应，请检查坐标、时间对齐与试飞窗口')
    return best


def fit(runs, profile):
    fits = {}
    for scenario, axis in AXES.items():
        records = runs.get(scenario, [])
        if not records:
            raise ValueError(f'缺少 {scenario} 标定日志')
        results = []
        for path in records:
            data = csv_data(path, COLUMNS)
            if not voltage_ok(data, profile):
                raise ValueError(f'{path} 含超出档案范围的电池电压')
            results.append(fit_axis(data[:, 0], data[:, 10 + axis], data[:, 4 + axis]))
        fits[scenario] = {'nominal': {name: float(np.median([r[name] for r in results])) for name in results[0]},
                          'range': {name: [min(r[name] for r in results), max(r[name] for r in results)]
                                    for name in ('gain', 'tau_s', 'delay_s')}, 'trials': results}
    return fits


def voltage_ok(data, profile):
    low, high = profile['battery']['voltage_window_v']
    return bool(np.all((data[:, -1] >= low) & (data[:, -1] <= high)))


def rise_time(data, axis):
    t, v, command = data[:, 0], data[:, 4 + axis], data[:, 10 + axis]
    jump = int(np.argmax(np.abs(np.diff(command)))) + 1
    amplitude = command[jump] - command[jump - 1]
    if abs(amplitude) < .05 or jump < 3 or len(t) - jump < 10:
        raise ValueError('需要包含完整阶跃前后窗口')
    baseline = float(np.median(v[:jump]))
    plateau = float(np.median(v[jump + (len(t) - jump) * 3 // 4:]))
    if (plateau - baseline) / amplitude < .05:
        raise ValueError('未观测到可判定的正向平台响应')
    fraction = (v - baseline) / (plateau - baseline)
    crossings = []
    for level in (.1, .9):
        indices = np.flatnonzero((fraction[:-1] < level) & (fraction[1:] >= level)) + 1
        candidates = indices[indices >= max(jump, crossings[-1][0] if crossings else jump)]
        if not len(candidates):
            raise ValueError('阶跃试飞窗口缺少上升阈值交点')
        i = candidates[0]
        crossings.append((i, np.interp(level, fraction[i-1:i+1], t[i-1:i+1])))
    return max(float(crossings[1][1] - crossings[0][1]), 1e-9)


def evaluate(pairs, profile, calibration_digests=()):
    banned = set(calibration_digests)
    scenarios = {'hover', *AXES, 'trajectory'}
    counts = dict.fromkeys(scenarios, 0)
    results = []
    for pair in pairs:
        scenario = pair['scenario']
        if scenario not in scenarios:
            raise ValueError(f'未知科目：{scenario}')
        trial = trial_digest(pair['real'])
        if trial in banned:
            raise ValueError('验收日志与标定日志重复，或已用于同一份验收报告')
        banned.add(trial)
        real, sim = [csv_data(pair[name], COLUMNS) for name in ('real', 'sim')]
        if not voltage_ok(real, profile):
            raise ValueError('验收日志包含超出档案范围的电池电压')
        truth = 'px4_estimate'
        if pair.get('reference'):
            reference = csv_data(pair['reference'], ['t_s', 'x', 'y', 'z'])
            if reference[0, 0] > real[0, 0] or reference[-1, 0] < real[-1, 0]:
                raise ValueError('独立参考数据未覆盖验收窗口')
            real[:, 1:4] = np.column_stack([np.interp(real[:, 0], reference[:, 0], reference[:, i]) for i in range(1, 4)])
            truth = 'independent_reference'
        counts[scenario] += 1
        def rmse(data):
            return float(np.sqrt(np.mean(np.sum((data[:, 1:4] - data[:, 7:10]) ** 2, axis=1))))
        record = dict(scenario=scenario, position_basis=truth, velocity_basis='px4_estimate',
                      real_position_rmse_m=rmse(real), sim_position_rmse_m=rmse(sim))
        if scenario in AXES:
            record['real_rise_s'], record['sim_rise_s'] = [rise_time(data, AXES[scenario]) for data in (real, sim)]
            record['rise_relative_error'] = abs(record['sim_rise_s'] / record['real_rise_s'] - 1)
            record['meets_provisional_threshold'] = record['rise_relative_error'] <= .20
        if scenario == 'trajectory':
            record['rmse_gap_m'] = abs(record['real_position_rmse_m'] - record['sim_position_rmse_m'])
            record['meets_provisional_threshold'] = record['rmse_gap_m'] <= .15
        results.append(record)
    return {'trials': results, 'counts': counts, 'coverage_complete': min(counts.values()) >= 3,
            'all_provisional_thresholds_met': min(counts.values()) >= 3 and all(
                record.get('meets_provisional_threshold', True) for record in results)}


def write_report(path, report):
    Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
