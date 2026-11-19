import argparse
import json
from pathlib import Path

from .assets import write_assets
from .bundle import create, sha256
from .calibration import AXES, evaluate, fit, normalize_ulog, trial_digest, write_report
from .profile import load


def main():
    parser = argparse.ArgumentParser(prog='sim2real')
    sub = parser.add_subparsers(dest='command', required=True)
    assets = sub.add_parser('assets', help='Generate OmniDrones and Gazebo assets')
    assets.add_argument('profile', type=Path)
    assets.add_argument('directory', type=Path)
    export = sub.add_parser('export', help='Build a baseline or TorchScript deployment bundle')
    export.add_argument('profile', type=Path)
    export.add_argument('directory', type=Path)
    export.add_argument('--actor', type=Path)
    normalize = sub.add_parser('normalize', help='Extract an aligned ENU flight data segment')
    normalize.add_argument('ulog', type=Path)
    normalize.add_argument('commands', type=Path)
    normalize.add_argument('output', type=Path)
    normalize.add_argument('--start-px4-s', required=True, type=float)
    normalize.add_argument('--end-px4-s', required=True, type=float)
    normalize.add_argument('--command-offset-s', required=True, type=float)
    identify = sub.add_parser('fit', help='Identify closed-loop gains, constants and delays')
    identify.add_argument('profile', type=Path)
    identify.add_argument('output', type=Path)
    for label, axis in zip(AXES, 'xyz'):
        identify.add_argument(f'--{label}', required=True, action='append', type=Path, dest=axis)
    verify = sub.add_parser('evaluate', help='Compare independent real and simulated trials')
    verify.add_argument('profile', type=Path)
    verify.add_argument('pairs', type=Path)
    verify.add_argument('output', type=Path)
    verify.add_argument('--calibration', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'normalize':
            normalize_ulog(args.ulog, args.commands, args.output, args.start_px4_s,
                           args.end_px4_s, args.command_offset_s)
            return
        profile = load(args.profile)
        if args.command == 'assets':
            write_assets(profile, args.directory)
        if args.command == 'export':
            create(profile, args.directory, args.actor)
        if args.command == 'fit':
            datasets = {scenario: getattr(args, scenario[-1]) for scenario in AXES}
            write_report(args.output, {'profile_sha256': sha256(args.profile), 'axes': fit(datasets, profile),
                                      'input_sha256': [trial_digest(path) for paths in datasets.values() for path in paths]})
        if args.command == 'evaluate':
            pairs = json.loads(args.pairs.read_text(encoding='utf-8'))
            calibration = json.loads(args.calibration.read_text(encoding='utf-8'))
            if calibration['profile_sha256'] != sha256(args.profile):
                raise ValueError('标定报告的机型哈希不匹配')
            write_report(args.output, evaluate(pairs, profile, calibration_digests=calibration['input_sha256']))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    main()
