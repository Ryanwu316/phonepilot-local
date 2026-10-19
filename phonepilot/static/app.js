"use strict";
const $ = (id) => document.getElementById(id);
let status = { phase: "disconnected" },
  mode = "",
  streamAbort = null;
let liveTimer = null,
  polling = false,
  actionBusy = false,
  lastFrame = 0;
let gesture = null,
  recording = null,
  recordParts = [],
  recordStream = null;
let frameProblem = false,
  eventsSignature = null;
const screen = $("screen"),
  context = screen.getContext("2d", { alpha: false });
let displaySize = null;
let connectingRequest = false;
let connectionMethod = "usb",
  deviceScan = 0;

// Fit the complete frame inside the available content area, excluding the border.
function fitDisplay(width, height, maxWidth, maxHeight) {
  if (![width, height, maxWidth, maxHeight].every((value) => Number.isFinite(value) && value > 0))
    return null;
  const scale = Math.min(maxWidth / width, maxHeight / height);
  return { width: width * scale, height: height * scale };
}

function layoutDisplay() {
  const area = $("phone-area"),
    phone = $("phone");
  const style = getComputedStyle(area),
    shell = getComputedStyle(phone);
  const pixels = (value) => parseFloat(value) || 0;
  const horizontal =
    pixels(shell.borderLeftWidth) +
    pixels(shell.borderRightWidth) +
    pixels(shell.paddingLeft) +
    pixels(shell.paddingRight);
  const vertical =
    pixels(shell.borderTopWidth) +
    pixels(shell.borderBottomWidth) +
    pixels(shell.paddingTop) +
    pixels(shell.paddingBottom);
  const width =
    area.clientWidth - pixels(style.paddingLeft) - pixels(style.paddingRight) - horizontal;
  const height =
    area.clientHeight - pixels(style.paddingTop) - pixels(style.paddingBottom) - vertical;
  const ready = displaySize && status.phase === "connected" && freshPicture();
  const size = ready ? displaySize : { width: 4, height: 5 };
  const fitted = fitDisplay(
    size.width,
    size.height,
    ready ? width : Math.min(width, 320),
    ready ? height : Math.min(height, 400),
  );
  if (!fitted) return;
  phone.style.width = `${fitted.width}px`;
  phone.style.height = `${fitted.height}px`;
  phone.style.setProperty("--screen-radius", `${Math.min(fitted.width, fitted.height) * 0.13}px`);
  phone.dataset.orientation = size.width > size.height ? "landscape" : "portrait";
}

new ResizeObserver(layoutDisplay).observe($("phone-area"));

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme-toggle").setAttribute("aria-label", theme === "dark" ? "切换浅色外观" : "切换深色外观");
  $("theme-toggle").setAttribute("title", theme === "dark" ? "切换浅色外观" : "切换深色外观");
}
try {
  applyTheme(localStorage.getItem("phonepilot-theme") === "dark" ? "dark" : "light");
} catch (_) {
  applyTheme("light");
}
$("theme-toggle").onclick = () => {
  const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(theme);
  try {
    localStorage.setItem("phonepilot-theme", theme);
  } catch (_) {}
};
for (const id of ["device-settings", "setup-open"])
  $(id).onclick = () => $("device-dialog").showModal();
$("close-device-dialog").onclick = () => $("device-dialog").close();
$("expand-view").onclick = () => {
  const expanded = document.body.classList.toggle("focus-view");
  $("expand-view").setAttribute("aria-pressed", String(expanded));
  $("expand-view").setAttribute("aria-label", expanded ? "恢复操作面板" : "放大手机画面");
};

function freshPicture() {
  return !!lastFrame && !frameProblem && Date.now() - lastFrame <= 6000;
}

function syncView() {
  const connected = status.phase === "connected",
    connecting = status.phase === "connecting";
  const fresh = connected && freshPicture();
  const unavailable = connected && (frameProblem || (lastFrame && !fresh));
  const labels = {
    disconnected: "未连接",
    connecting: "连接中",
    reconnecting: "正在恢复连接",
    disconnecting: "断开中",
    error: "连接失败",
    offline: "服务离线",
  };
  $("status-label").textContent = connected
    ? status.paused
      ? "控制已暂停"
      : fresh
        ? status.demo
          ? "演示画面"
          : mode === "live"
            ? "实时画面"
            : "截图更新"
        : unavailable
          ? "画面不可用"
          : "获取画面中"
    : labels[status.phase] || "未连接";
  $("status-pill").classList.toggle("connected", fresh && !status.paused);
  $("status-pill").classList.toggle("stale", !!unavailable || !!status.paused);
  $("phone").classList.toggle("has-frame", fresh);
  layoutDisplay();
  $("empty").classList.toggle("hidden", fresh || connecting);
  $("device-name").textContent = status.device?.name || "连接你的 iPhone";
  const recovering = status.phase === "reconnecting";
  $("connection-hint").textContent = recovering
    ? status.recovery_stage === "waiting_device"
      ? "等待原手机返回 · 请检查数据线并解锁"
      : `正在恢复原设备 · 第 ${status.recovery_attempt || 1} 次`
    : status.demo
      ? "演示设备 · 未操作真实手机"
      : connected
        ? status.paused
          ? "已连接 · 控制已暂停"
          : fresh
            ? `${status.transport === "wifi" ? "无线" : "数据线"}已连接 · 随时可以接手`
            : "已连接 · 等待画面恢复"
        : connecting
          ? "正在与你的 iPhone 建立连接"
          : status.phase === "offline"
            ? "本地服务已离线，请重新启动"
            : "让手机成为工作流的一部分";
  $("empty-title").textContent = recovering
    ? "连接中断，正在恢复"
    : status.phase === "error"
      ? "连接需要检查"
      : connected
        ? unavailable
          ? "画面暂时不可用"
          : "正在获取手机画面"
        : status.demo
          ? "先体验一下手机操控"
          : "下一步，连接你的手机";
  $("empty-eyebrow").textContent = connected
    ? unavailable
      ? "等待重新连接"
      : "正在准备画面"
    : "HELLO, IPHONE";
  $("empty-description").textContent = recovering
    ? "请保持原手机连接并解锁。未完成的点击或输入不会自动重试。"
    : status.error ||
      (connected
        ? unavailable
          ? "请检查数据线，确认手机已解锁。"
          : "设备已连接，正在等待第一帧画面。"
        : status.demo
          ? "体验点按、滑动与输入，不会操作真实手机。"
          : "连接你的真实 iPhone，开始点按、滑动与输入。");
  $("empty-note").textContent = recovering
    ? "恢复后请重新确认当前页面"
    : connected
      ? "画面更新后即可操作"
      : "从设备设置开始";
  $("retry").classList.toggle("hidden", !status.device || status.phase !== "error");
  $("retry").disabled = status.phase !== "error";
  $("refresh-picture").disabled = !connected;
  $("setup-open").firstChild.textContent = connected ? "查看连接 " : "连接设备 ";
  $("control-title").textContent = status.paused ? "控制已暂停" : "控制权，始终在你";
  $("control-note").textContent = status.paused
    ? "画面仍可查看，恢复后继续操作"
    : "暂停控制后，仍可查看画面";
  $("stream-caption").textContent = fresh
    ? `${status.demo ? "演示画面" : mode === "live" ? "实时画面" : "截图更新"} · 原始比例${status.paused ? " · 控制已暂停" : " · 点按、滑动或长按"}`
    : unavailable
      ? "画面尚未更新，手机操作暂不可用"
      : connecting
        ? "正在连接，画面将自动适配"
        : "连接后，按设备画面自动适配";
  updateButtons();
}

function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").classList.remove("hidden");
  $("notice").classList.toggle("error", error);
  clearTimeout(notice.timer);
  notice.timer = setTimeout(() => $("notice").classList.add("hidden"), error ? 9000 : 3500);
}

async function api(path, data, options = {}, renewed = false) {
  const { timeout = 15000, ...fetchOptions } = options;
  const headerDeadline = new AbortController();
  const headerTimer = setTimeout(() => headerDeadline.abort(), timeout);
  const response = await fetch(path, {
    ...fetchOptions,
    signal: path.startsWith("/stream/")
      ? AbortSignal.any([headerDeadline.signal, ...(options.signal ? [options.signal] : [])])
      : AbortSignal.any([
          AbortSignal.timeout(timeout),
          ...(options.signal ? [options.signal] : []),
        ]),
    credentials: "same-origin",
    cache: "no-store",
    ...(data === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        }),
  }).finally(() => clearTimeout(headerTimer));
  if (response.status === 401 && data === undefined && !renewed) {
    await fetch("/", {
      credentials: "same-origin",
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    return api(path, data, options, true);
  }
  if (!response.ok) {
    let detail = `请求未完成（${response.status}）`;
    try {
      const result = await response.json();
      detail = typeof result.detail === "string" ? result.detail : "输入格式不正确";
    } catch (_) {}
    throw Error(detail);
  }
  return response;
}

function canControl() {
  return status.phase === "connected" && freshPicture() && !status.paused && !actionBusy;
}
function manualAddressMissing() {
  return (
    connectionMethod === "wifi" &&
    $("devices").selectedOptions?.[0]?.dataset.manual === "true" &&
    !$("wireless-host").value.trim()
  );
}
function updateConnectionMode() {
  const busy =
    ["connecting", "connected", "reconnecting", "disconnecting"].includes(status.phase) ||
    status.setup_busy ||
    connectingRequest;
  document.querySelectorAll("[data-transport]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.transport === connectionMethod));
    button.disabled = busy;
  });
  $("wireless-address").classList.toggle("hidden", connectionMethod !== "wifi");
  $("transport-hint").textContent =
    connectionMethod === "wifi"
      ? "手机与 Mac 连接同一 Wi-Fi，并保持手机解锁。首次使用需插线准备一次。"
      : "通过数据线连接，解锁并信任这台 Mac。";
  $("step-one-title").textContent = connectionMethod === "wifi" ? "连接同一 Wi-Fi" : "连接数据线";
  $("step-one-note").textContent =
    connectionMethod === "wifi"
      ? "Mac 与 iPhone 在同一网络，手机保持解锁。"
      : "解锁 iPhone，选择信任这台 Mac。";
  $("step-two-title").textContent =
    connectionMethod === "wifi" ? "完成一次无线准备" : "完成设备准备";
  $("step-two-note").textContent =
    connectionMethod === "wifi"
      ? "已配对可直接连接；首次需切换数据线，选择手机并准备无线。"
      : "在 Xcode 设备中心配对，并开启手机的开发者模式。";
  $("prepare-wireless").textContent = status.setup_busy
    ? "正在准备无线连接…"
    : "为这部手机准备无线连接";
  $("prepare-wireless").disabled = busy || connectionMethod !== "usb" || !$("devices").value;
  if (status.setup_busy) {
    $("devices").disabled = true;
    $("connect").disabled = true;
  }
  if (manualAddressMissing() || connectingRequest) $("connect").disabled = true;
}
function updateButtons() {
  document
    .querySelectorAll("[data-button], [data-key]")
    .forEach((button) => (button.disabled = !canControl()));
  $("send-text").disabled = !canControl() || !$("text").value;
  $("open-app").disabled = !canControl() || !$("apps").value;
  for (const id of ["load-apps", "pause"]) $(id).disabled = status.phase !== "connected";
  $("snapshot").disabled = status.phase !== "connected" || !freshPicture();
  $("record").disabled = !recording && (status.phase !== "connected" || !freshPicture());
  $("pause-label").textContent = status.paused ? "恢复控制" : "暂停控制";
  $("pause").classList.toggle("danger", !!status.paused);
  updateConnectionMode();
}

function render(next) {
  const previous = status;
  status = next;
  const connected = next.phase === "connected",
    connecting = next.phase === "connecting",
    recovering = next.phase === "reconnecting",
    busy = connected || connecting || recovering;
  if (busy && next.transport) connectionMethod = next.transport;
  $("mode").textContent = next.demo ? "演示模式 · 未操作真机" : "本机运行";
  $("mode").classList.toggle("demo", next.demo);
  $("device-dot").classList.toggle("on", connected);
  if (connected || connecting) $("device-meta").textContent = next.device?.name || "iPhone";
  $("connect").classList.toggle("hidden", busy);
  $("disconnect").classList.toggle("hidden", !busy);
  $("disconnect").disabled = next.phase === "disconnecting";
  $("devices").disabled = busy;
  $("connect").disabled =
    !$("devices").value || $("refresh").disabled || next.phase === "disconnecting";
  $("loading").classList.toggle("hidden", !connecting);
  $("empty").classList.toggle("hidden", busy);
  $("phone").classList.toggle("active", connected);
  syncView();
  renderEvents(next.events || []);
  if (next.error && previous.error !== next.error) notice(next.error, true);
  if (
    connected &&
    (previous.phase !== "connected" || previous.session_epoch !== next.session_epoch)
  )
    startPicture();
  if (connected && previous.phase !== "connected" && $("device-dialog").open)
    $("device-dialog").close();
  if (!connected && previous.phase === "connected") {
    stopPicture();
    stopRecording();
  }
}

function renderEvents(events) {
  const signature = JSON.stringify(events);
  if (signature === eventsSignature) return;
  eventsSignature = signature;
  $("event-count").textContent = String(events.length);
  $("event-count").classList.toggle("hidden", !events.length);
  const parent = $("events");
  parent.replaceChildren();
  if (!events.length) {
    const node = document.createElement("div");
    node.className = "no-events";
    node.innerHTML =
      '<svg class="icon" aria-hidden="true"><use href="#i-clock"/></svg><strong>还没有操作记录</strong><p>连接手机后，从一次点按开始。</p>';
    parent.append(node);
  }
  for (const event of events) {
    const row = document.createElement("div");
    row.className = `event${event.ok ? "" : " failed"}`;
    const time = document.createElement("time");
    time.textContent = event.time;
    const label = document.createElement("span");
    label.textContent = event.label;
    const result = document.createElement("b");
    result.textContent = event.demo ? "演示" : event.ok ? "✓" : "!";
    row.append(time, label, result);
    parent.append(row);
  }
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    render(await (await api("/api/status", undefined, { timeout: 5000 })).json());
  } catch (error) {
    if (status.phase !== "offline") {
      render({ ...status, phase: "offline" });
      $("status-label").textContent = "本地服务已离线";
      notice("本地服务已离线，请重新启动 PhonePilot。", true);
    }
  } finally {
    polling = false;
  }
}

async function refreshDevices() {
  const scan = ++deviceScan,
    transport = connectionMethod;
  $("refresh").disabled = true;
  $("connect").disabled = true;
  $("device-meta").textContent =
    transport === "wifi" ? "正在查找无线 iPhone…" : "正在查找数据线连接的 iPhone…";
  try {
    const data = await (await api(`/api/devices?transport=${transport}`)).json();
    if (scan !== deviceScan || transport !== connectionMethod) return;
    const selected = $("devices").value;
    $("devices").replaceChildren();
    for (const device of data.devices) {
      const option = document.createElement("option");
      option.value = device.id;
      option.dataset.manual = String(!!device.manual_required);
      option.textContent = `${device.name}${device.version ? " · " + device.version : ""}${device.trusted ? "" : " · 待信任"}`;
      $("devices").append(option);
    }
    if (
      !data.devices.length &&
      status.device &&
      ["connected", "connecting", "reconnecting"].includes(status.phase)
    ) {
      const option = document.createElement("option");
      option.value = status.device.id;
      option.textContent = `${status.device.name || "iPhone"} · 当前会话`;
      $("devices").append(option);
    } else if (!data.devices.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = transport === "wifi" ? "尚未发现已配对的无线 iPhone" : "尚未发现 iPhone";
      $("devices").append(option);
    } else if (data.devices.some((device) => device.id === selected)) $("devices").value = selected;
    if (status.phase !== "connected")
      $("device-meta").textContent = data.devices.length
        ? data.devices.some((device) => device.wireless_enabled === false)
          ? "已信任 · 无线接入尚未开启，可在下方准备"
          : $("devices").selectedOptions?.[0]?.dataset.manual === "true"
            ? "已保存配对，尚未发现无线地址；请填写手机 IP"
            : "设备可见，连接后开始控制"
        : transport === "wifi"
          ? "检查同一 Wi-Fi 和无线配对，再点击刷新"
          : "通过数据线连接 iPhone，再点击刷新";
    $("connect").disabled =
      !$("devices").value ||
      manualAddressMissing() ||
      connectingRequest ||
      ["connecting", "connected", "reconnecting", "disconnecting"].includes(status.phase);
  } catch (error) {
    notice(error.message, true);
  } finally {
    if (scan === deviceScan) $("refresh").disabled = false;
    updateConnectionMode();
  }
}

function drawFrame(frame, width, height) {
  const wasFresh = freshPicture();
  if (screen.width !== width || screen.height !== height) {
    screen.width = width;
    screen.height = height;
    displaySize = { width, height };
    layoutDisplay();
    $("display-info").textContent =
      `画面 ${width} × ${height} · ${width > height ? "横向" : width < height ? "纵向" : "方形"} · 等比适配`;
    // A gesture observed before a rotation no longer has valid coordinates.
    gesture = null;
  }
  context.drawImage(frame, 0, 0, width, height);
  lastFrame = Date.now();
  frameProblem = false;
  if (!wasFresh) syncView();
}

async function drawSnapshot(signal) {
  const response = await api("/api/screenshot?max_size=1600", undefined, { signal });
  const bitmap = await createImageBitmap(await response.blob());
  try {
    if (!signal.aborted) drawFrame(bitmap, bitmap.width, bitmap.height);
  } finally {
    bitmap.close();
  }
}

function stopPicture() {
  gesture = null;
  if (streamAbort) streamAbort.abort();
  streamAbort = null;
  clearInterval(liveTimer);
  liveTimer = null;
  lastFrame = 0;
  frameProblem = false;
  displaySize = null;
  screen.width = screen.height = 1;
  $("display-info").textContent = "连接后自动适配画面比例";
  layoutDisplay();
  syncView();
}

async function snapshotLoop(signal) {
  mode = "snapshot";
  while (!signal.aborted) {
    try {
      await drawSnapshot(signal);
    } catch (error) {
      if (signal.aborted) return;
      frameProblem = true;
      syncView();
    }
    await new Promise((resolve) => {
      const done = () => {
        clearTimeout(id);
        signal.removeEventListener("abort", done);
        resolve();
      };
      const id = setTimeout(done, 1800);
      signal.addEventListener("abort", done, { once: true });
    });
  }
}

async function runLive(signal) {
  if (!("VideoDecoder" in window)) throw Error("此侧栏暂不支持实时视频解码");
  const codec = await (await api("/stream/codec", undefined, { signal })).json();
  const description = Uint8Array.from(atob(codec.description), (value) => value.charCodeAt(0));
  const config = { codec: codec.codec, description, optimizeForLatency: true };
  if (!(await VideoDecoder.isConfigSupported(config)).supported)
    throw Error("当前侧栏不支持 HEVC 解码");
  let failed = null,
    timestamp = 0,
    needKey = true;
  const decoder = new VideoDecoder({
    output: (frame) => {
      try {
        if (!signal.aborted) drawFrame(frame, frame.displayWidth, frame.displayHeight);
      } finally {
        frame.close();
      }
    },
    error: (error) => {
      failed = error;
    },
  });
  decoder.configure(config);
  let reader;
  try {
    const response = await api("/stream/stream.bin", undefined, { signal });
    reader = response.body.getReader();
    let buffer = new Uint8Array(0);
    mode = "live";
    syncView();
    while (!signal.aborted) {
      let readTimer;
      const { value, done } = await Promise.race([
        reader.read(),
        new Promise((_, reject) => {
          readTimer = setTimeout(() => reject(Error("实时画面暂未更新")), 8000);
        }),
      ]).finally(() => clearTimeout(readTimer));
      if (done) throw Error("实时画面连接已结束");
      if (failed) throw failed;
      const merged = new Uint8Array(buffer.length + value.length);
      merged.set(buffer);
      merged.set(value, buffer.length);
      buffer = merged;
      let offset = 0;
      while (buffer.length - offset >= 5) {
        const length = new DataView(buffer.buffer, buffer.byteOffset + offset, 4).getUint32(0);
        if (length < 2 || length > 8 * 1024 * 1024) throw Error("视频帧格式不正确");
        if (buffer.length - offset < length + 4) break;
        const type = buffer[offset + 4],
          isKey = type === 0 || type === 2;
        if (type > 2) throw Error("未知的视频帧类型");
        if (type === 2 || decoder.decodeQueueSize > 6) {
          decoder.reset();
          decoder.configure(config);
          needKey = true;
        }
        if (!needKey || isKey) {
          decoder.decode(
            new EncodedVideoChunk({
              type: isKey ? "key" : "delta",
              timestamp,
              data: buffer.subarray(offset + 5, offset + 4 + length),
            }),
          );
          timestamp += 16667;
          needKey = false;
        }
        offset += length + 4;
      }
      buffer = buffer.slice(offset);
      if (buffer.length > 8 * 1024 * 1024) throw Error("视频缓冲区异常");
    }
  } finally {
    if (reader) await reader.cancel().catch(() => {});
    if (decoder.state !== "closed") decoder.close();
  }
}

function startPicture() {
  stopPicture();
  const controller = new AbortController();
  streamAbort = controller;
  const signal = controller.signal;
  liveTimer = setInterval(() => {
    syncView();
  }, 2000);
  (async () => {
    await drawSnapshot(signal).catch(() => {});
    if (status.demo) return snapshotLoop(signal);
    if (!("VideoDecoder" in window)) return snapshotLoop(signal);
    for (const delay of [3000, 8000, 15000]) {
      try {
        await runLive(signal);
      } catch (error) {
        if (signal.aborted) return;
        mode = "snapshot";
        syncView();
        // Keep showing verified screenshots while waiting to retry video.
        const until = Date.now() + delay;
        while (!signal.aborted && Date.now() < until) {
          try {
            await drawSnapshot(signal);
          } catch (_) {
            if (!signal.aborted) {
              frameProblem = true;
              syncView();
            }
          }
          await new Promise((resolve) => {
            const done = () => {
              clearTimeout(timer);
              signal.removeEventListener("abort", done);
              resolve();
            };
            const timer = setTimeout(done, 1800);
            signal.addEventListener("abort", done, { once: true });
          });
        }
      }
    }
    if (!signal.aborted) {
      notice("当前使用截图更新。可点击“刷新画面”再次尝试实时视频。");
      return snapshotLoop(signal);
    }
  })().catch((error) => {
    if (!signal.aborted) notice(error.message, true);
  });
}

async function act(action) {
  if (!canControl()) return;
  if (!lastFrame || Date.now() - lastFrame > 6000) {
    notice("请等待最新画面，再进行操作。", true);
    return;
  }
  actionBusy = true;
  updateButtons();
  try {
    await api("/api/action", action, { timeout: 50000 });
    if (mode !== "live" && streamAbort) await drawSnapshot(streamAbort.signal);
    await poll();
  } catch (error) {
    notice(error.message, true);
  } finally {
    actionBusy = false;
    updateButtons();
  }
}

function point(event) {
  const rect = screen.getBoundingClientRect();
  const scale = Math.min(rect.width / screen.width, rect.height / screen.height);
  const width = screen.width * scale,
    height = screen.height * scale;
  const x = (event.clientX - rect.left - (rect.width - width) / 2) / width;
  const y = (event.clientY - rect.top - (rect.height - height) / 2) / height;
  if (x < 0 || y < 0 || x >= 1 || y >= 1) return null;
  return { x, y };
}
screen.addEventListener("pointerdown", (event) => {
  if (!canControl() || event.button !== 0) return;
  const start = point(event);
  if (!start) return;
  screen.setPointerCapture(event.pointerId);
  gesture = { ...start, time: performance.now(), pointer: event.pointerId };
  event.preventDefault();
});
screen.addEventListener("pointerup", (event) => {
  if (!gesture || gesture.pointer !== event.pointerId) return;
  const start = gesture;
  gesture = null;
  const end = point(event);
  if (!end) return;
  const distance = Math.hypot((end.x - start.x) * screen.width, (end.y - start.y) * screen.height);
  const seconds = (performance.now() - start.time) / 1000;
  if (distance > 10)
    act({
      kind: "drag",
      x: start.x,
      y: start.y,
      x2: end.x,
      y2: end.y,
      duration: Math.min(3, Math.max(0.15, seconds)),
    });
  else
    act({
      kind: seconds > 0.55 ? "long_press" : "tap",
      x: start.x,
      y: start.y,
      duration: Math.min(3, Math.max(0.6, seconds)),
    });
});
screen.addEventListener("pointercancel", () => {
  gesture = null;
});

function download(blob, filename) {
  const url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
const stamp = () => new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
function stopRecording() {
  if (recording && recording.state !== "inactive") recording.stop();
}
function toggleRecording() {
  if (recording) {
    stopRecording();
    return;
  }
  if (!("MediaRecorder" in window) || !screen.captureStream || !lastFrame) {
    notice("当前侧栏暂不支持画面录制。", true);
    return;
  }
  const mimeType = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/mp4"].find((value) =>
    MediaRecorder.isTypeSupported(value),
  );
  if (!mimeType) {
    notice("没有可用的视频编码器。", true);
    return;
  }
  recordParts = [];
  recordStream = screen.captureStream(25);
  recording = new MediaRecorder(recordStream, { mimeType });
  recording.ondataavailable = (event) => {
    if (event.data.size) recordParts.push(event.data);
  };
  recording.onstop = () => {
    if (recordParts.length)
      download(
        new Blob(recordParts, { type: mimeType }),
        `PhonePilot-${status.demo ? "DEMO-" : ""}${stamp()}.${mimeType.includes("mp4") ? "mp4" : "webm"}`,
      );
    recordStream.getTracks().forEach((track) => track.stop());
    recordStream = recording = null;
    recordParts = [];
    $("record").innerHTML =
      '<svg class="icon" aria-hidden="true"><use href="#i-record"/></svg><span>录制</span>';
    $("record").classList.remove("recording");
    updateButtons();
  };
  recording.onerror = () => {
    notice("录制中断，请检查保存的视频。", true);
    stopRecording();
  };
  recording.start(1000);
  $("record").textContent = "● 结束录制";
  $("record").classList.add("recording");
  notice("正在录制侧栏画面，停止后保存到本机；不录制声音。");
}

$("refresh-picture").onclick = () => {
  if (status.phase === "connected") startPicture();
};
$("retry").onclick = async () => {
  $("retry").disabled = true;
  try {
    render(await (await api("/api/retry", {})).json());
  } catch (error) {
    notice(error.message, true);
  } finally {
    syncView();
  }
};
$("copy-parcel-task").onclick = async () => {
  try {
    await navigator.clipboard.writeText(
      "用 PhonePilot 查看淘宝待收货订单，逐个核对所有包裹和拆分包裹的物流。列出商品简称、最新状态和更新时间、异常及待确认项；以实际物流页面为依据，不确认收货、不下单、不联系商家。断线恢复后重新观察页面再继续。",
    );
    notice("查包裹指令已复制，粘贴到当前 AI 聊天即可开始。");
  } catch (_) {
    notice("请在当前聊天里说：用 PhonePilot 查看淘宝待收货包裹。", true);
  }
};
document.querySelectorAll("[data-transport]").forEach((button) => {
  button.onclick = () => {
    connectionMethod = button.dataset.transport;
    $("devices").replaceChildren();
    $("connect").disabled = true;
    updateConnectionMode();
    refreshDevices();
  };
});
$("prepare-wireless").onclick = async () => {
  $("prepare-wireless").disabled = true;
  try {
    const result = await (
      await api("/api/prepare-wireless", { device_id: $("devices").value }, { timeout: 45000 })
    ).json();
    notice(result.message);
    connectionMethod = "wifi";
    $("devices").replaceChildren();
    updateConnectionMode();
    await refreshDevices();
  } catch (error) {
    notice(error.message, true);
  } finally {
    updateConnectionMode();
  }
};
$("refresh").onclick = refreshDevices;
$("devices").onchange = () => {
  $("connect").disabled = !$("devices").value;
  updateConnectionMode();
};
$("wireless-host").oninput = () => {
  $("connect").disabled = !$("devices").value || manualAddressMissing() || connectingRequest;
};
$("connect").onclick = async () => {
  connectingRequest = true;
  $("connect").disabled = true;
  updateConnectionMode();
  try {
    render(
      await (
        await api("/api/connect", {
          device_id: $("devices").value,
          transport: connectionMethod,
          host: connectionMethod === "wifi" ? $("wireless-host").value.trim() || null : null,
        })
      ).json(),
    );
  } catch (error) {
    notice(error.message, true);
    $("connect").disabled = !$("devices").value;
  } finally {
    connectingRequest = false;
    await poll();
    updateConnectionMode();
  }
};
$("disconnect").onclick = async () => {
  $("disconnect").disabled = true;
  try {
    render(await (await api("/api/disconnect", {}, { timeout: 45000 })).json());
  } catch (error) {
    notice(error.message, true);
  }
};
$("pause").onclick = async () => {
  try {
    render(await (await api("/api/pause", { paused: !status.paused })).json());
  } catch (error) {
    notice(error.message, true);
  }
};
$("text").oninput = updateButtons;
$("send-text").onclick = () => act({ kind: "type", text: $("text").value });
document
  .querySelectorAll("[data-button]")
  .forEach(
    (button) => (button.onclick = () => act({ kind: "button", name: button.dataset.button })),
  );
document
  .querySelectorAll("[data-key]")
  .forEach((button) => (button.onclick = () => act({ kind: "key", name: button.dataset.key })));
$("snapshot").onclick = async () => {
  try {
    download(
      await (await api("/api/screenshot?max_size=4096")).blob(),
      `PhonePilot-${status.demo ? "DEMO-" : ""}${stamp()}.png`,
    );
  } catch (error) {
    notice(error.message, true);
  }
};
$("record").onclick = toggleRecording;
$("load-apps").onclick = async () => {
  $("load-apps").disabled = true;
  try {
    const response = await (await api("/api/apps")).json();
    const apps = Array.isArray(response.apps)
      ? response.apps
      : response.apps.apps || response.apps.applications || [];
    $("apps").replaceChildren();
    for (const app of apps) {
      const id = app.bundleIdentifier || app.bundleID || app.bundleId;
      if (!id) continue;
      const option = document.createElement("option");
      option.value = id;
      option.textContent = app.name || app.displayName || id;
      $("apps").append(option);
    }
    if (!$("apps").options.length) notice("设备没有返回可见 App。", true);
  } catch (error) {
    notice(error.message, true);
  } finally {
    updateButtons();
  }
};
$("apps").onchange = updateButtons;
$("open-app").onclick = () => act({ kind: "launch", bundle_id: $("apps").value });
document.querySelectorAll("[data-tab]").forEach(
  (button) =>
    (button.onclick = () => {
      document.querySelectorAll("[data-tab]").forEach((tab) => {
        tab.setAttribute("aria-selected", tab === button ? "true" : "false");
        tab.tabIndex = tab === button ? 0 : -1;
      });
      document
        .querySelectorAll(".tab-panel")
        .forEach((panel) =>
          panel.classList.toggle("hidden", panel.id !== `tab-${button.dataset.tab}`),
        );
    }),
);
document.querySelector(".tabs").addEventListener("keydown", (event) => {
  const tabs = [...document.querySelectorAll("[data-tab]")],
    current = tabs.indexOf(document.activeElement);
  if (current < 0 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const index =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
  tabs[index].click();
  tabs[index].focus();
});
$("copy-config").onclick = async () => {
  try {
    const config = await (await api("/api/mcp-config")).json();
    const text = JSON.stringify(config, null, 2);
    $("config").textContent = text;
    $("config").classList.remove("hidden");
    try {
      await navigator.clipboard.writeText(text);
      notice("配置已复制，请在当前应用的 MCP 设置中添加。它需要重新加载后生效。");
    } catch (_) {
      notice("配置已显示，可选中复制。请在当前应用的 MCP 设置中添加。");
    }
  } catch (error) {
    notice(error.message, true);
  }
};
$("doctor").onclick = async () => {
  try {
    const result = await (
      await api(`/api/doctor?transport=${connectionMethod}`, undefined, { timeout: 25000 })
    ).json();
    $("doctor-result").replaceChildren();
    for (const check of result.checks) {
      const row = document.createElement("div");
      row.className = "check";
      const name = document.createElement("span");
      name.textContent = check.name;
      const value = document.createElement("b");
      value.textContent = check.ok ? "通过" : "待处理";
      value.classList.toggle("missing", !check.ok);
      row.append(name, value);
      $("doctor-result").append(row);
      const detail = document.createElement("p");
      detail.className = "check-detail";
      detail.textContent = check.hint || check.detail;
      $("doctor-result").append(detail);
    }
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = `检查不会自动修改手机设置${result.demo ? " · 当前为演示模式" : ""}`;
    $("doctor-result").append(note);
    $("device-dialog").close();
    $("diagnostics").showModal();
  } catch (error) {
    notice(error.message, true);
  }
};
$("close-dialog").onclick = () => $("diagnostics").close();
window.addEventListener("pagehide", () => {
  stopPicture();
  stopRecording();
});
poll().then(refreshDevices);
setInterval(poll, 1200);
setInterval(() => {
  if (
    $("device-dialog").open &&
    !$("refresh").disabled &&
    ["disconnected", "error"].includes(status.phase)
  )
    refreshDevices();
}, 5000);
