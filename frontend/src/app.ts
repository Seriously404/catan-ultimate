/** Replace this placeholder with the application as features are added. */
export function mountApplication(root: HTMLElement): void {
  const title = document.createElement("h1");
  title.textContent = "Catan Ultimate";

  const message = document.createElement("p");
  message.textContent = "Project foundation ready. The game is not implemented yet.";

  const health = document.createElement("a");
  health.href = "/api/health";
  health.textContent = "Check server health";

  root.replaceChildren(title, message, health);
}
