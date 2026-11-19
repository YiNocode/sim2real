"""Integration test for an isolated local PX4/Gazebo instance on Linux."""

import argparse
import errno
import json
import os
import pty
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import rclpy
from geometry_msgs.msg import PointStamped
from px4_msgs.msg import TelemetryStatus, VehicleLocalPosition, VehicleStatus
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

from sim2real.bundle import create
from sim2real.profile import EXAMPLE, load


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('px4_root', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--bundle', type=Path)
    args = parser.parse_args()
    workspace = args.output.resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    bundle = args.bundle.resolve() if args.bundle else workspace / 'bundle'
    if not args.bundle:
        create(load(EXAMPLE), bundle)
    prefix = '/sim2real_smoke/fmu'
    os.environ['ROS_DOMAIN_ID'] = '83'
    env = {**os.environ, 'GZ_PARTITION': f'sim2real_{os.getpid()}', 'PX4_UXRCE_DDS_NS': 'sim2real_smoke',
           'PX4_UXRCE_DDS_PORT': '18888', 'PX4_PARAM_COM_OF_LOSS_T': '0.5', 'PX4_PARAM_COM_OBL_RC_ACT': '4',
           'PX4_PARAM_MIS_TAKEOFF_ALT': '2.5'}
    processes = []
    rclpy.init()
    node = Node('sim2real_smoke')
    status = None
    state = None

    def receive_status(msg):
        nonlocal status
        status = msg

    def receive_state(msg):
        nonlocal state
        state = msg

    for name in ('vehicle_status', 'vehicle_status_v1', 'vehicle_status_v2', 'vehicle_status_v4'):
        node.create_subscription(VehicleStatus, prefix + '/out/' + name, receive_status, qos_profile_sensor_data)
    for name in ('vehicle_local_position', 'vehicle_local_position_v1'):
        node.create_subscription(VehicleLocalPosition, prefix + '/out/' + name, receive_state, qos_profile_sensor_data)
    reference = node.create_publisher(PointStamped, '/sim2real/reference_enu', 10)
    telemetry = node.create_publisher(TelemetryStatus, prefix + '/in/telemetry_status', 10)
    desired = [0., 0., 1.5]
    publish_reference = True

    def publish():
        if publish_reference:
            msg = PointStamped()
            msg.header.frame_id = 'map_enu'
            msg.header.stamp = node.get_clock().now().to_msg()
            msg.point.x, msg.point.y, msg.point.z = desired
            reference.publish(msg)

    node.create_timer(.05, publish)

    def ground_station_heartbeat():
        message = TelemetryStatus(timestamp=node.get_clock().now().nanoseconds // 1000, heartbeat_type_gcs=True)
        telemetry.publish(message)

    node.create_timer(.5, ground_station_heartbeat)

    def wait_for(condition, seconds, goal):
        print(f'SITL: {goal}', flush=True)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for process in processes:
                if process.poll() is not None:
                    raise RuntimeError(f'A SITL dependency exited at {goal}; see {workspace}')
            rclpy.spin_once(node, timeout_sec=.05)
            if condition():
                return
        state_flags = ({field: getattr(state, field) for field in
                        ('xy_valid', 'z_valid', 'v_xy_valid', 'v_z_valid', 'heading_good_for_control')}
                       if state is not None else None)
        nav = int(status.nav_state) if status is not None else None
        raise RuntimeError(f'Timed out at {goal}; state={state_flags}; nav_state={nav}; see {workspace}')

    def start(command, log, cwd=None):
        with (workspace / log).open('wb') as output:
            process = subprocess.Popen(command, env=env, stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True, cwd=cwd)
        processes.append(process)
        return process

    master, slave = pty.openpty()
    try:
        start(['MicroXRCEAgent', 'udp4', '-p', '18888'], 'dds.log')
        px4 = subprocess.Popen([sys.executable, '-m', 'sim2real.sitl', str(EXAMPLE), str(args.px4_root),
                                str(workspace / 'sitl')], stdin=slave, stdout=slave, stderr=slave,
                               start_new_session=True, env=env)
        os.close(slave)
        processes.append(px4)

        def drain():
            with (workspace / 'px4.log').open('wb', buffering=0) as log:
                while True:
                    try:
                        chunk = os.read(master, 16384)
                    except OSError as error:
                        if error.errno in (errno.EIO, errno.EBADF):
                            return
                        raise
                    if not chunk:
                        return
                    log.write(chunk)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        wait_for(lambda: state is not None and status is not None, 45, 'DDS position and status messages')
        wait_for(lambda: state.xy_valid and state.z_valid, 50, 'valid local position')
        os.write(master, b'commander takeoff\n')
        armed = lambda: status.arming_state == VehicleStatus.ARMING_STATE_ARMED
        wait_for(lambda: armed() or status.pre_flight_checks_pass, 25, 'PX4 preflight checks')
        if not armed():
            os.write(master, b'commander takeoff\n')
        wait_for(armed, 12, 'PX4 simulated takeoff')
        wait_for(lambda: state.z < -2.0 and abs(state.vz) < .4 and state.heading_good_for_control,
                 35, 'hover and final heading alignment')
        start([sys.executable, '-m', 'sim2real.px4', str(bundle), '--prefix', prefix,
               '--log', str(workspace / 'commands.csv'), '--execute', '--sitl'], 'runtime.log', cwd=bundle)
        commands = workspace / 'commands.csv'
        wait_for(lambda: commands.is_file(), 75, 'deployment process startup')
        wait_for(lambda: commands.stat().st_size > 400, 12, 'policy publishing')
        ready_since = time.monotonic()
        wait_for(lambda: time.monotonic() - ready_since > 1.4, 2, 'Offboard heartbeat warmup')
        os.write(master, b'commander mode offboard\n')
        wait_for(lambda: status.nav_state == VehicleStatus.NAVIGATION_STATE_OFFBOARD, 8, 'Offboard mode')
        hover_until = time.monotonic() + 2.
        wait_for(lambda: time.monotonic() > hover_until and -1.9 < state.z < -1.1 and abs(state.vz) < .4,
                 22, 'Offboard hover altitude')
        desired[0] = .44
        wait_for(lambda: state.y > .14, 12, 'ENU east position response')
        moved_east = float(state.y)
        publish_reference = False
        wait_for(lambda: status.nav_state != VehicleStatus.NAVIGATION_STATE_OFFBOARD, 6, 'Offboard loss recovery')
        report = {'hover': 'passed', 'east_tracking_m': moved_east, 'reference_loss': 'passed',
                  'fallback_nav_state': int(status.nav_state)}
        (workspace / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(report)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in reversed(processes):
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        os.close(master)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
