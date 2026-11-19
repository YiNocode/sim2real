import argparse
import csv
import math
import os
from collections import deque
from pathlib import Path

import numpy as np

from .assets import write_assets
from .bundle import create
from .calibration import COLUMNS, csv_data
from .policy import DEFAULTS, OBSERVATIONS, limit, observation
from .profile import load


def rollout(args, profile, output):
    from isaacsim import SimulationApp

    app = SimulationApp({'headless': True, 'multi_gpu': False, 'active_gpu': args.gpu,
                         'physics_gpu': args.gpu, 'width': 320, 'height': 240})
    try:
        samples, labels = simulate(args, profile, output)
        if args.train:
            import torch

            torch.manual_seed(args.seed)
            actor = output / 'actor.ts'
            train(samples, labels, actor)
            create(profile, output / 'bundle', actor)
    except BaseException:
        import traceback

        traceback.print_exc()
        raise
    finally:
        app.close()


def simulate(args, profile, output):
    import json

    import torch
    from omegaconf import OmegaConf
    from omni.isaac.core.objects import GroundPlane
    from omni.isaac.core.simulation_context import SimulationContext
    from omni_drones.controllers import LeePositionController
    from omni_drones.robots.drone import MultirotorBase

    asset_dir = output / 'assets'
    write_assets(profile, asset_dir)

    class ProfiledQuad(MultirotorBase):
        usd_path = str(asset_dir / 'aircraft.usda')
        param_path = str(asset_dir / 'rotors.yaml')

    dt = .01
    cfg = OmegaConf.load(args.omni_root / 'examples' / 'demo.yaml')
    cfg.sim.dt = dt
    cfg.sim.device = f'cuda:{args.gpu}'
    sim = SimulationContext(stage_units_in_meters=1., physics_dt=dt, rendering_dt=dt, sim_params=cfg.sim,
                            backend='torch', device=cfg.sim.device)
    drone = ProfiledQuad()
    drone.spawn(translations=[(0., 0., 1.5)], prim_paths=['/World/ProfiledQuad'])
    GroundPlane('/World/Ground', z_position=0.)
    sim.reset()
    drone.initialize(prim_paths_expr='/World/ProfiledQuad')
    controller = LeePositionController(9.81, drone.params).to(drone.device)
    propulsion = profile['propulsion']
    drone.tau_up.fill_(1 - math.exp(-dt / propulsion['rise_s']))
    drone.tau_down.fill_(1 - math.exp(-dt / propulsion['fall_s']))
    drone.throttle.fill_(math.sqrt(profile['geometry']['takeoff_mass_kg'] * 9.81 / float(drone.KF.sum())))
    localization = profile['localization']
    rng = np.random.default_rng(args.seed)
    config = DEFAULTS
    calibration = json.loads(args.calibration.read_text(encoding='utf-8')) if args.calibration else None
    if calibration is not None and calibration['profile_sha256'] != profile_hash(args.profile):
        raise ValueError('标定报告不属于当前机型档案')
    gains, proxy_delays = np.ones(3), np.zeros(3)
    if calibration:
        for i, axis in enumerate('xyz'):
            fit = calibration['axes'][f'step-{axis}']
            values = fit['nominal']
            if args.randomize:
                values = {name: rng.uniform(*fit['range'][name]) for name in ('gain', 'tau_s', 'delay_s')}
            gains[i] = values['gain']
            proxy_delays[i] = max(0., values['delay_s'] - localization['delay_s'] - propulsion['rise_s'] / 2)
            controller.vel_gain[i] = min(6., max(.4, 1 / values['tau_s']))
    replay = csv_data(args.replay, COLUMNS) if args.replay else None
    if replay is not None:
        duration = replay[-1, 0] - replay[0, 0]
        origin = replay[0, 1:4] - [0., 0., 1.5]
    else:
        duration = args.seconds
        origin = np.zeros(3)
    previous = deque(maxlen=math.ceil(localization['delay_s'] / dt) + 2)
    incoming = deque(maxlen=math.ceil(max(proxy_delays) / dt) + 2)
    samples, labels, rows, estimates = [], [], [], []
    actor = None
    if args.actor:
        actor = torch.jit.load(str(args.actor), map_location='cpu').eval()
    last_observation_time = -math.inf
    last_observed = None
    action = np.zeros(3)
    yaw_target = torch.zeros(1, device=drone.device)
    with torch.no_grad():
        for step in range(int(duration / dt) + 1):
            t = step * dt
            state = drone.get_state()[..., :13].reshape(1, 13)
            raw = state[0].cpu().numpy()
            if not np.isfinite(raw).all() or raw[2] < .2:
                raise RuntimeError(f'仿真状态无效或触地，t={t:.2f}s')
            position = raw[:3] + origin
            velocity = raw[7:10]
            w, x, y, z = raw[3:7]
            yaw = math.atan2(2 * (w*z + x*y), 1 - 2 * (y*y + z*z))
            previous.append((position, velocity.copy(), yaw))
            if t + 1e-9 >= last_observation_time + 1 / localization['rate_hz']:
                delayed = min(len(previous)-1, round(localization['delay_s']/dt))
                p, v, h = previous[-1-delayed]
                last_observed = (p + rng.normal(0, localization['position_noise_std_m'], 3),
                                 v + rng.normal(0, localization['velocity_noise_std_mps'], 3), h)
                last_observation_time = t
            if replay is not None:
                index = np.searchsorted(replay[:, 0], replay[0, 0] + t + 1e-8, side='right') - 1
                goal = replay[index, 7:10]
                voltage = replay[index, -1]
            else:
                goal = np.array([.5 * math.sin(.65 * t), .4 * math.sin(.43 * t), 1.5 + .13 * math.sin(.33*t)])
                voltage = profile['battery']['voltage_window_v'][1]
            if replay is not None and not args.reference_replay:
                action = limit(replay[index, 10:13], config['speed_limit_mps'])
            else:
                if step % round(1 / (dt * config['policy_rate_hz'])) == 0:
                    p, v, h = last_observed
                    obs = observation(p, v, h, goal, config)
                    label = limit(.8 * (goal - p), config['speed_limit_mps'])
                    samples.append(obs)
                    labels.append(label)
                    raw_action = actor(torch.from_numpy(obs[None]))[0].numpy() if actor else label
                    action = limit(raw_action, config['speed_limit_mps'])
            incoming.append(action)
            motor_command = [incoming[-1-min(len(incoming)-1, round(delay/dt))][i] * gains[i]
                             for i, delay in enumerate(proxy_delays)]
            target = torch.tensor([motor_command], device=drone.device, dtype=state.dtype)
            commands = controller.compute(state, target_vel=target, target_yaw=yaw_target)
            drone.apply_action(commands.reshape(1, 1, 4))
            stamp = t + (replay[0, 0] if replay is not None else 0.)
            rows.append([stamp, *position, *velocity, *goal, *action, voltage])
            measured_position, measured_velocity, _ = last_observed
            estimates.append([stamp, *measured_position, *measured_velocity, *goal, *action, voltage])
            sim.step(render=False)
    for name, log in (('rollout.csv', rows), ('estimate.csv', estimates)):
        with (output / name).open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(COLUMNS)
            writer.writerows(log)
    print(f'OmniDrones simulated {len(rows)} steps', flush=True)
    return samples, labels


def profile_hash(path):
    from .bundle import sha256

    return sha256(path)


def train(samples, labels, out):
    import torch
    from torch import nn

    torch.set_num_threads(1)
    inputs, targets = [torch.as_tensor(np.asarray(values), dtype=torch.float32) for values in (samples, labels)]
    model = nn.Sequential(nn.Linear(len(OBSERVATIONS), 32), nn.Tanh(), nn.Linear(32, 3))
    optimizer = torch.optim.Adam(model.parameters(), lr=.008)
    for _ in range(350):
        loss = nn.functional.mse_loss(model(inputs), targets)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
    print(f'Behavior cloning MSE: {loss.item():.7f}', flush=True)
    torch.jit.trace(model.eval(), inputs[:1]).save(str(out))


def main():
    parser = argparse.ArgumentParser(description='OmniDrones rollout and baseline behavior cloning')
    parser.add_argument('profile', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--omni-root', type=Path, default=Path(os.environ.get(
        'OMNIDRONES_ROOT', Path.home() / 'omnidrones_fegru' / 'OmniDrones')))
    parser.add_argument('--seconds', type=float, default=24.)
    parser.add_argument('--gpu', type=int, default=0)
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--calibration', type=Path)
    parser.add_argument('--randomize', action='store_true')
    parser.add_argument('--replay', type=Path)
    parser.add_argument('--reference-replay', action='store_true', help='Run the policy against replay reference positions')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--train', action='store_true')
    mode.add_argument('--actor', type=Path)
    args = parser.parse_args()
    if ((args.reference_replay and not args.replay)
            or (args.replay and not args.reference_replay and (args.train or args.actor))
            or (args.randomize and not args.calibration) or args.seconds <= 0):
        parser.error('检查 --replay、--train、--actor、--calibration、--randomize 和时长参数')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    profile = load(args.profile)
    rollout(args, profile, args.output)


if __name__ == '__main__':
    main()
