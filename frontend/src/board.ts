import type { Action, Event, Game, Seat } from "./types";

const colors: Record<string, string> = {wood: "#39785c", brick: "#c07856", wool: "#80a66b",
  wheat: "#d6b461", ore: "#829098", desert: "#ccba91"};

/** Shared local camera and vector renderer for play, testing, and owner controls. */
export class BoardRenderer {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private game: Game | null = null;
  private seats: Seat[] = [];
  private scale = 50;
  private angle = 0;
  private hidden = new Map<string, number>();
  private x = 0;
  private y = 0;
  private width = 0;
  private height = 0;
  private dirty = true;
  private boardKey = "";
  private actions: Action[] = [];
  private mode = "";
  private adminTarget = "";
  private selected = "";
  private animation: { edge?: string; vertex?: string; at: number } = {at: 0};
  private pointers = new Map<number, {x: number; y: number}>();
  private down = {x: 0, y: 0, moved: false};
  onSelect: (id: string, action?: Action) => void = () => {};

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas 2D is required");
    this.ctx = ctx;
    new ResizeObserver(() => this.resize()).observe(canvas);
    canvas.addEventListener("wheel", e => {
      e.preventDefault();
      this.zoom(Math.exp(-e.deltaY * 0.0015), e.offsetX, e.offsetY);
    }, {passive: false});
    canvas.addEventListener("pointerdown", e => {
      canvas.setPointerCapture(e.pointerId);
      this.pointers.set(e.pointerId, {x: e.offsetX, y: e.offsetY});
      this.down = {x: e.offsetX, y: e.offsetY, moved: false};
    });
    canvas.addEventListener("pointermove", e => {
      const previous = this.pointers.get(e.pointerId);
      if (!previous) return;
      if (this.pointers.size === 2) {
        const other = [...this.pointers.entries()].find(([id]) => id !== e.pointerId)![1];
        const before = Math.hypot(previous.x - other.x, previous.y - other.y);
        const after = Math.hypot(e.offsetX - other.x, e.offsetY - other.y);
        if (before > 1) this.zoom(after / before, (e.offsetX + other.x) / 2, (e.offsetY + other.y) / 2);
        this.down.moved = true;
      } else {
        this.x += e.offsetX - previous.x;
        this.y += e.offsetY - previous.y;
        if (Math.hypot(e.offsetX - this.down.x, e.offsetY - this.down.y) > 5) this.down.moved = true;
        this.dirty = true;
      }
      this.pointers.set(e.pointerId, {x: e.offsetX, y: e.offsetY});
    });
    canvas.addEventListener("pointerup", e => {
      if (!this.down.moved && this.pointers.size === 1) this.pick(e.offsetX, e.offsetY);
      this.pointers.delete(e.pointerId);
    });
    canvas.addEventListener("pointercancel", e => this.pointers.delete(e.pointerId));
    const frame = () => {
      if (this.dirty || performance.now() - this.animation.at < 700) this.draw();
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }

  set(game: Game | null, seats: Seat[], actions: Action[], mode: string, adminTarget = ""): void {
    this.game = game; this.seats = seats; this.actions = actions;
    this.mode = mode; this.adminTarget = adminTarget;
    const key = game ? game.seed + Object.keys(game.board.tiles).length : "";
    if (key !== this.boardKey) { this.boardKey = key; this.fit(); }
    this.dirty = true;
  }
  setRotation(angle: number): void { if (Math.abs(this.angle-angle)>.001) {this.angle=angle;this.fit();} }
  hidePiece(id: string): void { this.hidden.set(id,(this.hidden.get(id) ?? 0)+1); this.dirty=true; }
  showPiece(id: string): void { const n=(this.hidden.get(id) ?? 1)-1; if(n>0)this.hidden.set(id,n);else this.hidden.delete(id);this.dirty=true; }
  clearHidden(): void { this.hidden.clear(); this.dirty=true; }
  screenPoint(id: string): {x:number;y:number} {
    const b=this.game?.board; if(!b)return {x:this.x,y:this.y};
    const edge=b.edges[id], v=edge ? {x:(b.vertices[edge.a]!.x+b.vertices[edge.b]!.x)/2,y:(b.vertices[edge.a]!.y+b.vertices[edge.b]!.y)/2} : b.vertices[id] ?? b.tiles[id];
    if(!v)return {x:this.x,y:this.y};
    return {x:this.x+(v.x*Math.cos(this.angle)-v.y*Math.sin(this.angle))*this.scale,
      y:this.y+(v.x*Math.sin(this.angle)+v.y*Math.cos(this.angle))*this.scale};
  }
  animate(event: Event): void {
    this.animation = {at: performance.now(), ...(event.edge ? {edge: event.edge} : {}),
      ...(event.vertex ? {vertex: event.vertex} : {})};
    this.dirty = true;
  }
  select(id: string): void { this.selected = id; this.dirty = true; }
  zoom(factor: number, px = this.width / 2, py = this.height * 0.45): void {
    const next = Math.max(10, Math.min(220, this.scale * factor));
    this.x = px - (px - this.x) * next / this.scale;
    this.y = py - (py - this.y) * next / this.scale;
    this.scale = next; this.dirty = true;
  }
  fit(): void {
    if (!this.game) return;
    const vertices = Object.values(this.game.board.vertices);
    const xs = vertices.map(v => v.x*Math.cos(this.angle)-v.y*Math.sin(this.angle)), ys = vertices.map(v => v.x*Math.sin(this.angle)+v.y*Math.cos(this.angle));
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    this.scale = Math.max(10, Math.min(this.width * 0.59 / (maxX - minX + 2.4),
      this.height * 0.54 / (maxY - minY + 2.4)));
    this.x = this.width * 0.53 - (minX + maxX) / 2 * this.scale;
    this.y = this.height * 0.43 - (minY + maxY) / 2 * this.scale;
    this.dirty = true;
  }
  private resize(): void {
    this.width = this.canvas.clientWidth; this.height = this.canvas.clientHeight;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    this.canvas.width = this.width * dpr; this.canvas.height = this.height * dpr;
    this.fit(); this.dirty = true;
  }
  private pick(px: number, py: number): void {
    if (!this.game) return;
    const b = this.game.board;
    const sx=(px-this.x)/this.scale, sy=(py-this.y)/this.scale;
    const world = {x:sx*Math.cos(this.angle)+sy*Math.sin(this.angle), y:-sx*Math.sin(this.angle)+sy*Math.cos(this.angle)};
    const candidates: {id: string; x: number; y: number; action?: Action}[] = [];
    if (this.adminTarget) {
      if (this.adminTarget === "vertex") for (const [id, v] of Object.entries(b.vertices)) candidates.push({id, ...v});
      if (this.adminTarget === "tile") for (const [id, v] of Object.entries(b.tiles)) candidates.push({id, ...v});
      if (this.adminTarget === "edge") for (const [id, e] of Object.entries(b.edges)) {
        const a = b.vertices[e.a]!, v = b.vertices[e.b]!;
        candidates.push({id, x: (a.x + v.x) / 2, y: (a.y + v.y) / 2});
      }
    } else for (const action of this.actions) {
      if (action.type !== this.mode) continue;
      if (typeof action.vertex === "string") candidates.push({id: action.vertex, ...b.vertices[action.vertex]!, action});
      if (typeof action.tile === "string") candidates.push({id: action.tile, ...b.tiles[action.tile]!, action});
      if (typeof action.edge === "string") {
        const e = b.edges[action.edge]!, a = b.vertices[e.a]!, v = b.vertices[e.b]!;
        candidates.push({id: action.edge, x: (a.x + v.x) / 2, y: (a.y + v.y) / 2, action});
      }
    }
    candidates.sort((a, b) => Math.hypot(a.x - world.x, a.y - world.y) - Math.hypot(b.x - world.x, b.y - world.y));
    const hit = candidates[0];
    if (hit && Math.hypot(hit.x - world.x, hit.y - world.y) * this.scale <
      (this.adminTarget === "tile" || this.mode === "move_robber" ? this.scale * 0.85 : Math.max(16, this.scale * 0.3))) {
      this.selected = hit.id; this.dirty = true; this.onSelect(hit.id, hit.action);
    }
  }
  private draw(): void {
    this.dirty = false;
    const c = this.ctx, dpr = Math.min(devicePixelRatio || 1, 2);
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, this.width, this.height);
    if (!this.game) return;
    const g = this.game, b = g.board;
    c.translate(this.x, this.y); c.scale(this.scale, this.scale); c.rotate(this.angle);
    this.rim();
    const color = (pid: string | null) => this.seats.find(p => p.id === pid)?.color ?? "#eee";
    const allowed = new Set(this.actions.filter(a => a.type === this.mode).map(a => String(a.edge ?? a.vertex ?? a.tile)));
    const progress = Math.min(1, (performance.now() - this.animation.at) / 550);
    for (const [tid, tile] of Object.entries(b.tiles)) {
      const vertices = tile.vertices.map(v => b.vertices[v]!);
      c.beginPath(); vertices.forEach((v, i) => i ? c.lineTo(v.x, v.y) : c.moveTo(v.x, v.y)); c.closePath();
      c.fillStyle = colors[tile.resource] ?? colors.desert!; c.fill();
      c.strokeStyle = "#182e2c"; c.lineWidth = 0.055; c.stroke();
      if (allowed.has(tid) || this.selected === tid && this.adminTarget === "tile") {
        c.strokeStyle = "#f8e3a0"; c.lineWidth = 0.09; c.stroke();
      }
      c.save(); c.translate(tile.x, tile.y);
      c.globalAlpha = 0.5; this.terrain(tile.resource); c.globalAlpha = 1;
      if (tile.number) {
        c.beginPath(); c.arc(0, 0.03, 0.285, 0, Math.PI * 2); c.fillStyle = "#f8efd9"; c.fill();
        c.strokeStyle = "#7d745c"; c.lineWidth = 0.025; c.stroke();
        c.fillStyle = tile.number === 6 || tile.number === 8 ? "#b33f36" : "#263834";
        c.textAlign = "center"; c.textBaseline = "middle"; c.font = "bold 0.28px system-ui";
        c.fillText(String(tile.number), 0, -0.005);
        c.font = "0.11px system-ui";
        c.fillText("•".repeat(6 - Math.abs(7 - tile.number)), 0, 0.19);
      }
      if (tid === g.robber) {
        c.fillStyle = "#1a2428";
        c.beginPath(); c.arc(0.34, -0.31, 0.12, 0, Math.PI * 2); c.fill();
        c.beginPath(); c.moveTo(0.25, -0.22); c.lineTo(0.14, 0.2); c.lineTo(0.55, 0.2); c.lineTo(0.43, -0.22); c.closePath(); c.fill();
        c.fillStyle = "#f16a63"; c.beginPath(); c.arc(0.35, -0.3, 0.025, 0, Math.PI * 2); c.fill();
      }
      c.restore();
    }
    for (const [eid, edge] of Object.entries(b.edges)) {
      const a = b.vertices[edge.a]!, v = b.vertices[edge.b]!;
      if (edge.port) {
        const x = (a.x + v.x) / 2, y = (a.y + v.y) / 2;
        const tile = b.tiles[edge.tiles[0]!]!;
        const dx = x - tile.x, dy = y - tile.y;
        const length=Math.hypot(dx,dy), bx=x+dx/length*.79, by=y+dy/length*.79;
        c.strokeStyle = "#d8ba7e"; c.lineWidth = 0.075; c.beginPath(); c.moveTo(a.x,a.y);c.lineTo(bx,by);c.moveTo(v.x,v.y);c.lineTo(bx,by);c.stroke();
        c.save();c.translate(bx,by);c.rotate(-this.angle);
        c.beginPath();c.roundRect(-.43,-.4,.86,.87,.13);c.fillStyle="#f1dfb5";c.fill();c.lineWidth=.035;c.strokeStyle="#725335";c.stroke();
        c.save();c.translate(0,.1);c.scale(.46,.46);
        if(edge.port==="any") { c.strokeStyle="#426f80";c.lineWidth=.08;c.beginPath();c.arc(0,-.4,.31,0,Math.PI*2);c.moveTo(-.4,-.4);c.lineTo(.4,-.4);c.moveTo(0,-.8);c.lineTo(0,0);c.stroke(); }
        else this.terrain(edge.port);
        c.restore();c.fillStyle="#314342";c.font="bold .22px system-ui";c.textAlign="center";c.fillText(edge.port==="any" ? "3:1" : "2:1",0,.31);
        c.font=".10px system-ui";c.fillText(({wool:"Sheep",wood:"Wood",brick:"Brick",wheat:"Grain",ore:"Ore",any:"Any"} as Record<string,string>)[edge.port] ?? edge.port,0,-.25);c.restore();
      }
      if (this.hidden.has(eid)) continue;
      if (edge.owner || allowed.has(eid) || this.adminTarget === "edge" && this.selected === eid) {
        const t = this.animation.edge === eid ? progress : 1;
        c.lineCap = "round"; c.lineWidth = edge.owner ? 0.13 : 0.08;
        c.strokeStyle = edge.owner ? "#172626" : "#ffe4a3";
        c.beginPath(); c.moveTo(a.x, a.y); c.lineTo(a.x + (v.x - a.x) * t, a.y + (v.y - a.y) * t); c.stroke();
        if (edge.owner) {
          c.lineWidth = 0.085; c.strokeStyle = color(edge.owner); c.stroke();
        }
      }
    }
    for (const [vid, vertex] of Object.entries(b.vertices)) {
      const building = this.hidden.has(vid) ? null : vertex.building;
      if (building) {
        c.save(); c.translate(vertex.x, vertex.y);
        const t = this.animation.vertex === vid ? 0.3 + progress * 0.7 : 1; c.scale(t, t);
        c.fillStyle = color(building.owner); c.strokeStyle = "#192e2e"; c.lineWidth = 0.04;
        c.beginPath(); c.moveTo(-0.17, 0.15); c.lineTo(-0.17, -0.08); c.lineTo(0, -0.25);
        c.lineTo(0.17, -0.08); c.lineTo(0.17, 0.15); c.closePath(); c.fill(); c.stroke();
        if (building.level === 2) { c.fillRect(0.12, -0.02, 0.19, 0.17); c.strokeRect(0.12, -0.02, 0.19, 0.17); }
        c.restore();
      }
      if (allowed.has(vid) || this.adminTarget === "vertex" && this.selected === vid) {
        c.beginPath(); c.arc(vertex.x, vertex.y, 0.18, 0, Math.PI * 2);
        c.strokeStyle = "#ffe7ad"; c.lineWidth = 0.045; c.stroke();
        if (!building) { c.fillStyle = "#ffe7ad88"; c.fill(); }
      }
    }
  }

  private rim(): void {
    const b=this.game!.board, c=this.ctx, v=Object.values(b.vertices);
    const left=Math.min(...v.map(p=>p.x))-1.15,right=Math.max(...v.map(p=>p.x))+1.15;
    const top=Math.min(...v.map(p=>p.y))-1.05,bottom=Math.max(...v.map(p=>p.y))+1.05, mid=(top+bottom)/2, width=right-left;
    const corners=[[left+width*.25,top],[right-width*.25,top],[right,mid],[right-width*.25,bottom],[left+width*.25,bottom],[left,mid]];
    c.save();c.beginPath();corners.forEach(([x,y],i)=>i?c.lineTo(x!,y!):c.moveTo(x!,y!));c.closePath();
    c.fillStyle="#347995";c.fill();c.strokeStyle="#704f32";c.lineWidth=.24;c.stroke();c.strokeStyle="#dec28a";c.lineWidth=.1;c.stroke();c.clip();
    c.strokeStyle="#9fd1d340";c.lineWidth=.025;
    for(let y=top+.25;y<bottom;y+=.42) for(let x=left+.15;x<right;x+=.65){c.beginPath();c.moveTo(x,y);c.quadraticCurveTo(x+.15,y+.1,x+.32,y);c.stroke();}
    c.restore();
  }

  private terrain(resource: string): void {
    const c = this.ctx; c.fillStyle = "#173c31"; c.strokeStyle = "#71552f"; c.lineWidth = 0.035;
    if (resource === "wood") {
      for (const x of [-0.48, 0.37]) { c.beginPath(); c.moveTo(x, -0.73); c.lineTo(x - 0.22, -0.22); c.lineTo(x + 0.22, -0.22); c.closePath(); c.fill(); }
    } else if (resource === "ore") {
      c.fillStyle = "#293d4a"; c.beginPath(); c.moveTo(-0.7, -0.2); c.lineTo(-0.35, -0.74); c.lineTo(0, -0.2); c.lineTo(0.3, -0.65); c.lineTo(0.67, -0.12); c.closePath(); c.fill();
    } else if (resource === "brick") {
      c.fillStyle = "#774837"; for (let i = 0; i < 3; i++) c.fillRect(-0.5 + i * 0.32, -0.58, 0.27, 0.16);
    } else if (resource === "wheat") {
      c.strokeStyle = "#796327"; for (const x of [-0.45, 0.4]) { c.beginPath(); c.moveTo(x, -0.15); c.lineTo(x, -0.73); c.moveTo(x, -0.45); c.lineTo(x + 0.12, -0.6); c.moveTo(x, -0.5); c.lineTo(x - 0.12, -0.65); c.stroke(); }
    } else if (resource === "wool") {
      c.fillStyle = "#e9ebd6"; c.beginPath(); c.ellipse(-0.24, -0.48, 0.24, 0.14, 0, 0, Math.PI * 2); c.fill();
      c.fillStyle = "#354637"; c.beginPath(); c.arc(0, -0.48, 0.075, 0, Math.PI * 2); c.fill();
    }
  }
}
