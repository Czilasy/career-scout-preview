/* One moving source, twelve reflected sectors. No independent flower overlays. */
export function mountKaleidoscope(canvas: HTMLCanvasElement): () => void {
  const screenContext = canvas.getContext('2d', { alpha: false });
  const source = document.createElement('canvas');
  source.width = source.height = 960;
  const sourceContext = source.getContext('2d', { alpha: false });
  if (!screenContext || !sourceContext) return () => {};
  const ctx = screenContext;
  const paint = sourceContext;
  const colors = ['#00eee0', '#eaff15', '#ff239e', '#862eff', '#ff831c', '#1686ff'];
  const shards = Array.from({ length: 220 }, (_, i) => ({
    x: (i * 317 + 91) % 1100 - 70, y: (i * 193 + 53) % 1100 - 70,
    size: 26 + (i * 29) % 65, color: colors[i % colors.length]!,
    angle: i * 2.399, phase: i * 1.731
  }));
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let width = 0, height = 0, ratio = 1, time = 4.2, last = 0;
  let frame: number | undefined;
  let disposed = false;
  const paused = () => disposed || document.hidden || reduced.matches;
  function polygon(context: CanvasRenderingContext2D, points: Array<[number, number]>, fill: string, stroke: string | null = '#14042e', line = 3) {
    context.beginPath();
    points.forEach(([x, y], i) => i ? context.lineTo(x, y) : context.moveTo(x, y));
    context.closePath(); context.fillStyle = fill; context.fill();
    if (stroke) { context.strokeStyle = stroke; context.lineWidth = line; context.stroke(); }
  }
  function texture(t: number) {
    paint.fillStyle = '#130622'; paint.fillRect(0, 0, 960, 960);
    for (const shard of shards) {
      const s = shard.size * (1 + Math.sin(t * .42 + shard.phase) * .2);
      const x = (shard.x + Math.sin(t * .27 + shard.phase) * 58 + 960) % 960;
      const y = (shard.y + Math.cos(t * .33 + shard.phase) * 63 + 960) % 960;
      for (const dx of [-960, 0, 960]) for (const dy of [-960, 0, 960]) {
      if (x+dx+s < 0 || x+dx-s > 960 || y+dy+s < 0 || y+dy-s > 960) continue;
      paint.save(); paint.translate(x+dx, y+dy);
      paint.rotate(shard.angle + Math.sin(t * .21 + shard.phase) * .5);
      polygon(paint, [[0,-s], [s*.64,-s*.18], [s*.46,s*.7], [0,s], [-s*.64,s*.18], [-s*.46,-s*.7]], shard.color);
      polygon(paint, [[0,-s], [s*.64,-s*.18], [0,0], [-s*.46,-s*.7]], '#ffffff48', null);
      polygon(paint, [[0,0], [s*.46,s*.7], [0,s], [-s*.64,s*.18]], '#14042270', null);
      polygon(paint, [[0,-s*.5], [s*.22,0], [0,s*.48], [-s*.22,0]], colors[(colors.indexOf(shard.color)+2)%6]!, '#170b2e', 2);
      paint.restore();
      }
    }
    // The mirrored filaments join into rings which open and close with the source.
    for (let i = 0; i < 7; i++) {
      const y = 110 + i * 125;
      for (const dy of [-960,0,960]) {
      paint.beginPath();
      for (let x = 0; x <= 960; x += 16) {
        const py = y + dy + Math.sin(x/960*Math.PI*2+t*.31+i)*85;
        if (x===0) paint.moveTo(x,py); else paint.lineTo(x,py);
      }
      paint.strokeStyle = i % 2 ? '#00f6f0' : '#ff49b9'; paint.lineWidth = 11; paint.stroke();
      paint.strokeStyle = '#f6ffff'; paint.lineWidth = 1.5; paint.stroke();
      }
    }
  }
  function render(t: number) {
    texture(t);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.fillStyle = '#10041d'; ctx.fillRect(0, 0, width, height);
    const upload = document.querySelector<HTMLElement>('.file-drop');
    const uploadRect = upload?.getClientRects().length ? upload.getBoundingClientRect() : null;
    const cx = uploadRect ? uploadRect.left + uploadRect.width / 2 : width * .52;
    const cy = uploadRect && uploadRect.top < height ? uploadRect.top + uploadRect.height / 2 : height * .48;
    const radius = Math.hypot(width, height) * 1.1;
    const count = 12, step = Math.PI * 2 / count;
    const scale = (width < 800 ? .6 : .88) * (1.03 + Math.sin(t * .24) * .12);
    const pattern = ctx.createPattern(source, 'repeat');
    if (!pattern) return;
    for (let i = 0; i < count; i++) {
      ctx.save(); ctx.translate(cx, cy);
      ctx.rotate(i * step - Math.PI / 2 + Math.sin(t * .13) * .045);
      if (i % 2) { ctx.rotate(step); ctx.scale(1, -1); }
      ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(radius, 0);
      ctx.arc(0, 0, radius, 0, step + .002); ctx.closePath(); ctx.clip();
      ctx.scale(scale, scale); ctx.rotate(t * .055);
      ctx.translate(-420 + Math.sin(t*.19)*130, -390 + Math.cos(t*.17)*125);
      ctx.fillStyle = pattern;
      ctx.fillRect(-radius*2, -radius*2, radius*4, radius*4);
      ctx.restore();
    }
    const depth = ctx.createRadialGradient(cx, cy, 8, cx, cy, Math.max(width,height)*.85);
    depth.addColorStop(0, '#ffffff08'); depth.addColorStop(.48, '#10052600'); depth.addColorStop(1, '#03000d77');
    ctx.fillStyle = depth; ctx.fillRect(0,0,width,height);
  }
  function tick(now: number) {
    if (paused()) { frame = undefined; last = 0; return; }
    if (last && now - last < 1000 / 30) { frame = requestAnimationFrame(tick); return; }
    if (last) time += Math.min((now-last)/1000, .1);
    last = now; render(time); frame = requestAnimationFrame(tick);
  }
  function sync() {
    if (paused()) { if (frame !== undefined) cancelAnimationFrame(frame); frame = undefined; last = 0; }
    else if (frame === undefined) frame = requestAnimationFrame(tick);
  }
  function resize() {
    width = innerWidth; height = innerHeight; ratio = Math.min(devicePixelRatio || 1, 1.5);
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    render(time); sync();
  }
  reduced.addEventListener('change', sync); document.addEventListener('visibilitychange', sync);
  window.addEventListener('resize', resize);
  resize();
  return () => {
    disposed = true;
    if (frame !== undefined) cancelAnimationFrame(frame);
    reduced.removeEventListener('change', sync);
    document.removeEventListener('visibilitychange', sync);
    window.removeEventListener('resize', resize);
  };
}
