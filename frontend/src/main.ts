import "./global.css";
import { mountApplication } from "./app";

const root = document.querySelector<HTMLDivElement>("#app");
if (!root) throw new Error("The HTML entry point must contain #app.");

mountApplication(root);
