import { BoardRenderer } from "./board";
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
  private mode = "";
  private lastEvent = -1;
  private page = location.pathname === "/testing" ? "testing" : location.pathname === "/secret" ? "admin" : "play";
  private selected = "";
  private actor = "";
  private editTarget = "vertex";
  private errorTimer = 0;

  constructor() {
    this.renderer = new BoardRenderer($("#board"));
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
    if (state.game_id !== this.state?.game_id) { this.lastEvent = state.revision; this.mode = ""; this.actor = ""; }
    if (state.game?.phase !== this.state?.game?.phase || state.game?.active !== this.state?.game?.active) this.mode = "";
    this.state = state;
    this.renderLobby(); this.renderGame(); this.renderChat(); this.renderTools();
    if (this.lastEvent >= 0) for (const event of state.events.filter(e => e.id > this.lastEvent)) this.animate(event);
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
    try { await this.api("command", {command, id, revision: ["chat", "ready", "save", "end_session"].includes(command.type) ? null : this.state?.revision}); this.closeDialog(); }
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
    this.renderer.set(g, s.players, actions, this.mode, this.page === "play" ? "" : this.editTarget);
    $("#round-info").innerHTML = g ? `<span class="eyebrow">${g.expanded ? "5–6 · 30 HEXES · PAIRED TURNS" : "BASE GAME · 19 HEXES"}${g.modified ? " · MODIFIED" : ""}</span><h2>${g.winner ? `${h(this.name(g.winner))} wins!` : `${h(this.name(g.active))}${g.secondary ? " · Player 2" : ""}`}</h2><span>${h(label(g.phase))} · target ${g.target} VP</span> <span class="dice">${g.dice.map(n => `[${n}]`).join(" ")}</span>` : "";
    $("#pause").hidden = !s.paused || !s.game || this.page === "testing";
    $("#pause").innerHTML = `<b>Game paused and saved</b><span>${s.players.filter(p => !p.online).map(p => h(p.name)).join(", ") || "The host paused the table"}</span><button data-do="save-checkpoint">Save checkpoint</button><button data-do="end-vote">Vote to end session</button>${s.owner ? '<button data-do="resume">Resume</button>' : ""}`;
    const has = (kind: string) => actions.some(a => a.type === kind); let buttons = "";
    if (g && (s.me || this.page === "testing")) {
      if (has("roll")) buttons += '<button data-do="roll" class="primary">Roll dice</button>';
      for (const [kind, text] of [["build_road", "Road"], ["build_settlement", "Settlement"], ["build_city", "City"], ["move_robber", "Move robber"]]) if (has(kind!)) buttons += `<button data-do="mode" data-mode="${kind}" class="${this.mode === kind ? "selected" : ""}">${text}</button>`;
      if (has("buy_dev")) buttons += '<button data-do="buy-dev">Buy development</button>';
      if (has("bank_trade")) buttons += '<button data-do="bank">Bank / ports</button>';
      if (g.phase === "action" && !g.secondary && this.page === "play") buttons += '<button data-do="trade">Offer trade</button>';
      if (has("discard")) buttons += `<button data-do="discard" class="danger">Discard ${g.discards[s.me!] ?? "cards"}</button>`;
      for (const a of actions.filter(a => a.type === "steal")) buttons += `<button data-do="steal" data-player="${h(a.victim)}">Steal from ${h(this.name(String(a.victim)))}</button>`;
      if (has("end_turn")) buttons += '<button data-do="end-turn" class="primary">End turn</button>';
    }
    $("#actions").innerHTML = buttons;
    $("#hint").textContent = g ? this.mode && actions.some(a => a.type === this.mode) ? `Choose a highlighted ${this.mode === "build_road" ? "edge" : this.mode === "move_robber" ? "hex" : "intersection"}. Drag to pan · scroll / pinch to zoom.` : actions.length ? "Choose an action below. Drag to pan · scroll / pinch to zoom." : "Waiting for the other players. Drag to pan · scroll / pinch to zoom." : "";
    const handPlayer = this.page === "testing" ? this.actor || g?.active : s.me;
    const me = handPlayer ? g?.players[handPlayer] : undefined;
    $("#hand").innerHTML = me?.resources ? `<div class="hand-cards">${resources.map(r => `<div class="card ${r}"><span>${r}</span><strong>${me.resources![r]}</strong></div>`).join("")}</div><div class="dev-cards">${Object.entries((me.dev ?? []).reduce<Record<string, number>>((a, c) => {a[c.type] = (a[c.type] ?? 0) + 1; return a;}, {})).map(([card, count]) => `<button data-do="dev" data-card="${card}" ${actions.some(a => a.card === card) ? "" : "disabled"}>${h(label(card))} ×${count}</button>`).join("")}</div><div class="hand-caption">${h(this.name(s.me))} · ${me.points ?? 0} VP · roads ${me.pieces?.road ?? 0}/15 · settlements ${me.pieces?.settlement ?? 0}/5 · cities ${me.pieces?.city ?? 0}/4</div>` : "";
    $("#bank-stock").textContent = g ? `Bank: ${resources.map(r => `${r} ${g.bank[r]}`).join(" · ")} · Dev ${g.deck_count ?? "—"}` : "";
    $("#offers").innerHTML = (g?.offers ?? []).map(o => `<div class="offer"><b>${h(this.name(o.from))}</b> offers ${h(bundleText(o.give))} for ${h(bundleText(o.want))}${actions.some(a => a.type === "accept_trade" && a.offer === o.id) ? `<button data-do="accept" data-offer="${o.id}">Accept</button>` : ""}${o.from === s.me ? `<button data-do="cancel-offer" data-offer="${o.id}">Cancel</button>` : ""}</div>`).join("");
    this.renderOpponents();
  }
  private renderOpponents(): void {
    const s = this.state, g = this.game(); if (!s || !g || this.page !== "play") { $("#opponents").innerHTML = ""; return; }
    const viewer = Math.max(0, g.order.indexOf(s.me ?? "")); const ids = [...g.order.slice(viewer + 1), ...g.order.slice(0, viewer)];
    $("#opponents").innerHTML = ids.map((pid, i) => {
      const angle = (150 + 240 * i / Math.max(1, ids.length - 1)) * Math.PI / 180;
      const p = s.players.find(p => p.id === pid)!; const data = g.players[pid]!;
      return `<div class="opponent ${g.active === pid ? "active" : ""}" data-seat="${h(pid)}" style="left:${51 + 39 * Math.cos(angle)}%;top:${44 + 32 * Math.sin(angle)}%;--seat:${p.color}"><b>${h(p.name)}</b><div class="card-backs"><span>▥ ${data.resource_count ?? 0}</span><span>✦ ${data.dev_count ?? 0}</span></div><small>${data.points ?? 0} VP · ${data.knights} ⚔${g.longest_road === pid ? " · Longest road" : ""}${g.largest_army === pid ? " · Largest army" : ""}</small><small>${p.thinking ? "Thinking…" : p.online ? "" : "Disconnected"}</small></div>`;
    }).join("");
  }
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
  private animate(event: Event): void {
    if (event.edge || event.vertex) this.renderer.animate(event); if (event.type !== "steal") return;
    const table = $("#table").getBoundingClientRect();
    const point = (pid?: string) => {
      const element = pid === this.state?.me ? $("#hand") : document.querySelector(`[data-seat="${CSS.escape(pid ?? "")}"]`); const box = element?.getBoundingClientRect();
      return {x: (box ? box.left + box.width / 2 : table.left + table.width / 2) - table.left, y: (box ? box.top + box.height / 2 : table.top + table.height / 2) - table.top};
    };
    const from = point(event.victim), to = point(event.actor); const card = document.createElement("div"); card.className = "flying-card"; card.textContent = event.resource ?? "▥"; $("#table").append(card);
    card.animate([{transform: `translate(${from.x}px,${from.y}px) rotate(-15deg)`, opacity: 1}, {transform: `translate(${to.x}px,${to.y}px) rotate(15deg)`, opacity: 0.3}], {duration: 800, easing: "ease-in-out"}).onfinish = () => card.remove();
  }

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
    const commands: Record<string, Action> = {roll: {type: "roll"}, "end-turn": {type: "end_turn"}, "buy-dev": {type: "buy_dev"}, "save-checkpoint": {type: "save"}, "end-vote": {type: "end_session"}};
    if (kind && commands[kind]) { await this.move(commands[kind]!); return; }
    if (kind === "steal") { await this.move({type: "steal", victim: button.dataset.player}); return; }
    if (kind === "accept" || kind === "cancel-offer") { await this.move({type: kind === "accept" ? "accept_trade" : "cancel_trade", offer: Number(button.dataset.offer)}); return; }
    if (kind === "bank") { this.modal("Trade with the bank / ports", `<form id="bank-form"><p>Give <strong id="bank-ratio">${g?.ratios?.wood ?? 4}</strong> cards for 1.</p><label>Give<select id="bank-give" name="give">${options([...resources])}</select></label><label>Receive<select name="receive">${options(["brick", "wood", "wool", "wheat", "ore"])}</select></label><button class="primary">Trade</button></form>`); return; }
    if (kind === "trade") {
      const html = s?.players.filter(p => p.id !== s.me && (g?.active === s.me || p.id === g?.active)).map(p => `<option value="${h(p.id)}">${h(p.name)}</option>`).join("");
      this.modal("Offer a player trade", `<form id="trade-form"><label>Recipient<select name="to">${g?.active === s?.me ? '<option value="">Anyone at the table</option>' : ""}${html}</select></label><h3>You give</h3><div class="resource-fields">${fields("give_")}</div><h3>You receive</h3><div class="resource-fields">${fields("want_")}</div><button class="primary">Make offer</button></form>`); return;
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
      case "trade-form": await this.move({type: "offer_trade", to: value("to") || null, give: bundle(data, "give_"), want: bundle(data, "want_")}); return;
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
