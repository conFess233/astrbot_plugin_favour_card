import { placement, clampPosition, inspectImage } from "./crop-geometry.js";

const byId = (id) => document.getElementById(id);

export async function openCropDialog(file, slot, limits) {
  if (file.size > limits.upload_max_mb * 1024 * 1024) throw new Error(`原图超过 ${limits.upload_max_mb} MB，请选择较小文件。`);
  const header = inspectImage(new Uint8Array(await file.arrayBuffer()));
  if (header.animated) throw new Error("不接受动图，请先导出静态图片。");
  if (!header.width || !header.height || header.width * header.height > limits.upload_max_pixels) throw new Error("原图像素超过限制，请先缩小图片。");
  const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  const dialog = byId("crop-dialog");
  const canvas = byId("crop-canvas");
  const overview = byId("source-canvas");
  const zoom = byId("crop-zoom");
  const confirm = byId("crop-confirm");
  const error = byId("crop-error");
  const previousFocus = document.activeElement;
  const controller = new AbortController();
  const options = { signal: controller.signal };
  canvas.width = slot.width;
  canvas.height = slot.height;
  let crop = bitmap.width > slot.width || bitmap.height > slot.height;
  let base = placement(bitmap.width, bitmap.height, slot.width, slot.height, crop);
  let state = { ...base };
  let pointer = null;
  zoom.value = "1";
  zoom.disabled = !crop;
  confirm.disabled = false;
  byId("crop-mode").hidden = crop;
  error.textContent = "";
  byId("crop-description").textContent = `${file.name} · ${bitmap.width} × ${bitmap.height} → ${slot.width} × ${slot.height}。${crop ? "原图超出槽位，必须手动确认裁剪。" : "默认完整留白适配，不拉伸；也可以主动裁剪。"}`;
  confirm.textContent = crop ? "确认裁剪并上传" : "确认留白成品并上传";

  function draw() {
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, state.x, state.y, bitmap.width * state.scale, bitmap.height * state.scale);
    const ctx = overview.getContext("2d");
    ctx.clearRect(0, 0, overview.width, overview.height);
    const full = placement(bitmap.width, bitmap.height, overview.width, overview.height, false);
    ctx.drawImage(bitmap, full.x, full.y, bitmap.width * full.scale, bitmap.height * full.scale);
    if (crop) {
      ctx.fillStyle = "rgba(0,0,0,.6)";
      ctx.fillRect(full.x, full.y, bitmap.width * full.scale, bitmap.height * full.scale);
      const sx = -state.x / state.scale;
      const sy = -state.y / state.scale;
      const sw = slot.width / state.scale;
      const sh = slot.height / state.scale;
      ctx.drawImage(bitmap, sx, sy, sw, sh, full.x + sx * full.scale, full.y + sy * full.scale, sw * full.scale, sh * full.scale);
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 2;
      ctx.strokeRect(full.x + sx * full.scale, full.y + sy * full.scale, sw * full.scale, sh * full.scale);
    }
  }

  function move(dx, dy) {
    if (!crop) return;
    Object.assign(state, clampPosition(state.x + dx, state.y + dy, bitmap.width, bitmap.height, state.scale, slot.width, slot.height));
    draw();
  }

  canvas.addEventListener("pointerdown", (event) => {
    if (!crop) return;
    pointer = { x: event.clientX, y: event.clientY };
    canvas.setPointerCapture(event.pointerId);
  }, options);
  canvas.addEventListener("pointermove", (event) => {
    if (!pointer) return;
    const ratio = canvas.width / canvas.getBoundingClientRect().width;
    move((event.clientX - pointer.x) * ratio, (event.clientY - pointer.y) * ratio);
    pointer = { x: event.clientX, y: event.clientY };
  }, options);
  for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) canvas.addEventListener(name, () => { pointer = null; }, options);
  canvas.addEventListener("keydown", (event) => {
    const offset = { ArrowLeft: [-10, 0], ArrowRight: [10, 0], ArrowUp: [0, -10], ArrowDown: [0, 10] }[event.key];
    if (offset && crop) { event.preventDefault(); move(...offset); }
  }, options);
  zoom.addEventListener("input", () => {
    const scale = base.scale * Number(zoom.value);
    const factor = scale / state.scale;
    state.x = slot.width / 2 - (slot.width / 2 - state.x) * factor;
    state.y = slot.height / 2 - (slot.height / 2 - state.y) * factor;
    state.scale = scale;
    move(0, 0);
  }, options);
  byId("crop-reset").addEventListener("click", () => { state = { ...base }; zoom.value = "1"; draw(); }, options);
  byId("crop-mode").addEventListener("click", () => {
    crop = true;
    base = placement(bitmap.width, bitmap.height, slot.width, slot.height, true);
    state = { ...base };
    zoom.disabled = false;
    byId("crop-mode").hidden = true;
    confirm.textContent = "确认裁剪并上传";
    draw();
  }, options);
  byId("crop-cancel").addEventListener("click", () => dialog.close("cancel"), options);
  let result = null;
  const closed = new Promise((resolve) => dialog.addEventListener("close", resolve, { once: true }));
  confirm.addEventListener("click", async () => {
    confirm.disabled = true;
    error.textContent = "";
    try {
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("无法导出成品，请重新选择图片。");
      if (!dialog.open) return;
      result = new File([blob], "cropped.png", { type: "image/png" });
      dialog.close("confirm");
    } catch (failure) {
      error.textContent = failure.message;
    } finally {
      confirm.disabled = false;
    }
  }, options);
  try {
    draw();
    dialog.showModal();
    canvas.focus();
    await closed;
    return result;
  } finally {
    controller.abort();
    bitmap.close();
    canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    previousFocus?.focus();
  }
}
