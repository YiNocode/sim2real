import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml


def write_assets(profile, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    geometry, motor = profile['geometry'], profile['propulsion']
    mass = geometry['takeoff_mass_kg']
    inertia = geometry['inertia_kg_m2']
    com = geometry['com_flu_m']
    rotors = geometry['rotors']
    locations = [rotor['xyz_flu_m'] for rotor in rotors]
    mixer_locations = [[x - com[0], y - com[1], z - com[2]] for x, y, z in locations]
    directions = [1 if r['direction'] == 'ccw' else -1 for r in rotors]
    config = {
        'name': 'hummingbird', 'mass': mass, 'l': geometry['wheelbase_m'], 'drag_coef': 0.0,
        'inertia': dict(zip(('xx', 'yy', 'zz'), inertia)),
        'rotor_configuration': {
            'num_rotors': 4, 'directions': directions,
            'arm_lengths': [math.hypot(x, y) for x, y, _ in mixer_locations],
            'rotor_angles': [math.atan2(y, x) for x, y, _ in mixer_locations],
            'force_constants': [motor['thrust_coefficient']] * 4,
            'moment_constants': [motor['torque_coefficient']] * 4,
            'max_rotation_velocities': [motor['max_rad_s']] * 4,
        },
    }
    geometry_matrix = [[y, -x, -d, 1] for (x, y, _), d in zip(mixer_locations, directions)]
    import numpy as np
    if np.linalg.matrix_rank(geometry_matrix) != 4:
        raise ValueError('旋翼几何和旋向无法提供完整的四轴控制分配')
    (directory / 'rotors.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')

    # Give rotor links positive mass and account for their mass, CoM and inertia in the base.
    rotor_mass = min(mass, min(inertia) / max(geometry['wheelbase_m'] ** 2, .000001)) * 0.0001
    base_mass = mass - 4 * rotor_mass
    base_com = [(mass * c - rotor_mass * sum(p[i] for p in locations)) / base_mass
                for i, c in enumerate(com)]
    rotor_inertia = rotor_mass * 1e-5
    offsets = np.array([np.subtract(base_com, com), *[np.subtract(p, com) for p in locations]])
    base_tensor = np.diag(inertia) - np.eye(3) * (4 * rotor_inertia)
    for offset, link_mass in zip(offsets, [base_mass, *([rotor_mass] * 4)]):
        base_tensor -= link_mass * (np.dot(offset, offset) * np.eye(3) - np.outer(offset, offset))
    eigenvalues, axes = np.linalg.eigh(base_tensor)
    if np.any(eigenvalues <= 0):
        raise ValueError('旋翼位置相对质心过远，整机惯量与几何档案不匹配')
    if np.linalg.det(axes) < 0:
        axes[:, 2] *= -1
    quaternion = rotation_quaternion(axes)
    usd = ['#usda 1.0', '(\n    defaultPrim = "Aircraft"\n    metersPerUnit = 1\n    upAxis = "Z"\n)',
           'def Xform "Aircraft" (prepend apiSchemas = ["PhysicsArticulationRootAPI"])', '{',
           rigid_link('base_link', base_mass, base_com, eigenvalues, quaternion)]
    for i, location in enumerate(locations):
        usd.append(rigid_link(f'rotor_{i}', rotor_mass, (0, 0, 0), [rotor_inertia] * 3,
                              (1, 0, 0, 0), location))
    usd.append('    def Scope "joints" {')
    for i, location in enumerate(locations):
        usd.append(f'''        def PhysicsRevoluteJoint "rotor_joint_{i}" {{
            rel physics:body0 = </Aircraft/base_link>
            rel physics:body1 = </Aircraft/rotor_{i}>
            point3f physics:localPos0 = {usd_tuple(location)}
            uniform token physics:axis = "Z"
        }}''')
    usd.append('    }')
    (directory / 'aircraft.usda').write_text('\n'.join([*usd, '}']) + '\n', encoding='utf-8')
    allocation = {'CA_ROTOR_COUNT': 4}
    for i, ((x, y, z), rotor) in enumerate(zip(mixer_locations, rotors)):
        allocation.update({f'CA_ROTOR{i}_PX': x, f'CA_ROTOR{i}_PY': -y, f'CA_ROTOR{i}_PZ': -z,
                           f'CA_ROTOR{i}_KM': (1 if rotor['direction'] == 'ccw' else -1)
                           * motor['torque_coefficient'] / motor['thrust_coefficient']})
    (directory / 'sitl-allocation.json').write_text(json.dumps(allocation, indent=2), encoding='utf-8')
    write_sdf(directory / 'model.sdf', profile, base_mass, base_com, base_tensor, rotor_mass, rotor_inertia)


def rotation_quaternion(matrix):
    import numpy as np
    # Largest-diagonal form avoids loss of precision near 180 degrees.
    diagonal = np.diag(matrix)
    values = np.array([1 + np.trace(matrix), *(1 + 2 * diagonal - np.trace(matrix))])
    index = int(np.argmax(values))
    main = math.sqrt(values[index]) / 2
    x, y, z = matrix[2, 1] - matrix[1, 2], matrix[0, 2] - matrix[2, 0], matrix[1, 0] - matrix[0, 1]
    xy, xz, yz = matrix[0, 1] + matrix[1, 0], matrix[0, 2] + matrix[2, 0], matrix[1, 2] + matrix[2, 1]
    result = ((0, x, y, z), (x, 0, xy, xz), (y, xy, 0, yz), (z, xz, yz, 0))[index]
    return tuple(main if j == index else value / (4 * main) for j, value in enumerate(result))


def usd_tuple(values):
    return '(' + ', '.join(f'{value:.12g}' for value in values) + ')'


def rigid_link(name, mass, com, inertia, quaternion, location=(0, 0, 0)):
    visual = '''
        def Cube "body" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {
            double size = 0.12
        }''' if name == 'base_link' else ''
    return f'''    def Xform "{name}" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]) {{
        double3 xformOp:translate = {usd_tuple(location)}
        uniform token[] xformOpOrder = ["xformOp:translate"]
        float physics:mass = {mass:.12g}
        point3f physics:centerOfMass = {usd_tuple(com)}
        float3 physics:diagonalInertia = {usd_tuple(inertia)}
        quatf physics:principalAxes = {usd_tuple(quaternion)}{visual}
    }}'''


def write_sdf(path, profile, mass, com, tensor, rotor_mass, rotor_inertia):
    def element(parent, tag, value=None, **attributes):
        child = ET.SubElement(parent, tag, attributes)
        if value is not None:
            child.text = str(value)
        return child

    def inertial(link, link_mass, location, entries):
        node = element(link, 'inertial')
        element(node, 'pose', ' '.join(map(str, [*location, 0, 0, 0])))
        element(node, 'mass', link_mass)
        matrix = element(node, 'inertia')
        for name, value in zip(('ixx', 'ixy', 'ixz', 'iyy', 'iyz', 'izz'), entries):
            element(matrix, name, value)

    def add_noise(origin, channel, standard_deviation):
        noise = element(element(origin, channel), 'noise', type='gaussian')
        element(noise, 'mean', 0)
        element(noise, 'stddev', standard_deviation)

    root = ET.Element('sdf', version='1.9')
    model = element(root, 'model', name='sim2real_quad')
    base = element(model, 'link', name='base_link')
    inertial(base, mass, com, [tensor[i, j] for i, j in ((0, 0), (0, 1), (0, 2), (1, 1), (1, 2), (2, 2))])
    for kind in ('collision', 'visual'):
        shape = element(element(element(base, kind, name=kind), 'geometry'), 'box')
        element(shape, 'size', '0.12 0.12 0.12')
    for kind, rate in (('imu', 250), ('air_pressure', 50), ('magnetometer', 100), ('navsat', 30)):
        sensor = element(base, 'sensor', name=f'{kind}_sensor', type=kind)
        element(sensor, 'gz_frame_id', 'base_link')
        element(sensor, 'always_on', 'true')
        element(sensor, 'update_rate', rate)

        if kind == 'imu':
            imu = element(sensor, 'imu')
            for channel, sigma in (('angular_velocity', .00087), ('linear_acceleration', .006)):
                vector = element(imu, channel)
                for axis in 'xyz':
                    add_noise(vector, axis, sigma)
        if kind == 'air_pressure':
            add_noise(element(sensor, kind), 'pressure', .1)
        if kind == 'magnetometer':
            magnetometer = element(sensor, kind)
            for axis in 'xyz':
                add_noise(magnetometer, axis, .0001)
    motor = profile['propulsion']
    for index, rotor in enumerate(profile['geometry']['rotors']):
        name = f'rotor_{index}'
        link = element(model, 'link', name=name)
        element(link, 'pose', ' '.join(map(str, [*rotor['xyz_flu_m'], 0, 0, 0])))
        inertial(link, rotor_mass, (0, 0, 0), [rotor_inertia, 0, 0, rotor_inertia, 0, rotor_inertia])
        joint = element(model, 'joint', name=f'{name}_joint', type='revolute')
        element(joint, 'parent', 'base_link')
        element(joint, 'child', name)
        element(element(joint, 'axis'), 'xyz', '0 0 1')
        plugin = element(model, 'plugin', filename='gz-sim-multicopter-motor-model-system',
                         name='gz::sim::systems::MulticopterMotorModel')
        for field, value in {
            'jointName': f'{name}_joint', 'linkName': name, 'turningDirection': rotor['direction'],
            'timeConstantUp': motor['rise_s'], 'timeConstantDown': motor['fall_s'],
            'maxRotVelocity': motor['max_rad_s'], 'motorConstant': motor['thrust_coefficient'],
            'momentConstant': motor['torque_coefficient'] / motor['thrust_coefficient'],
            'commandSubTopic': 'command/motor_speed', 'motorNumber': index,
            'rotorDragCoefficient': 0, 'rollingMomentCoefficient': 0, 'rotorVelocitySlowdownSim': 10,
            'motorType': 'velocity',
        }.items():
            element(plugin, field, value)
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)
    path.with_name('model.config').write_text(
        '<model><name>sim2real_quad</name><version>1.0</version><sdf version="1.9">model.sdf</sdf></model>\n',
        encoding='utf-8')
