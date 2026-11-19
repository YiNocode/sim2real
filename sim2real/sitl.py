import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

from .assets import write_assets
from .profile import load


def main():
    parser = argparse.ArgumentParser(description='Launch PX4 SITL with a generated Gazebo quadrotor')
    parser.add_argument('profile', type=Path)
    parser.add_argument('px4_root', type=Path)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--print-env', action='store_true')
    args = parser.parse_args()
    profile = load(args.profile)
    checkout = args.px4_root.resolve()
    px4 = checkout / 'build/px4_sitl_default/bin/px4'
    if not args.print_env and not px4.is_file():
        parser.error(f'PX4 SITL 未构建：{px4}')
    run = args.run_dir.resolve()
    run.mkdir(parents=True, exist_ok=False)
    assets = run / 'models' / 'sim2real_quad'
    write_assets(profile, assets)
    allocation = json.loads((assets / 'sitl-allocation.json').read_text(encoding='utf-8'))
    omega = profile['propulsion']['max_rad_s']
    for i in range(1, 5):
        allocation[f'SIM_GZ_EC_FUNC{i}'] = 100 + i
        allocation[f'SIM_GZ_EC_MIN{i}'] = round(.12 * omega)
        allocation[f'SIM_GZ_EC_MAX{i}'] = round(omega)
    paths = [str(assets.parent), os.environ.get('GZ_SIM_RESOURCE_PATH', '')]
    model_repo = checkout / 'Tools/simulation/gz'
    paths.extend(str(path) for path in (model_repo / 'models', model_repo / 'worlds') if path.is_dir())
    overrides = {f'PX4_PARAM_{name}': str(value) for name, value in allocation.items()}
    overrides.update(PX4_SYS_AUTOSTART='4001', PX4_GZ_STANDALONE='1', PX4_GZ_MODEL_NAME='sim2real_quad',
                     PX4_GZ_WORLD='default', HEADLESS='1',
                     GZ_SIM_RESOURCE_PATH=os.pathsep.join(path for path in paths if path))
    server_config = checkout / 'src/modules/simulation/gz_bridge/server.config'
    if server_config.is_file():
        overrides['GZ_SIM_SERVER_CONFIG_PATH'] = str(server_config)
    plugin_path = checkout / 'build/px4_sitl_default/src/modules/simulation/gz_plugins'
    if plugin_path.is_dir():
        overrides['GZ_SIM_SYSTEM_PLUGIN_PATH'] = os.pathsep.join(
            path for path in (str(plugin_path), os.environ.get('GZ_SIM_SYSTEM_PLUGIN_PATH', '')) if path)
    overrides['GZ_PARTITION'] = os.environ.get('GZ_PARTITION', f'sim2real_{os.getpid()}')
    if args.print_env:
        print(json.dumps(overrides, indent=2))
        return
    if shutil.which('gz') is None:
        parser.error('找不到 Gazebo gz 工具')
    template = model_repo / 'worlds' / 'default.sdf'
    if not template.is_file():
        parser.error(f'找不到 PX4 默认 Gazebo 世界：{template}')
    world_path = run / 'world.sdf'
    write_world(template, world_path)
    etc_candidates = (checkout / 'build/px4_sitl_default/rootfs/etc',
                      checkout / 'build/px4_sitl_default/etc', checkout / 'ROMFS/px4fmu_common')
    etc = next((path for path in etc_candidates if (path / 'init.d-posix/rcS').is_file()), None)
    if etc is None:
        parser.error('找不到已构建的 PX4 POSIX 启动脚本')
    (run / 'etc').symlink_to(etc, target_is_directory=True)
    environment = {**os.environ, **overrides}
    with (run / 'gazebo.log').open('wb') as log:
        gz = subprocess.Popen(['gz', 'sim', '-s', '-r', str(world_path)], env=environment,
                              cwd=run, stdout=log, stderr=subprocess.STDOUT)
        try:
            result = subprocess.run([str(px4), '-w', str(run)], cwd=run, env=environment)
        finally:
            gz.terminate()
            try:
                gz.wait(timeout=5)
            except subprocess.TimeoutExpired:
                gz.kill()
                gz.wait()
    raise SystemExit(result.returncode)


def write_world(template, output):
    sdf = ET.parse(template)
    world = sdf.getroot().find('world')
    if world is None or world.get('name') != 'default':
        raise ValueError('PX4 Gazebo 模板必须包含 default 世界')
    include = ET.SubElement(world, 'include')
    ET.SubElement(include, 'uri').text = 'model://sim2real_quad'
    ET.SubElement(include, 'name').text = 'sim2real_quad'
    ET.SubElement(include, 'pose').text = '0 0 0.15 0 0 0'
    sdf.write(output, encoding='utf-8', xml_declaration=True)


if __name__ == '__main__':
    main()
