export const resources = ["wood", "brick", "wool", "wheat", "ore"] as const;
export type Resource = typeof resources[number];
export type Bundle = Record<Resource, number>;
export interface Action { type: string; [key: string]: unknown }
export interface Building { owner: string; level: number }
export interface Vertex { x: number; y: number; tiles: string[]; edges: string[]; building: Building | null }
export interface Edge { a: string; b: string; owner: string | null; tiles: string[]; port: string | null }
export interface Tile { x: number; y: number; q: number; r: number; resource: string; number: number; vertices: string[] }
export interface Board { tiles: Record<string, Tile>; vertices: Record<string, Vertex>; edges: Record<string, Edge> }
export interface GamePlayer { resources?: Bundle; resource_count?: number; dev?: {type: string; bought: number}[];
  dev_count?: number; points?: number; knights: number; pieces?: Record<string, number> }
export interface Game { board: Board; players: Record<string, GamePlayer>; order: string[]; active: string;
  phase: string; expanded: boolean; target: number; seed: string; turn: number; secondary: boolean;
  primary: number; dev_played: boolean; dice: number[]; robber: string; winner: string | null;
  modified: boolean; bank: Bundle; legal?: Action[]; ratios?: Bundle; deck_count?: number; deck?: string[];
  discards: Record<string, number>; longest_road: string | null; largest_army: string | null;
  road_lengths: Record<string, number>; offers: Offer[] }
export interface Offer { id: number; from: string; to: string | null; give: Bundle; want: Bundle }
export interface Seat { id: string; name: string; color: string; bot: boolean; ready: boolean;
  online: boolean; thinking: boolean; provider: string }
export interface Chat { id: string; from: string; to: string[]; text: string; at: number }
export interface Event { id: number; type: string; actor?: string; text: string;
  edge?: string; vertex?: string; victim?: string; resource?: string }
export interface State { revision: number; game_id: string; me: string | null; owner: boolean; paused: boolean;
  pause_reason: string; players: Seat[]; game: Game | null; chat: Chat[]; events: Event[];
  raw_game?: Game | null; sandbox?: Game | null; sandbox_actions?: Record<string, Action[]>;
  sandbox_ratios?: Record<string, Bundle>;
  bot_errors?: Record<string, string> }

export function escape(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;"})[c]!);
}
export function label(value: string): string { return value.replaceAll("_", " "); }
