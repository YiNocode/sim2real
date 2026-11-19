import argparse
import csv
import math
import time
from pathlib import Path

import numpy as np

from .bundle import open_bundle
from .policy import Sample, enu_ned, fresh, heading_flip, limit, local_position, observation


class Control:
    def __init__(self, config, actor, velocity_calibration=False):
        self.config = config
        self.actor = actor
        self.velocity_calibration = velocity_calibration
        self.state = None
        self.reference = None
        self.latest = None
        self.last_resets = None
        self.estimate_reset = False

    def update_state(self, state):
        if state is not None:
            if self.last_resets is not None and self.last_resets != state.reset_counters:
                self.estimate_reset = True
            self.last_resets = state.reset_counters
        self.state = state
        if self.estimate_reset or state is None:
            self.latest = None

    def infer(self, now):
        self.latest = None
        if self.estimate_reset or not fresh(self.state, now, self.config['timeout_s']) or not fresh(
                self.reference, now, self.config['timeout_s']):
            return
        target = self.state.position if self.velocity_calibration else self.reference.position
        inputs = observation(self.state.position, self.state.velocity, self.state.yaw, target, self.config)
        if not np.isfinite(inputs).all():
            raise ValueError('观测包含无效数值')
        if self.velocity_calibration:
            action = self.reference.velocity
        elif self.actor is None:
            action = .8 * (np.asarray(target) - self.state.position)
        else:
            import torch
            with torch.inference_mode():
                action = self.actor(torch.from_numpy(inputs[None]))[0].numpy()
        output = limit(action, self.config['speed_limit_mps'])
        self.latest = Sample(position=target, velocity=output.tolist(), yaw=0., received=now, reset_counters=())

    def ready(self, now):
        timeout = self.config['timeout_s']
        return (not self.estimate_reset and fresh(self.state, now, timeout) and fresh(self.reference, now, timeout)
                and fresh(self.latest, now, timeout))


def main(argv=None):
    parser = argparse.ArgumentParser(description='PX4 ROS 2 velocity Offboard runtime')
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--prefix', default='/fmu')
    parser.add_argument('--local-position-topic', help='Override the firmware local-position topic')
    parser.add_argument('--reference-topic', default='/sim2real/reference_enu')
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--sitl', action='store_true', help='Use simulation data, may load example profiles')
    parser.add_argument('--velocity-calibration', action='store_true', help='Accept explicit velocity steps using the baseline bundle')
    args = parser.parse_args(argv)
    config, actor = open_bundle(args.bundle)
    if args.velocity_calibration and actor is not None:
        parser.error('速度阶跃需使用比例基线部署包')
    if args.execute and not args.sitl and (config['example'] or config['localization'] == 'none' or
                                          config['firmware'] in ('未填写', 'unknown')):
        parser.error('实飞机型需要真实档案、有效定位来源和已核对的固件版本')

    import rclpy
    from geometry_msgs.msg import PointStamped, Vector3Stamped
    from px4_msgs.msg import OffboardControlMode, TrajectorySetpoint, VehicleLocalPosition
    from rclpy.executors import ExternalShutdownException
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data

    class Runtime(Node):
        def __init__(self, writer, stream):
            super().__init__('sim2real_offboard')
            self.control = Control(config, actor, args.velocity_calibration)
            self.writer = writer
            self.stream = stream
            self.prefix = args.prefix.rstrip('/')
            state_topics = ([args.local_position_topic] if args.local_position_topic else
                            [self.prefix + '/out/vehicle_local_position' + suffix for suffix in ('', '_v1')])
            for topic in state_topics:
                self.create_subscription(VehicleLocalPosition, topic, self.receive_state, qos_profile_sensor_data)
            if args.velocity_calibration:
                self.create_subscription(Vector3Stamped, '/sim2real/calibration_velocity_enu',
                                         self.receive_velocity, qos_profile_sensor_data)
            else:
                self.create_subscription(PointStamped, args.reference_topic, self.receive_reference, qos_profile_sensor_data)
            self.mode = self.create_publisher(OffboardControlMode, self.prefix + '/in/offboard_control_mode', 10)
            self.setpoint = self.create_publisher(TrajectorySetpoint, self.prefix + '/in/trajectory_setpoint', 10)
            self.create_timer(1 / config['policy_rate_hz'], self.infer)
            self.create_timer(1 / config['setpoint_rate_hz'], self.publish)
            self.last_error = 0.

        def receive_state(self, msg):
            self.control.update_state(local_position(msg, time.monotonic()))

        def receive_reference(self, msg):
            values = [msg.point.x, msg.point.y, msg.point.z]
            self.set_reference(msg.header, values, False)

        def receive_velocity(self, msg):
            values = [msg.vector.x, msg.vector.y, msg.vector.z]
            self.set_reference(msg.header, values, True)

        def set_reference(self, header, values, is_velocity):
            valid = header.frame_id == 'map_enu' and all(map(math.isfinite, values))
            sent = header.stamp.sec + header.stamp.nanosec / 1e9
            age = self.get_clock().now().nanoseconds / 1e9 - sent
            position, velocity = ([], values) if is_velocity else (values, [])
            self.control.reference = (Sample(position, velocity, 0., time.monotonic() - max(0., age), ())
                                      if valid and -.05 <= age <= config['timeout_s'] else None)

        def infer(self):
            try:
                self.control.infer(time.monotonic())
            except (ValueError, RuntimeError, TypeError) as error:
                now = time.monotonic()
                if now - self.last_error > 1:
                    self.get_logger().error(str(error))
                    self.last_error = now

        def publish(self):
            if not self.control.ready(time.monotonic()):
                return
            latest = self.control.latest
            stamp = self.get_clock().now().nanoseconds // 1000
            action = enu_ned(latest.velocity)
            if args.execute:
                mode = OffboardControlMode(timestamp=stamp, velocity=True)
                trajectory = TrajectorySetpoint(timestamp=stamp, position=[math.nan]*3, velocity=action,
                                                acceleration=[math.nan]*3, jerk=[math.nan]*3,
                                                yaw=heading_flip(config['heading_enu_rad']), yawspeed=math.nan)
                self.setpoint.publish(trajectory)
                self.mode.publish(mode)
            self.writer.writerow([stamp / 1e6, *latest.position, *latest.velocity, int(args.execute)])
            self.stream.flush()

    rclpy.init()
    try:
        with args.log.open('x', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow('t_s ref_x ref_y ref_z cmd_vx cmd_vy cmd_vz published'.split())
            node = Runtime(writer, stream)
            try:
                rclpy.spin(node)
            except (KeyboardInterrupt, ExternalShutdownException):
                pass
            finally:
                node.destroy_node()
    finally:
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
