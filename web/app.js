import { setupTutorial } from './tutorial.js';

const $ = id => document.getElementById(id);
const sources = {vio:'视觉惯性里程计（VIO）', flow_range:'光流 + 测距', gnss:'GNSS', rtk:'RTK', external:'其他外部定位', none:'暂无位置估计'};
const scenarios = {hover:'悬停', 'step-x':'X 轴阶跃', 'step-y':'Y 轴阶跃', 'step-z':'Z 轴阶跃', trajectory:'轨迹跟踪'};
const statusLabels = {specification:'规格', estimated:'估计', measured:'实测'};
const sections = [
  ['基础与硬件', [
    ['id','机型 ID','text'], ['revision','版本号','number'], ['geometry.wheelbase_m','轴距（m）','number'],
    ['geometry.takeoff_mass_kg','起飞重量（kg，含电池）','number'], ['hardware.motor','电机','text'],
    ['hardware.propeller','桨','text'], ['hardware.esc','电调','text'], ['hardware.flight_controller','飞控型号','text'],
    ['hardware.firmware','PX4 固件版本','text'], ['hardware.companion','机载电脑与系统','text'],
  ]],
  ['电池与定位', [
    ['battery.cells','电池串数','number'], ['battery.capacity_mah','容量（mAh）','number'],
    ['battery.voltage_window_v','验收电压范围（V，最低 / 最高）','vector'], ['localization.source','PX4 定位来源',sources],
  ]],
  ['动力学与定位细节', [
    ['geometry.com_flu_m','质心 XYZ（m）','vector',
      '实飞装配断电后，从机架侧面和正面分别记录两种悬挂姿态下的铅垂线，用交点确定质心。相对旋翼坐标所用原点填写 X 前、Y 左、Z 上的有符号距离。'],
    ['geometry.inertia_kg_m2','机体轴惯量 XYZ（kg·m²）','vector',
      '双线摆：分别让通过质心的 X、Y、Z 轴竖直，记录多周期小角度摆动并求平均周期 T。两线等长 L、上下间距同为 d 时，I = m g d² T² / (16π² L)，扣除夹具惯量。也可用 CAD 按实际装配质量估算。'],
    ['propulsion.thrust_coefficient','推力系数（N/(rad/s)²）','number',
      '用带防护的单旋翼测力台，按实际电机、桨和电压记录多组稳态推力 F 与转速 ω。传感器去皮后，按 F = kω² 拟合 k。转速用 RPM × 2π/60 转成 rad/s。'],
    ['propulsion.torque_coefficient','力矩系数（N·m/(rad/s)²）','number',
      '用带扭矩测量的旋翼台架读取各稳态转速的反扭矩绝对值 Q，按 Q = kω² 拟合 k。若用力臂测力，Q = 力 × 垂直力臂。'],
    ['propulsion.max_rad_s','最大角速度（rad/s）','number',
      '台架在记录的工作电压下，使用实装桨和电调，在允许的最大输出读取稳定负载转速。用机械 RPM × 2π/60 换算，记录对应电压。'],
    ['propulsion.rise_s','电机加速时间常数（s）','number',
      '在防护台架给较低到较高的转速指令阶跃，记录同步转速。扣除起始纯延迟，读取转速完成全变化量 63.2% 的时间，多次取中位数。'],
    ['propulsion.fall_s','电机减速时间常数（s）','number',
      '在相同台架给较高到较低的转速指令阶跃，扣除起始纯延迟，读取转速下降了全变化量 63.2% 的时间，多次取中位数。'],
    ['localization.mounting_xyz_flu_m','定位安装 XYZ（m）','vector',
      '用卡尺或装配图测机体原点到 VIO 标定参考点或光流镜头中心的位移，按 X 前、Y 左、Z 上记正负。测距器单独安装时，在来源备注记录它的偏移。'],
    ['localization.mounting_rpy_rad','定位安装 RPY（rad）','vector',
      '从外参标定或装配图读取传感器到机体 FLU 的旋转，按右手系写成 Roll、Pitch、Yaw（R = Rz·Ry·Rx）。用角尺核对安装方向，角度乘 π/180 转为弧度。'],
    ['localization.rate_hz','定位更新率（Hz）','number',
      '在机载端记录策略实际收到的有效 PX4 融合本地位置消息，按接收时间计算相邻间隔，取中位数的倒数。仅有 ULog 时，先核对日志是否降采样，并注明测量点。'],
    ['localization.delay_s','定位延迟（s）','number',
      '用标尺和已同步的视频记录离散往返位移，对齐机载接收时间，对比参考与 PX4 估计的同轴速度峰值或互相关，取多次滞后的中位数。仅有采样与接收时间差时，注明它是链路近似。'],
    ['localization.position_noise_std_m','位置噪声标准差（m）','number',
      '在代表性照明、纹理和高度下采集有效融合位置。对齐独立参考后计算三轴误差标准差，填三轴最大值并保留各轴结果。只有静置记录时，计算去均值后的短时波动，另记漂移。'],
    ['localization.velocity_noise_std_mps','速度噪声标准差（m/s）','number',
      '采集有效的 PX4 融合速度。静置时以零速度为参考；有同步独立参考时，计算同坐标系下各轴速度残差的标准差。填三轴最大值并保留各轴结果。'],
  ]],
];
const groups = {geometry:'几何与质量',hardware:'硬件',battery:'电池',propulsion:'推进',localization:'定位'};
let example;
let exampleMode = false;
let profiles = [];
const profileForm = $('profile-form');
const flightForm = $('flight-form');

function element(tag, content, className) {
  const node = document.createElement(tag);
  if (content) node.textContent = content;
  if (className) node.className = className;
  return node;
}

function options(select, choices) {
  select.replaceChildren(...Object.entries(choices).map(([value, label]) => new Option(label, value)));
}

function field(name, title, type, measurement) {
  const label = element('label');
  const caption = element('span', title);
  const input = element(typeof type === 'object' ? 'select' : 'input');
  input.required = true;
  input.name = name;
  if (typeof type === 'object') options(input, type);
  else {
    input.type = type === 'number' ? 'number' : 'text';
    if (type === 'number') input.step = 'any';
    if (type === 'vector') input.placeholder = '用逗号分隔';
  }
  label.append(caption, input);
  if (measurement) {
    const note = element('span', '测量方法 · ' + measurement, 'measurement-note');
    caption.id = `caption-${name}`;
    note.id = `measurement-${name}`;
    input.setAttribute('aria-labelledby', caption.id);
    input.setAttribute('aria-describedby', note.id);
    label.append(note);
  }
  return label;
}

function setupForm() {
  const parent = $('profile-fields');
  for (const [index, [title, entries]] of sections.entries()) {
    const group = element(index === 2 ? 'details' : 'fieldset');
    group.append(element(index === 2 ? 'summary' : 'legend', title));
    if (index === 2) {
      group.append(element('p', '无动捕可用同步视频与标尺作独立参考。没有台架或独立参考时，可按同配置规格或短时日志估算；在「参数来源与 PX4 快照」中标为估计，并注明涉及的字段、依据和测量工况。', 'field-note'));
    }
    const fields = element('div', '', 'fields');
    fields.append(...entries.map(entry => field(...entry)));
    group.append(fields);
    parent.append(group);
    if (index !== 0) continue;
    const motors = element('fieldset');
    const layout = element('div','','inline');
    const suggest = element('button','按轴距填标准 X 架','secondary');
    suggest.type = 'button';
    suggest.addEventListener('click',() => {
      const size = Number(profileForm.elements['geometry.wheelbase_m'].value);
      if (!(size > 0)) { notice('请先输入轴距。',true); return; }
      const offset = size / (2 * Math.SQRT2);
      [[1,-1],[-1,1],[1,1],[-1,-1]].forEach(([x,y],i) => {
        [x*offset,y*offset,0].forEach((value,j) => { profileForm.elements[`rotor.${i}.${j}`].value = value.toFixed(8); });
        profileForm.elements[`rotor.${i}.direction`].value = i < 2 ? 'ccw' : 'cw';
      });
      notice('已填标准 X 架坐标，请按实际 PX4 电机编号和旋向核对。');
    });
    layout.append(element('p','机体系 FLU：X 向前、Y 向左、Z 向上。从上方观察旋向。','field-note'),suggest);
    motors.append(element('legend','旋翼坐标与旋向'),layout);
    const grid = element('div','','rotor-grid');
    for (let i = 0; i < 4; i++) {
      const row = element('div','','rotor');
      row.append(element('span',`M${i+1}`), ...['X','Y','Z'].map((axis, j) => field(`rotor.${i}.${j}`,`${axis}（m）`,'number')),
        field(`rotor.${i}.direction`,'旋向',{cw:'CW',ccw:'CCW'}));
      grid.append(row);
    }
    motors.append(grid);
    parent.append(motors);
  }
  const provenance = element('details');
  provenance.append(element('summary','参数来源与 PX4 快照'));
  const fields = element('div','','fields');
  for (const [name,label] of Object.entries(groups)) {
    fields.append(field(`provenance.${name}.status`,`${label}状态`,statusLabels), field(`provenance.${name}.source`,`${label}来源`,'text'));
  }
  const snapshot = element('label','PX4 参数快照（可选）','wide');
  const input = element('input');
  input.type = 'file'; input.name = 'px4_params'; input.accept = '.params,.param,.txt';
  snapshot.append(input); fields.append(snapshot); provenance.append(fields); parent.append(provenance);
  profileForm.addEventListener('invalid', event => {
    const details = event.target.closest('details');
    if (details) details.open = true;
  }, true);
}

function assign(root, path, value) {
  const keys = path.split('.');
  const last = keys.pop();
  const parent = keys.reduce((object, key) => (object[key] ??= {}), root);
  parent[last] = value;
}

function lookup(root, path) {
  return path.split('.').reduce((object,key) => object[key],root);
}

function fill(profile) {
  exampleMode = Boolean(profile.example);
  for (const [,entries] of sections) for (const [path] of entries) {
    const value = lookup(profile,path);
    profileForm.elements[path].value = Array.isArray(value) ? value.join(', ') : value;
  }
  profile.geometry.rotors.forEach((rotor,i) => {
    rotor.xyz_flu_m.forEach((value,j) => { profileForm.elements[`rotor.${i}.${j}`].value = value; });
    profileForm.elements[`rotor.${i}.direction`].value = rotor.direction;
  });
  for (const name of Object.keys(groups)) for (const key of ['status','source']) {
    profileForm.elements[`provenance.${name}.${key}`].value = profile.provenance[name][key];
  }
  $('profile-state').textContent = exampleMode ? '当前内容来自示例机型' : '';
  profileForm.elements.example.checked = exampleMode;
  profileForm.elements.px4_params.value = '';
}

function readProfile() {
  const form = profileForm.elements;
  const profile = {example: form.example.checked};
  for (const [,entries] of sections) for (const [path,,type] of entries) {
    const value = form[path].value.trim();
    if (type === 'vector' && value.split(/[,，]/).some(item => !item.trim())) throw new Error(`${path} 包含空值`);
    assign(profile,path,type === 'vector' ? value.split(/[,，]/).map(Number) : type === 'number' ? Number(value) : value);
  }
  profile.geometry.rotors = Array.from({length:4}, (_,i) => ({output:i+1,
    xyz_flu_m:[0,1,2].map(j => Number(form[`rotor.${i}.${j}`].value)), direction:form[`rotor.${i}.direction`].value}));
  for (const name of Object.keys(groups)) for (const key of ['status','source']) {
    const path = `provenance.${name}.${key}`;
    assign(profile,path,form[path].value.trim());
  }
  return profile;
}

async function api(path, body) {
  const response = await fetch(`/api/${path}`, body ? {method:'POST',body} : {});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '请求失败');
  return result;
}

function notice(message, error = false) {
  const box = $('notice');
  box.hidden = false;
  box.textContent = message;
  box.classList.toggle('error',error);
  if (box.getBoundingClientRect().top < 0) box.scrollIntoView({block:'center'});
}

function profileKey(profile) { return `${profile.id}-r${profile.revision}`; }

function pickSource() {
  const profile = profiles.find(item => profileKey(item) === $('flight-profile').value);
  $('flight-source').value = profile?.localization.source || 'vio';
}

function renderProfiles() {
  $('profile-count').textContent = profiles.length;
  const current = $('flight-profile').value;
  $('flight-profile').replaceChildren(new Option('请选择机型版本',''), ...profiles.map(item => new Option(profileKey(item),profileKey(item))));
  if (current) $('flight-profile').value = current;
  const workshop = $('workshop-profile');
  const selected = workshop.value;
  workshop.replaceChildren(...Array.from($('flight-profile').options, option => option.cloneNode(true)));
  workshop.value = selected;
  tutorial.setProfiles(profiles.map(profileKey));
  $('save-flight').disabled = !profiles.length;
  const list = $('profile-list');
  list.replaceChildren();
  for (const profile of profiles) {
    const card = element('div','','record');
    const identifier = profileKey(profile);
    const download = element('a','下载 YAML');
    download.href = `/api/profiles/${identifier}/download`;
    const edit = element('button','复制为新版本','secondary');
    edit.type = 'button';
    edit.addEventListener('click',() => {
      const latest = Math.max(...profiles.filter(item => item.id === profile.id).map(item => item.revision));
      fill({...profile,revision:latest+1});
      profileForm.elements.id.focus();
    });
    const details = element('div','','inline');
    details.append(download,edit);
    card.append(element('h3',identifier), element('small',`${profile.geometry.takeoff_mass_kg} kg · ${profile.geometry.wheelbase_m} m · ${sources[profile.localization.source]}`),details);
    list.append(card);
  }
  if (!profiles.length) list.append(element('p','保存机型后，可登记该版本的试飞日志。','empty'));
}

async function refreshFlights() {
  const records = await api('flights');
  $('flight-count').textContent = records.length;
  const list = $('flight-list');
  list.replaceChildren();
  for (const flight of records) {
    const card = element('div','','record');
    const date = new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short'}).format(new Date(flight.created_at));
    const tag = element('span',flight.truth === 'px4_estimate' ? '基于 PX4 估计' : '已上传独立参考','tag');
    card.append(element('h3',`${scenarios[flight.scenario]} · ${flight.purpose === 'fit' ? '标定' : '验收'}`),
      element('small',`${flight.profile} · ${sources[flight.source]} · ${date}`),
      element('small',`${flight.files.ulog.name} · ${flight.voltage_v} V`));
    const tags = element('div','','inline');
    tags.append(tag);
    if (!flight.voltage_in_window) tags.append(element('span','超出电压窗口','tag alert'));
    const links = element('div','','inline');
    const labels = {ulog:'下载 ULog', commands:'机载指令', reference:'参考轨迹'};
    for (const kind of Object.keys(flight.files)) {
      const link = element('a',labels[kind]);
      link.href = `/api/flights/${flight.id}/${kind}`;
      links.append(link);
    }
    card.append(tags);
    if (flight.notes) card.append(element('small',flight.notes));
    card.append(links);
    list.append(card);
  }
  if (!records.length) list.append(element('p','暂无飞行日志。','empty'));
}

const tabs = ['aircraft','flights','workshop','tutorial-edit','tutorial-view'];
for (const tab of tabs) $('tab-'+tab).addEventListener('click',() => {
  for (const item of tabs) {
    $('tab-'+item).setAttribute('aria-selected',String(item === tab));
    $(item).hidden = item !== tab;
  }
  $('notice').hidden = true;
  if (tab === 'tutorial-view') tutorial.refreshPlayer();
  else tutorial.pause();
});

const workshopProfile = $('workshop-profile');
const pidForm = $('pid-form');
const wiringForm = $('wiring-form');
const addWiring = $('add-wiring');
const wiringPreview = $('wiring-preview');
const wiringPreviewImage = wiringPreview.querySelector('img');
let wiringRequest = 0;
let wiringPreviewUrl;

function clearWiringPreview() {
  if (wiringPreviewUrl) URL.revokeObjectURL(wiringPreviewUrl);
  wiringPreviewUrl = undefined;
  wiringPreviewImage.removeAttribute('src');
  wiringPreview.hidden = true;
}

function closeWiringForm() {
  wiringForm.reset();
  wiringForm.elements.image.setCustomValidity('');
  clearWiringPreview();
  wiringForm.hidden = true;
  addWiring.setAttribute('aria-expanded','false');
}

addWiring.addEventListener('click',() => {
  wiringForm.hidden = false;
  addWiring.setAttribute('aria-expanded','true');
  wiringForm.elements.caption.focus();
});

$('cancel-wiring').addEventListener('click',() => {
  closeWiringForm();
  addWiring.focus();
});

wiringForm.elements.image.addEventListener('change',() => {
  const input = wiringForm.elements.image;
  const [file] = input.files;
  clearWiringPreview();
  input.setCustomValidity('');
  if (!file) return;
  if (file.size > 10 * 1024 ** 2) {
    input.setCustomValidity('参考图片不能超过 10 MiB。');
    input.reportValidity();
    return;
  }
  wiringPreviewUrl = URL.createObjectURL(file);
  wiringPreviewImage.src = wiringPreviewUrl;
  wiringPreview.hidden = false;
});

wiringPreviewImage.addEventListener('error',() => {
  if (!wiringPreviewUrl) return;
  clearWiringPreview();
  wiringForm.elements.image.setCustomValidity('无法预览，请选择有效的 JPG 或 PNG 图片。');
  wiringForm.elements.image.reportValidity();
});

async function refreshWiring() {
  const identifier = workshopProfile.value;
  const request = ++wiringRequest;
  const list = $('wiring-list');
  list.replaceChildren();
  if (!identifier) return;
  const endpoint = `profiles/${identifier}/wiring`;
  const records = await api(endpoint);
  if (request !== wiringRequest) return;
  for (const record of records) {
    const photo = element('figure','','wiring-photo');
    const link = element('a');
    link.href = `/api/${endpoint}/${record.id}`;
    link.target = '_blank';
    link.rel = 'noopener';
    const image = element('img');
    image.src = link.href;
    image.alt = record.caption;
    image.loading = 'lazy';
    link.append(image);
    const caption = element('figcaption',record.caption,'wiring-caption');
    const remove = element('button','删除','secondary');
    remove.type = 'button';
    remove.setAttribute('aria-label',`删除焊接示例：${record.caption}`);
    remove.addEventListener('click',async () => {
      if (!window.confirm(`确定删除 ${identifier} 的焊接示例「${record.caption}」及其参考图片？`)) return;
      remove.disabled = true;
      try {
        const response = await fetch(link.href, {method:'DELETE'});
        if (!response.ok && response.status !== 404) throw new Error('删除失败，请重试。');
        if (workshopProfile.value === identifier) await refreshWiring();
        notice(response.ok ? '已删除焊接示例。' : '这条焊接示例已不存在。');
      } catch (error) { notice(error.message,true); }
      finally { remove.disabled = false; }
    });
    caption.append(remove);
    photo.append(link,caption);
    list.append(photo);
  }
  if (!records.length) list.append(element('p','这个版本还没有参考照片。','field-note'));
}

workshopProfile.addEventListener('change',async () => {
  $('workshop-tools').hidden = !workshopProfile.value;
  $('workshop-empty').hidden = Boolean(workshopProfile.value);
  $('pid-result').replaceChildren();
  pidForm.reset();
  addWiring.disabled = !workshopProfile.value;
  $('wiring-select-help').hidden = Boolean(workshopProfile.value);
  closeWiringForm();
  try { await refreshWiring(); }
  catch (error) { notice(error.message,true); }
});

pidForm.addEventListener('submit',async event => {
  event.preventDefault();
  const identifier = workshopProfile.value;
  const result = $('pid-result');
  const button = event.submitter;
  result.replaceChildren();
  button.disabled = true;
  try {
    const rows = await api(`profiles/${identifier}/pid-compare`,new FormData(pidForm));
    if (workshopProfile.value !== identifier) return;
    const changed = rows.filter(row => row.status === 'changed').length;
    const missing = rows.filter(row => row.status === 'missing').length;
    const table = element('table','','pid-table');
    const headings = table.createTHead().insertRow();
    for (const title of ['参数','参考值','当前值','状态']) {
      const heading = element('th',title);
      heading.scope = 'col';
      headings.append(heading);
    }
    const body = table.createTBody();
    const labels = {same:'一致',changed:'不同',missing:'文件中缺项'};
    for (const row of rows) {
      const line = body.insertRow();
      for (const value of [row.name,row.baseline ?? '—',row.current ?? '—',labels[row.status]]) {
        line.insertCell().textContent = value;
      }
    }
    result.append(element('p',`共 ${rows.length} 项，${changed} 项不同，${missing} 项缺失。`),table);
  } catch (error) { notice(error.message,true); }
  finally { button.disabled = false; }
});

wiringForm.addEventListener('submit',async event => {
  event.preventDefault();
  const identifier = workshopProfile.value;
  const button = event.submitter;
  button.disabled = true;
  try {
    await api(`profiles/${identifier}/wiring`,new FormData(wiringForm));
    if (workshopProfile.value === identifier) {
      closeWiringForm();
      addWiring.focus();
      await refreshWiring();
    }
    notice(`已上传 ${identifier} 的焊接示例。`);
  } catch (error) { notice(error.message,true); }
  finally { button.disabled = false; }
});

const tutorial = setupTutorial({$, element, api});
setupForm();
options($('flight-source'),Object.fromEntries(Object.entries(sources).filter(([key])=>key !== 'none')));
$('flight-profile').addEventListener('change',pickSource);
$('load-example').addEventListener('click',() => fill(example));
$('import-yaml').addEventListener('change',async event => {
  const [file] = event.target.files;
  if (!file) return;
  try {
    const body = new FormData(); body.append('yaml',file);
    fill(await api('parse-profile',body));
    notice('已导入表单，保存后生成机型版本。');
  } catch (error) { notice(error.message,true); }
  finally { event.target.value = ''; }
});

profileForm.addEventListener('submit',async event => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const body = new FormData();
    body.append('profile',JSON.stringify(readProfile()));
    const [snapshot] = profileForm.elements.px4_params.files;
    if (snapshot) body.append('px4_params',snapshot);
    const result = await api('profiles',body);
    profiles.push(result.profile);
    renderProfiles();
    $('flight-profile').value = result.id; pickSource();
    notice(`已保存 ${result.id} 并生成仿真资产。`);
  } catch (error) { notice(error.message,true); }
  finally { button.disabled = false; }
});

flightForm.addEventListener('submit',async event => {
  event.preventDefault();
  event.submitter.disabled = true;
  try {
    const result = await api('flights',new FormData(flightForm));
    flightForm.reset();
    $('flight-profile').value = result.profile;
    pickSource();
    await refreshFlights();
    notice('已上传日志并关联机型版本。');
  } catch (error) { notice(error.message,true); }
  finally { event.submitter.disabled = false; }
});

try {
  [example, profiles] = await Promise.all([api('example'),api('profiles')]);
  renderProfiles();
  fill({...example, example:false, id:'', hardware:Object.fromEntries(Object.keys(example.hardware).map(key=>[key,''])),
    provenance:Object.fromEntries(Object.entries(example.provenance).map(([key,value])=>[key,{...value,source:''}]))});
  await refreshFlights();
} catch (error) { notice(error.message,true); }
