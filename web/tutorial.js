const groups = {materials:'制作材料', tools:'所需工具', steps:'装配步骤'};
const imageTypes = '.jpg,.jpeg,.png';
const videoTypes = '.mp4,.webm';

export function setupTutorial({$, element, api, mediaUrl, local = false}) {
  const editor = $('tutorial-editor');
  const editSelect = $('tutorial-edit-profile');
  const viewSelect = $('tutorial-view-profile');
  const player = $('tutorial-player');
  const slide = $('tutorial-slide');
  const nav = $('tutorial-step-nav');
  const lists = {};
  let position = 0;
  let published;
  let editRequest = 0;
  let viewRequest = 0;

  const endpoint = id => `profiles/${id}/tutorial`;
  const url = mediaUrl ?? ((id, name) => `/api/${endpoint(id)}/media/${name}`);
  const pause = () => slide.querySelectorAll('video').forEach(video => video.pause());

  function button(text, onClick) {
    const node = element('button',text,'secondary');
    node.type = 'button';
    node.addEventListener('click',onClick);
    return node;
  }

  function mediaNode(name, source, title) {
    const video = /\.(mp4|webm)$/i.test(name);
    const node = element(video ? 'video' : 'img');
    node.src = source;
    if (video) {
      node.controls = true;
      node.preload = 'metadata';
      node.playsInline = true;
      node.setAttribute('aria-label',title);
    } else {
      node.alt = title;
      node.loading = 'lazy';
    }
    return node;
  }

  function addRow(group, row = {}) {
    const step = group === 'steps';
    const card = element('section','','tutorial-card');
    const title = element('h4');
    const header = element('div','','tutorial-row-header');
    header.append(title);
    if (step) {
      header.append(button('上移',() => {
        if (card.previousElementSibling) card.previousElementSibling.before(card);
        renumber(group);
      }),button('下移',() => {
        if (card.nextElementSibling) card.nextElementSibling.after(card);
        renumber(group);
      }));
    }
    header.append(button('移除',() => {
      clearAttachmentPreviews();
      card.remove();
      renumber(group);
    }));
    card.append(header);
    function field(name, label, limit, multiline = false) {
      const holder = element('label',label);
      const input = element(multiline ? 'textarea' : 'input');
      input.name = name;
      input.maxLength = limit;
      input.value = row[name] ?? '';
      if (multiline) input.rows = name === 'instructions' ? 5 : 3;
      holder.append(input);
      card.append(holder);
    }
    field('title',step ? '步骤标题' : group === 'materials' ? '材料名称' : '工具名称',120);
    if (step) {
      field('instructions','详细操作步骤',6000,true);
      field('outcome','完成后的结果或检查标准',3000,true);
    }
    const attachments = element('ul','','tutorial-attachments');
    const media = [...(row.media ?? [])];
    const previewUrls = [];
    function clearAttachmentPreviews() {
      previewUrls.forEach(source => URL.revokeObjectURL(source));
      previewUrls.length = 0;
    }
    card.clearAttachmentPreviews = clearAttachmentPreviews;
    function showAttachments() {
      clearAttachmentPreviews();
      attachments.replaceChildren();
      for (const attachment of media) {
        const saved = typeof attachment === 'string';
        const name = saved ? attachment : attachment.name;
        const source = saved ? url(editSelect.value,attachment) : URL.createObjectURL(attachment);
        if (!saved) previewUrls.push(source);
        const item = element('li');
        const title = card.querySelector('[name=title]').value.trim() || '附件预览';
        const preview = mediaNode(name,source,title);
        if (preview.tagName === 'VIDEO') {
          const message = element('p','无法播放此视频。请尝试 MP4（H.264）或 WebM（VP8 / VP9）。','field-note');
          message.hidden = true;
          preview.addEventListener('error',() => { message.hidden = false; });
          item.append(preview,message);
        } else item.append(preview);
        item.append(button('移除附件',() => {
          media.splice(media.indexOf(attachment),1);
          showAttachments();
        }));
        attachments.append(item);
      }
    }
    function addFiles(labelText, accept, maxMiB) {
      const label = element('label',labelText);
      const files = element('input');
      files.type = 'file';
      files.accept = accept;
      files.multiple = step;
      label.append(files);
      card.append(label);
      files.addEventListener('change',() => {
        const selected = Array.from(files.files);
        files.value = '';
        const limit = step ? 8 : 1;
        if (!selected.length) return;
        const valid = selected.every(file => accept.split(',').some(suffix => file.name.toLowerCase().endsWith(suffix)));
        const oversized = selected.some(file => file.size > maxMiB * 1024 ** 2);
        if (!valid || oversized || (step && media.length + selected.length > limit)) {
          $('tutorial-edit-status').textContent = !valid ? '请选择此栏支持的文件格式。'
            : oversized ? `单个附件不能超过 ${maxMiB} MiB。` : '每步的图片和视频合计最多 8 个附件。';
          return;
        }
        if (!step) media.length = 0;
        media.push(...selected);
        $('tutorial-edit-status').textContent = '附件已选中，可直接预览。请保存草稿以保留附件。';
        showAttachments();
      });
    }
    addFiles(step ? '上传步骤图片' : '对应图片',imageTypes,10);
    if (step) addFiles('上传步骤视频（MP4 / WebM，单个最多 100 MiB）',videoTypes,100);
    card.append(attachments);
    card.tutorialData = body => {
      const data = Object.fromEntries(Array.from(card.querySelectorAll('[name]'), input => [input.name,input.value]));
      data.media = media.map(attachment => {
        if (typeof attachment === 'string') return attachment;
        const key = `upload_${body.uploadCount++}`;
        body.form.append(key,attachment);
        return key;
      });
      return data;
    };
    lists[group].append(card);
    showAttachments();
    renumber(group);
  }

  function renumber(group) {
    Array.from(lists[group].children).forEach((row,index) => {
      row.querySelector('h4').textContent = group === 'steps' ? `第 ${index + 1} 步` : `${groups[group]} ${index + 1}`;
    });
  }

  for (const [key,title] of Object.entries(groups)) {
    const section = element('fieldset');
    lists[key] = element('div');
    const label = {materials:'添加材料',tools:'添加工具',steps:'添加步骤'}[key];
    section.append(element('legend',title),lists[key],button(label,() => {
      if (lists[key].children.length < 50) addRow(key);
      else $('tutorial-edit-status').textContent = `${title}最多 50 项。`;
    }));
    $('tutorial-edit-rows').append(section);
  }

  function showDraft(draft) {
    for (const key of Object.keys(groups)) {
      Array.from(lists[key].children).forEach(row => row.clearAttachmentPreviews());
      lists[key].replaceChildren();
      for (const row of draft[key]) addRow(key,row);
    }
  }

  editSelect.addEventListener('change',async () => {
    const request = ++editRequest;
    const id = editSelect.value;
    const status = $('tutorial-edit-status');
    editor.hidden = true;
    status.textContent = id ? '正在读取教程…' : '请选择一个已保存的机型版本。';
    if (!id) return;
    try {
      const state = await api(endpoint(id));
      if (request !== editRequest) return;
      showDraft(state.draft);
      editor.hidden = false;
      status.textContent = local ? '填写内容后可保存草稿或生成预览。'
        : state.published ? '已有发布版本，可继续编辑草稿。' : '填写内容后可保存草稿或发布。';
    } catch (error) { if (request === editRequest) status.textContent = error.message; }
  });

  editor.addEventListener('submit',async event => {
    event.preventDefault();
    const id = editSelect.value;
    const action = event.submitter.value;
    const status = $('tutorial-edit-status');
    const body = {form:new FormData(),uploadCount:0};
    const draft = Object.fromEntries(Object.keys(groups).map(key =>
      [key,Array.from(lists[key].children,row => row.tutorialData(body))]));
    body.form.append('action',action);
    body.form.append('tutorial',new Blob([JSON.stringify(draft)],{type:'application/json'}),'tutorial.json');
    const controls = Array.from(editor.elements).filter(control => !control.disabled);
    controls.push(editSelect);
    controls.forEach(control => { control.disabled = true; });
    status.textContent = '正在保存文字和附件…';
    try {
      const state = await api(endpoint(id),body.form);
      showDraft(state.draft);
      status.textContent = action === 'publish' ? (local ? '预览已更新。' : '已发布，可在「教程演示」中查看。') : '草稿已保存。';
    } catch (error) { status.textContent = error.message; }
    finally { controls.forEach(control => { control.disabled = false; }); }
  });

  function showSlide(index, focus = false) {
    pause();
    position = index;
    const id = viewSelect.value;
    const content = element('div','','tutorial-slide-content');
    slide.replaceChildren(content);
    if (index === 0) {
      content.append(element('h3','安装前准备'));
      for (const key of ['materials','tools']) {
        const grid = element('div','','tutorial-gallery');
        for (const item of published[key]) {
          const card = element('figure','','tutorial-card');
          for (const file of item.media) card.append(mediaNode(file,url(id,file),item.title));
          card.append(element('figcaption',item.title));
          grid.append(card);
        }
        content.append(element('h4',groups[key]),grid);
      }
    } else {
      const step = published.steps[index - 1];
      content.append(element('h3',`第 ${index} 步 · ${step.title}`),
        element('h4','操作步骤'),element('p',step.instructions,'tutorial-text'),
        element('h4','完成结果'),element('p',step.outcome,'tutorial-text'));
      const gallery = element('div','','tutorial-gallery');
      for (const file of step.media) gallery.append(mediaNode(file,url(id,file),step.title));
      content.append(gallery);
    }
    Array.from(nav.children).forEach((item,i) => {
      if (i === index) item.setAttribute('aria-current','step');
      else item.removeAttribute('aria-current');
    });
    $('tutorial-prev').disabled = index === 0;
    $('tutorial-next').disabled = index === published.steps.length;
    $('tutorial-next').textContent = index === published.steps.length ? '已到最后一步' : '下一步';
    if (focus) slide.focus();
  }

  async function refreshPlayer() {
    const request = ++viewRequest;
    const id = viewSelect.value;
    const status = $('tutorial-view-status');
    pause();
    player.hidden = true;
    status.textContent = id ? '正在读取教程…' : '请选择一个已保存的机型版本。';
    if (!id) return;
    try {
      const content = await api(`${endpoint(id)}/published`);
      if (request !== viewRequest) return;
      published = content;
      if (!published) { status.textContent = local ? '请先在编写页生成预览。' : '该机型版本尚未发布教程。'; return; }
      const titles = ['安装前准备',...published.steps.map((_,index) => `第 ${index + 1} 步`)];
      nav.replaceChildren(...titles.map((title,index) => button(title,() => showSlide(index,true))));
      status.textContent = `共 ${published.steps.length} 个装配步骤`;
      player.hidden = false;
      showSlide(0);
    } catch (error) { if (request === viewRequest) status.textContent = error.message; }
  }

  viewSelect.addEventListener('change',refreshPlayer);
  $('tutorial-prev').addEventListener('click',() => showSlide(position - 1,true));
  $('tutorial-next').addEventListener('click',() => showSlide(position + 1,true));

  function setProfiles(ids) {
    for (const select of [editSelect,viewSelect]) {
      const selection = select.value;
      select.replaceChildren(new Option('请选择机型版本',''),...ids.map(id => new Option(id,id)));
      select.value = selection;
    }
  }

  return {setProfiles,refreshPlayer,pause};
}
