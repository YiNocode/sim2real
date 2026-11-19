import { setupTutorial } from './tutorial.js';

const $ = id => document.getElementById(id);
const element = (tag, content, className) => {
  const node = document.createElement(tag);
  if (content) node.textContent = content;
  if (className) node.className = className;
  return node;
};
const empty = () => ({materials:[],tools:[],steps:[]});
const keys = Object.keys(empty());
const types = {jpg:'image/jpeg',jpeg:'image/jpeg',png:'image/png',mp4:'video/mp4',webm:'video/webm'};
const MiB = 1024 ** 2;
const headerLimit = 2 * MiB;
const archiveLimit = 500 * MiB;
const urls = new Map();
const status = $('storage-status');
let state;
const ready = new Promise((resolve,reject) => {
  const request = indexedDB.open('sim2real-tutorial-editor',1);
  request.onupgradeneeded = () => request.result.createObjectStore('tutorials');
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(new Error('浏览器无法打开本地存储。'));
});

async function storage(mode, operation) {
  const database = await ready;
  return new Promise((resolve,reject) => {
    const transaction = database.transaction('tutorials',mode);
    const request = operation(transaction.objectStore('tutorials'));
    transaction.oncomplete = () => resolve(request.result);
    transaction.onabort = () => reject(new Error('无法保存或读取草稿，请检查浏览器存储空间。'));
  });
}

function validate(draft, assets, complete = false) {
  for (const key of keys) {
    if (!Array.isArray(draft?.[key]) || draft[key].length > 50) throw new Error('教程列表格式无效。');
    if (complete && !draft[key].length) throw new Error('请先添加材料、工具和操作步骤。');
    for (const row of draft[key]) {
      const fields = key === 'steps' ? {title:120,instructions:6000,outcome:3000} : {title:120};
      for (const [name,limit] of Object.entries(fields)) {
        if (typeof row?.[name] !== 'string' || row[name].length > limit || (complete && !row[name].trim())) {
          throw new Error('请填写名称、操作和完成结果，并检查文字长度。');
        }
      }
      if (!Array.isArray(row.media) || row.media.length > (key === 'steps' ? 8 : 1)) throw new Error('附件数量超限。');
      if (complete && key === 'materials' && !row.media.length) throw new Error('请为每项材料添加对应图片。');
      for (const name of row.media) {
        const asset = assets.get(name);
        if (!asset || (key !== 'steps' && !asset.type.startsWith('image/'))) throw new Error('教程附件缺失或格式无效。');
      }
    }
  }
}

async function save(next) {
  await storage('readwrite',store => store.put(next,'current'));
  for (const [name,url] of urls) {
    if (!next.assets.has(name)) {
      URL.revokeObjectURL(url);
      urls.delete(name);
    }
  }
  state = next;
}

async function api(path, form) {
  if (!form) return path.endsWith('/published') ? state.published : state;
  const model = $('model').value.trim();
  if (!model) throw new Error('请填写机型与版本。');
  const draft = JSON.parse(await form.get('tutorial').text());
  const assets = new Map(state.assets);
  for (const key of keys) {
    for (const row of draft[key]) {
      row.media = row.media.map(reference => {
        if (!reference.startsWith('upload_')) return reference;
        const file = form.get(reference);
        const extension = file?.name.split('.').pop().toLowerCase();
        const type = types[extension];
        if (!type || !file.size || file.size > (type.startsWith('video/') ? 100 : 10) * MiB) {
          throw new Error('附件格式或大小无效。');
        }
        const filename = `${crypto.randomUUID()}.${extension}`;
        assets.set(filename,file.slice(0,file.size,type));
        return filename;
      });
    }
  }
  const complete = form.get('action') === 'publish';
  validate(draft,assets,complete);
  const published = complete ? draft : state.published;
  const used = new Set([draft,published].filter(Boolean).flatMap(doc => keys.flatMap(key => doc[key].flatMap(row => row.media))));
  for (const name of assets.keys()) if (!used.has(name)) assets.delete(name);
  await save({model,draft,published,assets});
  return state;
}

const tutorial = setupTutorial({$,element,api,local:true,mediaUrl:(_,name) => {
  if (!urls.has(name)) urls.set(name,URL.createObjectURL(state.assets.get(name)));
  return urls.get(name);
}});
tutorial.setProfiles(['local']);
for (const id of ['tutorial-edit-profile','tutorial-view-profile']) $(id).value = 'local';
function loadEditor() {
  $('model').value = state.model;
  $('tutorial-edit-profile').dispatchEvent(new Event('change'));
}

function switchTab(preview) {
  $('tutorial-edit').hidden = preview;
  $('tutorial-view').hidden = !preview;
  $('edit-tab').setAttribute('aria-selected',String(!preview));
  $('view-tab').setAttribute('aria-selected',String(preview));
  if (preview) tutorial.refreshPlayer();
  else tutorial.pause();
}
$('edit-tab').onclick = () => switchTab(false);
$('view-tab').onclick = () => switchTab(true);

// A length-prefixed JSON manifest followed by the original binary attachments.
$('export').onclick = () => {
  if (!state?.model) { status.textContent = '请先填写机型并保存草稿。'; return; }
  const attachments = [...state.assets];
  const files = attachments.map(([name,blob]) => [name,blob.size]);
  const metadata = new TextEncoder().encode(JSON.stringify({
    format:'sim2real-tutorial-v1',model:state.model,draft:state.draft,published:state.published,files
  }));
  const length = new ArrayBuffer(4);
  new DataView(length).setUint32(0,metadata.length);
  const archive = new Blob([length,metadata,...attachments.map(([,blob]) => blob)]);
  if (metadata.length > headerLimit || archive.size > archiveLimit) {
    status.textContent = '教程包超过导出限制（500 MiB），请先缩小附件。';
    return;
  }
  const url = URL.createObjectURL(archive);
  const link = element('a');
  link.href = url;
  link.download = 'sim2real.s2rtutorial';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url),30000);
  status.textContent = '已导出教程和附件。';
};

$('import').onchange = async event => {
  const [file] = event.target.files;
  event.target.value = '';
  if (!file || !state) return;
  try {
    if (file.size < 4 || file.size > archiveLimit) throw new Error('教程包大小无效。');
    const length = new DataView(await file.slice(0,4).arrayBuffer()).getUint32(0);
    if (!length || length > headerLimit || length + 4 > file.size) throw new Error('教程包格式无效。');
    const manifest = JSON.parse(await file.slice(4,4 + length).text());
    const {format,model,draft,published,files} = manifest;
    if (format !== 'sim2real-tutorial-v1' || typeof model !== 'string' || !model.trim() || model.length > 120 || !Array.isArray(files)) {
      throw new Error('请选择此编写器导出的教程包。');
    }
    const assets = new Map();
    let offset = length + 4;
    for (const [name,size] of files) {
      const match = typeof name === 'string' && /^[a-f0-9-]{36}\.(jpg|jpeg|png|mp4|webm)$/.exec(name);
      const type = match && types[match[1]];
      if (!type || assets.has(name) || !Number.isSafeInteger(size) || size < 1 ||
          size > (type.startsWith('video/') ? 100 : 10) * MiB || offset + size > file.size) {
        throw new Error('教程包中的附件无效。');
      }
      assets.set(name,file.slice(offset,offset + size,type));
      offset += size;
    }
    if (offset !== file.size) throw new Error('教程包长度不匹配。');
    validate(draft,assets);
    if (published) validate(published,assets,true);
    if (!window.confirm('打开教程包会替换当前草稿及未保存的编辑。确定打开？')) return;
    await save({model,draft,published,assets});
    switchTab(false);
    loadEditor();
    status.textContent = '已打开教程包。';
  } catch (error) { status.textContent = error.message; }
};

try {
  state = await storage('readonly',store => store.get('current'))
    ?? {model:'',draft:empty(),published:null,assets:new Map()};
  loadEditor();
} catch (error) { status.textContent = error.message; }
