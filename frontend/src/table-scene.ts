import { card, cardNames, dice, piece } from "./cards";
import { escape as h, resources } from "./types";
import type { Action, Event, Game, State } from "./types";
import type { BoardRenderer } from "./board";

type Point = {x: number; y: number};
const wait = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));

/** One seat layout and one ordered, local presentation system for every game action. */
export class TableScene {
  private table = document.querySelector<HTMLElement>("#table")!;
  private layer: HTMLDivElement;
  private diceNode: HTMLDivElement;
  private stealNode: HTMLDivElement;
  private state: State | null = null;
  private latest: Game | null = null;
  private display: Game | null = null;
  private actions: Action[] = [];
  private viewer: string | null = null;
  private pending = 0;
  private rolling = false;
  private chatIds = new Set<string>();
  private initializedChat = false;
  private animations = new Set<Animation>();
  private html = "";
  private reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  onSteal: (victim: string, index: number) => void = () => {};

  constructor(private renderer: BoardRenderer) {
    this.layer = document.createElement("div"); this.layer.id = "scene-effects";
    this.diceNode = document.createElement("div"); this.diceNode.className = "table-dice";
    this.stealNode = document.createElement("div"); this.stealNode.id = "steal-stage"; this.stealNode.hidden = true;
    this.layer.append(this.diceNode, this.stealNode); this.table.append(this.layer);
    this.stealNode.addEventListener("click", e => {
      const button = (e.target as Element).closest<HTMLButtonElement>("[data-steal-index]");
      if (!button || button.disabled || !this.latest?.steal_target) return;
      this.stealNode.querySelectorAll("button").forEach(b => b.disabled = true);
      this.onSteal(this.latest.steal_target, Number(button.dataset.stealIndex));
    });
  }

  reset(): void {
    for (const a of this.animations) a.cancel();
    this.layer.querySelectorAll(".motion-token,.chat-bubble,.trade-reaction").forEach(n => n.remove());
    this.pending = 0; this.display = null; this.html = ""; this.initializedChat = false;
    this.chatIds.clear(); this.stealNode.hidden = true;
    this.renderer.clearHidden();
  }
  reserve(events: Event[]): void {
    this.pending += events.length;
    for (const e of events) if (e.edge || e.vertex) this.renderer.hidePiece(e.edge ?? e.vertex!);
  }
  sync(state: State, game: Game | null, actions: Action[], viewer = state.me): void {
    this.state = state; this.latest = game; this.actions = actions; this.viewer = viewer;
    if (!this.pending || !this.display) this.display = game;
    this.render();
    if (!this.pending && !this.rolling) this.restDice();
    if (game?.phase === "steal" && game.steal_target && this.stealNode.hidden) this.showSteal(game.steal_target);
    if (!this.pending && game?.phase !== "steal") this.stealNode.hidden = true;
    for (const message of state.chat) {
      if (this.initializedChat && !this.chatIds.has(message.id)) this.bubble(message.from, message.text, !!message.to.length);
      this.chatIds.add(message.id);
    }
    this.initializedChat = true;
    if (this.chatIds.size > 600) this.chatIds = new Set(state.chat.map(m => m.id));
  }
  relayout(): void { this.html = ""; this.render(); if (!this.rolling) this.restDice(); }
  private name(pid: string): string { return this.state?.players.find(p => p.id === pid)?.name ?? "Player"; }
  private color(pid?: string): string { return this.state?.players.find(p => p.id === pid)?.color ?? "#dfc183"; }
  private center(): Point { return {x: this.table.clientWidth * .53, y: this.table.clientHeight * .43}; }
  private anchor(pid?: string | null, zone = "hand"): Point {
    const selector = pid ? `[data-seat="${CSS.escape(pid)}"] [data-zone="${zone}"]` : `[data-bank="${zone}"]`;
    const element = this.table.querySelector<HTMLElement>(selector);
    const box = element?.getBoundingClientRect(), table = this.table.getBoundingClientRect();
    return box ? {x: box.left + box.width / 2 - table.left, y: box.top + box.height / 2 - table.top} : this.center();
  }
  private fan(types: string[], own: boolean, dev = false): string {
    const n = types.length, spread = Math.min(22, 240 / Math.max(1, n - 1));
    return types.map((type, i) => {
      const k = i - (n - 1) / 2, angle = k * Math.min(7, 75 / Math.max(1, n - 1));
      const y = Math.abs(k) ** 2 * Math.min(.4, 17 / Math.max(1, ((n - 1) / 2) ** 2));
      const enabled = own && dev && this.actions.some(a => a.type === "play_dev" && a.card === type);
      return `<button class="fan-card ${own ? "own-card" : ""} ${dev && own ? "peek-card" : ""}" style="--fan-x:${k * spread}px;--fan-y:${y}px;--fan-a:${angle}deg;z-index:${i + 1}" ${dev && own ? `data-do="dev" data-card="${h(type)}"` : ""} ${enabled || !dev ? "" : "disabled"} aria-label="${h(own ? cardNames[type] ?? type : dev ? "Face-down development card" : "Face-down resource card")}">${card(own ? type : dev ? "development" : "back", "", !own || dev)}${dev && own ? `<span class="peek-face">${card(type)}</span>` : ""}</button>`;
    }).join("");
  }
  private render(): void {
    const s = this.state, g = this.display;
    if (!s || !g) { document.querySelector("#opponents")!.innerHTML = ""; document.querySelector("#bank-stock")!.innerHTML = ""; this.diceNode.hidden = true; return; }
    const physical = s.players.filter(p => g.order.includes(p.id));
    const index = Math.max(0, physical.findIndex(p => p.id === this.viewer));
    const w = this.table.clientWidth, height = this.table.clientHeight;
    const ownScale = Math.min(1, Math.max(.57, w / 1100));
    const otherScale = Math.min(.55, Math.max(.30, w / 2350));
    const html = physical.map((p, i) => {
      const own = p.id === this.viewer, relative = (i - index + physical.length) % physical.length;
      const theta = Math.PI / 2 + relative * 2 * Math.PI / physical.length;
      const x = w * .53 + Math.cos(theta) * w * .36;
      const y = own ? height - 107 * ownScale : height * .43 + Math.sin(theta) * height * .31;
      const rotation = theta * 180 / Math.PI - 90, scale = own ? ownScale : otherScale;
      const d = g.players[p.id]!;
      const hand = own && d.resources ? resources.flatMap(r => Array(d.resources![r]).fill(r)) : Array(d.resource_count ?? Object.values(d.resources ?? {}).reduce((a,b) => a+b, 0)).fill("back");
      const dev = own && d.dev ? d.dev.map(c => c.type) : Array(d.dev_count ?? d.dev?.length ?? 0).fill("development");
      const played = d.played?.length ? d.played : Array(d.knights).fill("knight");
      const labelX = Math.cos(theta) * (own ? 0 : 115 * scale);
      const labelY = Math.sin(theta) * (own ? 100 * scale : 115 * scale);
      const ny = Math.max(16, Math.min(height - 15, y + labelY)) - y;
      return `<section class="player-station ${own ? "local-station" : ""} ${g.active === p.id ? "active-station" : ""}" data-seat="${h(p.id)}" style="left:${x}px;top:${y}px;--seat:${p.color};--station-angle:${rotation}deg;--station-scale:${scale}">
        <div class="personal-board"><div class="resource-fan" data-zone="hand">${this.fan(hand, own)}</div><div class="dev-hand" data-zone="dev">${this.fan(dev, own, true)}</div>
        <div class="achievement-slot" data-zone="longest_road">${g.longest_road === p.id ? card("longest_road") : '<span>Longest<br>road</span>'}</div><div class="achievement-slot army-slot" data-zone="largest_army">${g.largest_army === p.id ? card("largest_army") : '<span>Largest<br>army</span>'}</div>
        <div class="played-hand" data-zone="played">${played.map(c => card(c)).join("")}</div>
        <div class="piece-box" data-zone="supply"><span>${piece("road", p.color)}<small>${15 - (d.pieces?.road ?? 0)}</small></span><span>${piece("settlement", p.color)}<small>${5 - (d.pieces?.settlement ?? 0)}</small></span><span>${piece("city", p.color)}<small>${4 - (d.pieces?.city ?? 0)}</small></span></div><i class="dice-anchor" data-zone="dice"></i></div>
        <div class="seat-name" data-zone="name" style="left:${labelX}px;top:${ny}px"><b>${h(p.name)}</b><span>${p.warming ? "Loading model…" : p.thinking ? "Thinking…" : !p.online ? "Disconnected" : g.active === p.id ? "Your turn" : ""}</span></div></section>`;
    }).join("");
    if (html !== this.html) { document.querySelector("#opponents")!.innerHTML = html; this.html = html; }
    document.querySelector("#bank-stock")!.innerHTML = `<div class="bank-achievements">${["longest_road","largest_army"].map(type => `<div data-bank="${type}" class="bank-award ${g[type as "longest_road"] ? "empty-award" : ""}">${card(type)}</div>`).join("")}</div><div class="bank-grid">${[...resources,"development"].map(r => `<div class="bank-pile" data-bank="${r}">${card(r, "", r === "development")}<small>${r === "development" ? g.deck_count ?? g.deck?.length ?? 0 : g.bank[r as keyof typeof g.bank]}</small></div>`).join("")}</div><strong class="bank-label">THE BANK</strong>`;
  }
  private nextRoller(): string | null {
    const g = this.latest; if (!g) return null;
    if (g.phase === "opening_roll" || g.phase === "roll") return g.active;
    return g.phase.startsWith("setup") ? g.order[0]! : g.order[(g.primary + 1) % g.order.length]!;
  }
  private restDice(owner = this.nextRoller(), values = this.latest?.dice ?? []): void {
    if (!this.latest || !owner) { this.diceNode.hidden = true; return; }
    const p = this.anchor(owner, "dice"); this.diceNode.hidden = false;
    this.diceNode.innerHTML = dice(values.length ? values : [1,1]);
    this.diceNode.style.transform = `translate(${p.x - 43}px,${p.y - 20}px) scale(.65)`;
    this.diceNode.title = `Next roll: ${this.name(owner)}`;
  }
  private async run(node: HTMLElement, frames: Keyframe[], ms: number): Promise<void> {
    const animation = node.animate(frames, {duration: this.reduced ? 1 : ms, fill:"forwards", easing:"ease-in-out"});
    this.animations.add(animation);
    await animation.finished.catch(() => {}); this.animations.delete(animation);
  }
  private async fly(content: string, from: Point, to: Point, ms = 850, arc = 65, scaleFrom = .65, scaleTo = .65, flip = false): Promise<void> {
    const node = document.createElement("div"); node.className = "motion-token";
    node.innerHTML = flip ? `<span class="flip-inner"><span class="flip-front">${content}</span><span class="flip-back">${card("back", "", true)}</span></span>` : content;
    this.layer.append(node);
    const dx = to.x - from.x, dy = to.y - from.y, distance = Math.hypot(dx,dy) || 1;
    const control = {x:(from.x + to.x)/2 - dy/distance*arc, y:(from.y+to.y)/2 + dx/distance*arc};
    const frames = Array.from({length:21}, (_,i) => {
      const t=i/20, u=1-t, x=u*u*from.x+2*u*t*control.x+t*t*to.x, y=u*u*from.y+2*u*t*control.y+t*t*to.y;
      return {transform:`translate(${x-28}px,${y-38}px) rotate(${(1-t)*-12}deg) scale(${scaleFrom+(scaleTo-scaleFrom)*t})`, opacity:1};
    });
    const inner = node.querySelector<HTMLElement>(".flip-inner");
    if (inner) void this.run(inner, [{transform:"rotateY(180deg)"},{transform:"rotateY(0deg)"}], ms*.6);
    await this.run(node, frames, ms); node.remove();
  }
  private bubble(pid: string, text: string, privateMessage: boolean): void {
    const p = this.anchor(pid, "name"), node = document.createElement("div");
    node.className = `chat-bubble ${privateMessage ? "private-bubble" : ""}`;
    node.textContent = `${privateMessage ? "🔒 " : ""}${text}`; node.title = text;
    node.style.left = `${Math.max(120,Math.min(this.table.clientWidth-120,p.x))}px`;
    node.style.top = `${Math.max(45,Math.min(this.table.clientHeight-80,p.y-38))}px`;
    this.layer.querySelector(`[data-bubble="${CSS.escape(pid)}"]`)?.remove(); node.dataset.bubble = pid;
    this.layer.append(node);
    setTimeout(() => { void this.run(node,[{opacity:1},{opacity:0}],250).then(() => node.remove()); }, Math.min(10000,4500+text.length*20));
  }
  private reaction(event: Event): void {
    if (!event.response || !event.actor) return;
    const p=this.anchor(event.actor,"name"), node=document.createElement("div");
    node.className=`trade-reaction ${event.response}`; node.textContent=event.response === "accepted" ? "✓ Accepted" : "✕ Rejected";
    Object.assign(node.style,{left:`${p.x}px`,top:`${Math.max(12,p.y-30)}px`}); this.layer.append(node);
    void this.run(node,[{opacity:0,transform:"translate(-50%,5px)"},{opacity:1,offset:.15,transform:"translate(-50%,0)"},{opacity:1,offset:.75},{opacity:0}],1800).then(()=>node.remove());
  }
  private showSteal(victim: string): void {
    const g=this.latest, me=this.viewer; if (!g) return;
    const count=g.players[victim]?.resource_count ?? 0, interactive=g.active === me;
    const center=this.center(), target=this.anchor(g.active,"hand");
    const angle=interactive ? 0 : Math.atan2(target.y-center.y,target.x-center.x)*180/Math.PI-90;
    this.stealNode.innerHTML=`<div class="steal-caption">${interactive ? `Choose a card from ${h(this.name(victim))}` : `${h(this.name(g.active))} is choosing from ${h(this.name(victim))}`}</div><div class="steal-fan" style="transform:rotate(${angle}deg)">${Array.from({length:count},(_,i)=>{
      const k=i-(count-1)/2; return `<button data-steal-index="${i}" ${interactive ? "" : "disabled"} style="--fan-x:${k*Math.min(32,360/Math.max(1,count-1))}px;--fan-y:${Math.abs(k)*2}px;--fan-a:${k*Math.min(8,80/Math.max(1,count-1))}deg" aria-label="Choose face-down card ${i+1}">${card("back","",true)}</button>`;
    }).join("")}</div>`;
    this.stealNode.style.left=`${center.x}px`; this.stealNode.style.top=`${center.y}px`; this.stealNode.hidden=false;
  }
  private async reveal(type: string, from: Point, to: Point, award = false): Promise<void> {
    const center=this.center();
    await this.fly(card(type, award ? "award-card" : ""),from,center,600,65,.6,2.2,true);
    const node=document.createElement("div"); node.className="motion-token revealed-card"; node.innerHTML=card(type);
    node.style.transform=`translate(${center.x-28}px,${center.y-38}px) scale(2.2)`; this.layer.append(node);
    if (!this.reduced) await wait(500); node.remove();
    await this.fly(card(type),center,to,650,-65,2.2,.6);
  }
  private async roll(event: Event): Promise<void> {
    this.rolling=true; this.diceNode.hidden=false;
    const values=event.dice ?? [1,1], from=this.anchor(event.actor,"dice"), center=this.center(), to=this.anchor(event.next_roller,"dice");
    this.diceNode.innerHTML=`${dice(values)}<b class="dice-total">${values.reduce((a,b)=>a+b,0)}</b>`;
    const frame=(p:Point,scale:number,rotate:number) => ({transform:`translate(${p.x-43}px,${p.y-20}px) rotate(${rotate}deg) scale(${scale})`});
    const total=this.diceNode.querySelector<HTMLElement>(".dice-total")!;
    void this.run(total,[{opacity:0},{opacity:0,offset:.38},{opacity:1,offset:.48},{opacity:1,offset:.72},{opacity:0,offset:.85},{opacity:0}],2350);
    await this.run(this.diceNode,[frame(from,.65,-20),{...frame({x:center.x-45,y:center.y-30},1.2,520),offset:.28},
      {...frame(center,2.7,720),offset:.45},{...frame(center,2.7,720),offset:.72},frame(to,.65,720)],2350);
    this.rolling=false; this.restDice(event.next_roller,values);
  }
  async play(event: Event): Promise<void> {
    const started=performance.now();
    try {
      this.reaction(event);
      const jobs: Promise<void>[]=[];
      if (event.type === "roll" || event.type === "opening_roll") jobs.push(this.roll(event));
      if (event.type === "inspect_steal" && event.victim) {
        this.showSteal(event.victim);
        const from=this.anchor(event.victim,"hand"), center=this.center();
        jobs.push(this.run(this.stealNode,[{transform:`translate(${from.x-center.x}px,${from.y-center.y}px) scale(.35)`,opacity:.3},{transform:"translate(0,0) scale(1)",opacity:1}],650));
      }
      if (event.type === "steal" && event.victim) {
        this.stealNode.hidden=true;
        jobs.push(this.fly(card(event.resource ?? "back","",!event.resource),this.center(),this.anchor(event.actor,"hand"),1000,75,1.2,.65));
      }
      if (event.type.startsWith("build_") && (event.edge || event.vertex)) {
        const kind=event.type.slice(6), point=this.renderer.screenPoint(event.edge ?? event.vertex!);
        jobs.push(this.fly(piece(kind,this.color(event.actor)),this.anchor(event.actor,"supply"),point,1100,95,.45,1.1));
        if (kind === "city") jobs.push(this.fly(piece("settlement",this.color(event.actor)),point,this.anchor(event.actor,"supply"),1100,95,1.1,.45));
      }
      if (event.type === "play_dev" && event.card) jobs.push(this.reveal(event.card,this.anchor(event.actor,"dev"),this.anchor(event.actor,"played")));
      if (event.type !== "steal") {
        const transfers:{type:string;from:Point;to:Point;flip:boolean}[]=[];
        for (const effect of event.effects ?? []) {
          if (effect.kind === "development_cards" && effect.count>0) {
            transfers.push({type:"development",from:this.anchor(null,"development"),to:this.anchor(effect.player,"dev"),flip:false}); continue;
          }
          if (effect.kind !== "resources") continue;
          const trade=event.type === "trade_completed", partner=effect.player === event.proposer ? event.partner : event.proposer;
          if (effect.resources) for (const r of resources) {
            const n=effect.resources[r] ?? 0;
            if (trade && n>0) continue;
            for (let i=0;i<Math.abs(n);i++) transfers.push({type:r,
              from:n<0 ? this.anchor(effect.player,"hand") : this.anchor(null,r),
              to:trade ? this.anchor(partner,"hand") : n<0 ? this.anchor(null,r) : this.anchor(effect.player,"hand"),flip:n<0});
          } else {
            const n=trade ? effect.spent ?? 0 : Math.abs(effect.count);
            for(let i=0;i<n;i++) transfers.push({type:"back",from:effect.count<0 || trade ? this.anchor(effect.player,"hand") : this.anchor(null,"development"),to:trade ? this.anchor(partner,"hand") : effect.count<0 ? this.anchor(null,"development") : this.anchor(effect.player,"hand"),flip:false});
          }
        }
        transfers.forEach((t,i) => jobs.push((async()=>{if(!this.reduced)await wait(i*Math.min(12,240/Math.max(1,transfers.length))); await this.fly(card(t.type,"",t.type==="back" || t.type==="development"),t.from,t.to,850,35+(i%5)*16,.65,.65,t.flip);})()));
      }
      await Promise.all(jobs);
      if (event.edge || event.vertex) this.renderer.showPiece(event.edge ?? event.vertex!);
      for (const award of event.awards ?? []) await this.reveal(award.card,this.anchor(award.from,award.card),this.anchor(award.to,award.card),true);
      const remaining=(event.duration_ms ?? 0)-(performance.now()-started);
      if (!this.reduced && remaining>0) await wait(remaining);
    } finally {
      this.pending=Math.max(0,this.pending-1);
      if (!this.pending) {
        this.display=this.latest; this.render();
        if(this.latest?.phase!=="steal")this.stealNode.hidden=true;
        this.restDice();
      }
    }
  }
}
