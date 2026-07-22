// Overrides navigator.webdriver so X's login flow doesn't block automated
// browsers on sight (plan §17 G-webdriver-login, proven live 2026-07-05:
// X refused to log in at all while this read `true`). Combined with
// StealthySession's own patchright backend + `--disable-blink-features=
// AutomationControlled` + ignoring `--enable-automation` (scrapling's
// defaults), this reproduces the exact config validated against a real
// X login.
Object.defineProperty(navigator, "webdriver", { get: () => undefined });
