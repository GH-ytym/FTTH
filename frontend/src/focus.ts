// Chromium marks editable inputs focus-visible even after a mouse click.
// Track keyboard navigation explicitly so pointer focus stays unobtrusive.
document.documentElement.dataset.navigation = "pointer";
document.addEventListener("pointerdown", () => {
  document.documentElement.dataset.navigation = "pointer";
}, true);
document.addEventListener("keydown", (event) => {
  if (event.key === "Tab") document.documentElement.dataset.navigation = "keyboard";
}, true);
