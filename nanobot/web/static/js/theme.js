const THEME_KEY = "nanobot_theme";
const PALETTE_KEY = "nanobot_palette";

const VALID_THEMES = new Set(["auto", "light", "dark"]);
let allowedPalettes = new Set(["indigo", "gold", "ocean", "rose"]);

function normalizeTheme(value) {
  const v = String(value || "").trim().toLowerCase();
  return VALID_THEMES.has(v) ? v : "auto";
}

function normalizePalette(value) {
  const v = String(value || "").trim().toLowerCase();
  return allowedPalettes.has(v) ? v : "indigo";
}

export function getThemePreference() {
  try {
    return normalizeTheme(localStorage.getItem(THEME_KEY));
  } catch {
    return "auto";
  }
}

export function applyThemePreference(theme) {
  const normalized = normalizeTheme(theme);
  if (normalized === "auto") {
    document.documentElement.removeAttribute("data-theme");
  } else {
    document.documentElement.setAttribute("data-theme", normalized);
  }
  return normalized;
}

export function setThemePreference(theme) {
  const normalized = normalizeTheme(theme);
  try {
    if (normalized === "auto") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, normalized);
  } catch {
    // ignore storage errors
  }
  return applyThemePreference(normalized);
}

export function getPalettePreference() {
  try {
    return normalizePalette(localStorage.getItem(PALETTE_KEY));
  } catch {
    return "indigo";
  }
}

export function applyPalettePreference(palette) {
  const normalized = normalizePalette(palette);
  if (normalized === "indigo") {
    document.documentElement.removeAttribute("data-palette");
  } else {
    document.documentElement.setAttribute("data-palette", normalized);
  }
  return normalized;
}

export function setPalettePreference(palette) {
  const normalized = normalizePalette(palette);
  try {
    if (normalized === "indigo") localStorage.removeItem(PALETTE_KEY);
    else localStorage.setItem(PALETTE_KEY, normalized);
  } catch {
    // ignore storage errors
  }
  return applyPalettePreference(normalized);
}

function updateThemeButtons(container, activeTheme) {
  for (const btn of container.querySelectorAll("button[data-theme]")) {
    const isActive = String(btn.getAttribute("data-theme") || "") === activeTheme;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-pressed", isActive ? "true" : "false");
  }
}

export function initThemeSwitcher(container) {
  if (!container) return;

  const initial = applyThemePreference(getThemePreference());
  updateThemeButtons(container, initial);

  container.addEventListener("click", (e) => {
    const target = e.target;
    const btn = target && target.closest ? target.closest("button[data-theme]") : null;
    if (!btn) return;
    const next = setThemePreference(btn.getAttribute("data-theme"));
    updateThemeButtons(container, next);
  });
}

export function initPaletteSwitcher(selectEl) {
  if (!selectEl) return;

  try {
    const values = Array.from(selectEl.querySelectorAll("option"))
      .map((opt) => String(opt.value || "").trim().toLowerCase())
      .filter(Boolean);
    if (values.length) {
      allowedPalettes = new Set(values);
      allowedPalettes.add("indigo");
    }
  } catch {
    // ignore DOM errors
  }

  const initial = applyPalettePreference(getPalettePreference());
  selectEl.value = initial;

  selectEl.addEventListener("change", () => {
    const next = setPalettePreference(selectEl.value);
    selectEl.value = next;
  });
}
