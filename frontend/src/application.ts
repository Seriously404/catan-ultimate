import { BoardRenderer } from "./board";
import { TableScene } from "./table-scene";
import { cards } from "./cards";
import { escape as h, label, resources } from "./types";
import { fields, shell } from "./ui";
import type { Action, Bundle, Event, Game, State } from "./types";

const $ = <T extends HTMLElement = HTMLElement>(selector: string): T => {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Missing element ${selector}`);
  return element;
};
const bundleText = (b: Bundle) => resources.filter(r => b[r]).map(r => `${b[r]} ${r}`).join(", ");
const options = (values: string[]) => values.map(v => `<option>${h(v)}</option>`).join("");
function bundle(data: FormData, prefix: string): Bundle {
  return Object.fromEntries(resources.map(r => [r, Number(data.get(prefix + r) ?? 0)])) as Bundle;
}
function download(name: string, data: Blob): void {
  const link = document.createElement("a"); link.href = URL.createObjectURL(data); link.download = name;
  link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

class Application {
  private state: State | null = null;
  private readonly profile = new URLSearchParams(location.search).get("profile") ?? "default";
  private readonly storageKey = `catan.seat.${this.profile}`;
  private token = localStorage.getItem(this.storageKey) ?? "";
  private hostKey = sessionStorage.getItem("catan.host") ?? "";
  private ws: WebSocket | null = null;
  private renderer: BoardRenderer;
  private scene: TableScene;
  private offersHTML = "";
  private mode = "";
  private lastEvent = -1;
  private page = location.pathname === "/testing" ? "testing" : location.pathname === "/secret" ? "admin" : "play";
  private selected = "";
  private actor = "";
  private editTarget = "vertex";
  private errorTimer = 0;
  private clockOffset = 0;
  private animationChain: Promise<void> = Promise.resolve();
  private animationGeneration = 0;

  constructor() {
    this.renderer = new BoardRenderer($("#board"));
    this.scene = new TableScene(this.renderer);
    this.scene.onSteal = (victim, index) => { void this.move({type: "steal", victim, card_index: index}).catch(e => {this.error(e); this.scene.relayout();}); };
    this.renderer.onSelect = (id, action) => {
      if (this.page === "testing" && action) {
        void this.move(action).catch(e => this.error(e));
      } else if (this.page !== "play") {
        this.selected = id; $("#selection").textContent = id;
        const raw = this.game();
        $("#selected-detail").textContent = JSON.stringify(raw?.board.vertices[id] ?? raw?.board.edges[id] ?? raw?.board.tiles[id], null, 2);
      } else if (action) void this.move(action).catch(e => this.error(e));
    };
    document.addEventListener("click", event => {
      const button = (event.target as Element).closest<HTMLButtonElement>("[data-do]");
      if (button) void this.click(button).catch(error => this.error(error));
    });
    document.addEventListener("submit", event => { event.preventDefault(); void this.submit(event.target as HTMLFormElement).catch(e => this.error(e)); });
    document.addEventListener("change", event => {
      const input = event.target as HTMLInputElement;
      if (input.id === "edit-target") { this.editTarget = input.value; this.renderGame(); }
      if (input.id === "actor") { this.actor = input.value; this.renderGame(); }
      if (input.id === "bank-give") $("#bank-ratio").textContent = String(this.game()?.ratios?.[input.value as keyof Bundle] ?? 4);
      if (input.id === "import-save" || input.id === "import-scenario") void this.file(input).catch(e => this.error(e));
    });
    addEventListener("resize", () => this.renderOpponents());
    setInterval(() => {
      this.renderOffers();
      const busy = this.page === "play" && this.serverNow() < (this.state?.presentation_until ?? 0);
      document.querySelectorAll<HTMLButtonElement>("#actions button").forEach(button => {
        button.disabled = busy && button.dataset.do !== "trade";
      });
    }, 200);
    $("#modal-close").addEventListener("click", () => this.closeDialog());
    if (this.page !== "play") {
      $("#tools").hidden = false;
      $("#tools-toggle").hidden = false;
      $("#page-title").textContent = this.page === "testing" ? "Scenario workshop" : "Host controls";
    }
    this.connect();
  }
  private headers(): Record<string, string> {
    return {"Content-Type": "application/json", ...(this.token ? {Authorization: `Bearer ${this.token}`} : {}),
      ...(this.hostKey ? {"X-Host-Key": this.hostKey} : {})};
  }
  private async api(path: string, data?: unknown): Promise<Record<string, unknown>> {
    const response = await fetch(`/api/${path}`, {headers: this.headers(), ...(data !== undefined ? {method: "POST", body: JSON.stringify(data)} : {}), signal: AbortSignal.timeout(15000)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail ?? `Request failed: ${response.status}`);
    return result;
  }
  private async refresh(): Promise<void> { this.receive(await this.api("state") as unknown as State); }
  private connect(): void {
    this.ws?.close();
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/game`);
    this.ws = ws; $("#connection").textContent = "Connecting…";
    ws.onopen = () => ws.send(JSON.stringify({token: this.token, host_key: this.hostKey}));
    ws.onmessage = e => { const message = JSON.parse(e.data); if (message.type === "state") { $("#connection").textContent = "Connected"; this.receive(message.state); } };
    ws.onclose = () => { if (this.ws !== ws) return; $("#connection").textContent = "Reconnecting…"; setTimeout(() => { if (this.ws === ws) this.connect(); }, 1800); };
    void this.refresh().catch(e => {
      if (String(e).includes("invalid") && this.token) { this.error("Your seat key is invalid. Use Join / recover seat."); this.token = ""; localStorage.removeItem(this.storageKey); this.connect(); }
    });
  }
  private receive(state: State): void {
    if (this.state && state.revision < this.state.revision) return;
    if (state.game_id !== this.state?.game_id) { this.lastEvent = state.revision; this.mode = ""; this.actor = ""; this.animationGeneration++; this.scene.reset(); }
    if (state.game?.phase !== this.state?.game?.phase || state.game?.active !== this.state?.game?.active) this.mode = "";
    const events = state.events.filter(e => e.id > this.lastEvent);
    this.scene.reserve(events);
    this.state = state;
    this.clockOffset = Date.now() / 1000 - state.server_time;
    this.renderLobby(); this.renderGame(); this.renderChat(); this.renderTools();
    if (this.lastEvent >= 0) for (const event of events) {
      const generation = this.animationGeneration;
      this.animationChain = this.animationChain.then(async () => {
        if (generation !== this.animationGeneration) return;
        await this.animate(event);
      }).catch(error => console.error("Animation failed", error));
    }
    this.lastEvent = Math.max(this.lastEvent, ...state.events.map(e => e.id));
  }
  private game(): Game | null {
    if (this.page !== "testing") return this.state?.game ?? null;
    const g = this.state?.sandbox;
    return g ? {...g, ratios: this.state?.sandbox_ratios?.[this.actor || g.active] ??
      {wood: 4, brick: 4, wool: 4, wheat: 4, ore: 4}, deck_count: g.deck?.length ?? 0} : null;
  }
  private actions(): Action[] { return this.page === "testing" ? this.state?.sandbox_actions?.[this.actor || this.game()?.active || ""] ?? [] : this.state?.game?.legal ?? []; }
  private name(pid: string | null | undefined): string { return this.state?.players.find(p => p.id === pid)?.name ?? (pid === "host" ? "Host" : "Player"); }
  private async move(command: Action): Promise<void> {
    if (this.page === "testing") { await this.host({type: "test_command", player: this.actor || this.game()?.active, command}); return; }
    if (this.state?.paused && !["chat", "save", "end_session"].includes(command.type)) { this.error("Game paused. Reconnect all human seats or resume from host controls."); return; }
    const id = crypto.randomUUID?.() ?? Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, "0")).join("");
    try { await this.api("command", {command, id, revision: ["chat", "ready", "save", "end_session", "offer_trade", "accept_trade", "reject_trade", "select_trade", "cancel_trade"].includes(command.type) ? null : this.state?.revision}); this.closeDialog(); }
    catch (error) { void this.refresh(); throw error; }
  }
  private async host(command: Action): Promise<Record<string, unknown>> { const result = await this.api("host", command); await this.refresh(); return result; }
  private error(error: unknown): void {
    $("#toast").textContent = error instanceof Error ? error.message : String(error); $("#toast").hidden = false;
    clearTimeout(this.errorTimer); this.errorTimer = window.setTimeout(() => $("#toast").hidden = true, 7000);
  }
  private modal(title: string, content: string): void {
    $("#modal-title").textContent = title; $("#modal-body").innerHTML = content;
    const dialog = $<HTMLDialogElement>("#modal"); if (!dialog.open) dialog.showModal();
  }
  private closeDialog(): void { $<HTMLDialogElement>("#modal").close(); }

  private renderLobby(): void {
    const s = this.state; if (!s) return;
    $("#lobby").hidden = (!!s.game && (!!s.me || s.owner)) || this.page !== "play";
    $("#lobby h1").textContent = s.game ? "Recover your seat." : "Gather your settlers.";
    $("#lobby-players").innerHTML = s.players.map(p => `<div class="lobby-player"><i style="background:${p.color}"></i><div><b>${h(p.name)}</b><small>${p.bot ? h(p.provider) : p.online ? "Online" : "Disconnected"}</small></div><span>${p.ready ? "Ready" : "Not ready"}</span>${s.owner ? `<button class="quiet" data-do="remove" data-player="${h(p.id)}">×</button>` : ""}</div>`).join("");
    $("#join-area").hidden = !!s.me; $("#ready-area").hidden = !s.me;
    $("#ready-area").innerHTML = s.me ? `<p>You are <b>${h(this.name(s.me))}</b></p><button data-do="ready" class="primary">${s.players.find(p => p.id === s.me)?.ready ? "Unready" : "Ready to play"}</button> <button data-do="seat-key" class="quiet">My recovery key</button>` : "";
    $("#host-lobby").hidden = !s.owner; $("#host-invitation").hidden = s.owner;
    $("#seats-count").textContent = `${s.players.length} / 6 seats`;
    const html = s.players.map(p => `<option value="${h(p.id)}">${h(p.name)}</option>`).join("");
    const chat = $<HTMLSelectElement>("#recipients"); const selected = [...chat.selectedOptions].map(o => o.value);
    if (chat.dataset.options !== html) { chat.innerHTML = '<option value="">Public table</option>' + html; for (const option of chat.options) option.selected = selected.includes(option.value); chat.dataset.options = html; }
  }
  private renderGame(): void {
    const s = this.state, g = this.game(); if (!s) return;
    const actions = this.actions();
    if (!this.mode) this.mode = ({setup_settlement: "build_settlement", setup_road: "build_road", free_roads: "build_road", robber: "move_robber"} as Record<string, string>)[g?.phase ?? ""] ?? "";
    const viewer = this.page === "testing" ? this.actor || g?.active || null : s.me;
    const physical = s.players.filter(p => g?.order.includes(p.id));
    this.renderer.setRotation(-Math.max(0, physical.findIndex(p => p.id === viewer)) * Math.PI * 2 / Math.max(1, physical.length));
    this.renderer.set(g, s.players, actions, this.mode, this.page === "play" ? "" : this.editTarget);
    $("#round-info").innerHTML = g ? `<span class="eyebrow">${g.expanded ? "5–6 · 30 HEXES · PAIRED TURNS" : "BASE GAME · 19 HEXES"}${g.modified ? " · MODIFIED" : ""}</span><h2>${g.winner ? `${h(this.name(g.winner))} wins!` : `${h(this.name(g.active))}${g.secondary ? " · Player 2" : ""}`}</h2><span>${h(label(g.phase))} · target ${g.target} VP</span>${g.phase === "opening_roll" ? `<p class="opening-summary">Opening round ${g.opening?.round ?? 1} · highest roll starts<br>${Object.entries(g.opening?.rolls ?? {}).map(([pid,n]) => `${h(this.name(pid))}: ${n}`).join(" · ")}</p>` : ""}` : "";
    $("#pause").hidden = !s.paused || !s.game || this.page === "testing";
    $("#pause").innerHTML = `<b>Game paused and saved</b><span>${s.players.filter(p => !p.online).map(p => h(p.name)).join(", ") || "The host paused the table"}</span><button data-do="save-checkpoint">Save checkpoint</button><button data-do="end-vote">Vote to end session</button>${s.owner ? '<button data-do="resume">Resume</button>' : ""}`;
    const has = (kind: string) => actions.some(a => a.type === kind); let buttons = "";
    if (g && (s.me || this.page === "testing")) {
      if (has("opening_roll")) buttons += '<button data-do="opening-roll" class="primary">Roll for first player</button>';
      if (has("roll")) buttons += '<button data-do="roll" class="primary">Roll dice</button>';
      for (const [kind, text] of [["build_road", "Road"], ["build_settlement", "Settlement"], ["build_city", "City"], ["move_robber", "Move robber"]]) if (has(kind!)) buttons += `<button data-do="mode" data-mode="${kind}" class="${this.mode === kind ? "selected" : ""}">${text}</button>`;
      if (has("buy_dev")) buttons += '<button data-do="buy-dev">Buy development</button>';
      if (has("bank_trade")) buttons += '<button data-do="bank">Bank / ports</button>';
      if (g.phase === "action" && !g.secondary && this.page === "play") buttons += '<button data-do="trade">Offer trade</button>';
      if (has("discard")) buttons += `<button data-do="discard" class="danger">Discard ${g.discards[s.me!] ?? "cards"}</button>`;
      for (const a of actions.filter(a => a.type === "inspect_steal")) buttons += `<button data-do="choose-victim" data-player="${h(a.victim)}">Steal from ${h(this.name(String(a.victim)))}</button>`;
      if (has("end_turn")) buttons += '<button data-do="end-turn" class="primary">End turn</button>';
    }
    $("#actions").innerHTML = buttons;
    $("#hint").textContent = g ? this.mode && actions.some(a => a.type === this.mode) ? `Choose a highlighted ${this.mode === "build_road" ? "edge" : this.mode === "move_robber" ? "hex" : "intersection"}. Drag to pan · scroll / pinch to zoom.` : actions.length ? "Choose an action below. Drag to pan · scroll / pinch to zoom." : "Waiting for the other players. Drag to pan · scroll / pinch to zoom." : "";
    $("#hand").innerHTML = "";
    this.scene.sync(s,g,actions,viewer);
    this.renderOffers();
  }
  private serverNow(): number { return Date.now() / 1000 - this.clockOffset; }
  private renderOffers(): void {
    const actions=this.actions(), me=this.state?.me;
    const html=(this.game()?.offers ?? []).map(o => {
      const mine=o.from===me, pending=!!me && o.responses[me]==="pending";
      const give=mine ? o.give : o.want, get=mine ? o.want : o.give;
      const can=(type:string) => actions.some(a=>a.type===type && a.offer===o.id);
      const responseButtons=pending && !o.selecting ? `<div class="offer-buttons"><button class="primary" data-do="accept" data-offer="${o.id}" ${can("accept_trade") ? "" : 'disabled title="Not enough resources"'}>Accept</button><button data-do="reject" data-offer="${o.id}">Reject</button></div>` : "";
      const status=o.selecting ? "Choose a trading partner" : o.window_closed ? "Open · first acceptance wins" : `<span data-offer-clock="${o.closes_at}"></span>`;
      const responses=Object.entries(o.responses).map(([pid,answer])=>`<li class="response-${answer}"><span>${h(this.name(pid))}</span><b>${answer === "accepted" ? "✓ Accepted" : answer === "rejected" ? "✕ Rejected" : "Waiting…"}</b>${actions.some(a=>a.type==="select_trade" && a.offer===o.id && a.player===pid) ? `<button data-do="select-partner" data-offer="${o.id}" data-player="${h(pid)}">Trade with ${h(this.name(pid))}</button>` : ""}</li>`).join("");
      const pile=(bundle:Bundle) => cards(bundle) || '<em class="gift-note">Nothing · a gift</em>';
      return `<article class="offer"><h3>${h(this.name(o.from))} offers:</h3><small>${h(o.to.length ? `Only: ${o.to.map(p=>this.name(p)).join(", ")}` : "Open to the table")}</small><div class="exchange-cards"><label>${me ? "You give" : "They request"}</label><div class="trade-card-row">${pile(give)}</div><span class="exchange-arrows" aria-label="in exchange for">⇅</span><label>${me ? "You get" : "They give"}</label><div class="trade-card-row">${pile(get)}</div></div>${responseButtons}<p class="offer-status">${status}</p><ul class="offer-responses">${responses}</ul>${can("cancel_trade") ? `<button class="quiet" data-do="cancel-offer" data-offer="${o.id}">Cancel offer</button>` : ""}</article>`;
    }).join("");
    if(html!==this.offersHTML){$("#offers").innerHTML=html;this.offersHTML=html;}
    document.querySelectorAll<HTMLElement>("[data-offer-clock]").forEach(el=>{const n=Math.max(0,Math.ceil(Number(el.dataset.offerClock)-this.serverNow()));el.textContent=n ? `Collecting responses · ${n}s` : "Checking responses…";});
  }
  private renderOpponents(): void { this.scene.relayout(); }
  private renderChat(): void {
    const s = this.state; if (!s) return; const box = $("#messages"); const bottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
    box.innerHTML = s.chat.map(m => `<div class="message ${m.to.length ? "private" : ""}"><small>${h(this.name(m.from))}${m.to.length ? ` → ${m.to.map(p => h(this.name(p))).join(", ")} · private` : ""}</small><p>${h(m.text)}</p></div>`).join(""); if (bottom) box.scrollTop = box.scrollHeight;
    $("#log").innerHTML = s.events.slice().reverse().map(e => `<div class="log-line"><b>${h(this.name(e.actor))}</b> ${h(e.text)}${e.resource ? ` · ${h(e.resource)}` : ""}</div>`).join("");
    $<HTMLInputElement>("#chat-text").disabled = !s.me;
  }
  private renderTools(): void {
    const s = this.state; if (!s) return;
    $("#host-badge").textContent = s.owner ? "Host unlocked" : "Host key";
    $("#tool-controls").hidden = !s.owner; $("#tool-locked").hidden = s.owner;
    if (this.page === "play" || !s.owner) return;
    const html = s.players.map(p => `<option value="${h(p.id)}">${h(p.name)}</option>`).join("");
    for (const selector of ["#actor", "#edit-player", "#resource-player"]) {
      const select = $<HTMLSelectElement>(selector), current = select.value;
      if (select.dataset.options !== html) { select.innerHTML = html; select.dataset.options = html; }
      if (html.includes(`value="${h(current)}"`)) select.value = current;
    }
    if (!this.actor) $<HTMLSelectElement>("#actor").value = this.game()?.active ?? "";
    const botSelect = $<HTMLSelectElement>("#bot-player");
    const bots = s.players.filter(p => p.bot).map(p => `<option value="${h(p.id)}">${h(p.name)}</option>`).join("");
    if (botSelect.dataset.options !== bots) { botSelect.innerHTML = bots; botSelect.dataset.options = bots; }
    $("#bot-errors").textContent = Object.entries(s.bot_errors ?? {}).map(([p, error]) => `${this.name(p)}: ${error}`).join("\n");
  }
  private async animate(event: Event): Promise<void> { await this.scene.play(event); }

  private async click(button: HTMLButtonElement): Promise<void> {
    const s = this.state, g = this.game(), kind = button.dataset.do;
    if (kind === "fit") { this.renderer.fit(); return; }
    if (kind === "tools-toggle") { $("#tools").hidden = !$("#tools").hidden; return; }
    if (kind === "zoom-in" || kind === "zoom-out") { this.renderer.zoom(kind === "zoom-in" ? 1.2 : 1 / 1.2); return; }
    if (kind === "chat-toggle") { $("#sidebar").classList.toggle("mobile-open"); return; }
    if (kind === "chat-tab" || kind === "log-tab") { $("#chat-panel").hidden = kind !== "chat-tab"; $("#log").hidden = kind !== "log-tab"; return; }
    if (kind === "host-key") { this.modal("Unlock host controls", '<form id="host-login"><label>Host key (printed in the server terminal)<input name="key" type="password" required autocomplete="off"></label><button class="primary">Unlock</button></form>'); return; }
    if (kind === "seat-key") {
      if (!s?.me) this.modal("Recover your seat", '<form id="join-form"><label>Paste your private recovery key<input name="token" required autocomplete="off"></label><button class="primary">Recover seat</button></form>');
      else this.modal("Your seat recovery key", `<p>Keep this private. It restores only your seat after reconnecting or loading a save.</p><textarea readonly rows="3">${h(this.token)}</textarea><button data-do="download-seat">Download recovery key</button>`); return;
    }
    if (kind === "download-seat") { download("catan-seat.json", new Blob([JSON.stringify({token: this.token, player: s?.me})], {type: "application/json"})); return; }
    if (kind === "ready") { await this.move({type: "ready", ready: !s?.players.find(p => p.id === s.me)?.ready}); return; }
    if (kind === "remove") { await this.host({type: "remove_player", player: button.dataset.player}); return; }
    if (kind === "mode") { this.mode = button.dataset.mode ?? ""; if (this.page === "testing") {this.editTarget = ""; $<HTMLSelectElement>("#edit-target").value = "";} this.renderGame(); return; }
    const commands: Record<string, Action> = {"opening-roll": {type: "opening_roll"}, roll: {type: "roll"}, "end-turn": {type: "end_turn"}, "buy-dev": {type: "buy_dev"}, "save-checkpoint": {type: "save"}, "end-vote": {type: "end_session"}};
    if (kind && commands[kind]) { await this.move(commands[kind]!); return; }
    if (kind === "choose-victim") { await this.move({type: "inspect_steal", victim: button.dataset.player}); return; }
    if (["accept", "reject", "select-partner", "cancel-offer"].includes(kind ?? "")) {
      const types: Record<string, string> = {accept: "accept_trade", reject: "reject_trade", "select-partner": "select_trade", "cancel-offer": "cancel_trade"};
      await this.move({type: types[kind!]!, offer: Number(button.dataset.offer), player: button.dataset.player}); return;
    }
    if (kind === "bank") { this.modal("Trade with the bank / ports", `<form id="bank-form"><p>Give <strong id="bank-ratio">${g?.ratios?.wood ?? 4}</strong> cards for 1.</p><label>Give<select id="bank-give" name="give">${options([...resources])}</select></label><label>Receive<select name="receive">${options(["brick", "wood", "wool", "wheat", "ore"])}</select></label><button class="primary">Trade</button></form>`); return; }
    if (kind === "trade") {
      const html = s?.players.filter(p => p.id !== s.me && (g?.active === s.me || p.id === g?.active)).map(p => `<option value="${h(p.id)}">${h(p.name)}</option>`).join("");
      this.modal("Offer a player trade", `<form id="trade-form"><label>Recipients · select several; none means everyone eligible<select name="to" multiple size="5">${html}</select></label><h3>You give</h3><div class="resource-fields">${fields("give_")}</div><h3>You receive</h3><div class="resource-fields">${fields("want_")}</div><p>For a gift, select its recipient and leave one side empty. Acceptances are collected for five seconds. If several players accept, you choose the partner.</p><button class="primary">Make offer</button></form>`); return;
    }
    if (kind === "discard") { const action = this.actions().find(a => a.type === "discard"); this.modal("Choose cards to discard", `<form id="discard-form"><p>Discard exactly ${g?.discards[s!.me!]} resources.</p><div class="resource-fields">${fields("discard_", action?.resources as Bundle)}</div><button class="primary">Discard cards</button></form>`); return; }
    if (kind === "dev") {
      const card = button.dataset.card!; const actions = this.actions().filter(a => a.type === "play_dev" && a.card === card);
      if (card === "monopoly") this.modal("Monopoly", `<form id="monopoly-form"><label>Take all of<select name="resource">${options([...resources])}</select></label><button class="primary">Play monopoly</button></form>`);
      else if (card === "year_of_plenty") this.modal("Year of plenty", `<form id="plenty-form"><p>Choose two cards available in the bank.</p><div class="resource-fields">${fields("plenty_", actions[0]?.resources as Bundle)}</div><button class="primary">Take resources</button></form>`);
      else if (actions[0]) await this.move(actions[0]); return;
    }
    if (kind === "save-export") { const response = await fetch("/api/save", {headers: this.headers()}); if (!response.ok) throw new Error("Only the host can export the complete private save. Other players can request a checkpoint."); download(`catan-${new Date().toISOString().slice(0, 10)}.ctn`, await response.blob()); return; }
    if (kind === "copy-invite") { const url = new URL(location.href); url.pathname = "/"; url.search = ""; this.modal("Invite friends", `<p>Share this address once your host is reachable over the Internet or LAN.</p><input readonly value="${h(url.href)}"><p>This server has one lobby; no room selection is needed.</p>`); return; }
    if (kind === "pause" || kind === "resume") { await this.host({type: kind}); return; }
    if (kind === "end-game") { this.modal("End current game?", '<p>Export a .ctn save first if you want to keep this game after returning to the lobby.</p><button data-do="confirm-end" class="danger">End game and return to lobby</button>'); return; }
    if (kind === "confirm-end") { await this.host({type: "end_game"}); this.closeDialog(); return; }
    if (kind === "test-create") { this.actor = ""; await this.host({type: "test_create", seed: "workshop", fixture: $<HTMLSelectElement>("#fixture").value}); this.renderGame(); return; }
    if (kind === "publish") { await this.host({type: "publish_test"}); return; }
    if (kind === "scenario-import") { $<HTMLInputElement>("#import-scenario").click(); return; }
    if (kind === "json-refresh") { $<HTMLTextAreaElement>("#state-json").value = JSON.stringify(this.page === "testing" ? s?.sandbox : s?.raw_game, null, 2); return; }
    if (kind === "json-apply") { await this.host({type: this.page === "testing" ? "test_replace" : "replace_game", game: JSON.parse($<HTMLTextAreaElement>("#state-json").value)}); return; }
    if (kind === "scenario-export") { download("catan-scenario.json", new Blob([JSON.stringify(this.page === "testing" ? s?.sandbox : s?.raw_game, null, 2)], {type: "application/json"})); return; }
    if (kind === "recovery") { const pid = $<HTMLSelectElement>("#resource-player").value; const result = await this.host({type: "recover_seat", player: pid}); this.modal("New recovery key", `<p>Give this key privately to ${h(this.name(pid))}. Their old key is invalid.</p><textarea readonly>${h(result.token)}</textarea>`); return; }
  }
  private async submit(form: HTMLFormElement): Promise<void> {
    const data = new FormData(form), value = (name: string) => String(data.get(name) ?? ""); const edit = this.page === "testing" ? "test_edit" : "edit";
    switch (form.id) {
      case "join-form": { const result = await this.api("join", {name: value("name"), invite: value("invite"), token: value("token") || this.token}); this.token = String(result.token); localStorage.setItem(this.storageKey, this.token); this.closeDialog(); this.connect(); return; }
      case "host-login": { this.hostKey = value("key"); const result = await this.api("state"); if (!result.owner) { this.hostKey = ""; throw new Error("Host key is incorrect"); } sessionStorage.setItem("catan.host", this.hostKey); this.closeDialog(); this.connect(); return; }
      case "chat-form": { const recipients = [...$<HTMLSelectElement>("#recipients").selectedOptions].map(o => o.value).filter(Boolean); await this.move({type: "chat", text: value("text"), to: recipients}); $<HTMLInputElement>("#chat-text").value = ""; return; }
      case "start-form": await this.host({type: "start", seed: value("seed"), target: Number(value("target")), quick: value("setup") !== "manual", scenario: value("setup") === "scenario"}); return;
      case "bot-form": await this.host({type: "add_bot", name: value("name"), provider: value("provider"), model: value("model"), endpoint: value("endpoint"), timeout: Number(value("timeout"))}); form.reset(); return;
      case "bank-form": await this.move({type: "bank_trade", give: value("give"), receive: value("receive")}); return;
      case "configure-bot": await this.host({type: "configure_bot", player: value("player"), provider: value("provider"), model: value("model"), endpoint: value("endpoint"), timeout: Number(value("timeout")), api_key: value("api_key")}); return;
      case "trade-form": { const targets = data.getAll("to").map(String); await this.move({type: "offer_trade", to: targets.length ? targets : this.game()?.active !== this.state?.me ? [this.game()!.active] : [], give: bundle(data, "give_"), want: bundle(data, "want_")}); return; }
      case "discard-form": await this.move({type: "discard", resources: bundle(data, "discard_")}); return;
      case "monopoly-form": await this.move({type: "play_dev", card: "monopoly", resource: value("resource")}); return;
      case "plenty-form": await this.move({type: "play_dev", card: "year_of_plenty", resources: bundle(data, "plenty_")}); return;
      case "piece-form": await this.host({type: edit, edit: "piece", target: this.selected, player: value("erase") === "on" ? null : value("player"), level: Number(value("level"))}); return;
      case "tile-form": await this.host({type: edit, edit: "tile", target: this.selected, resource: value("resource"), number: Number(value("number"))}); return;
      case "resource-form": await this.host({type: edit, edit: "resources", player: value("player"), resources: bundle(data, "set_")}); return;
      case "turn-form": await this.host({type: edit, edit: "turn", player: $<HTMLSelectElement>("#resource-player").value, roll: value("roll") === "on"}); return;
      case "dice-form": await this.host({type: "rig_dice", dice: [Number(value("a")), Number(value("b"))]}); return;
      case "event-form": await this.host({type: "event", text: value("text")}); form.reset(); return;
      case "raw-command": await this.host({type: this.page === "testing" ? "test_command" : "admin_command", player: $<HTMLSelectElement>("#actor").value, command: JSON.parse(value("command"))}); return;
    }
  }
  private async file(input: HTMLInputElement): Promise<void> {
    const file = input.files?.[0]; if (!file) return;
    if (input.id === "import-save") { const response = await fetch("/api/import", {method: "POST", headers: {...this.headers(), "Content-Type": "application/octet-stream"}, body: file}); const result = await response.json(); if (!response.ok) throw new Error(result.detail); this.connect(); }
    else await this.host({type: this.page === "testing" ? "test_load" : "load_scenario", game: JSON.parse(await file.text())});
    input.value = "";
  }
}

export function mountApplication(root: HTMLElement): void { root.innerHTML = shell; new Application(); }
