"use strict";
// Read the gateway's fixed multipart contract, retaining at most one bounded part.
// JPEG bytes remain native; decoding happens only in the browser's image element.
globalThis.AM1MjpegFrames = async function* (body, clock = () => performance.now()) {
  const reader = body.getReader(), decoder = new TextDecoder();
  let buffer = new Uint8Array(), part = null;
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) {
        if (buffer.length || part) throw new Error("Truncated MJPEG part");
        return;
      }
      if (buffer.length + value.length > 2008192) throw new Error("Oversized MJPEG input");
      const combined = new Uint8Array(buffer.length + value.length);
      combined.set(buffer); combined.set(value, buffer.length); buffer = combined;
      while (buffer.length) {
        if (!part) {
          let end = -1;
          for (let i = 0; i < Math.min(buffer.length - 3, 8192); i++) {
            if (buffer[i] === 13 && buffer[i+1] === 10 && buffer[i+2] === 13 && buffer[i+3] === 10) { end = i; break; }
          }
          if (end < 0) { if (buffer.length > 8192) throw new Error("Oversized MJPEG header"); break; }
          const lines = decoder.decode(buffer.subarray(0,end)).split("\r\n"), headers = new Map();
          if (lines.shift() !== "--frame") throw new Error("Unexpected MJPEG boundary");
          for (const line of lines) {
            const colon = line.indexOf(":"), key = line.slice(0,colon).toLowerCase();
            if (colon < 1 || headers.has(key)) throw new Error("Invalid MJPEG header");
            headers.set(key,line.slice(colon+1).trim());
          }
          const length = Number(headers.get("content-length")), sequence = Number(headers.get("x-frame-sequence"));
          const age = Number(headers.get("x-frame-age-ms"));
          if (headers.get("content-type") !== "image/jpeg" || !Number.isInteger(length) || length < 4 || length > 1000000 ||
              !Number.isSafeInteger(sequence) || sequence < 1 || !Number.isFinite(age) || age < 0) throw new Error("Invalid MJPEG metadata");
          part = {length, sequence, age_ms: age, at: clock()};
          buffer = buffer.slice(end+4);
        }
        if (buffer.length < part.length + 2) break;
        if (buffer[part.length] !== 13 || buffer[part.length+1] !== 10) throw new Error("Invalid MJPEG trailer");
        const frame = {...part, jpeg: buffer.slice(0,part.length)};
        buffer = buffer.slice(part.length+2); part = null;
        yield frame;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
};
