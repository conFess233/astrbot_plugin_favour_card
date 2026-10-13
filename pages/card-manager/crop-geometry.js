// 固定槽位的纯几何与原图格式检查，供页面和小型自检共用。
export function placement(width, height, slotWidth, slotHeight, crop) {
  const scale = (crop ? Math.max : Math.min)(slotWidth / width, slotHeight / height);
  return { scale, x: (slotWidth - width * scale) / 2, y: (slotHeight - height * scale) / 2 };
}

export function clampPosition(x, y, width, height, scale, slotWidth, slotHeight) {
  return {
    x: Math.min(0, Math.max(slotWidth - width * scale, x)),
    y: Math.min(0, Math.max(slotHeight - height * scale, y)),
  };
}

export function inspectImage(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const text = (offset, length) => String.fromCharCode(...bytes.slice(offset, offset + length));
  if (bytes.length >= 24 && text(0, 8) === "\x89PNG\r\n\x1a\n") {
    let animated = false;
    for (let offset = 8; offset + 12 <= bytes.length;) {
      const length = view.getUint32(offset);
      if (text(offset + 4, 4) === "acTL") animated = true;
      offset += length + 12;
    }
    return { width: view.getUint32(16), height: view.getUint32(20), animated };
  }
  if (bytes.length >= 30 && text(0, 4) === "RIFF" && text(8, 4) === "WEBP") {
    const kind = text(12, 4);
    const u24 = (offset) => bytes[offset] | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16);
    if (kind === "VP8X") return { width: u24(24) + 1, height: u24(27) + 1, animated: Boolean(bytes[20] & 2) };
    if (kind === "VP8 ") return { width: view.getUint16(26, true) & 0x3fff, height: view.getUint16(28, true) & 0x3fff, animated: false };
    if (kind === "VP8L" && bytes[20] === 0x2f) {
      const bits = view.getUint32(21, true);
      return { width: (bits & 0x3fff) + 1, height: ((bits >>> 14) & 0x3fff) + 1, animated: false };
    }
  }
  if (bytes.length >= 4 && bytes[0] === 0xff && bytes[1] === 0xd8) {
    let offset = 2;
    while (offset + 4 <= bytes.length && bytes[offset] === 0xff) {
      while (bytes[offset + 1] === 0xff) offset++;
      const marker = bytes[offset + 1];
      if (marker === 0xda || marker === 0xd9) break;
      const length = view.getUint16(offset + 2);
      if (length < 2 || offset + 2 + length > bytes.length) break;
      if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker) && length >= 7) {
        return { width: view.getUint16(offset + 7), height: view.getUint16(offset + 5), animated: false };
      }
      offset += length + 2;
    }
  }
  throw new Error("请选择有效的静态 JPG、PNG 或 WebP 图片，SVG 和动图不受支持。");
}
