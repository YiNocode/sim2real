import argparse
import csv
import math
import time


def main():
    parser = argparse.ArgumentParser(description='Publish a time-stamped local ENU reference at 20 Hz')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--hold', type=float, nargs=3, metavar=('E', 'N', 'U'))
    group.add_argument('--csv', help='Columns t_s,ref_x,ref_y,ref_z. Times are relative to the first row.')
    group.add_argument('--velocity-step', type=float, nargs=3, metavar=('E', 'N', 'U'))
    parser.add_argument('--start-after', type=float, default=10.)
    parser.add_argument('--step-seconds', type=float, default=2.)
    parser.add_argument('--topic')
    args = parser.parse_args()
    samples = [tuple([0., *args.hold])] if args.hold else []
    is_step = args.velocity_step is not None
    if is_step:
        step = args.velocity_step
        if (sum(v != 0 for v in step) != 1 or not .05 <= math.hypot(*step) <= .6
                or not 2 <= args.start_after <= 120 or not .5 <= args.step_seconds <= 5):
            parser.error('速度阶跃需要单轴 0.05–0.6 m/s，并提供有效的开始时间与持续时长')
        samples = [(0., 0., 0., 0.), (args.start_after, *step),
                   (args.start_after + args.step_seconds, 0., 0., 0.)]
    if args.csv:
        with open(args.csv, encoding='utf-8-sig', newline='') as stream:
            samples = [tuple(float(row[key]) for key in ('t_s', 'ref_x', 'ref_y', 'ref_z'))
                       for row in csv.DictReader(stream)]
    if not samples or not all(math.isfinite(v) for row in samples for v in row) or any(
            first[0] >= second[0] for first, second in zip(samples, samples[1:])):
        parser.error('参考需要有限、严格递增的时间戳以及有限坐标')

    import bisect

    import rclpy
    from geometry_msgs.msg import PointStamped, Vector3Stamped
    from rclpy.executors import ExternalShutdownException
    from rclpy.node import Node

    class Reference(Node):
        def __init__(self):
            super().__init__('sim2real_reference')
            topic = args.topic or ('/sim2real/calibration_velocity_enu' if is_step else '/sim2real/reference_enu')
            self.publisher = self.create_publisher(Vector3Stamped if is_step else PointStamped, topic, 10)
            self.times = [sample[0] - samples[0][0] for sample in samples]
            self.started = time.monotonic()
            self.create_timer(.05, self.publish)

        def publish(self):
            t = time.monotonic() - self.started
            i = bisect.bisect_right(self.times, t) - 1
            values = samples[i][1:]
            if not is_step and i + 1 < len(samples):
                a = (t - self.times[i]) / (self.times[i+1] - self.times[i])
                values = [x + a * (y - x) for x, y in zip(values, samples[i+1][1:])]
            msg = Vector3Stamped() if is_step else PointStamped()
            msg.header.frame_id = 'map_enu'
            msg.header.stamp = self.get_clock().now().to_msg()
            point = msg.vector if is_step else msg.point
            point.x, point.y, point.z = values
            self.publisher.publish(msg)

    rclpy.init()
    node = Reference()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
