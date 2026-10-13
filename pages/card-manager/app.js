import { openCropDialog } from "./cropper.js";

const byId = (id) => document.getElementById(id);
const bridge = window.AstrBotPluginPage ?? window.AstrBotPluginView;
let images = null;
let quotes = null;
let settings = null;
let settingsDraft = null;
let settingsDirty = false;
let settingsSaving = false;
const drafts = new Map();
let uploading = false;
let pendingTasks = 0;

function notify(message, type = "success") {
  const container = byId("notifications");
  const toast = element("div", undefined, `toast toast-${type}`);
  const title = { success: "成功", error: "失败", warning: "提示" }[type];
  const content = element("div", undefined, "toast-content");
  content.append(element("strong", title), element("p", message));
  const icon = element("span", { success: "✓", error: "!", warning: "i" }[type], "toast-icon");
  icon.setAttribute("aria-hidden", "true");
  let timer;
  const dismiss = () => { clearTimeout(timer); toast.remove(); };
  toast.addEventListener("dismiss", dismiss);
  const close = button("×", () => {
    dismiss();
    if (previousFocus?.isConnected) previousFocus.focus();
  }, "toast-close");
  close.setAttribute("aria-label", `关闭${title}通知`);
  const previousFocus = document.activeElement;
  toast.append(icon, content, close);
  const startTimer = () => {
    clearTimeout(timer);
    if (type !== "error" && !toast.contains(document.activeElement) && !toast.matches(":hover")) {
      timer = setTimeout(dismiss, type === "warning" ? 6000 : 4200);
    }
  };
  toast.addEventListener("mouseenter", () => clearTimeout(timer));
  toast.addEventListener("mouseleave", startTimer);
  toast.addEventListener("focusin", () => clearTimeout(timer));
  toast.addEventListener("focusout", startTimer);
  if (container.children.length >= 3) {
    const oldest = [...container.children].find((node) => !node.contains(document.activeElement));
    oldest?.dispatchEvent(new Event("dismiss"));
  }
  container.append(toast);
  startTimer();
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function button(text, action, className) {
  const node = element("button", text, className);
  node.type = "button";
  node.addEventListener("click", () => action(node));
  return node;
}

async function task(control, errorNode, action, successMessage = "操作完成") {
  if (control.disabled) return;
  control.disabled = true;
  control.setAttribute("aria-busy", "true");
  errorNode.textContent = "";
  pendingTasks++;
  byId("loading-bar").hidden = false;
  let status = "操作完成";
  byId("status").textContent = pendingTasks > 1 ? `正在处理 ${pendingTasks} 项操作…` : "正在处理…";
  try {
    const result = await action();
    if (result === false) status = "操作已取消";
    else if (successMessage !== null) notify(typeof result === "string" ? result : successMessage);
  } catch (error) {
    errorNode.textContent = error.message;
    status = "操作失败，请查看提示";
    notify(error.message, "error");
  } finally {
    pendingTasks--;
    byId("loading-bar").hidden = pendingTasks === 0;
    byId("status").textContent = pendingTasks ? `仍有 ${pendingTasks} 项操作正在处理…` : status;
    control.disabled = false;
    control.removeAttribute("aria-busy");
  }
}

async function confirmAction(title, text) {
  const dialog = byId("confirm-dialog");
  const previousFocus = document.activeElement;
  byId("confirm-title").textContent = title;
  byId("confirm-text").textContent = text;
  dialog.returnValue = "cancel";
  const closed = new Promise((resolve) => dialog.addEventListener("close", resolve, { once: true }));
  dialog.showModal();
  dialog.querySelector("button").focus();
  await closed;
  previousFocus?.focus();
  return dialog.returnValue === "confirm";
}

function updateDirty() {
  const count = [...drafts.values()].filter((draft) => draft.dirty).length;
  byId("draft-count").textContent = count ? `${count} 个阶段未保存` : "已保存";
  byId("settings-dirty").textContent = settingsDirty ? "未保存" : "已保存";
  byId("settings-save").disabled = !settingsDirty || settingsSaving;
  byId("settings-discard").disabled = !settingsDirty || settingsSaving;
}

function hasDirtyDrafts() {
  return settingsDirty || [...drafts.values()].some((draft) => draft.dirty);
}

function renderSettings() {
  byId("settings-reload").hidden = !settings.requires_reload;
  const fields = byId("settings-fields");
  fields.replaceChildren();
  for (const [key, field] of Object.entries(settings.schema)) {
    const wrapper = element("div", undefined, "settings-field");
    const label = element("label", field.description);
    label.htmlFor = `config-${key}`;
    let input;
    if (field.options) {
      input = element("select");
      for (const value of field.options) {
        const labels = key === "theme" ? { default: "默认卡片" } : { fallback: "占位图", hide: "隐藏", default: "默认台词" };
        const option = element("option", labels[value] ?? value);
        option.value = value;
        input.append(option);
      }
      input.value = settingsDraft[key];
    } else if (field.type === "bool") {
      input = element("input");
      input.type = "checkbox";
      input.checked = settingsDraft[key];
    } else if (field.type === "list" || key === "default_quote") {
      input = element("textarea");
      input.rows = 3;
      input.value = field.type === "list" ? settingsDraft[key].join("\n") : settingsDraft[key];
      if (key === "default_quote") input.maxLength = 120;
    } else {
      input = element("input");
      input.type = field.type === "int" ? "number" : "text";
      input.value = settingsDraft[key];
      if (field.type === "int") {
        input.step = "1";
        input.required = true;
        input.min = field.minimum;
        input.max = field.maximum;
      }
      if (key === "character_name") { input.maxLength = 40; input.required = true; }
    }
    input.id = label.htmlFor;
    input.disabled = Boolean(field.read_only);
    input.dataset.configKey = key;
    input.addEventListener("input", () => {
      settingsDraft[key] = field.type === "bool" ? input.checked : field.type === "int" ? Number(input.value) : field.type === "list" ? input.value.split(/[\n,，]/).map((group) => group.trim()).filter(Boolean) : input.value;
      settingsDirty = true;
      updateDirty();
    });
    wrapper.append(label, input);
    if (field.hint) wrapper.append(element("p", field.hint, "muted"));
    if (field.type === "list") wrapper.append(element("p", "群号每行一个或以逗号分隔；留空表示不限群。", "muted"));
    fields.append(wrapper);
  }
}

async function refreshAll() {
  byId("image-list").setAttribute("aria-busy", "true");
  byId("quote-list").setAttribute("aria-busy", "true");
  try {
    const [nextImages, nextQuotes, nextSettings] = await Promise.all([
      bridge.apiGet("images/list"), bridge.apiGet("quotes/list"), bridge.apiGet("settings/get"),
    ]);
    images = nextImages;
    quotes = nextQuotes;
    settings = nextSettings;
    settingsDraft = structuredClone(settings.values);
    settingsDirty = false;
    drafts.clear();
    renderImages();
    renderQuotes();
    renderSettings();
    updateDirty();
  } catch (error) {
    if (!images) byId("image-list").replaceChildren(element("p", "图片列表加载失败，请点击顶部刷新重试。", "empty-state"));
    if (!quotes) byId("quote-list").replaceChildren(element("p", "阶段台词加载失败，请点击顶部刷新重试。", "empty-state"));
    throw error;
  } finally {
    byId("image-list").setAttribute("aria-busy", "false");
    byId("quote-list").setAttribute("aria-busy", "false");
  }
}

async function refreshImages() {
  images = await bridge.apiGet("images/list");
  renderImages();
}

function renderImages() {
  byId("image-count").textContent = `${images.images.length} / ${images.limits.image_pool_limit}`;
  byId("image-limits").textContent = `槽位 ${images.slot.width} × ${images.slot.height} · 原图最多 ${images.limits.upload_max_mb} MB / ${Math.floor(images.limits.upload_max_pixels / 1000000)} 百万像素 · 超尺寸必须确认裁剪`;
  const list = byId("image-list");
  list.replaceChildren();
  if (!images.images.length) list.append(element("p", "还没有角色图片。上传后，每次查询会从启用图片中随机选取。", "empty-state"));
  for (const image of images.images) {
    const tile = element("article", undefined, "image-tile");
    const thumb = element("img");
    thumb.alt = `角色素材 ${image.id.slice(0, 8)}`;
    if (image.src) thumb.src = image.src;
    else thumb.alt += "（文件缺失，请重新上传）";
    tile.append(thumb, element("p", `${image.enabled ? "已启用" : "已停用"} · ${image.width} × ${image.height} · ${Math.round(image.size / 1024)} KB`, "muted"));
    const actions = element("div", undefined, "image-actions");
    const errorNode = element("p", "", "error");
    errorNode.setAttribute("role", "alert");
    actions.append(
      button(image.enabled ? "停用" : "启用", (control) => task(control, errorNode, async () => {
        await bridge.apiPost("images/update", { id: image.id, enabled: !image.enabled });
        await refreshImages();
        return image.enabled ? "图片已停用，不再参与随机抽取" : "图片已启用";
      })),
      button("卡片预览", (control) => task(control, errorNode, () => preview(image.id), "卡片预览已更新")),
      button("删除", (control) => task(control, errorNode, async () => {
        if (!await confirmAction("删除角色图片", `删除图片 ${image.id.slice(0, 8)}，此后不再被随机选中。删除不可恢复。`)) return false;
        await bridge.apiPost("images/delete", { id: image.id, confirm: true });
        await refreshImages();
        return "图片已删除";
      }), "danger"),
    );
    tile.append(actions, errorNode);
    list.append(tile);
  }
}

async function preview(imageId) {
  const favour = byId("preview-favour").value;
  const params = { favour };
  if (imageId) params.image_id = imageId;
  const data = await bridge.apiGet("card/preview", params);
  byId("preview-image").src = data.src;
  byId("preview-image").hidden = false;
  byId("preview-empty").hidden = true;
  byId("preview-error").textContent = "";
}

function getDraft(level) {
  if (!drafts.has(level.key)) {
    const saved = quotes.stages[level.key];
    drafts.set(level.key, { quotes: structuredClone(saved?.quotes ?? []), color: saved?.color ?? level.color ?? "#111111", bulk: "", dirty: false, migrateFrom: null });
  }
  return drafts.get(level.key);
}

function newQuote(text = "") {
  return { id: crypto.randomUUID().replaceAll("-", ""), text, enabled: true };
}

function markDirty(draft) {
  draft.dirty = true;
  updateDirty();
}

function renderQuotes() {
  byId("quote-warning").textContent = quotes.warning ?? "";
  byId("quote-warning").hidden = !quotes.warning;
  const list = byId("quote-list");
  list.replaceChildren();
  if (!quotes.levels.length) list.append(element("p", "当前没有可用等级。启用 Favour Ultra 并修正等级配置后刷新；旧台词仍保留。", "empty-state"));
  for (const level of quotes.levels) list.append(renderStage(level));
  const orphanList = byId("orphan-list");
  orphanList.replaceChildren();
  byId("orphan-section").hidden = !quotes.orphan_keys.length;
  for (const key of quotes.orphan_keys) {
    const saved = quotes.stages[key];
    const row = element("div", undefined, "orphan-row");
    row.append(element("p", `${saved.display_name}（${key}）· ${saved.quotes.length} 条台词`));
    const details = element("details");
    details.append(element("summary", "查看保留的原台词"));
    for (const quote of saved.quotes) details.append(element("p", `${quote.enabled ? "启用" : "停用"} · ${quote.text}`, "muted"));
    row.append(details);
    const select = element("select");
    select.setAttribute("aria-label", `${saved.display_name} 的迁移目标阶段`);
    for (const level of quotes.levels) {
      const option = element("option", `${level.name}（${level.minimum}～${level.maximum}）`);
      option.value = level.key;
      select.append(option);
    }
    const errorNode = element("p", "", "error");
    const migrate = button("载入目标草稿", (control) => task(control, errorNode, async () => {
      const target = quotes.levels.find((level) => level.key === select.value);
      if (!target) throw new Error("当前没有可用的迁移目标。");
      if (!await confirmAction("载入旧阶段台词", `将 ${saved.display_name} 的 ${saved.quotes.length} 条台词载入 ${target.name} 草稿，替换该目标当前草稿。原阶段保留，需再次确认保存才生效。`)) return false;
      const draft = getDraft(target);
      draft.quotes = structuredClone(saved.quotes);
      draft.color = saved.color ?? target.color ?? "#111111";
      draft.bulk = "";
      draft.migrateFrom = key;
      markDirty(draft);
      renderQuotes();
      byId(`stage-${target.key}`).open = true;
      return "旧台词已载入目标草稿，确认保存后生效";
    }));
    migrate.disabled = !quotes.levels.length;
    row.append(select, migrate, errorNode);
    orphanList.append(row);
  }
}

function renderStage(level) {
  const draft = getDraft(level);
  const saved = quotes.stages[level.key];
  const changed = saved && (saved.display_name !== level.name || saved.min !== level.minimum || saved.max !== level.maximum);
  const details = element("details", undefined, "stage");
  details.id = `stage-${level.key}`;
  details.open = draft.dirty;
  details.append(element("summary", `${level.name} · ${level.minimum}～${level.maximum}`));
  const body = element("div", undefined, "stage-content");
  const colorRow = element("div", undefined, "stage-color-field");
  const colorLabel = element("label", "好感度与阶段名称颜色");
  const colorPicker = element("input");
  colorPicker.type = "color";
  colorPicker.id = `stage-color-${level.key}`;
  colorPicker.value = /^#[0-9a-f]{6}$/i.test(draft.color) ? draft.color : "#111111";
  colorLabel.htmlFor = colorPicker.id;
  const colorText = element("input");
  colorText.type = "text";
  colorText.maxLength = 7;
  colorText.value = draft.color;
  colorText.setAttribute("aria-label", `${level.name}颜色 HEX`);
  colorPicker.addEventListener("input", () => { draft.color = colorPicker.value; colorText.value = draft.color; colorText.removeAttribute("aria-invalid"); markDirty(draft); });
  colorText.addEventListener("input", () => {
    draft.color = colorText.value;
    const valid = /^#[0-9a-f]{6}$/i.test(draft.color);
    colorText.setAttribute("aria-invalid", String(!valid));
    if (valid) colorPicker.value = draft.color;
    markDirty(draft);
  });
  colorRow.append(colorLabel, colorPicker, colorText);
  body.append(colorRow, element("p", "使用 #RRGGBB 格式，浅色卡片建议选择较深的文字颜色。保存后预览使用所选原色。", "muted"));
  if (changed) body.append(element("p", "阶段名称或范围已变化，原台词保留；确认保存后才重新用于抽取。", "notice"));
  if (draft.migrateFrom) body.append(element("p", `正在从 ${draft.migrateFrom} 迁移，原条目保留。`, "notice"));
  function rerender() {
    const replacement = renderStage(level);
    replacement.open = true;
    details.replaceWith(replacement);
  }
  draft.quotes.forEach((quote, index) => {
    const row = element("div", undefined, "quote-row");
    const text = element("textarea");
    text.rows = 2;
    text.maxLength = 120;
    text.value = quote.text;
    text.setAttribute("aria-label", `${level.name} 第 ${index + 1} 条台词，最多 120 字`);
    text.addEventListener("input", () => { quote.text = text.value; markDirty(draft); });
    const actions = element("div", undefined, "quote-actions");
    const label = element("label");
    const enabled = element("input");
    enabled.type = "checkbox";
    enabled.checked = quote.enabled;
    enabled.addEventListener("change", () => { quote.enabled = enabled.checked; markDirty(draft); });
    label.append(enabled, document.createTextNode("启用"));
    const up = button("上移", () => {
      [draft.quotes[index - 1], draft.quotes[index]] = [draft.quotes[index], draft.quotes[index - 1]];
      markDirty(draft); rerender();
    });
    up.disabled = index === 0;
    const down = button("下移", () => {
      [draft.quotes[index + 1], draft.quotes[index]] = [draft.quotes[index], draft.quotes[index + 1]];
      markDirty(draft); rerender();
    });
    down.disabled = index === draft.quotes.length - 1;
    actions.append(label, up, down, button("删除", () => { draft.quotes.splice(index, 1); markDirty(draft); rerender(); }, "danger"));
    row.append(text, actions);
    body.append(row);
  });
  const errorNode = element("p", "", "stage-error");
  errorNode.setAttribute("role", "alert");
  const bulk = element("textarea");
  bulk.rows = 3;
  bulk.id = `bulk-${level.key}`;
  bulk.value = draft.bulk;
  bulk.addEventListener("input", () => { draft.bulk = bulk.value; markDirty(draft); });
  const label = element("label", "批量添加（每行一条，导入后需保存）", "bulk-label");
  label.htmlFor = bulk.id;
  body.append(label, bulk, button("添加多行", () => {
    const lines = bulk.value.split(/\r?\n/).filter((line) => line.trim());
    if (lines.some((line) => line.length > 120) || lines.length + draft.quotes.length > 500) {
      errorNode.textContent = "单条最多 120 字，每阶段最多 500 条；输入仍保留。";
      return;
    }
    if (!lines.length) return;
    draft.quotes.push(...lines.map(newQuote));
    draft.bulk = "";
    markDirty(draft); rerender();
  }));
  const actions = element("div", undefined, "stage-actions");
  actions.append(
    button("新增台词", () => { draft.quotes.push(newQuote()); markDirty(draft); rerender(); }),
    button("保存此阶段", (control) => task(control, errorNode, async () => {
      if (draft.bulk.trim()) throw new Error("批量输入尚未添加，请先点击「添加多行」或清空该输入。");
      if ((changed || draft.migrateFrom) && !await confirmAction("确认阶段台词映射", `将 ${draft.quotes.length} 条台词保存到 ${level.name}（${level.minimum}～${level.maximum}）。原旧阶段仍保留。`)) return false;
      const controls = [...body.querySelectorAll("button,input,textarea")];
      const disabled = controls.map((node) => node.disabled);
      controls.forEach((node) => { node.disabled = true; });
      try {
        const response = await bridge.apiPost("quotes/save", {
          key: level.key, revision: quotes.revision, quotes: draft.quotes, color: draft.color,
          migrate_from: draft.migrateFrom, confirm: true,
        });
        quotes.revision = response.revision;
        quotes.stages = response.stages;
        draft.dirty = false;
        draft.migrateFrom = null;
        draft.quotes = structuredClone(response.stages[level.key].quotes);
        draft.color = response.stages[level.key].color ?? level.color ?? "#111111";
        updateDirty(); rerender();
        return `${level.name}的颜色与台词已保存`;
      } finally {
        controls.forEach((node, index) => { node.disabled = disabled[index]; });
      }
    }), "primary"),
    button("放弃此阶段修改", (control) => task(control, errorNode, async () => {
      if (draft.dirty && !await confirmAction("放弃草稿", `放弃 ${level.name} 尚未保存的修改，恢复最近保存的台词。`)) return false;
      drafts.delete(level.key);
      updateDirty(); rerender();
      return "已恢复此阶段最近保存的台词";
    })),
  );
  body.append(actions, errorNode);
  details.append(body);
  return details;
}

function activateTab(name) {
  for (const tab of ["images", "quotes", "settings"]) {
    const active = tab === name;
    byId(`${tab}-tab`).setAttribute("aria-selected", String(active));
    byId(`${tab}-tab`).tabIndex = active ? 0 : -1;
    byId(`${tab}-panel`).hidden = !active;
  }
}
const tabs = ["images", "quotes", "settings"];
for (const name of tabs) {
  byId(`${name}-tab`).addEventListener("click", () => activateTab(name));
  byId(`${name}-tab`).addEventListener("keydown", (event) => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs.at(-1) : tabs[(tabs.indexOf(name) + (event.key === "ArrowLeft" ? -1 : 1) + tabs.length) % tabs.length];
      activateTab(next); byId(`${next}-tab`).focus();
    }
  });
}
byId("preview").addEventListener("click", () => task(byId("preview"), byId("preview-error"), () => preview(), "卡片预览已更新"));
byId("refresh").addEventListener("click", () => task(byId("refresh"), byId("upload-error"), async () => {
  if (hasDirtyDrafts() && !await confirmAction("刷新已保存数据", "刷新会放弃所有未保存的配置、颜色和台词草稿，是否继续？")) return false;
  await refreshAll();
  return "已刷新配置、图片与阶段设置";
}));
byId("upload").addEventListener("click", () => { if (!uploading && images) byId("image-files").click(); });
byId("image-files").addEventListener("change", async (event) => {
  const files = [...event.target.files];
  event.target.value = "";
  if (!files.length || uploading) return;
  uploading = true;
  byId("refresh").disabled = true;
  try {
    await task(byId("upload"), byId("upload-error"), async () => {
      let uploaded = 0;
      let duplicates = 0;
      for (const file of files) {
        const cropped = await openCropDialog(file, images.slot, images.limits);
        if (!cropped) continue;
        const response = await bridge.upload("images/upload", cropped);
        if (response.duplicate) duplicates++;
        else uploaded++;
        await refreshImages();
        try { await preview(response.image.id); }
        catch (error) {
          const message = `图片已入池，卡片预览失败：${error.message}`;
          byId("preview-error").textContent = message;
          notify(message, "warning");
        }
      }
      if (!uploaded && !duplicates) return false;
      return `新增 ${uploaded} 张图片${duplicates ? `，${duplicates} 张重复成品未新增` : ""}`;
    });
  } finally {
    uploading = false;
    byId("refresh").disabled = settingsSaving;
  }
});
window.addEventListener("beforeunload", (event) => {
  if (hasDirtyDrafts() || uploading) { event.preventDefault(); event.returnValue = ""; }
});

byId("settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  task(byId("settings-save"), byId("settings-error"), async () => {
    settingsSaving = true;
    byId("refresh").disabled = true;
    const controls = [...byId("settings-form").querySelectorAll("input,select,textarea,button")];
    const disabled = controls.map((node) => node.disabled);
    controls.forEach((node) => { node.disabled = true; });
    try {
      const response = await bridge.apiPost("settings/save", { values: settingsDraft, revision: settings.revision });
      settings = { ...settings, ...response };
      settingsDraft = structuredClone(response.values);
      settingsDirty = false;
      renderSettings();
      byId("settings-reload").hidden = !response.requires_reload;
      try { await refreshImages(); }
      catch (error) { notify(`配置已保存，图片列表刷新失败：${error.message}`, "warning"); }
      return response.requires_reload ? "配置已保存；渲染并发上限需重载插件后生效" : "插件配置已保存";
    } finally {
      settingsSaving = false;
      byId("refresh").disabled = uploading;
      controls.forEach((node, index) => { node.disabled = disabled[index]; });
      updateDirty();
    }
  }).then(() => updateDirty());
});
byId("settings-discard").addEventListener("click", () => task(byId("settings-discard"), byId("settings-error"), async () => {
  if (!await confirmAction("放弃配置草稿", "恢复最近读取或保存的插件配置，未保存输入会被丢弃。")) return false;
  settingsDraft = structuredClone(settings.values);
  settingsDirty = false;
  renderSettings();
  updateDirty();
  return "已恢复插件配置";
}).then(() => updateDirty()));
byId("preview-image").addEventListener("error", () => {
  byId("preview-error").textContent = "预览图片加载失败，请重新预览。";
  notify("预览图片加载失败，请重新预览。", "error");
});

try {
  if (!bridge) throw new Error("请从 AstrBot 插件详情页打开此管理视图。");
  await bridge.ready();
  await task(byId("refresh"), byId("upload-error"), refreshAll, null);
} catch (error) {
  byId("upload-error").textContent = error.message;
  byId("image-list").replaceChildren(element("p", "请从 AstrBot 插件详情页打开管理视图。", "empty-state"));
  byId("image-list").setAttribute("aria-busy", "false");
  notify(error.message, "error");
}
