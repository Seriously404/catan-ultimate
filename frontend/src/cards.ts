import { escape as h, resources } from "./types";
import type { Bundle } from "./types";

export const cardNames: Record<string, string> = {wood: "Wood", brick: "Brick", wool: "Sheep",
  wheat: "Grain", ore: "Ore", development: "Development", knight: "Knight", road_building: "Road building",
  year_of_plenty: "Year of plenty", monopoly: "Monopoly", victory_point: "Victory point",
  longest_road: "Longest road", largest_army: "Largest army", back: "Resource card"};
const paths: Record<string, string> = {
  wood: '<path d="M32 5 12 34h11L9 47h18v12h10V47h18L41 34h11Z" fill="#215c40"/><path d="M32 13v34" stroke="#90bb71" stroke-width="2"/>',
  brick: '<g fill="#bd6648" stroke="#6a3727" stroke-width="2"><path d="m7 22 18-9 19 8-18 10Z"/><path d="M7 22v13l19 10V31Z"/><path d="m26 31 18-10v14L26 45Z"/><path d="m26 40 17-9 15 8-17 10Z"/><path d="M26 40v13l15 7V49Z"/><path d="m41 49 17-10v13l-17 8Z"/></g>',
  wool: '<g fill="#f5efd7" stroke="#6a7350" stroke-width="2"><circle cx="23" cy="29" r="11"/><circle cx="35" cy="24" r="12"/><circle cx="43" cy="34" r="12"/><circle cx="29" cy="39" r="12"/></g><path d="M19 43v10m23-10v10" stroke="#394032" stroke-width="5"/><ellipse cx="49" cy="35" rx="7" ry="10" fill="#374437"/><circle cx="51" cy="32" r="1.6" fill="white"/>',
  wheat: '<g stroke="#a67520" stroke-width="3" fill="#e6bc4f"><path d="m28 58 7-46M18 57l-5-32M38 58l14-33"/><path d="M34 20Q16 11 28 5q13 7 6 15ZM33 32Q14 25 22 17q16 5 11 15ZM34 25Q51 12 47 6q-16 3-13 19ZM32 41Q50 34 48 25q-17 1-16 16Z"/></g>',
  ore: '<path d="m7 45 9-25 17-8 19 12 7 26-20 10-21-3Z" fill="#7e95a0" stroke="#344c5b" stroke-width="3"/><path d="m16 20 15 18 21-14M31 38l8 22M7 45l24-7" fill="none" stroke="#c3d0cd" stroke-width="2"/>',
  knight: '<path d="m21 57 4-18-8-12 15-18 17 18-8 12 4 18Z" fill="#aebfc5" stroke="#425968" stroke-width="3"/><path d="M23 28h19M32 14v20" stroke="#48616a" stroke-width="4"/><path d="m14 10 36 44m1-44L14 54" stroke="#d6b669" stroke-width="4"/>',
  road_building: '<path d="m10 61 17-20-8-16L38 3h19L36 26l9 18-20 17Z" fill="#a27047"/><path d="m19 59 17-18-9-16L48 4" stroke="#f5d791" stroke-dasharray="6 4" stroke-width="3" fill="none"/>',
  longest_road: '<path d="m10 61 17-20-8-16L38 3h19L36 26l9 18-20 17Z" fill="#a27047"/><path d="m19 59 17-18-9-16L48 4" stroke="#f5d791" stroke-dasharray="6 4" stroke-width="3" fill="none"/>',
  largest_army: '<path d="M10 10h44v22Q54 51 32 61 10 51 10 32Z" fill="#557d8e" stroke="#ddbf79" stroke-width="4"/><path d="m21 41 22-22m-22 0 22 22" stroke="#f0dfb6" stroke-width="5"/>',
  development: '<path d="m32 5 7 19 20 8-20 7-7 20-8-20-19-7 19-8Z" fill="#dcc180" stroke="#7b5837" stroke-width="2"/><circle cx="32" cy="32" r="8" fill="#5c785e"/>',
  year_of_plenty: '<path d="M12 24q20 17 41 0l-8 29H21Z" fill="#d8b866" stroke="#7c623b" stroke-width="3"/><circle cx="22" cy="23" r="9" fill="#a1bb6f"/><circle cx="39" cy="20" r="10" fill="#c98157"/><path d="m29 6 9 14" stroke="#7c623b" stroke-width="4"/>',
  monopoly: '<path d="M6 25 32 8l26 17ZM12 28v24m13-24v24m14-24v24m13-24v24M7 56h50" fill="#ddc48a" stroke="#8b7043" stroke-width="5"/>',
  victory_point: '<path d="m32 5 8 18 20 2-15 14 4 21-17-10-18 10 4-21L3 25l21-2Z" fill="#e3b953" stroke="#9b7839" stroke-width="3"/>',
};
export function icon(type: string): string {
  return `<svg viewBox="0 0 64 64" aria-hidden="true">${paths[type] ?? paths.development}</svg>`;
}
export function piece(type: string, color: string): string {
  const shape = type === "road" ? '<path d="M5 37 52 13l7 12L12 49Z"/>' : type === "city"
    ? '<path d="M5 54V29l13-13 14 13v9h8V18l9-11 10 11v36Z"/>'
    : '<path d="M9 55V29L32 9l23 20v26Z"/>';
  return `<svg viewBox="0 0 64 64" aria-hidden="true"><g fill="${h(color)}" stroke="#172c29" stroke-width="3" stroke-linejoin="round">${shape}</g></svg>`;
}
export function card(type: string, extra = "", back = false): string {
  return `<span class="paper-card ${h(type)} ${extra} ${back ? "face-down" : ""}" title="${h(cardNames[type] ?? type)}"><span class="card-art">${icon(back ? "development" : type)}</span><span class="card-title">${h(back ? "CATAN" : cardNames[type] ?? type)}</span></span>`;
}
export function cards(bundle: Partial<Bundle>, extra = ""): string {
  return resources.flatMap(r => Array.from({length: Math.max(0, bundle[r] ?? 0)}, () => card(r, extra))).join("");
}
export function dice(values: number[]): string {
  const pips: Record<number, number[]> = {1:[4], 2:[0,8], 3:[0,4,8], 4:[0,2,6,8], 5:[0,2,4,6,8], 6:[0,2,3,5,6,8]};
  return `<span class="dice-pair">${values.map(v => `<span class="die" role="img" aria-label="${v}">${Array.from({length:9}, (_,i) => `<i class="${pips[v]?.includes(i) ? "pip" : ""}"></i>`).join("")}</span>`).join("")}</span>`;
}
