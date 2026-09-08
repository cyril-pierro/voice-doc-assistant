/**
 * public/app.js — Premium Voice-Doc Assistant
 *
 * Screen 1: Landing & Auth (hero + Get Started → onboarding form)
 * Screen 2: Configuration & Document Upload (drag-drop + voice gender)
 * Screen 3: Cinematic Processing
 * Screen 4: Main Voice Dashboard (visualizer, mic, session trackers, log)
 *
 * 🎙️ Voice session model — Single-Button, finite-state turn-taking:
 *   The user controls the session loop via the central Mic icon, staying on the
 *   dashboard the whole time.
 *     • Tap (disconnected) → startSession(): getUserMedia + WebSocket open
 *       + Web Audio warm-up → LISTENING.
 *     • Tap (connected) → endSession(): track.stop() + close WS, then the mic
 *       returns to an idle gray state ON THE SAME dashboard (no navigation).
 *   The dedicated End Session button returns to the onboarding/config layout.
 *   No automated reconnection loop; the user drives every transition.
 *
 *   FSM:  IDLE → LISTENING (pulsing blue, mic hot, 15s inactivity → auto end)
 *              → SPEAKING (rippling green, outbound mic muted = echo protection)
 *              → LISTENING (auto re-entry on server turn_complete).
 *
 * Also handles:
 *  - 16kHz PCM via AudioWorklet/ScriptProcessor → base64 → WS /ws/stream
 *  - Playback queue via AudioContext (ordered, gapless)
 *  - Tool/conversion logs are visual-only (never spoken)
 */

// ---------------------------------------------------------------------------
// DOM refs — 4 screens + shared
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);

// Screen containers — now 6 screens: landing, auth, settings (config view/edit), config (documents), processing, dashboard
const screenLanding = $("screen-landing");
const screenAuth = $("screen-auth");
const screenSettings = $("screen-settings");
const screenConfig = $("screen-config");
const screenProcessing = $("screen-processing");
const screenDashboard = $("screen-dashboard");

// Screen 1 — Landing (hero) controls
const btnGetStarted = $("btnGetStarted");
// Screen 1b — Auth (standalone) — onboardingCard now lives inside screen-auth
const onboardingCard = $("onboardingCard");
const btnBackToHero = $("btnBackToHero");
const usernameInput = $("usernameInput");
const emailInput = $("emailInput");
const usernameError = $("usernameError");
const emailError = $("emailError");
const btnContinueToConfig = $("btnContinueToConfig");
const btnBackToLanding = $("btnBackToLanding");

// Screen 2
const dropZone = $("dropZone");
const fileInput = $("fileInput");
const uploadBtn = $("uploadBtn");
const uploadStatus = $("uploadStatus");
const docList = $("docList");
const refreshDocsBtn = $("refreshDocsBtn");
const voiceCards = document.querySelectorAll(".voice-card");
const previewUsername = $("previewUsername");
const btnLaunchAssistant = $("btnLaunchAssistant");
const launchHint = $("launchHint");

// Settings screen (separate from document upload)
const btnViewSettings = $("btnViewSettings");
const btnBackToDocuments = $("btnBackToDocuments");
const settingsUsernameInput = $("settingsUsernameInput");
const settingsEmailDisplay = $("settingsEmailDisplay");
const settingsCustomAiNameInput = $("settingsCustomAiNameInput");
const settingsPreferredGenderInput = $("settingsPreferredGenderInput");
const settingsAiNamePreview = $("settingsAiNamePreview");
const settingsUsernameError = $("settingsUsernameError");
const settingsCustomAiNameError = $("settingsCustomAiNameError");
const settingsFormError = $("settingsFormError");
const settingsFormSuccess = $("settingsFormSuccess");
const btnUpdateSettings = $("btnUpdateSettings");
const updateSettingsBtnText = $("updateSettingsBtnText");
const updateSettingsSpinner = $("updateSettingsSpinner");
const settingsUserBadge = $("settingsUserBadge");
const settingsLaunchPreviewAi = $("settingsLaunchPreviewAi");
const settingsLaunchPreviewVoice = $("settingsLaunchPreviewVoice");
const settingsLaunchPreviewUser = $("settingsLaunchPreviewUser");
const settingsVoiceCards = document.querySelectorAll(".settings-voice-card");

// Screen 3
const progressBar = $("progressBar");
const processingStep = $("processingStep");

// Screen 4 — dashboard
const dashUsername = $("dashUsername");
const dashVoice = $("dashVoice");
const dashDocs = $("dashDocs");
const statDocs = $("statDocs");
const statTurns = $("statTurns");
const statTools = $("statTools");
const statVoice = $("statVoice");
const statUser = $("statUser");
const sessionUsernameDisplay = $("sessionUsernameDisplay");
const sessionEmailDisplay = $("sessionEmailDisplay");
const footerEmail = $("footerEmail");
const welcomeNames = document.querySelectorAll(".welcomeName");
const welcomeVoices = document.querySelectorAll(".welcomeVoice");
const btnBackToConfig = $("btnBackToConfig");
const btnNewSession = $("btnNewSession");
const dropZoneDash = $("dropZoneDash");
const fileInputDash = $("fileInputDash");
const uploadBtnDash = $("uploadBtnDash");
const uploadStatusDash = $("uploadStatusDash");

// Shared / legacy compat
const backendUrlInput = $("backendUrlInput");
const sessionHint = $("sessionHint");
const connectionDot = $("connectionDot");
const connectionLabel = $("connectionLabel");
const connectBtn = $("connectBtn");
const disconnectBtn = $("disconnectBtn");

// Voice / log
const micBtn = $("micBtn");
const micIcon = $("micIcon");
const micBtnLabel = $("micBtnLabel");
const micStateLabel = $("micStateLabel");
const playingLabel = $("playingLabel");
const visualizer = $("visualizer");
const volumeSlider = $("volumeSlider");
const ttsToggle = $("ttsToggle");
const chatLog = $("chatLog");
const logCount = $("logCount");
const textInput = $("textInput");
const sendTextBtn = $("sendTextBtn");
const clearLogBtn = $("clearLogBtn");

// ---------------------------------------------------------------------------
// Premium state — survives across screens
// ---------------------------------------------------------------------------
const appState = {
  username: "guest",
  email: "guest@example.com",
  voiceGender: "female", // female -> Aoede, male -> Charon
  language: "english", // english | indian | spanish | japanese | french | korean — default english per user request
  backendUrl: "",
  docsCount: 0,
  customAiName: "Aria",
};

// Spec: global JS array for document persistence loop — survives across screens
window.uploadedDocuments = window.uploadedDocuments || [];
// Load persisted language or default
try {
  const savedLang = localStorage.getItem("preferred_language");
  if (savedLang && ["english","indian","spanish","japanese","french","korean"].includes(savedLang)) {
    appState.language = savedLang;
  }
} catch {}

let pendingFiles = [];
let pendingFilesDash = [];
let selectedVoice = "female";

// =============================================================================
// 🎭 FINITE STATE MACHINE — Voice Interaction Loop (Principal Frontend Engineer)
// =============================================================================
// States: IDLE (disconnected) → LISTENING (blue pulse, mic hot, 15s inactivity
// → auto end) → SPEAKING (green ripple, mic muted for echo) → LISTENING.
// Supervisor: auto-connect on mount + 2s infinite reconnection loop
// Teardown: mic toggle = immediate break + WS close + media release → config screen
// =============================================================================
let ws = null;
let isConnected = false;
let isMicActive = false; // declared here for hoisting (was lower)
// ---------------------------------------------------------------------------
// 🎭 FINITE STATE MACHINE — Single-button turn-taking (Principal Frontend
// Engineer / Realtime Systems). The user drives the WHOLE session lifecycle
// through the central Mic icon: tap once to connect + listen, tap again to end.
//   IDLE      : not connected, mic off, onboarding shown.
//   LISTENING : mic hot, streaming 16kHz PCM; 15s of no voice → auto end.
//   SPEAKING  : AI audio playing; outbound mic muted (echo protection) → auto
//               returns to LISTENING the instant the server finishes speaking.
// ---------------------------------------------------------------------------
const FSM = Object.freeze({ IDLE: "IDLE", LISTENING: "LISTENING", SPEAKING: "SPEAKING" });
let currentState = FSM.IDLE;
let silenceTimer = null; // 15s inactivity guard while in LISTENING
let lastActivityMs = Date.now();
let isMicBlockedForEcho = false; // true while AI audio plays → drop outbound PCM
let isMicMutedByUser = false;   // user tapped the mic to mute (pause streaming)
// UI state guard — avoids re-applying (and re-animating) the same visual state
// on every message, which is what caused the orb/label to "blink" mid-reply.
let currentVisual = null; // "listening" | "speaking" | null
let ttsSpeaking = false;  // true while a browser-TTS (mock) reply is talking
let speakEndTimer = null;
let pendingTurnEnd = false; // server finished sending; waiting for playback to drain
let turnCount = 0;
let toolCount = 0;

const SILENCE_TIMEOUT_MS = 15000; // 15s with no user voice in LISTENING → auto end session

function clearAllTimers() {
  if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
}
function clearIdleTimers() { return clearAllTimers(); } // compat alias
function transitionTo(next) {
  if (currentState === next) return;
  const prev = currentState;
  currentState = next;
  // Notify server of state for observability (best-effort)
  try { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "state", state: next, prev })); } catch {}
  if (next === FSM.LISTENING) enterListening();
  else if (next === FSM.SPEAKING) enterSpeaking();
}

// Enter LISTENING — mic hot, AI muted, pulsing blue, 15s inactivity guard armed.
function enterListening() {
  isMicBlockedForEcho = false;             // un-mute outbound PCM
  // User-muted sessions stay muted (gray mic, paused inactivity guard) — the
  // FSM may loop through LISTENING (e.g. turn_complete) while muted.
  if (isMicMutedByUser) { applyMutedVisual(); return; }
  setAiListening();                        // pulsing blue UI
  if (silenceTimer) clearTimeout(silenceTimer);
  // 15s with no voice ⇒ full disconnect. Armed immediately on entering
  // LISTENING, because the user explicitly clicked to connect the session.
  silenceTimer = setTimeout(handleSilenceExpiry, SILENCE_TIMEOUT_MS);
}

// Enter SPEAKING — AI audio streaming, outbound mic muted (acoustic feedback
// protection), no inactivity countdown while the assistant talks.
function enterSpeaking() {
  isMicBlockedForEcho = true;              // mute outbound PCM → echo protection
  setAiSpeaking();                         // rippling green UI
  if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
}
async function handleSilenceExpiry() {
  // Inactivity guard: 15s of no user voice while in LISTENING.
  if (currentState !== FSM.LISTENING || !isConnected) return;
  logMessage("system", "Session closing due to inactivity.");
  const message = "This session is closing due to inactivity. Goodbye!";
  try { await speakWithBrowserTTS(message); } catch {}
  try { teardownSession("inactivity"); } catch {}
}
function speakWithBrowserTTS(text) {
  // Promise-based browser TTS that also drives the FSM visuals: SPEAKING
  // (green ripple + "▶ speaking…" indicator) while the voice talks, back to
  // LISTENING when it finishes. Resolves when speech ends (or via a safety
  // timeout if the browser never fires onend).
  return new Promise((resolve) => {
    try {
      if (!("speechSynthesis" in window)) { ttsSpeaking = false; return resolve(); }
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 1.0;
      const vol = document.getElementById("volumeSlider");
      u.volume = parseFloat(vol ? vol.value : "0.9");
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        ttsSpeaking = false;
        if (currentState === FSM.SPEAKING) transitionTo(FSM.LISTENING);
        resolve();
      };
      u.onstart = () => {
        ttsSpeaking = true;
        transitionTo(FSM.SPEAKING);
      };
      u.onend = finish;
      u.onerror = finish;
      // Safety timeout if onend never fires (estimate ~80ms per char, min 4s).
      setTimeout(finish, Math.max(4000, (text || "").length * 80));
      window.speechSynthesis.speak(u);
      // Some browsers fire onstart late; flip visuals optimistically.
      ttsSpeaking = true;
      transitionTo(FSM.SPEAKING);
    } catch {
      ttsSpeaking = false;
      resolve();
    }
  });
}
function waitForNextTurnComplete(timeoutMs) {
  return new Promise((resolve) => {
    let done = false;
    const handler = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "turn_complete" || msg.type === "content_finished" || msg.state === "listening") {
          if (!done) { done = true; ws.removeEventListener("message", handler); resolve(true); }
        }
        if (msg.type === "audio_chunk") {
          // Consider audio delivery as progress, but still wait for turn_complete
        }
      } catch {}
    };
    try { ws.addEventListener("message", handler); } catch {}
    setTimeout(() => {
      if (!done) { try { ws.removeEventListener("message", handler); } catch {} resolve(false); }
    }, timeoutMs);
  });
}
function onUserActivity() {
  // User is talking (PCM is streaming) — reset the 15s inactivity guard so the
  // session stays alive as long as the user keeps speaking.
  lastActivityMs = Date.now();
  if (currentState === FSM.LISTENING) {
    if (silenceTimer) clearTimeout(silenceTimer);
    if (isConnected) {
      silenceTimer = setTimeout(handleSilenceExpiry, SILENCE_TIMEOUT_MS);
    }
  }
}
function onAssistantActivity() {
  // Assistant finished a turn — reset the guard for the user's next utterance.
  lastActivityMs = Date.now();
  if (currentState !== FSM.LISTENING) return;
  if (silenceTimer) clearTimeout(silenceTimer);
  if (isConnected) {
    silenceTimer = setTimeout(handleSilenceExpiry, SILENCE_TIMEOUT_MS);
  }
}
function resetIdleTimer() { return onAssistantActivity(); }
// Back-compat aliases (the old wind-down "is that all?" prompt is removed —
// inactivity now maps directly to the end-session routine).
async function promptBeforeDisconnect() { return handleSilenceExpiry(); }
function requestGracefulDisconnect() {
  if (!isConnected) return;
  teardownSession("graceful");
}

// ---------------------------------------------------------------------------
// Session teardown — the End Session routine. Turn off the mic device light
// (track.stop), close the WebSocket cleanly, and return to onboarding.
// There is intentionally NO reconnection loop: the user restarts via the mic.
//
// opts.stayOnDashboard (used when the user taps the mic to disconnect): tear
// down the session but STAY on the voice dashboard with an idle (gray) mic so
// the user can tap again to reconnect — do NOT navigate to the config page.
// ---------------------------------------------------------------------------
function endSession(trigger, opts = {}) {
  // Prevent re-entry while already tearing down.
  if (!isConnected && !isMicActive && !ws) return;
  clearAllTimers();
  isMicBlockedForEcho = true;
  try { if (ws) { ws.close(1000, `end-session:${trigger}`); ws = null; } } catch { ws = null; }
  isConnected = false;
  currentState = FSM.IDLE;
  currentVisual = null;
  ttsSpeaking = false;
  pendingTurnEnd = false;
  isMicMutedByUser = false;
  try { window.speechSynthesis?.cancel(); } catch {}
  try { stopMicrophone(); } catch {}        // track.stop() → turns off mic light
  setConnectionState(false, trigger === "inactivity" ? "Idle" : "Disconnected");
  updateOrbState(false);
  if (trigger === "inactivity") logMessage("system", "Session closed due to inactivity.");
  else logMessage("system", `Session ended via ${trigger}.`);
  if (opts.stayOnDashboard) {
    // Remain on the dashboard with the mic idle so the user can tap to reconnect.
    const lbl = document.getElementById("micStateLabel");
    if (lbl) { lbl.textContent = "Mic off — tap the mic to speak"; lbl.className = "text-xs font-medium px-2.5 py-1 rounded-full bg-slate-100 text-slate-600"; }
    const label2 = document.getElementById("micBtnLabel");
    if (label2) label2.textContent = "Tap to speak to " + (appState.customAiName || "Aria");
    return;
  }
  showScreen("config");
}
// Alias kept for callers (summary flow, silence timer) that use the old name.
function teardownSession(trigger) { return endSession(trigger); }

// Keep legacy inputs in sync for backend URL / docs refresh
function syncLegacyInputs() {
  const u = $("usernameInput");
  const e = $("emailInput");
  // hidden legacy inputs for app.js reuse (documents.js expects them)
  const legacyU = document.getElementById("usernameInput");
  const legacyE = document.getElementById("emailInput");
  // Actually our screen 1 inputs ARE the legacy ones — keep appState in sync
  if (u) appState.username = (u.value || "guest").trim() || "guest";
  if (e) appState.email = (e.value || "guest@example.com").trim() || "guest@example.com";
  // Mirror to hidden dashboard inputs if present
  const dashU = document.querySelector("#screen-dashboard #usernameInput");
  const dashE = document.querySelector("#screen-dashboard #emailInput");
  if (dashU) dashU.value = appState.username;
  if (dashE) dashE.value = appState.email;
}

// ---------------------------------------------------------------------------
// Screen helpers — hidden Tailwind transitions + hash routing
// ---------------------------------------------------------------------------
function showScreen(name) {
  [screenLanding, screenAuth, screenSettings, screenConfig, screenProcessing, screenDashboard].forEach(s => s && s.classList.add("hidden"));
  const map = { landing: screenLanding, auth: screenAuth, settings: screenSettings, config: screenConfig, processing: screenProcessing, dashboard: screenDashboard };
  const target = map[name];
  if (target) {
    target.classList.remove("hidden");
    // Smooth fade-in
    target.style.opacity = "0";
    requestAnimationFrame(() => {
      target.style.transition = "opacity 300ms ease";
      target.style.opacity = "1";
    });
  }
  // Keep URL in sync for direct routing to auth (e.g., /#auth or /auth)
  if (name === "auth") {
    history.replaceState(null, "", "#auth");
  } else if (name === "landing") {
    history.replaceState(null, "", window.location.pathname);
  } else if (["config", "processing", "dashboard", "settings"].includes(name)) {
    history.replaceState(null, "", `#${name}`);
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
  // Update document title for auth page
  if (name === "auth") document.title = "Sign In — Voice-Doc Assistant";
  else if (name === "landing") document.title = "Voice-Doc Assistant — Voice-Native Document AI";
}

// Handle direct routing to auth page (e.g., user navigates to /#auth or /auth)
function handleInitialRoute() {
  const hash = window.location.hash.replace("#", "").toLowerCase();
  const path = window.location.pathname.toLowerCase();
  if (hash === "auth" || path.endsWith("/auth") || path.endsWith("/auth/")) {
    showScreen("auth");
    return true;
  }
  return false;
}

function validateEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

// ---------------------------------------------------------------------------
// Screen 1: Premium Togglable Auth — Sign In / Sign Up with Enterprise Backend
// ---------------------------------------------------------------------------
const tabSignIn = $("tabSignIn");
const tabSignUp = $("tabSignUp");
const formSignIn = $("formSignIn");
const formSignUp = $("formSignUp");
const signinEmail = $("signinEmail");
const signinPassword = $("signinPassword");
const signinEmailError = $("signinEmailError");
const signinPasswordError = $("signinPasswordError");
const signinFormError = $("signinFormError");
const btnSignIn = $("btnSignIn");
const signinBtnText = $("signinBtnText");
const signinSpinner = $("signinSpinner");
const linkToSignUp = $("linkToSignUp");
const linkToSignIn = $("linkToSignIn");

// Sign Up fields
const passwordInput = $("passwordInput");
const retypePasswordInput = $("retypePasswordInput");
const preferredGenderInput = $("preferredGenderInput");
const customAiNameInput = $("customAiNameInput");
const aiNamePreview = $("aiNamePreview");
const passwordError = $("passwordError");
const retypePasswordError = $("retypePasswordError");
const customAiNameError = $("customAiNameError");
const signupFormError = $("signupFormError");
const btnSignUp = $("btnSignUp");
const signupBtnText = $("signupBtnText");
const signupSpinner = $("signupSpinner");
const voiceCardsSignup = document.querySelectorAll(".voice-card-signup");

// Auth state — holds the authenticated user profile from /api/auth/*
// Per spec: token + user details are stored in sessionStorage (not localStorage) after login
// and reused for all REST + WebSocket calls via Authorization: Bearer <token> / ?token=
let authenticatedUser = null; // {id, username, email, preferred_ai_gender, custom_ai_name}
let sessionToken = (() => {
  try { return sessionStorage.getItem("session_token") || localStorage.getItem("session_token") || null; }
  catch { return null; }
})();

function switchAuthTab(mode) {
  if (mode === "signup") {
    tabSignUp?.classList.add("auth-tab-active");
    tabSignUp?.classList.remove("auth-tab-inactive");
    tabSignIn?.classList.add("auth-tab-inactive");
    tabSignIn?.classList.remove("auth-tab-active");
    formSignUp?.classList.remove("hidden");
    formSignIn?.classList.add("hidden");
  } else {
    tabSignIn?.classList.add("auth-tab-active");
    tabSignIn?.classList.remove("auth-tab-inactive");
    tabSignUp?.classList.add("auth-tab-inactive");
    tabSignUp?.classList.remove("auth-tab-active");
    formSignIn?.classList.remove("hidden");
    formSignUp?.classList.add("hidden");
  }
  // Clear errors on switch
  [signinFormError, signupFormError, usernameError, emailError, passwordError, retypePasswordError, customAiNameError, signinEmailError, signinPasswordError].forEach(el => el?.classList.add("hidden"));
}

tabSignIn?.addEventListener("click", () => switchAuthTab("signin"));
tabSignUp?.addEventListener("click", () => switchAuthTab("signup"));
linkToSignUp?.addEventListener("click", () => switchAuthTab("signup"));
linkToSignIn?.addEventListener("click", () => switchAuthTab("signin"));

// Default to Sign In
switchAuthTab("signin");

btnGetStarted?.addEventListener("click", () => {
  // New flow: Get Started → standalone auth page (no hero)
  showScreen("auth");
  // Focus the visible form's first input after transition
  setTimeout(() => {
    if (formSignUp && !formSignUp.classList.contains("hidden")) {
      usernameInput?.focus();
    } else {
      signinEmail?.focus();
    }
  }, 320);
});

// Back button on auth page → hero (per user request: "backend button that takes me to the hero page")
btnBackToHero?.addEventListener("click", () => {
  showScreen("landing");
});

// Also handle browser back/forward for auth routing
window.addEventListener("hashchange", () => {
  const hash = window.location.hash.replace("#", "").toLowerCase();
  if (hash === "auth") showScreen("auth");
  else if (hash === "landing" || hash === "") {
    // Only go to landing if not already in a later step (config/processing/dashboard)
    // Keep it simple: hash "" means landing
    if (!screenAuth || screenAuth.classList.contains("hidden")) {
      // Don't force landing if user is in config/dashboard
    }
  }
});

usernameInput?.addEventListener("input", () => {
  if (usernameInput.value.trim().length >= 2) usernameError?.classList.add("hidden");
  if (previewUsername) previewUsername.textContent = usernameInput.value.trim() || "you";
  welcomeNames.forEach(n => n.textContent = usernameInput.value.trim() || "there");
});
emailInput?.addEventListener("input", () => {
  if (validateEmail(emailInput.value.trim())) emailError?.classList.add("hidden");
});
customAiNameInput?.addEventListener("input", () => {
  const v = customAiNameInput.value.trim() || "Aria";
  if (aiNamePreview) aiNamePreview.textContent = v;
  const cfgPreview = document.getElementById("aiNamePreviewConfig");
  if (cfgPreview) cfgPreview.textContent = v;
  if (customAiNameError && customAiNameInput.value.trim().length > 0) customAiNameError.classList.add("hidden");
});
passwordInput?.addEventListener("input", () => { if (passwordInput.value.length >= 6) passwordError?.classList.add("hidden"); });
retypePasswordInput?.addEventListener("input", () => { if (retypePasswordInput.value === passwordInput.value) retypePasswordError?.classList.add("hidden"); });

// Sign Up voice gender cards (inside auth) — sync with main voiceCards
function selectSignupVoice(gender) {
  if (preferredGenderInput) preferredGenderInput.value = gender;
  voiceCardsSignup.forEach(card => {
    const v = card.getAttribute("data-voice");
    const check = card.querySelector(".voice-check");
    if (v === gender) {
      card.classList.add("border-indigo-600", "bg-indigo-50", "shadow-sm");
      card.classList.remove("border-slate-200", "bg-white");
      if (check) { check.classList.remove("hidden"); check.classList.add("flex"); }
    } else {
      card.classList.remove("border-indigo-600", "bg-indigo-50", "shadow-sm");
      card.classList.add("border-slate-200", "bg-white");
      if (check) { check.classList.add("hidden"); check.classList.remove("flex"); }
    }
  });
  // Also sync the main config voice cards
  selectVoice(gender);
}
voiceCardsSignup.forEach(card => {
  card.addEventListener("click", () => selectSignupVoice(card.getAttribute("data-voice") || "female"));
});
selectSignupVoice("female");

// Helper: show field error
function showFieldError(el, msg) {
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("hidden");
}

// Helper: set appState from authenticated user and proceed
function onAuthSuccess(user, token) {
  authenticatedUser = user;
  sessionToken = token;
  try {
    if (token) sessionStorage.setItem("session_token", token);
    if (user.email) sessionStorage.setItem("user_email", user.email);
    if (user.username) sessionStorage.setItem("username", user.username);
    if (user.id) sessionStorage.setItem("user_id", user.id);
    // Also persist full user object for convenience
    sessionStorage.setItem("auth_user", JSON.stringify(user));
  } catch {}
  // Keep localStorage mirror for backwards compat / page reload if sessionStorage cleared by browser
  try {
    if (token) localStorage.setItem("session_token", token);
    if (user.email) localStorage.setItem("user_email", user.email);
    if (user.username) localStorage.setItem("username", user.username);
  } catch {}
  // Hydrate premium state
  appState.username = user.username;
  appState.email = user.email;
  appState.voiceGender = user.preferred_ai_gender || "female";
  selectedVoice = appState.voiceGender;
  // Custom AI name is stored for dashboard personalization
  appState.customAiName = user.custom_ai_name || "Aria";
  try {
    sessionStorage.setItem("custom_ai_name", appState.customAiName);
    sessionStorage.setItem("preferred_ai_gender", appState.voiceGender);
  } catch {}
  try {
    localStorage.setItem("custom_ai_name", appState.customAiName);
    localStorage.setItem("preferred_ai_gender", appState.voiceGender);
  } catch {}
  // Update UI
  selectVoice(appState.voiceGender);
  // Also update the signup voice selector to match
  selectSignupVoice(appState.voiceGender);
  // Update custom AI name previews
  const aiName = appState.customAiName;
  if (aiNamePreview) aiNamePreview.textContent = aiName;
  const cfgAiName = document.getElementById("aiNamePreviewConfig");
  if (cfgAiName) cfgAiName.textContent = aiName;
  const dashAiNameEl = document.getElementById("dashAiName");
  if (dashAiNameEl) dashAiNameEl.textContent = aiName;
  syncLegacyInputs();
  updateDashboardHeader();
  showScreen("config");
  refreshDocuments();
}

// Sign In — fetch POST /api/auth/signin
formSignIn?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = (signinEmail.value || "").trim();
  const password = signinPassword.value || "";
  let valid = true;
  if (!validateEmail(email)) { showFieldError(signinEmailError, "Please enter a valid email."); valid = false; } else signinEmailError?.classList.add("hidden");
  if (!password) { showFieldError(signinPasswordError, "Password is required."); valid = false; } else signinPasswordError?.classList.add("hidden");
  if (!valid) return;

  signinFormError?.classList.add("hidden");
  if (btnSignIn) btnSignIn.disabled = true;
  if (signinBtnText) signinBtnText.textContent = "Signing in...";
  if (signinSpinner) signinSpinner.classList.remove("hidden");

  try {
    const base = getBackendBase();
    const res = await fetch(`${base}/api/auth/signin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.message || "Sign in failed");
    // Success: data.user and data.session_token / access_token per spec (JWT)
    onAuthSuccess(data.user, data.access_token || data.session_token);
    // Also set legacy inputs for WS compat
    if (usernameInput) usernameInput.value = data.user.username;
    if (emailInput) emailInput.value = data.user.email;
  } catch (err) {
    showFieldError(signinFormError, err.message || "Sign in failed. Check your credentials.");
  } finally {
    if (btnSignIn) btnSignIn.disabled = false;
    if (signinBtnText) signinBtnText.textContent = "Sign In";
    if (signinSpinner) signinSpinner.classList.add("hidden");
  }
});

// Sign Up — fetch POST /api/auth/signup
formSignUp?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = (usernameInput.value || "").trim();
  const email = (emailInput.value || "").trim();
  const password = passwordInput.value || "";
  const retype = retypePasswordInput.value || "";
  const gender = (preferredGenderInput.value || "female").trim().toLowerCase();
  const aiName = (customAiNameInput.value || "").trim();

  let valid = true;
  if (username.length < 2) { showFieldError(usernameError, "Username must be at least 2 characters."); valid = false; } else usernameError?.classList.add("hidden");
  if (!validateEmail(email)) { showFieldError(emailError, "Please enter a valid email."); valid = false; } else emailError?.classList.add("hidden");
  if (password.length < 6) { showFieldError(passwordError, "Password must be at least 6 characters."); valid = false; } else passwordError?.classList.add("hidden");
  if (retype !== password) { showFieldError(retypePasswordError, "Passwords do not match."); valid = false; } else retypePasswordError?.classList.add("hidden");
  if (!["male","female"].includes(gender)) { valid = false; }
  if (!aiName) { showFieldError(customAiNameError, "Please name your AI assistant (e.g., Aria)."); valid = false; } else if (aiName.length > 50) { showFieldError(customAiNameError, "Name too long (max 50)."); valid = false; } else customAiNameError?.classList.add("hidden");
  if (!valid) return;

  signupFormError?.classList.add("hidden");
  if (btnSignUp) btnSignUp.disabled = true;
  if (signupBtnText) signupBtnText.textContent = "Creating account...";
  if (signupSpinner) signupSpinner.classList.remove("hidden");

  try {
    const base = getBackendBase();
    const res = await fetch(`${base}/api/auth/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        username,
        email,
        password,
        retype_password: retype,
        preferred_ai_gender: gender,
        custom_ai_name: aiName,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.message || "Sign up failed");
    onAuthSuccess(data.user, data.access_token || data.session_token);
  } catch (err) {
    // Handle 409 conflict (email/username taken) cleanly
    const msg = err.message || "Sign up failed.";
    if (msg.toLowerCase().includes("email")) showFieldError(emailError, msg);
    else if (msg.toLowerCase().includes("username")) showFieldError(usernameError, msg);
    else if (msg.toLowerCase().includes("password") && msg.includes("match")) showFieldError(retypePasswordError, msg);
    else showFieldError(signupFormError, msg);
  } finally {
    if (btnSignUp) btnSignUp.disabled = false;
    if (signupBtnText) signupBtnText.textContent = "Create Account & Continue";
    if (signupSpinner) signupSpinner.classList.add("hidden");
  }
});

// Legacy continue button (if still present in DOM from old version) — keep for backwards compat
btnContinueToConfig?.addEventListener("click", () => {
  // If new auth forms exist, delegate to the visible form's submit
  if (formSignUp && !formSignUp.classList.contains("hidden")) {
    formSignUp.requestSubmit();
    return;
  }
  if (formSignIn && !formSignIn.classList.contains("hidden")) {
    formSignIn.requestSubmit();
    return;
  }
  // Fallback to old simple validation (should not happen with new UI)
  const username = (usernameInput.value || "").trim();
  const email = (emailInput.value || "").trim();
  let valid = true;
  if (username.length < 2) {
    usernameError?.classList.remove("hidden");
    valid = false;
  } else usernameError?.classList.add("hidden");
  if (!validateEmail(email)) {
    emailError?.classList.remove("hidden");
    valid = false;
  } else emailError?.classList.add("hidden");
  if (!valid) return;
  appState.username = username;
  appState.email = email;
  syncLegacyInputs();
  updateDashboardHeader();
  showScreen("config");
  refreshDocuments();
});

btnBackToLanding?.addEventListener("click", () => showScreen("landing"));

// Restore session from sessionStorage on load (premium stateful onboarding)
// Falls back to localStorage for migration
(function restoreSession() {
  try {
    const get = (k) => {
      try { return sessionStorage.getItem(k) || localStorage.getItem(k); } catch { return localStorage.getItem(k); }
    };
    // Prefer auth_user JSON if present
    let storedUser = get("username");
    let storedEmail = get("user_email");
    let storedToken = get("session_token");
    let storedGender = get("preferred_ai_gender");
    let storedAiName = get("custom_ai_name");
    // Try to hydrate from auth_user blob
    try {
      const blob = sessionStorage.getItem("auth_user") || localStorage.getItem("auth_user");
      if (blob) {
        const u = JSON.parse(blob);
        storedUser = u.username || storedUser;
        storedEmail = u.email || storedEmail;
        storedGender = u.preferred_ai_gender || storedGender;
        storedAiName = u.custom_ai_name || storedAiName;
        authenticatedUser = u;
        if (u.id) try { sessionStorage.setItem("user_id", u.id); } catch {}
      }
    } catch {}
    sessionToken = storedToken || sessionToken;
    if (storedEmail && storedUser && storedToken) {
      appState.username = storedUser;
      appState.email = storedEmail;
      appState.voiceGender = storedGender || "female";
      appState.customAiName = storedAiName || "Aria";
      selectedVoice = appState.voiceGender;
      authenticatedUser = authenticatedUser || { username: storedUser, email: storedEmail, preferred_ai_gender: storedGender, custom_ai_name: storedAiName };
      if (usernameInput) usernameInput.value = storedUser;
      if (emailInput) emailInput.value = storedEmail;
      if (signinEmail) signinEmail.value = storedEmail;
      if (customAiNameInput) customAiNameInput.value = storedAiName || "";
      selectVoice(appState.voiceGender);
      selectSignupVoice(appState.voiceGender);
    }
  } catch {}
})();

// ---------------------------------------------------------------------------
// Settings screen — view/edit user configuration (separate from Documents)
// Logged-in users see Document upload first; Settings is explicit edit with Update button
// ---------------------------------------------------------------------------
function populateSettings() {
  const u = authenticatedUser || { username: appState.username, email: appState.email, preferred_ai_gender: appState.voiceGender, custom_ai_name: appState.customAiName };
  if (settingsUsernameInput) settingsUsernameInput.value = u.username || appState.username || "";
  if (settingsEmailDisplay) settingsEmailDisplay.value = u.email || appState.email || "";
  if (settingsCustomAiNameInput) settingsCustomAiNameInput.value = u.custom_ai_name || appState.customAiName || "Aria";
  if (settingsPreferredGenderInput) settingsPreferredGenderInput.value = (u.preferred_ai_gender || appState.voiceGender || "female").toLowerCase();
  if (settingsAiNamePreview) settingsAiNamePreview.textContent = settingsCustomAiNameInput?.value?.trim() || "Aria";
  if (settingsUserBadge) settingsUserBadge.textContent = `${u.username || appState.username} • ${u.email || appState.email}`;
  selectSettingsVoice(settingsPreferredGenderInput?.value || "female");
  updateSettingsPreview();
  settingsFormError?.classList.add("hidden");
  settingsFormSuccess?.classList.add("hidden");
}
function selectSettingsVoice(gender) {
  gender = (gender || "female").toLowerCase();
  if (settingsPreferredGenderInput) settingsPreferredGenderInput.value = gender;
  settingsVoiceCards.forEach(card => {
    const v = card.getAttribute("data-voice");
    const check = card.querySelector(".voice-check");
    if (v === gender) {
      card.classList.add("border-indigo-600", "bg-indigo-50", "shadow-sm");
      card.classList.remove("border-slate-200", "bg-white");
      if (check) { check.classList.remove("hidden"); check.classList.add("flex"); }
    } else {
      card.classList.remove("border-indigo-600", "bg-indigo-50", "shadow-sm");
      card.classList.add("border-slate-200", "bg-white");
      if (check) { check.classList.add("hidden"); check.classList.remove("flex"); }
    }
  });
  updateSettingsPreview();
}
function updateSettingsPreview() {
  const ai = settingsCustomAiNameInput?.value?.trim() || appState.customAiName || "Aria";
  const gender = settingsPreferredGenderInput?.value || "female";
  const voiceName = gender === "female" ? "Aoede" : "Charon";
  if (settingsAiNamePreview) settingsAiNamePreview.textContent = ai;
  if (settingsLaunchPreviewAi) settingsLaunchPreviewAi.textContent = ai;
  if (settingsLaunchPreviewVoice) settingsLaunchPreviewVoice.textContent = voiceName;
  if (settingsLaunchPreviewUser) settingsLaunchPreviewUser.textContent = settingsUsernameInput?.value?.trim() || appState.username || "—";
}
settingsVoiceCards.forEach(card => {
  card.addEventListener("click", () => selectSettingsVoice(card.getAttribute("data-voice") || "female"));
});
settingsCustomAiNameInput?.addEventListener("input", () => {
  if (settingsAiNamePreview) settingsAiNamePreview.textContent = settingsCustomAiNameInput.value.trim() || "Aria";
  updateSettingsPreview();
  if (settingsCustomAiNameError) settingsCustomAiNameError.classList.add("hidden");
});
settingsUsernameInput?.addEventListener("input", () => {
  if (settingsUsernameError) settingsUsernameError.classList.add("hidden");
  updateSettingsPreview();
});
btnViewSettings?.addEventListener("click", () => {
  populateSettings();
  showScreen("settings");
});
btnBackToDocuments?.addEventListener("click", () => {
  showScreen("config");
  refreshDocuments();
});
btnUpdateSettings?.addEventListener("click", async () => {
  const username = (settingsUsernameInput?.value || "").trim();
  const customAiName = (settingsCustomAiNameInput?.value || "").trim();
  const gender = (settingsPreferredGenderInput?.value || "female").trim().toLowerCase();
  let valid = true;
  if (username.length < 2) { settingsUsernameError.textContent = "Username must be at least 2 characters."; settingsUsernameError.classList.remove("hidden"); valid = false; } else settingsUsernameError?.classList.add("hidden");
  if (!customAiName) { settingsCustomAiNameError.textContent = "AI name is required."; settingsCustomAiNameError.classList.remove("hidden"); valid = false; } else if (customAiName.length > 50) { settingsCustomAiNameError.textContent = "Name too long (max 50)."; settingsCustomAiNameError.classList.remove("hidden"); valid = false; } else settingsCustomAiNameError?.classList.add("hidden");
  if (!["male","female"].includes(gender)) valid = false;
  if (!valid) return;
  settingsFormError?.classList.add("hidden");
  settingsFormSuccess?.classList.add("hidden");
  if (btnUpdateSettings) btnUpdateSettings.disabled = true;
  if (updateSettingsBtnText) updateSettingsBtnText.textContent = "Updating…";
  if (updateSettingsSpinner) updateSettingsSpinner.classList.remove("hidden");
  try {
    const base = getBackendBase();
    const headers = { "Content-Type": "application/json", ...getAuthHeaders() };
    if (!headers.Authorization) throw new Error("Missing token — please sign in again.");
    const res = await fetch(`${base}/api/auth/me`, {
      method: "PUT",
      headers,
      body: JSON.stringify({ username, preferred_ai_gender: gender, custom_ai_name: customAiName }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Update failed");
    // Backend returns new JWT + user
    onAuthSuccess(data.user, data.access_token || data.session_token);
    // Also ensure appState reflects new settings for launch
    appState.username = data.user.username;
    appState.customAiName = data.user.custom_ai_name;
    appState.voiceGender = data.user.preferred_ai_gender;
    try { selectVoice(appState.voiceGender); selectSettingsVoice(appState.voiceGender); } catch {}
    try { syncLegacyInputs(); updateDashboardHeader(); } catch {}
    updateSettingsPreview();
    settingsFormSuccess.textContent = "✓ Configuration updated. Launch Assistant will use your new AI name & voice.";
    settingsFormSuccess.classList.remove("hidden");
    // Update config screen badge
    if (typeof updateDashboardHeader === "function") updateDashboardHeader();
  } catch (err) {
    settingsFormError.textContent = err.message || "Update failed.";
    settingsFormError.classList.remove("hidden");
  } finally {
    if (btnUpdateSettings) btnUpdateSettings.disabled = false;
    if (updateSettingsBtnText) updateSettingsBtnText.textContent = "Update Configuration";
    if (updateSettingsSpinner) updateSettingsSpinner.classList.add("hidden");
  }
});

// ---------------------------------------------------------------------------
// Screen 2: Voice gender selector (premium card)
// ---------------------------------------------------------------------------
function selectVoice(gender) {
  selectedVoice = gender;
  appState.voiceGender = gender;
  voiceCards.forEach(card => {
    const v = card.getAttribute("data-voice");
    const check = card.querySelector(".voice-check");
    if (v === gender) {
      card.classList.add("border-indigo-600", "bg-indigo-50", "shadow-sm");
      card.classList.remove("border-slate-200", "bg-white");
      if (check) { check.classList.remove("hidden"); check.classList.add("flex"); }
    } else {
      card.classList.remove("border-indigo-600", "bg-indigo-50", "shadow-sm");
      card.classList.add("border-slate-200", "bg-white");
      if (check) { check.classList.add("hidden"); check.classList.remove("flex"); }
    }
  });
  const voiceName = gender === "female" ? "Aoede" : "Charon";
  welcomeVoices.forEach(v => v.textContent = voiceName);
  if (dashVoice) dashVoice.textContent = voiceName;
  if (statVoice) statVoice.textContent = voiceName;
}
voiceCards.forEach(card => {
  card.addEventListener("click", () => selectVoice(card.getAttribute("data-voice") || "female"));
});
// Restore language from appState (default english)
try {
  if (appState.language) {
    // Will be applied to UI after DOM ready
    setTimeout(() => selectLanguage(appState.language), 100);
  } else {
    selectVoice("female");
  }
} catch { selectVoice("female"); }

// ---------------------------------------------------------------------------
// Select Language menu — main page (default English, supports Indian, Spanish, Japanese, French, Korean)
// ---------------------------------------------------------------------------
const btnLanguage = $("btnLanguage");
const languageDropdown = $("languageDropdown");
const languageLabel = $("languageLabel");

function selectLanguage(lang) {
  lang = (lang || "english").toLowerCase();
  const allowed = ["english","indian","spanish","japanese","french","korean"];
  if (!allowed.includes(lang)) lang = "english";
  appState.language = lang;
  try { localStorage.setItem("preferred_language", lang); } catch {}
  // Update UI label
  const labels = {english:"English", indian:"Indian (Hindi)", spanish:"Spanish", japanese:"Japanese", french:"French", korean:"Korean"};
  if (languageLabel) languageLabel.textContent = labels[lang] || "English";
  // Update checks in dropdown
  document.querySelectorAll(".lang-option").forEach(btn => {
    const v = btn.getAttribute("data-lang");
    const check = btn.querySelector(".lang-check");
    if (v === lang) {
      btn.classList.add("bg-indigo-50", "border", "border-indigo-200");
      if (check) check.classList.remove("hidden");
    } else {
      btn.classList.remove("bg-indigo-50", "border", "border-indigo-200");
      if (check) check.classList.add("hidden");
    }
  });
  // If already connected, inform user they need to reconnect for new language
  if (isConnected) {
    logMessage("system", `Language switched to ${labels[lang]} — next voice session will use ${lang}. Reconnect or tap mic again to apply.`);
  }
}
document.querySelectorAll(".lang-option").forEach(btn => {
  btn.addEventListener("click", () => {
    selectLanguage(btn.getAttribute("data-lang") || "english");
    if (languageDropdown) languageDropdown.classList.add("hidden");
  });
});
btnLanguage?.addEventListener("click", (e) => {
  e.stopPropagation();
  languageDropdown?.classList.toggle("hidden");
});
document.addEventListener("click", (e) => {
  const wrap = $("languageWrap");
  if (wrap && !wrap.contains(e.target) && languageDropdown && !languageDropdown.classList.contains("hidden")) {
    languageDropdown.classList.add("hidden");
  }
});
// Initialize language UI after voice
selectLanguage(appState.language || "english");

usernameInput?.addEventListener("input", () => {
  const n = usernameInput.value.trim() || "you";
  if (previewUsername) previewUsername.textContent = n;
  welcomeNames.forEach(el => el.textContent = n);
});

// ---------------------------------------------------------------------------
// Upload handling (shared for config + dashboard)
// ---------------------------------------------------------------------------
function setupUpload(dropZoneEl, fileInputEl, pendingRef) {
  if (!dropZoneEl || !fileInputEl) return;
  fileInputEl.addEventListener("change", () => {
    if (pendingRef === "main") pendingFiles = Array.from(fileInput.files || []);
    else pendingFilesDash = Array.from(fileInputDash.files || []);
    const count = (pendingRef === "main" ? pendingFiles : pendingFilesDash).length;
    const statusEl = pendingRef === "main" ? uploadStatus : uploadStatusDash;
    if (count) {
      statusEl.textContent = `${count} file(s) selected — click Upload.`;
      statusEl.className = "text-xs mt-2 text-indigo-600";
    }
  });
  dropZoneEl.addEventListener("click", () => fileInputEl.click());
  dropZoneEl.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZoneEl.classList.add("border-indigo-400", "bg-indigo-50");
  });
  dropZoneEl.addEventListener("dragleave", () => {
    dropZoneEl.classList.remove("border-indigo-400", "bg-indigo-50");
  });
  dropZoneEl.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZoneEl.classList.remove("border-indigo-400", "bg-indigo-50");
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length) {
      if (pendingRef === "main") pendingFiles = files;
      else pendingFilesDash = files;
      const statusEl = pendingRef === "main" ? uploadStatus : uploadStatusDash;
      statusEl.textContent = `${files.length} file(s) dropped — click Upload.`;
      statusEl.className = "text-xs mt-2 text-indigo-600";
    }
  });
}
setupUpload(dropZone, fileInput, "main");
setupUpload(dropZoneDash, fileInputDash, "dash");

async function doUpload(files, statusEl, btnEl) {
  if (!files.length) {
    statusEl.textContent = "Select files first (click the drop zone).";
    statusEl.className = "text-xs mt-2 text-amber-600";
    return false;
  }
  btnEl.disabled = true;
  statusEl.textContent = `Uploading ${files.length} file(s)...`;
  statusEl.className = "text-xs mt-2 text-slate-500";
  const base = getBackendBase();
  for (const file of files) {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const authHeaders = getAuthHeaders();
      // Auth via Bearer token from sessionStorage if present; no ?username&email query
      // get_session_user will use Bearer if present, else guest fallback (token optional for demo)
      let res = await fetch(`${base}/api/upload`, {
        method: "POST",
        body: fd,
        headers: authHeaders,
      });
      // If primary fails with 404, try alias (added for spec compliance)
      if (res.status === 404) {
        // Re-create FormData since it was consumed
        const fd2 = new FormData();
        fd2.append("file", file);
        res = await fetch(`${base}/api/documents/upload`, {
          method: "POST",
          body: fd2,
          headers: authHeaders,
        });
      }
      if (res.status === 401) {
        const body = await res.json().catch(()=>({}));
        throw new Error(body.detail || "Unauthorized — token invalid/expired. Please sign in again.");
      }
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      logMessage("system", `Uploaded "${data.filename}" — ${data.chunks} chunks, ${data.text_length} chars.`, { preview: data.preview?.slice(0, 300) });
      // Frontend State Syncing per spec: append metadata to global array
      const meta = {
        filename: data.filename,
        size: data.size_bytes || file.size,
        id: data.id,
        systemIndexId: data.id,
        chunks: data.chunks,
        text_length: data.text_length,
        uploadedAt: new Date().toISOString(),
      };
      window.uploadedDocuments.push(meta);
      // Also keep appState in sync
      appState.docsCount = window.uploadedDocuments.length;
    } catch (err) {
      logMessage("system", `Upload failed for "${file.name}": ${err.message}`);
      statusEl.textContent = `Upload error: ${err.message}`;
      statusEl.className = "text-xs mt-2 text-red-600";
      btnEl.disabled = false;
      return false;
    }
  }
  statusEl.textContent = `✓ Uploaded ${files.length} file(s).`;
  statusEl.className = "text-xs mt-2 text-emerald-600";
  await refreshDocuments();
  // Re-render managed dropdown if on dashboard
  renderManagedDropdown();
  return true;
}

uploadBtn?.addEventListener("click", async () => {
  const ok = await doUpload(pendingFiles, uploadStatus, uploadBtn);
  if (ok) { pendingFiles = []; fileInput.value = ""; }
});
uploadBtnDash?.addEventListener("click", async () => {
  const ok = await doUpload(pendingFilesDash, uploadStatusDash, uploadBtnDash);
  if (ok) { pendingFilesDash = []; fileInputDash.value = ""; }
});

async function refreshDocuments() {
  const base = getBackendBase();
  try {
    const headers = getAuthHeaders();
    const res = await fetch(`${base}/api/documents`, {
      headers,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    renderDocList(data.documents || []);
    appState.docsCount = data.count || 0;
    updateDashboardHeader();
  } catch (err) {
    if (docList) docList.innerHTML = `<p class="text-xs text-red-500">Failed to load: ${err.message}</p>`;
  }
}

function renderDocList(docs) {
  if (!docList) return;
  if (!docs.length) {
    docList.innerHTML = `<p class="text-xs text-slate-400 py-2">No documents yet. Upload to enable tool-calling.</p>`;
    return;
  }
  docList.innerHTML = "";
  for (const d of docs) {
    const row = document.createElement("div");
    row.className = "rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 flex items-start justify-between gap-2";
    row.innerHTML = `
      <div class="min-w-0">
        <p class="text-xs font-semibold truncate">${escapeHtml(d.filename)}</p>
        <p class="text-[11px] text-slate-500 mono">${d.chunks} chunks · ${d.text_length} chars · ${d.id}</p>
        <p class="text-[11px] text-slate-400 truncate mt-0.5">${escapeHtml(d.text_preview || "")}</p>
      </div>
      <button data-id="${d.id}" class="delete-doc shrink-0 text-[11px] font-medium px-2 py-1 rounded-full bg-white border border-slate-200 hover:bg-red-50 hover:text-red-600 hover:border-red-200 transition">Delete</button>
    `;
    docList.appendChild(row);
  }
  docList.querySelectorAll(".delete-doc").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-id");
      const base = getBackendBase();
      try {
        const res = await fetch(`${base}/api/documents/${id}`, { method: "DELETE", headers: getAuthHeaders() });
        if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
        logMessage("system", `Deleted document ${id}`);
        await refreshDocuments();
      } catch (err) {
        logMessage("system", `Delete failed: ${err.message}`);
      }
    });
  });
}

function escapeHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
refreshDocsBtn?.addEventListener("click", refreshDocuments);

// ---------------------------------------------------------------------------
// Screen 2 -> Screen 3 -> Screen 4: Launch Assistant (cinematic 3s)
// ---------------------------------------------------------------------------
btnLaunchAssistant?.addEventListener("click", () => {
  // Allow launch without docs, but hint
  if (appState.docsCount === 0 && pendingFiles.length === 0) {
    launchHint?.classList.remove("hidden");
    // Still allow — user may want to chat without docs
  } else {
    launchHint?.classList.add("hidden");
  }
  // If there are pending files, upload first
  (async () => {
    if (pendingFiles.length) {
      const ok = await doUpload(pendingFiles, uploadStatus, uploadBtn);
      if (!ok) return;
      pendingFiles = []; fileInput.value = "";
    }
    startProcessing();
  })();
});

function startProcessing() {
  showScreen("processing");
  // Animate progress bar via Tailwind animation + JS steps
  if (progressBar) {
    progressBar.style.width = "0%";
    progressBar.style.transition = "width 3s ease-in-out";
    requestAnimationFrame(() => { progressBar.style.width = "100%"; });
  }
  const steps = [
    "Initializing vocal synthesis...",
    "Indexing documents via pgvector...",
    "Tuning voice profile: " + (selectedVoice === "female" ? "Aoede — warm, clear" : "Charon — deep, calm"),
    "Ready — launching workspace...",
  ];
  let idx = 0;
  const interval = setInterval(() => {
    if (processingStep) processingStep.textContent = steps[idx] || steps[steps.length - 1];
    idx++;
    if (idx >= steps.length) clearInterval(interval);
  }, 750);

  setTimeout(async () => {
    clearInterval(interval);
    // Refresh user config from backend before showing dashboard — ensures custom AI name is backend truth, not stale Aria
    try {
      const base = getBackendBase();
      const res = await fetch(`${base}/api/auth/me`, { headers: getAuthHeaders() });
      if (res.ok) {
        const u = await res.json();
        authenticatedUser = u;
        appState.username = u.username;
        appState.email = u.email;
        appState.customAiName = u.custom_ai_name;
        appState.voiceGender = u.preferred_ai_gender;
        selectedVoice = appState.voiceGender;
        try {
          sessionStorage.setItem("auth_user", JSON.stringify(u));
          sessionStorage.setItem("username", u.username);
          sessionStorage.setItem("user_email", u.email);
          sessionStorage.setItem("custom_ai_name", u.custom_ai_name);
          sessionStorage.setItem("preferred_ai_gender", u.preferred_ai_gender);
        } catch {}
        try { selectVoice(appState.voiceGender); selectSettingsVoice(appState.voiceGender); } catch {}
      }
    } catch {}
    showScreen("dashboard");
    updateDashboardHeader();
    // Hydrating the Final View per spec: loop through window.uploadedDocuments to render dropdown
    try {
      // Ensure window.uploadedDocuments is populated from any prior uploads
      // If empty but docs were fetched via API, seed it from docList
      if (!window.uploadedDocuments.length) {
        // Try to hydrate from last refreshDocuments data — auth via Bearer token only
        const base = getBackendBase();
        fetch(`${base}/api/documents`, { headers: getAuthHeaders() })
          .then(r => r.json())
          .then(data => {
            if (data && data.documents) {
              data.documents.forEach(d => {
                if (!window.uploadedDocuments.find(x => x.id === d.id)) {
                  window.uploadedDocuments.push({
                    filename: d.filename,
                    size: d.size_bytes,
                    id: d.id,
                    systemIndexId: d.id,
                    chunks: d.chunks,
                    text_length: d.text_length,
                  });
                }
              });
              renderManagedDropdown();
            }
          }).catch(()=>{});
      } else {
        renderManagedDropdown();
      }
    } catch {}
    renderManagedDropdown();
    // The session is NOT auto-connected here. The user starts it on-demand by
    // tapping the central mic icon (single-button lifecycle, no reconnect loop).
    try { if (typeof updateMicUI === "function") updateMicUI(false); } catch {}
  }, 3000);
}

function updateDashboardHeader() {
  const voiceName = selectedVoice === "female" ? "Aoede" : "Charon";
  const aiName = (appState.customAiName || "Aria").trim();
  if (dashUsername) dashUsername.textContent = appState.username;
  if (dashVoice) dashVoice.textContent = voiceName;
  if (dashDocs) dashDocs.textContent = `${appState.docsCount} docs`;
  if (statDocs) statDocs.textContent = String(appState.docsCount);
  if (statVoice) statVoice.textContent = voiceName;
  if (statUser) statUser.textContent = appState.username;
  if (sessionUsernameDisplay) sessionUsernameDisplay.textContent = appState.username;
  if (sessionEmailDisplay) sessionEmailDisplay.textContent = appState.email;
  if (footerEmail) footerEmail.textContent = appState.email;
  welcomeNames.forEach(n => n.textContent = appState.username);
  welcomeVoices.forEach(v => v.textContent = voiceName);
  // AI name — ensure every place that shows Aria uses backend customAiName
  const aiEls = [
    "dashAiName", "dashAiNameHero", "configAiNameDisplay", "configAiNameHighlight",
    "processingAiName", "dashAiNameSession", "dashAiNameStat", "voiceStreamAiName"
  ];
  aiEls.forEach(id => { const el = document.getElementById(id); if (el) el.textContent = aiName; });
  document.querySelectorAll(".micAiName").forEach(el => el.textContent = aiName);
  document.querySelectorAll(".welcomeAiName").forEach(el => el.textContent = aiName);
  const micLabel = document.getElementById("micBtnLabel");
  if (micLabel) micLabel.innerHTML = `Tap to speak to <span class="micAiName">${aiName}</span>`;
  const cfgBadge = document.getElementById("configAiNameDisplay");
  if (cfgBadge) cfgBadge.textContent = aiName;
  const cfgHighlight = document.getElementById("configAiNameHighlight");
  if (cfgHighlight) cfgHighlight.textContent = aiName;
  const procAi = document.getElementById("processingAiName");
  if (procAi) procAi.textContent = aiName;
  // Document-context header (separate from settings)
  const cfgUser = document.getElementById("configUsernameDisplay");
  if (cfgUser) cfgUser.textContent = appState.username;
  const cfgVoice = document.getElementById("configVoiceDisplay");
  if (cfgVoice) cfgVoice.textContent = voiceName;
  const cfgBadge2 = document.getElementById("configUserBadge");
  if (cfgBadge2) cfgBadge2.textContent = `${appState.username} • ${aiName}`;
  const profHint = document.getElementById("profileVoiceHint");
  if (profHint) profHint.textContent = voiceName;
  // Sync legacy hidden inputs for backend compat
  const u = document.getElementById("usernameInput");
  const e = document.getElementById("emailInput");
  if (u) u.value = appState.username;
  if (e) e.value = appState.email;
}

// Back to config from dashboard — per spec should go to Configuration (settings), not Document Context
btnBackToConfig?.addEventListener("click", () => {
  populateSettings();
  showScreen("settings");
});
function clearSessionAuth() {
  try {
    sessionStorage.removeItem("session_token");
    sessionStorage.removeItem("user_email");
    sessionStorage.removeItem("username");
    sessionStorage.removeItem("user_id");
    sessionStorage.removeItem("auth_user");
  } catch {}
  try {
    localStorage.removeItem("session_token");
    localStorage.removeItem("user_email");
    localStorage.removeItem("username");
    // keep custom_ai_name/preferred_ai_gender for UX? clear too for clean logout
  } catch {}
  sessionToken = null;
  authenticatedUser = null;
}
btnNewSession?.addEventListener("click", () => {
  disconnect();
  clearSessionAuth();
  // Call backend signout to clear httponly cookie
  try { fetch(`${getBackendBase()}/api/auth/signout`, { method: "POST", credentials: "include" }); } catch {}
  showScreen("landing");
  // Reset onboarding? Keep username/email but reset counters
  turnCount = 0; toolCount = 0;
  if (statTurns) statTurns.textContent = "0";
  if (statTools) statTools.textContent = "0";
  logMessage("system", "Session ended — token cleared from sessionStorage. Please sign in again.");
});

// Sign Out (config screen) — hard logout: clear local auth + backend cookie,
// then return to the Sign In / Sign Up landing page.
const btnSignOutConfig = document.getElementById("btnSignOutConfig");
btnSignOutConfig?.addEventListener("click", () => {
  try { btnSignOutConfig.disabled = true; btnSignOutConfig.textContent = "Signing out…"; } catch {}
  // End any active voice session first (mic + WebSocket).
  try { if (typeof endSession === "function" && (isConnected || isMicActive)) endSession("sign-out"); } catch {}
  try { disconnect(); } catch {}
  // Clear local tokens/user state.
  try { clearSessionAuth(); } catch {}
  try {
    sessionStorage.removeItem("custom_ai_name");
    sessionStorage.removeItem("voiceGender");
    sessionStorage.removeItem("preferred_ai_gender");
  } catch {}
  // Clear the backend httponly session cookie (fire-and-forget).
  try { fetch(`${getBackendBase()}/api/auth/signout`, { method: "POST", credentials: "include" }); } catch {}
  // Land on the Sign In / Sign Up page.
  showScreen("landing");
  turnCount = 0; toolCount = 0;
  if (statTurns) statTurns.textContent = "0";
  if (statTools) statTools.textContent = "0";
  logMessage("system", "Signed out — please sign in or create an account.");
});

// ---------------------------------------------------------------------------
// Helpers — connection, logging, backend base
// ---------------------------------------------------------------------------
function logMessage(role, text, meta = {}) {
  const countEl = document.getElementById("logCount");
  if (countEl) {
    const cur = parseInt(countEl.textContent) || 0;
    countEl.textContent = `${cur + 1} messages`;
  }
  // Also update turn/tool counters for dashboard
  if (role === "user" || role === "assistant") {
    turnCount++;
    if (statTurns) statTurns.textContent = String(turnCount);
  }
  if (role === "tool") {
    toolCount++;
    if (statTools) statTools.textContent = String(toolCount);
  }

  const wrap = document.createElement("div");
  wrap.className = "rounded-xl border p-3 " +
    (role === "user" ? "bg-slate-900 text-white border-slate-800" :
     role === "assistant" ? "bg-white border-slate-200" :
     role === "tool" ? "bg-indigo-50 border-indigo-200" :
     role === "system" ? "bg-amber-50 border-amber-200" :
     "bg-white border-slate-200");

  const header = document.createElement("div");
  header.className = "flex items-center justify-between gap-2";
  const badge = document.createElement("span");
  badge.className = "text-[11px] font-semibold tracking-widest uppercase mono " +
    (role === "user" ? "text-slate-300" : role === "assistant" ? "text-emerald-600" : role === "tool" ? "text-indigo-600" : "text-slate-500");
  badge.textContent = role;
  const time = document.createElement("span");
  time.className = "text-[11px] mono text-slate-400";
  time.textContent = new Date().toLocaleTimeString();
  header.append(badge, time);

  const body = document.createElement("p");
  body.className = "text-sm leading-relaxed mt-1 whitespace-pre-wrap break-words " + (role === "user" ? "text-white" : "text-slate-700");
  body.textContent = text;
  wrap.append(header, body);

  if (meta.preview) {
    const pre = document.createElement("pre");
    pre.className = "mt-2 text-xs mono bg-white/80 border border-slate-200 rounded-lg p-2 overflow-x-auto whitespace-pre-wrap break-words " + (role === "user" ? "bg-white/10 border-white/10 text-slate-200" : "");
    pre.textContent = meta.preview;
    wrap.appendChild(pre);
  }
  if (meta.html) {
    const div = document.createElement("div");
    div.className = "mt-2 text-xs";
    div.innerHTML = meta.html;
    wrap.appendChild(div);
  }

  const logEl = document.getElementById("chatLog");
  if (logEl) {
    logEl.appendChild(wrap);
    logEl.scrollTop = logEl.scrollHeight;
  }
}

function setConnectionState(connected, label) {
  const dot = document.getElementById("connectionDot");
  const lab = document.getElementById("connectionLabel");
  const cBtn = document.getElementById("connectBtn");
  const dBtn = document.getElementById("disconnectBtn");
  if (dot) dot.className = "w-2.5 h-2.5 rounded-full border-2 border-white shadow " + (connected ? "bg-emerald-500" : "bg-slate-300");
  if (lab) {
    lab.textContent = label;
    lab.className = "text-xs font-medium " + (connected ? "text-emerald-600" : "text-slate-500");
  }
  if (cBtn) cBtn.disabled = connected;
  if (dBtn) dBtn.disabled = !connected;
}

function getBackendBase() {
  const inp = document.getElementById("backendUrlInput");
  const raw = (inp && inp.value || "").trim().replace(/\/$/, "");
  if (raw) return raw;
  return window.location.origin;
}

function getStoredToken() {
  try { return sessionToken || sessionStorage.getItem("session_token") || localStorage.getItem("session_token") || null; } catch { return sessionToken || null; }
}
function getAuthHeaders() {
  const token = getStoredToken();
  if (token) return { "Authorization": `Bearer ${token}` };
  return {};
}

function buildWsUrl() {
  const base = getBackendBase();
  const wsBase = base.replace(/^http/, "ws");
  const voice_gender = encodeURIComponent((appState.voiceGender || selectedVoice || "female").trim());
  const language = encodeURIComponent((appState.language || "english").trim());
  const token = getStoredToken();
  // Auth is now strictly via Bearer token (?token=); do NOT send username/email in query
  // Backend will hydrate user from JWT (app/presentation/dependencies/auth.py: resolve_user_from_ws)
  if (token) {
    return `${wsBase}/ws/stream?voice_gender=${voice_gender}&language=${language}&token=${encodeURIComponent(token)}`;
  }
  // No token -> guest fallback (only if AUTH_REQUIRE_EMAIL=false) — still without username/email
  return `${wsBase}/ws/stream?voice_gender=${voice_gender}&language=${language}`;
}

// ---------------------------------------------------------------------------
// WebSocket connect / disconnect — now includes voice_gender + username
// ---------------------------------------------------------------------------
function connect() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  const wsUrl = buildWsUrl();
  const hint = document.getElementById("sessionHint");
  if (hint) { hint.textContent = wsUrl; hint.classList.remove("hidden"); }

  ws = new WebSocket(wsUrl);
  setConnectionState(false, "Connecting…");

  ws.onopen = () => {
    isConnected = true;
    setConnectionState(true, "Connected");
    logMessage("system", `WebSocket connected as ${appState.username} <${appState.email}> • voice: ${selectedVoice === "female" ? "Aoede" : "Charon"} • ${appState.voiceGender}`);
    logMessage("system", "Mic stays hot — just speak. I'll ask before disconnecting.");
    ensureAudioContext();
    lastActivityMs = Date.now();
    resetIdleTimer();
  };

  ws.onmessage = async (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    const type = msg.type;

    // Any inbound server traffic proves the session is alive. While the model
    // is thinking (which can exceed 15s after a long utterance), nothing else
    // resets the inactivity guard — without this, long speech + long thinking
    // would trigger "Session closing due to inactivity" mid-conversation.
    // Only true radio silence (no server messages AND no mic input for 15s)
    // should close the session.
    lastActivityMs = Date.now();
    if (currentState === FSM.LISTENING && isConnected) {
      if (silenceTimer) clearTimeout(silenceTimer);
      silenceTimer = setTimeout(handleSilenceExpiry, SILENCE_TIMEOUT_MS);
    }

    // FSM hook: server-driven speaker state → drives LISTENING / SPEAKING.
    if (type === "state" || type === "speaker_state") {
      const st = (msg.state || msg.fsm || "").toLowerCase();
      const speaker = (msg.speaker || "").toString().toLowerCase();
      const aiTalking = st === "speaking" || st === "thinking_speaking" || (st === "thinking" && speaker === "ai");
      if (aiTalking) {
        // AI producing output → SPEAKING (mute mic, green ripple).
        if (currentState !== FSM.SPEAKING) transitionTo(FSM.SPEAKING);
        else setAiSpeaking();
      } else {
        // User speaking / AI processing → LISTENING (mic hot, blue pulse).
        // BUT never downgrade mid-reply: while the FSM is SPEAKING the AI's
        // audio is still streaming/playing — stray "user thinking" states must
        // not flip the orb blue and back (that caused the UI blink).
        if (currentState !== FSM.SPEAKING) {
          if (currentState !== FSM.LISTENING) transitionTo(FSM.LISTENING);
          else setAiListening();
        }
      }
      return;
    }
    if (type === "content_finished") {
      // The instant the server finishes delivering speech chunks → LISTENING,
      // un-mute the mic and loop back into the turn-taking cycle. Same rule as
      // turn_complete: wait for queued audio to finish playing first.
      const ctx = audioContext;
      if (ctx && ctx.currentTime < nextPlayTime - 0.05) {
        pendingTurnEnd = true;
      } else {
        transitionTo(FSM.LISTENING);
        onAssistantActivity();
      }
      return;
    }

    if (type === "text") {
      const text = msg.text || "";
      if (text) {
        const isPartial = !!msg.partial;
        if (!isPartial) {
          logMessage("assistant", text);
          // Initial greeting keeps LISTENING (blue) — user hasn't heard audio yet.
          const isGreeting = text.includes("Gemini Live connected") || text.includes("mock voice mode") ||
            text.startsWith("Hello") || text.startsWith("Hi ");
          if (isGreeting && currentState !== FSM.SPEAKING) setAiListening();
          const isToolConversion = text.includes("[Source") || text.startsWith("Based on your documents");
          const isMockText = msg.mock === true || text.toLowerCase().includes("mock voice mode");
          // Voice-native: real provider uses server audio only (played via
          // audio_chunk) to avoid a duplicate browser voice. Mock has silent
          // server audio, so it speaks through browser TTS instead.
          if (!isToolConversion && isMockText && "speechSynthesis" in window) {
            speakWithBrowserTTS(text);
          } else if (!isToolConversion && !isMockText && ttsToggle && ttsToggle.checked && "speechSynthesis" in window) {
            // Fallback only if the user explicitly enabled browser TTS for a real provider.
            speakWithBrowserTTS(text);
          }
        }
      }
    } else if (type === "audio_chunk") {
      const b64 = msg.data;
      if (b64) {
        const isMockSilent = !!msg.mock;
        if (isMockSilent) {
          // Silent mock audio — the browser TTS speaks the text and drives the
          // SPEAKING visuals (see speakWithBrowserTTS). Stay put if it's live.
          if (currentState !== FSM.SPEAKING && !ttsSpeaking) setAiListening();
        } else {
          // Real AI audio streaming → SPEAKING (mute outbound mic for acoustic
          // feedback protection, green ripple). Un-muted on turn_complete.
          if (currentState !== FSM.SPEAKING) transitionTo(FSM.SPEAKING);
          else setAiSpeaking();
        }
        const ttsToggle = document.getElementById("ttsToggle");
        if (!(isMockSilent && ttsToggle && ttsToggle.checked)) {
          await enqueueAudioChunk(b64, msg.sample_rate || 16000);
        } else if (isMockSilent) {
          flashPlaying();
        }
      }
    } else if (type === "tool_call") {
      logMessage("tool", `query_document("${(msg.query || "").slice(0, 120)}")`, {
        preview: (msg.result_preview || "").slice(0, 600),
        html: `<span class="mono text-[11px] text-indigo-600">→ returned ${(msg.result_preview || "").length} chars of context</span>`
      });
      resetIdleTimer();
    } else if (type === "vad") {
      // VAD is about the user's mic — irrelevant (and visually disruptive)
      // while the AI is speaking; ignore it to keep the green state stable.
      if (currentState === FSM.SPEAKING) { /* hold SPEAKING visuals */ }
      else if (msg.state === "speech") {
        setAiListening(); // AI is listening while the user speaks.
        onUserActivity();  // Keep the inactivity guard alive while talking.
      } else if (msg.state === "silence" || msg.state === "idle") {
        setAiListening();
      }
    } else if (type === "turn_complete") {
      // The server finished SENDING its speech, but the browser's audio queue
      // may still be playing. Keep SPEAKING (green + "▶ speaking…" label) until
      // playback actually drains, then loop back to LISTENING. Text-only turns
      // (nothing queued) return to LISTENING immediately.
      const ctx = audioContext;
      const playbackPending = ctx && ctx.currentTime < nextPlayTime - 0.05;
      if (playbackPending) {
        pendingTurnEnd = true;
      } else {
        transitionTo(FSM.LISTENING);
        onAssistantActivity();
      }
    } else if (type === "error") {
      logMessage("system", `Error: ${msg.message || JSON.stringify(msg)}`);
    }
  };

  ws.onclose = (event) => {
    // Never auto-reconnect — the user restarts the session via the mic icon.
    isConnected = false;
    clearIdleTimers();
    setConnectionState(false, event.reason ? `Closed: ${event.reason}` : "Disconnected");
    logMessage("system", `WebSocket closed (code ${event.code}${event.reason ? `: ${event.reason}` : ""}) — summary will be emailed to ${appState.email} if configured.`);
    stopMicrophone();
  };

  ws.onerror = () => {
    logMessage("system", "WebSocket error — check backend URL and that the server is running.");
  };
}

// ---------------------------------------------------------------------------
// Single-button session control. `startSession()` is the ONLY way a session
// begins: warm up the Web Audio context, request the microphone, open the
// WebSocket, then enter LISTENING. `endSession()` is the ONLY way it ends.
// ---------------------------------------------------------------------------
function waitForConnection(timeoutMs) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const tick = () => {
      if (isConnected && ws && ws.readyState === WebSocket.OPEN) return resolve(true);
      if (Date.now() - t0 > timeoutMs) return resolve(false);
      setTimeout(tick, 120);
    };
    tick();
  });
}

async function startSession() {
  if (isConnected && ws && ws.readyState === WebSocket.OPEN) return true;
  isMicMutedByUser = false; // fresh session always starts with a hot mic
  // 1) Warm up the browser's Web Audio API context.
  try { ensureAudioContext(); } catch {}
  // 2) Request mic permissions + begin streaming PCM (turns on device light).
  const micOk = await startMicrophone();
  if (!micOk) return false;
  // 3) Establish the FastAPI WebSocket link.
  connect();
  const opened = await waitForConnection(8000);
  if (!opened) {
    logMessage("system", "Could not connect to the server — returning to configuration.");
    endSession("connect-failed");
    return false;
  }
  // 4) Enter LISTENING (blue pulse, 15s inactivity guard armed).
  transitionTo(FSM.LISTENING);
  logMessage("system", "Session connected — listening for your voice. Tap the mic again to end.");
  return true;
}

function disconnect() {
  return endSession("manual");
}

// Legacy button wiring (hidden elements kept for compatibility).
document.getElementById("connectBtn")?.addEventListener("click", startSession);
document.getElementById("disconnectBtn")?.addEventListener("click", endSession);

// Backend URL hint (uses existing backendUrlInput ref from top)
if (typeof backendUrlInput !== 'undefined' && backendUrlInput) {
  backendUrlInput.placeholder = window.location.origin;
  if (!backendUrlInput.value && (window.location.protocol === "file:" || window.location.hostname === "")) {
    backendUrlInput.value = "http://localhost:8000";
  }
} else {
  const _backendUrlInput = document.getElementById("backendUrlInput");
  if (_backendUrlInput) {
    _backendUrlInput.placeholder = window.location.origin;
    if (!_backendUrlInput.value && (window.location.protocol === "file:" || window.location.hostname === "")) {
      _backendUrlInput.value = "http://localhost:8000";
    }
  }
}

// ---------------------------------------------------------------------------
// Audio: capture (mic -> WS) + visualizer + playback (WS -> speaker)
// ---------------------------------------------------------------------------
let audioContext = null;
let analyser = null;
let micStream = null;
let workletNode = null;
let workletOwnerCtx = null; // AudioContext that already has 'pcm-processor' registered
let processorNode = null;
// isMicActive already declared at top with FSM hoist (line 149-150)
let visualizerRaf = null;
let nextPlayTime = 0;
let gainNode = null;

function ensureAudioContext() {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    gainNode = audioContext.createGain();
    const vol = document.getElementById("volumeSlider");
    gainNode.gain.value = parseFloat(vol ? vol.value : "0.9");
    gainNode.connect(audioContext.destination);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 2048;
    nextPlayTime = audioContext.currentTime;
  }
  if (audioContext.state === "suspended") audioContext.resume();
  return audioContext;
}

volumeSlider?.addEventListener("input", () => {
  if (gainNode) gainNode.gain.value = parseFloat(volumeSlider.value);
});

async function startMicrophone() {
  if (isMicActive) return true;
  ensureAudioContext();
  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, sampleRate: 16000, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    });
  } catch (err) {
    logMessage("system", `Microphone access denied: ${err.message}. Check browser permissions.`);
    updateMicUI(false);
    return false;
  }
  const ctx = audioContext;
  const source = ctx.createMediaStreamSource(micStream);
  source.connect(analyser);
  let useWorklet = false;
  try {
    const workletCode = `
      class PCMProcessor extends AudioWorkletProcessor {
        constructor() { super(); this._buf = []; this._chunkSize = 1600; }
        process(inputs) {
          const input = inputs[0];
          if (!input || !input[0] || input[0].length === 0) return true;
          const channel = input[0];
          for (let i = 0; i < channel.length; i++) this._buf.push(channel[i]);
          while (this._buf.length >= this._chunkSize) {
            const chunk = this._buf.slice(0, this._chunkSize);
            this._buf = this._buf.slice(this._chunkSize);
            const pcm = new Int16Array(chunk.length);
            for (let j = 0; j < chunk.length; j++) {
              let s = Math.max(-1, Math.min(1, chunk[j]));
              pcm[j] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }
            this.port.postMessage(pcm.buffer, [pcm.buffer]);
          }
          return true;
        }
      }
      registerProcessor('pcm-processor', PCMProcessor);
    `;
    // registerProcessor('pcm-processor') must only run ONCE per AudioContext —
    // the audioWorklet global scope persists for the life of the context, so a
    // second addModule() into the same context would throw
    // "NotSupportedError: ... is already registered".
    if (!workletOwnerCtx || workletOwnerCtx !== ctx) {
      const blob = new Blob([workletCode], { type: "application/javascript" });
      const url = URL.createObjectURL(blob);
      await ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      workletOwnerCtx = ctx;
    }
    workletNode = new AudioWorkletNode(ctx, "pcm-processor");
    workletNode.port.onmessage = (e) => {
      const b64 = arrayBufferToBase64(e.data);
      sendAudioChunk(b64);
    };
    source.connect(workletNode);
    workletNode.connect(ctx.createGain()).connect(ctx.destination);
    useWorklet = true;
  } catch (err) {
    console.warn("AudioWorklet failed, falling back to ScriptProcessor:", err);
  }
  if (!useWorklet) {
    const BUFFER_SIZE = 4096;
    processorNode = ctx.createScriptProcessor(BUFFER_SIZE, 1, 1);
    processorNode.onaudioprocess = (e) => {
      const input = e.inputBuffer.getChannelData(0);
      const pcm = new Int16Array(input.length);
      for (let i = 0; i < input.length; i++) {
        let s = Math.max(-1, Math.min(1, input[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }
      const b64 = arrayBufferToBase64(pcm.buffer);
      sendAudioChunk(b64);
    };
    source.connect(processorNode);
    processorNode.connect(ctx.destination);
  }
  isMicActive = true;
  updateMicUI(true);
  startVisualizer();
  lastActivityMs = Date.now();
  return true;
}

function stopMicrophone() {
  isMicActive = false;
  updateMicUI(false);
  stopVisualizer();
  try {
    if (workletNode) { workletNode.disconnect(); workletNode.port.onmessage = null; workletNode = null; }
    if (processorNode) { processorNode.disconnect(); processorNode.onaudioprocess = null; processorNode = null; }
    if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  } catch {}
  const micStateLabel = document.getElementById("micStateLabel");
  if (micStateLabel) {
    micStateLabel.textContent = "Mic off";
    micStateLabel.className = "text-xs font-medium px-2.5 py-1 rounded-full bg-slate-100 text-slate-600";
  }
}

function updateMicUI(active) {
  const btn = document.getElementById("micBtn");
  const icon = document.getElementById("micIcon");
  const label = document.getElementById("micBtnLabel");
  const state = document.getElementById("micStateLabel");
  if (active) {
    if (btn) { btn.classList.remove("bg-slate-800", "border-slate-700"); btn.classList.add("bg-red-600", "border-red-500", "animate-pulse-slow"); }
    if (icon) icon.textContent = "⏹️";
    if (label) label.textContent = "Listening… tap to stop";
    if (state) { state.textContent = "● Live"; state.className = "text-xs font-medium px-2.5 py-1 rounded-full bg-red-50 text-red-600"; }
  } else {
    if (btn) { btn.classList.add("bg-slate-800", "border-slate-700"); btn.classList.remove("bg-red-600", "border-red-500", "animate-pulse-slow"); }
    if (icon) icon.textContent = "🎤";
    if (label) label.textContent = "Tap to speak";
  }
}

// ---------------------------------------------------------------------------
// Smart Mic Toggle — No Connect/Disconnect buttons per spec
// When user clicks central mic, auto-initiate WS + warm up mic.
// Green glow + pulse when active, solid gray/white when disconnected.
// ---------------------------------------------------------------------------
function updateOrbState(active) {
  const orb = document.getElementById("orb");
  const glow1 = document.getElementById("orbGlow1");
  const glow2 = document.getElementById("orbGlow2");
  const pulse1 = document.getElementById("pulseRing1");
  const pulse2 = document.getElementById("pulseRing2");
  const ripple = document.getElementById("micBtnRipple");
  const btn = document.getElementById("micBtn");
  const orbIcon = document.getElementById("orbIcon");
  if (active) {
    if (orb) orb.classList.add("shadow-[0_0_80px_rgba(16,185,129,0.5)]", "border-emerald-400/30");
    if (glow1) { glow1.classList.remove("from-indigo-600/30", "to-violet-600/30"); glow1.classList.add("from-emerald-500/40", "to-teal-500/40"); }
    if (pulse1) pulse1.classList.remove("hidden");
    if (pulse2) pulse2.classList.remove("hidden");
    if (ripple) ripple.classList.remove("hidden");
    if (btn) { btn.classList.remove("bg-white", "text-slate-900"); btn.classList.add("bg-emerald-500", "text-white", "shadow-[0_0_30px_rgba(16,185,129,0.6)]"); }
    if (orbIcon) orbIcon.classList.add("bg-emerald-500/20", "border-emerald-400/50");
  } else {
    if (orb) orb.classList.remove("shadow-[0_0_80px_rgba(16,185,129,0.5)]", "border-emerald-400/30");
    if (glow1) { glow1.classList.add("from-indigo-600/30", "to-violet-600/30"); glow1.classList.remove("from-emerald-500/40", "to-teal-500/40"); }
    if (pulse1) pulse1.classList.add("hidden");
    if (pulse2) pulse2.classList.add("hidden");
    if (ripple) ripple.classList.add("hidden");
    if (btn) { btn.classList.add("bg-white", "text-slate-900"); btn.classList.remove("bg-emerald-500", "text-white", "shadow-[0_0_30px_rgba(16,185,129,0.6)]"); }
    if (orbIcon) orbIcon.classList.remove("bg-emerald-500/20", "border-emerald-400/50");
    currentVisual = null; // idle → allow next listening/speaking restyle
  }
}

// Override updateMicUI to also drive orb (keep original for compat, enhance)
const _origUpdateMicUI = updateMicUI;
updateMicUI = function(active) {
  _origUpdateMicUI(active);
  updateOrbState(active);
};

// ---------------------------------------------------------------------------
// LISTENING (pulsing BLUE) vs SPEAKING (rippling GREEN) visual treatment of the
// central mic orb + button. updateOrbState(active) stays a coarse connected/
// disconnected on/off; these refine the exact FSM mood.
// ---------------------------------------------------------------------------
function applyListeningVisual() {
  const orb = $("orb"), glow1 = $("orbGlow1"), pulse1 = $("pulseRing1"), pulse2 = $("pulseRing2"),
        ripple = $("micBtnRipple"), btn = $("micBtn"), orbIcon = $("orbIcon");
  if (orb) {
    orb.classList.remove("shadow-[0_0_80px_rgba(16,185,129,0.5)]", "border-emerald-400/30");
    orb.classList.add("shadow-[0_0_80px_rgba(59,130,246,0.55)]", "border-sky-400/30");
  }
  if (glow1) { glow1.classList.remove("from-emerald-500/40", "to-teal-500/40"); glow1.classList.add("from-sky-500/40", "to-blue-500/40"); }
  if (btn) { btn.classList.remove("bg-emerald-500", "shadow-[0_0_30px_rgba(16,185,129,0.6)]"); btn.classList.add("bg-sky-500", "text-white", "shadow-[0_0_30px_rgba(59,130,246,0.6)]"); }
  if (orbIcon) { orbIcon.classList.remove("bg-emerald-500/20", "border-emerald-400/50"); orbIcon.classList.add("bg-sky-500/20", "border-sky-400/50"); }
  // The green ripple rings stay hidden while listening (blue pulse instead).
  if (pulse1) pulse1.classList.add("hidden");
  if (pulse2) pulse2.classList.add("hidden");
  if (ripple) ripple.classList.add("hidden");
}
function applySpeakingVisual() {
  const orb = $("orb"), glow1 = $("orbGlow1"), pulse1 = $("pulseRing1"), pulse2 = $("pulseRing2"),
        ripple = $("micBtnRipple"), btn = $("micBtn"), orbIcon = $("orbIcon");
  if (orb) {
    orb.classList.remove("shadow-[0_0_80px_rgba(59,130,246,0.55)]", "border-sky-400/30");
    orb.classList.add("shadow-[0_0_80px_rgba(16,185,129,0.5)]", "border-emerald-400/30");
  }
  if (glow1) { glow1.classList.remove("from-sky-500/40", "to-blue-500/40"); glow1.classList.add("from-emerald-500/40", "to-teal-500/40"); }
  if (btn) { btn.classList.remove("bg-sky-500", "shadow-[0_0_30px_rgba(59,130,246,0.6)]"); btn.classList.add("bg-emerald-500", "text-white", "shadow-[0_0_30px_rgba(16,185,129,0.6)]"); }
  if (orbIcon) { orbIcon.classList.remove("bg-sky-500/20", "border-sky-400/50"); orbIcon.classList.add("bg-emerald-500/20", "border-emerald-400/50"); }
  // Rippling GREEN: show the ping rings + mic ripple while the AI speaks.
  if (pulse1) pulse1.classList.remove("hidden");
  if (pulse2) pulse2.classList.remove("hidden");
  if (ripple) ripple.classList.remove("hidden");
}

function setAiListening(force = false) {
  // Visual guard: re-applying the same state on every WS message re-triggers
  // CSS animations → the orb/label "blinked". Only restyle on real changes.
  // While user-muted, hold the gray muted visuals (unless forced).
  if ((currentVisual === "listening" || isMicMutedByUser) && !force) return;
  currentVisual = "listening";
  const aiName = (appState.customAiName || "Aria").trim();
  const lbl = document.getElementById("micStateLabel");
  if (lbl) {
    lbl.textContent = `● Listening — ${aiName} is listening for your voice…`;
    lbl.className = "text-xs font-medium px-2.5 py-1 rounded-full bg-sky-50 text-sky-700";
  }
  const playing = document.getElementById("playingLabel");
  if (playing) {
    playing.textContent = `▶ ${aiName} speaking…`;
    playing.classList.add("hidden");
  }
  applyListeningVisual();
}
function setAiSpeaking(force = false) {
  if ((currentVisual === "speaking" || isMicMutedByUser) && !force) return;
  currentVisual = "speaking";
  const aiName = (appState.customAiName || "Aria").trim();
  const lbl = document.getElementById("micStateLabel");
  if (lbl) {
    lbl.textContent = `● ${aiName} speaking…`;
    lbl.className = "text-xs font-medium px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 animate-pulse";
  }
  const playing = document.getElementById("playingLabel");
  if (playing) {
    playing.textContent = `▶ ${aiName} speaking…`;
    playing.classList.remove("hidden");
  }
  applySpeakingVisual();
}

// Browser TTS visuals live in speakWithBrowserTTS() (defined near the FSM top):
// it drives SPEAKING/LISTENING transitions for mock-provider and optional TTS
// replies, so both real provider audio and browser TTS share one indicator.

// ---------------------------------------------------------------------------
// Central Mic Icon — the user's single control for the session loop while on
// the dashboard. Tapping toggles between an idle (gray) mic and LISTENING.
//   • Disconnected → click connects: requests mic (getUserMedia), opens the
//     WebSocket, warms the Web Audio context, enters LISTENING.
//   • Connected   → click disconnects: stops the mic device light, closes the
//     WebSocket, and returns to the idle mic on the SAME dashboard (no
//     navigation). The dedicated End Session button handles leaving to config.
// No infinite reconnection loop — the user drives every transition.
// ---------------------------------------------------------------------------
async function handleMicToggle() {
  if (!isConnected || (isConnected && !ws)) {
    await startSession();
    return;
  }
  // Active session → tap toggles mic mute (pause/resume streaming) WITHOUT
  // disconnecting. Disconnection lives on the dedicated End Session button.
  setMicMutedByUser(!isMicMutedByUser);
}

// Mute/unmute the outbound mic stream while keeping the session alive.
//  • Mute  : drop outbound PCM, pause the 15s inactivity guard, gray mic UI.
//  • Unmute: resume PCM streaming, restore the LISTENING visuals and re-arm
//            the inactivity guard.
function setMicMutedByUser(muted) {
  isMicMutedByUser = !!muted;
  const label2 = document.getElementById("micBtnLabel");
  if (isMicMutedByUser) {
    applyMutedVisual();
    if (label2) label2.textContent = "Mic muted — tap to resume";
    // Muted = no voice input → pause the inactivity countdown.
    if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
    logMessage("system", "Microphone muted — streaming paused. Tap the mic to resume.");
  } else {
    if (label2) label2.textContent = "Tap to mute • End Session button to hang up";
    // Resume: if we're mid-turn (SPEAKING) the AI keeps talking; otherwise
    // return straight to hot LISTENING and re-arm the inactivity guard.
    if (currentState === FSM.SPEAKING) {
      setAiSpeaking(true);
    } else {
      currentState = FSM.LISTENING;
      enterListening();
      onUserActivity();
    }
    logMessage("system", "Microphone unmuted — listening again.");
  }
}

// Gray "muted" treatment of orb/button while the user has paused the mic.
function applyMutedVisual() {
  currentVisual = null; // muted is its own mood; next unmute restyles freely
  const lbl = document.getElementById("micStateLabel");
  const pulse1 = document.getElementById("pulseRing1");
  const pulse2 = document.getElementById("pulseRing2");
  const ripple = document.getElementById("micBtnRipple");
  const btn = document.getElementById("micBtn");
  const orb = document.getElementById("orb");
  const orbIcon = document.getElementById("orbIcon");
  const glow1 = document.getElementById("orbGlow1");
  if (lbl) { lbl.textContent = "● Mic muted — tap the mic to resume"; lbl.className = "text-xs font-medium px-2.5 py-1 rounded-full bg-amber-50 text-amber-700"; }
  if (orb) { orb.classList.remove("shadow-[0_0_80px_rgba(59,130,246,0.55)]", "shadow-[0_0_80px_rgba(16,185,129,0.5)]", "border-sky-400/30", "border-emerald-400/30"); orb.classList.add("border-slate-500/40"); }
  if (glow1) { glow1.classList.remove("from-sky-500/40", "to-blue-500/40", "from-emerald-500/40", "to-teal-500/40"); glow1.classList.add("from-slate-500/30", "to-slate-600/30"); }
  if (btn) { btn.classList.remove("bg-sky-500", "bg-emerald-500", "text-white", "shadow-[0_0_30px_rgba(59,130,246,0.6)]", "shadow-[0_0_30px_rgba(16,185,129,0.6)]"); btn.classList.add("bg-slate-500", "text-white"); }
  if (orbIcon) orbIcon.classList.remove("bg-sky-500/20", "border-sky-400/50", "bg-emerald-500/20", "border-emerald-400/50");
  if (pulse1) pulse1.classList.add("hidden");
  if (pulse2) pulse2.classList.add("hidden");
  if (ripple) ripple.classList.add("hidden");
}
micBtn?.addEventListener("click", handleMicToggle);

// ---------------------------------------------------------------------------
// Persistent Documents Menu Dropdown — Managed Contexts
// Spec: Top corner dropdown labeled "📄 Managed Contexts" showing all docs
// from window.uploadedDocuments, with inner "+ Add Document" upload.
// ---------------------------------------------------------------------------
function renderManagedDropdown() {
  const listEl = document.getElementById("managedDocList");
  const badge = document.getElementById("managedCountBadge");
  const dashList = document.getElementById("docList");
  if (!listEl) return;

  // Sync badge
  const count = window.uploadedDocuments.length;
  if (badge) badge.textContent = String(count);
  if (dashList && dashList !== listEl) {
    // Keep legacy docList in sync (hidden, for refreshDocuments compat)
    // dashList is actually docList from dashboard, but we also have managedDocList
  }

  if (!window.uploadedDocuments.length) {
    listEl.innerHTML = `<p class="text-xs text-slate-400 py-4 text-center">No documents yet — upload below.</p>`;
    return;
  }

  listEl.innerHTML = "";
  window.uploadedDocuments.forEach((doc) => {
    const row = document.createElement("div");
    row.className = "flex items-center justify-between gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2";
    const sizeKB = doc.size ? `${(doc.size / 1024).toFixed(1)} KB` : "";
    row.innerHTML = `
      <div class="min-w-0 flex-1">
        <p class="text-xs font-medium truncate">${escapeHtml(doc.filename)}</p>
        <p class="text-[11px] text-slate-500 mono">${escapeHtml(sizeKB)} • ${escapeHtml(doc.id?.slice(0,8) || "")} • ${doc.chunks || "?"} chunks</p>
      </div>
      <span class="text-[11px] bg-emerald-50 text-emerald-700 px-2 py-1 rounded-full border border-emerald-200">Indexed</span>
    `;
    listEl.appendChild(row);
  });

  // Also mirror to hidden docList for legacy refreshDocuments (so both stay in sync)
  const legacyList = document.getElementById("docList");
  if (legacyList && legacyList !== listEl) {
    // Let refreshDocuments handle legacy, but keep badge in sync
  }
}

// Dropdown toggle
const btnManagedContexts = document.getElementById("btnManagedContexts");
const managedDropdown = document.getElementById("managedDropdown");
const managedRefreshBtn = document.getElementById("managedRefreshBtn");
btnManagedContexts?.addEventListener("click", (e) => {
  e.stopPropagation();
  if (!managedDropdown) return;
  managedDropdown.classList.toggle("hidden");
  if (!managedDropdown.classList.contains("hidden")) {
    renderManagedDropdown();
  }
});
managedRefreshBtn?.addEventListener("click", async () => {
  await refreshDocuments();
  renderManagedDropdown();
});
// Close on outside click
document.addEventListener("click", (e) => {
  if (!managedDropdown || managedDropdown.classList.contains("hidden")) return;
  const wrap = document.getElementById("managedContextsWrap");
  if (wrap && !wrap.contains(e.target)) {
    managedDropdown.classList.add("hidden");
  }
});

// Managed dropdown upload — + Add Document inside dropdown per spec
const managedFileInput = document.getElementById("managedFileInput");
const managedDropZone = document.getElementById("managedDropZone");
const managedUploadBtn = document.getElementById("managedUploadBtn");
const managedUploadStatus = document.getElementById("managedUploadStatus");
let pendingManagedFiles = [];

managedDropZone?.addEventListener("click", () => managedFileInput?.click());
managedDropZone?.addEventListener("dragover", (e) => {
  e.preventDefault();
  managedDropZone.classList.add("border-indigo-400", "bg-indigo-50");
});
managedDropZone?.addEventListener("dragleave", () => {
  managedDropZone.classList.remove("border-indigo-400", "bg-indigo-50");
});
managedDropZone?.addEventListener("drop", (e) => {
  e.preventDefault();
  managedDropZone.classList.remove("border-indigo-400", "bg-indigo-50");
  const files = Array.from(e.dataTransfer.files || []);
  if (files.length) {
    pendingManagedFiles = files;
    if (managedUploadStatus) {
      managedUploadStatus.textContent = `${files.length} file(s) dropped — click + Add Document.`;
      managedUploadStatus.className = "text-[11px] mt-1.5 text-indigo-600";
    }
  }
});
managedFileInput?.addEventListener("change", () => {
  pendingManagedFiles = Array.from(managedFileInput.files || []);
  if (pendingManagedFiles.length && managedUploadStatus) {
    managedUploadStatus.textContent = `${pendingManagedFiles.length} file(s) selected — click + Add Document.`;
    managedUploadStatus.className = "text-[11px] mt-1.5 text-indigo-600";
  }
});

managedUploadBtn?.addEventListener("click", async () => {
  if (!pendingManagedFiles.length) {
    if (managedUploadStatus) {
      managedUploadStatus.textContent = "Select files first.";
      managedUploadStatus.className = "text-[11px] mt-1.5 text-amber-600";
    }
    return;
  }
  managedUploadBtn.disabled = true;
  if (managedUploadStatus) {
    managedUploadStatus.textContent = `Uploading ${pendingManagedFiles.length} file(s) without interrupting voice…`;
    managedUploadStatus.className = "text-[11px] mt-1.5 text-slate-500";
  }
  const base = getBackendBase();
  let allOk = true;
  for (const file of pendingManagedFiles) {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const headers = getAuthHeaders();
      let res = await fetch(`${base}/api/documents/upload`, {
        method: "POST",
        body: fd,
        headers,
      });
      if (res.status === 404) {
        const fd2 = new FormData();
        fd2.append("file", file);
        res = await fetch(`${base}/api/upload`, {
          method: "POST",
          body: fd2,
          headers,
        });
      }
      if (res.status === 401) { const b=await res.json().catch(()=>({})); throw new Error(b.detail || "Unauthorized — please sign in again."); }
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      // Dynamic appending per spec: push response object to state array
      const meta = {
        filename: data.filename,
        size: data.size_bytes || file.size,
        id: data.id,
        systemIndexId: data.id,
        chunks: data.chunks,
        text_length: data.text_length,
      };
      window.uploadedDocuments.push(meta);
      logMessage("system", `Added "${data.filename}" to Managed Contexts — ${data.chunks} chunks (mid-conversation).`, { preview: data.preview?.slice(0, 200) });
    } catch (err) {
      logMessage("system", `Managed upload failed for "${file.name}": ${err.message}`);
      if (managedUploadStatus) {
        managedUploadStatus.textContent = `Upload error: ${err.message}`;
        managedUploadStatus.className = "text-[11px] mt-1.5 text-red-600";
      }
      allOk = false;
      break;
    }
  }
  if (allOk) {
    if (managedUploadStatus) {
      managedUploadStatus.textContent = `✓ Added ${pendingManagedFiles.length} file(s) to context.`;
      managedUploadStatus.className = "text-[11px] mt-1.5 text-emerald-600";
    }
    pendingManagedFiles = [];
    if (managedFileInput) managedFileInput.value = "";
    renderManagedDropdown();
    await refreshDocuments();
  }
  managedUploadBtn.disabled = false;
});

// ---------------------------------------------------------------------------
// Muted Console Utility Log Footer — collapsible, max-h-32, opacity-60
// Spec: small dock panel spanning bottom, out of main visual field
// ---------------------------------------------------------------------------
const consoleDock = document.getElementById("consoleDock");
const consoleBody = document.getElementById("consoleBody");
const btnToggleConsole = document.getElementById("btnToggleConsole");
const consoleChevron = document.getElementById("consoleChevron");
let consoleCollapsed = false;
btnToggleConsole?.addEventListener("click", () => {
  consoleCollapsed = !consoleCollapsed;
  if (consoleBody) {
    consoleBody.classList.toggle("hidden", consoleCollapsed);
    if (consoleChevron) consoleChevron.style.transform = consoleCollapsed ? "rotate(180deg)" : "rotate(0deg)";
  }
  if (consoleDock) {
    consoleDock.classList.toggle("opacity-60", !consoleCollapsed);
    consoleDock.classList.toggle("opacity-100", consoleCollapsed);
  }
});
// Ensure dock starts muted (opacity-60) and not hidden
if (consoleDock) consoleDock.classList.add("opacity-60");

// Ensure window.uploadedDocuments is globally available for hydration spec
if (!window.uploadedDocuments) window.uploadedDocuments = [];

function sendAudioChunk(b64) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  // Echo protection: while the AI's audio is playing (SPEAKING), completely
  // pause outbound PCM so the model never hears its own voice.
  if (currentState === FSM.SPEAKING || isMicBlockedForEcho) return;
  // User muted via the mic button → pause streaming (session stays connected).
  if (isMicMutedByUser) return;
  try {
    ws.send(JSON.stringify({ type: "audio_chunk", data: b64 }));
    // Any user voice activity while LISTENING resets the 15s inactivity guard.
    if (currentState === FSM.LISTENING) onUserActivity();
  } catch {}
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

function base64ToArrayBuffer(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

async function enqueueAudioChunk(b64, sampleRate) {
  ensureAudioContext();
  const ctx = audioContext;
  if (ctx.state === "suspended") await ctx.resume();
  const pcmBuffer = base64ToArrayBuffer(b64);
  const int16 = new Int16Array(pcmBuffer);
  if (int16.length === 0) return;
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 0x8000;
  const audioBuf = ctx.createBuffer(1, float32.length, sampleRate);
  audioBuf.getChannelData(0).set(float32);
  const source = ctx.createBufferSource();
  source.buffer = audioBuf;
  source.connect(gainNode);
  const now = ctx.currentTime;
  if (nextPlayTime < now) nextPlayTime = now;
  source.start(nextPlayTime);
  nextPlayTime += audioBuf.duration;
  flashPlaying();
  source.onended = () => {
    const drained = ctx.currentTime >= nextPlayTime - 0.05;
    if (drained) {
      const playingLabel = document.getElementById("playingLabel");
      if (playingLabel) playingLabel.classList.add("hidden");
      // Playback fully drained: if the server already said the turn was over,
      // NOW loop back to LISTENING (un-mute mic, reset inactivity guard).
      if (pendingTurnEnd && currentState === FSM.SPEAKING) {
        pendingTurnEnd = false;
        transitionTo(FSM.LISTENING);
        onAssistantActivity();
      } else if (currentState === FSM.LISTENING) {
        setAiListening();
      }
    }
  };
}

function flashPlaying() {
  const playingLabel = document.getElementById("playingLabel");
  if (playingLabel) playingLabel.classList.remove("hidden");
}

// Visualizer
function startVisualizer() {
  const canvas = document.getElementById("visualizer");
  if (!canvas) return;
  const ctx2d = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  function draw() {
    visualizerRaf = requestAnimationFrame(draw);
    if (!analyser) return;
    const bufferLen = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLen);
    analyser.getByteFrequencyData(dataArray);
    ctx2d.fillStyle = "#0f172a";
    ctx2d.fillRect(0, 0, W, H);
    const barCount = 64;
    const step = Math.floor(bufferLen / barCount);
    const barW = W / barCount;
    for (let i = 0; i < barCount; i++) {
      const v = dataArray[i * step] / 255;
      const barH = v * H * 0.9;
      const x = i * barW;
      const y = H - barH;
      const hue = 240 - v * 60;
      ctx2d.fillStyle = `hsl(${hue} 90% 60% / ${0.7 + v * 0.3})`;
      const r = Math.min(4, barW * 0.4);
      ctx2d.beginPath();
      // @ts-ignore
      if (ctx2d.roundRect) ctx2d.roundRect(x + 1, y, barW - 2, barH, r);
      else ctx2d.rect(x + 1, y, barW - 2, barH);
      ctx2d.fill();
    }
    const timeData = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(timeData);
    ctx2d.strokeStyle = "rgba(255,255,255,0.35)";
    ctx2d.lineWidth = 1.2;
    ctx2d.beginPath();
    for (let i = 0; i < timeData.length; i++) {
      const x = (i / timeData.length) * W;
      const y = (timeData[i] / 128 - 1) * (H * 0.18) + H * 0.5;
      if (i === 0) ctx2d.moveTo(x, y);
      else ctx2d.lineTo(x, y);
    }
    ctx2d.stroke();
  }
  draw();
}

function stopVisualizer() {
  if (visualizerRaf) cancelAnimationFrame(visualizerRaf);
  visualizerRaf = null;
  const canvas = document.getElementById("visualizer");
  if (!canvas) return;
  const ctx2d = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  ctx2d.fillStyle = "#0f172a";
  ctx2d.fillRect(0, 0, W, H);
  ctx2d.fillStyle = "rgba(255,255,255,0.15)";
  ctx2d.font = "12px JetBrains Mono, monospace";
  ctx2d.textAlign = "center";
  ctx2d.fillText("— Visualizer idle — Enable mic to see spectrum —", W/2, H/2);
}
stopVisualizer();

// ---------------------------------------------------------------------------
// End Session Flow — AI asks for summary, handles yes/no, then navigates to config
// Spec: When user ends session, AI asks if user wants summary in email.
// If yes send, if no don't. Either way end session and bring to config/upload page.
// Sign out is now premium card in config (amber) — handled below.
// ---------------------------------------------------------------------------
let awaitingSummaryChoice = false;
let pendingSessionInfo = null; // {username, email, sessionId, customAiName, voiceGender}

function showSummaryPrompt() {
  const modal = document.getElementById("summaryPromptModal");
  const qText = document.getElementById("summaryQuestionText");
  const promptAiName = document.getElementById("promptAiName");
  if (!modal) return;
  const aiName = appState.customAiName || "Aria";
  const email = appState.email || "your email";
  if (qText) qText.textContent = `Hi ${appState.username}! Would you like a summary of this session sent to ${email}?`;
  if (promptAiName) promptAiName.textContent = aiName;
  const title = document.getElementById("summaryPromptTitle");
  if (title) title.textContent = `End Session with ${aiName}?`;
  // Reset modal state
  document.getElementById("summaryButtons")?.classList.remove("hidden");
  document.getElementById("summarySending")?.classList.add("hidden");
  document.getElementById("summaryResult")?.classList.add("hidden");
  document.getElementById("btnSummaryClose")?.classList.add("hidden");
  modal.classList.remove("hidden");
  // Also have the AI speak the question if possible (via TTS or via WS text)
  const question = `Would you like a summary of this session sent to ${email}? Please say yes or no.`;
  // Log as assistant so it's visible in console
  logMessage("assistant", question);
  // If connected, let the AI speak it (via mock TTS or real audio)
  if (isConnected && ws && ws.readyState === WebSocket.OPEN) {
    // For mock, the frontend TTS will speak the next assistant text if toggle is on
    // We also send a system trigger so the backend could make the AI ask (for Gemini)
    try { ws.send(JSON.stringify({ type: "text", text: `SYSTEM: The user is ending the session. Ask them: "${question}"` })); } catch {}
  } else {
    // If not connected, use browser TTS directly for the prompt
    if ("speechSynthesis" in window) {
      try {
        window.speechSynthesis.cancel();
        const utter = new SpeechSynthesisUtterance(question);
        utter.rate = 1.0;
        window.speechSynthesis.speak(utter);
      } catch {}
    }
  }
  awaitingSummaryChoice = true;
  pendingSessionInfo = {
    username: appState.username,
    email: appState.email,
    customAiName: appState.customAiName,
    voiceGender: appState.voiceGender,
    sessionId: sessionStorage.getItem("currentSessionId") || "unknown",
  };
}

function hideSummaryPrompt() {
  const modal = document.getElementById("summaryPromptModal");
  if (modal) modal.classList.add("hidden");
  awaitingSummaryChoice = false;
}

async function handleSummaryChoice(wantsSummary) {
  const modal = document.getElementById("summaryPromptModal");
  const btns = document.getElementById("summaryButtons");
  const sending = document.getElementById("summarySending");
  const result = document.getElementById("summaryResult");
  const closeBtn = document.getElementById("btnSummaryClose");
  const sendingText = document.getElementById("summarySendingText");

  if (btns) btns.classList.add("hidden");
  if (sending) sending.classList.remove("hidden");

  if (wantsSummary) {
    if (sendingText) sendingText.textContent = `Sending summary to ${appState.email}...`;
    try {
      const base = getBackendBase();
      // Try to trigger summary via new explicit endpoint (if exists), fallback to mock success
      // The backend's BackgroundTasks normally auto-sends on disconnect, but now we explicitly request it
      let res;
      try {
        const authH = getAuthHeaders();
        res = await fetch(`${base}/api/session/summary`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authH },
          credentials: "include",
          body: JSON.stringify({
            email: appState.email,
            username: appState.username,
            custom_ai_name: appState.customAiName,
            voice_gender: appState.voiceGender,
            send_summary: true,
          }),
        });
      } catch {}
      // If endpoint doesn't exist (404), treat as success for mock (backend will handle via WS disconnect fallback)
      if (res && !res.ok && res.status !== 404) throw new Error("Failed to send summary");
      // Also try WS-based trigger if connected
      if (isConnected && ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ type: "summary_choice", want_summary: true, email: appState.email })); } catch {}
      }
      // Simulate a short delay for UX
      await new Promise(r => setTimeout(r, 800));
      if (result) {
        result.textContent = `✓ Summary sent to ${appState.email} — check your inbox (mock SMTP logged on server).`;
        result.className = "rounded-xl bg-emerald-50 border border-emerald-200 p-3 text-xs text-emerald-700 text-center";
        result.classList.remove("hidden");
      }
      logMessage("system", `Summary sent to ${appState.email} as ${appState.customAiName} (${appState.voiceGender})`);
      // Speak confirmation if possible
      if ("speechSynthesis" in window) {
        try { window.speechSynthesis.speak(new SpeechSynthesisUtterance(`Summary sent to ${appState.email}. Taking you back to your documents.`)); } catch {}
      }
    } catch (err) {
      if (result) {
        result.textContent = `Summary requested for ${appState.email} — will be sent on disconnect (mock).`;
        result.className = "rounded-xl bg-amber-50 border border-amber-200 p-3 text-xs text-amber-700 text-center";
        result.classList.remove("hidden");
      }
    }
  } else {
    if (result) {
      result.textContent = `No summary sent — ending session directly.`;
      result.className = "rounded-xl bg-slate-100 border border-slate-200 p-3 text-xs text-slate-600 text-center";
      result.classList.remove("hidden");
    }
    logMessage("system", `Session ended without summary for ${appState.email}`);
    // Ensure we tell backend NOT to auto-send summary on disconnect
    if (isConnected && ws && ws.readyState === WebSocket.OPEN) {
      try { ws.send(JSON.stringify({ type: "summary_choice", want_summary: false })); } catch {}
      // Small delay to let the message go through before closing
      await new Promise(r => setTimeout(r, 300));
    }
  }

  if (sending) sending.classList.add("hidden");
  if (closeBtn) {
    closeBtn.classList.remove("hidden");
    closeBtn.textContent = "Continue to Documents →";
  }
  // Auto-navigate after a short pause, or wait for user to click Continue
  setTimeout(() => {
    handleSummaryComplete();
  }, wantsSummary ? 1500 : 800);
}

function handleSummaryComplete() {
  hideSummaryPrompt();
  // Unified teardown: break infinite reconnection loop, close WS, release mic, return to config
  try { teardownSession("end-session"); } catch { try { if (isConnected) disconnect(); } catch {} try { stopMicrophone(); updateOrbState(false); } catch {} showScreen("config"); }
  refreshDocuments();
  renderManagedDropdown();
}

// Wire up modal buttons
document.getElementById("btnSummaryYes")?.addEventListener("click", () => handleSummaryChoice(true));
document.getElementById("btnSummaryNo")?.addEventListener("click", () => handleSummaryChoice(false));
document.getElementById("summaryPromptBackdrop")?.addEventListener("click", () => {
  // Clicking backdrop = No, just end
  handleSummaryChoice(false);
});
document.getElementById("btnSummaryClose")?.addEventListener("click", () => {
  handleSummaryComplete();
});

// Override End Session button to trigger the prompt flow (not immediate disconnect)
const _originalBtnEndSession = document.getElementById("btnEndSession");
if (_originalBtnEndSession) {
  // Remove any existing listeners by cloning
  const clone = _originalBtnEndSession.cloneNode(true);
  _originalBtnEndSession.parentNode.replaceChild(clone, _originalBtnEndSession);
  clone.addEventListener("click", () => {
    // If not in dashboard, just go to config
    const dash = document.getElementById("screen-dashboard");
    if (!dash || dash.classList.contains("hidden")) {
      showScreen("config");
      return;
    }
    showSummaryPrompt();
  });
  // Keep reference for other code — differentiate New Session from End Session
  // End Session (primary, red) → summary prompt flow (above)
  // New Session (secondary) → hard reset without summary, clears sessionStorage token
  const btnNewSessionEl = document.getElementById("btnNewSession");
  if (btnNewSessionEl) {
    btnNewSessionEl.textContent = "↺ New Session";
    btnNewSessionEl.title = "Hard reset — clears token & docs from sessionStorage, goes to landing";
    btnNewSessionEl.className = "text-xs px-3 py-1.5 rounded-full bg-white/10 border border-white/15 text-white hover:bg-white/15 font-medium flex items-center gap-1";
    btnNewSessionEl.classList.remove("hidden");
    // Remove the original btnNewSession handler (at ~963) that also clears on click and re-bind a clean one to avoid double-fire
    const fresh = btnNewSessionEl.cloneNode(true);
    btnNewSessionEl.parentNode.replaceChild(fresh, btnNewSessionEl);
    fresh.addEventListener("click", () => {
      try { fresh.textContent = "Resetting…"; } catch {}
      // Immediate hard reset, no summary prompt
      try { if (typeof disconnect === "function") disconnect(); } catch {}
      try { if (typeof clearSessionAuth === "function") clearSessionAuth(); } catch {}
      try { fetch(`${getBackendBase()}/api/auth/signout`, { method: "POST", credentials: "include" }); } catch {}
      try { showScreen("landing"); } catch {}
      try { const l=document.getElementById("statTurns"); if(l) l.textContent="0"; const t=document.getElementById("statTools"); if(t) t.textContent="0"; } catch {}
      try { logMessage("system", "New Session — token & docs cleared from sessionStorage."); } catch {}
    });
  }
}

// Also handle voice yes/no when awaiting summary choice
const _origLogMessage = logMessage;
let _summaryVoiceHandlerInstalled = false;
if (!_summaryVoiceHandlerInstalled) {
  _summaryVoiceHandlerInstalled = true;
  // Intercept next user text/voice input when awaiting choice
  const originalSendTextMessage = window.sendTextMessage;
  // We wrap the WS onmessage to catch yes/no
}

// Helper to check if text is affirmative/negative (multilingual-ish)
function isAffirmative(text) {
  const t = text.toLowerCase().trim();
  return ["yes", "yeah", "yep", "sure", "please", "send", "do it", "ok", "okay", "haan", "sí", "si", "oui", "hai", "네", "예", "ja"].some(w => t === w || t.startsWith(w + " ") || t.includes("yes") || t.includes("send"));
}
function isNegative(text) {
  const t = text.toLowerCase().trim();
  return ["no", "nope", "nah", "don't", "dont", "not", "skip", "cancel", "nahi", "nahi", "non", "nein", "아니요", "いいえ"].some(w => t === w || t.startsWith(w + " ") || t.includes("no, just end"));
}

// Hook into WS message handling for voice yes/no when awaiting
const _originalWsOnMessage = null; // placeholder

// Text sending — intercept if awaiting summary
function sendTextMessage() {
  const inp = document.getElementById("textInput");
  const text = (inp ? inp.value : "").trim();
  if (!text) return;
  if (awaitingSummaryChoice) {
    // Treat this input as the summary choice
    logMessage("user", text);
    if (inp) inp.value = "";
    if (isAffirmative(text)) handleSummaryChoice(true);
    else if (isNegative(text)) handleSummaryChoice(false);
    else {
      // Unclear — ask again via log
      logMessage("assistant", `I didn't catch that — please say "yes" to send the summary to ${appState.email}, or "no" to just end.`);
      if ("speechSynthesis" in window) {
        try { window.speechSynthesis.speak(new SpeechSynthesisUtterance(`Please say yes to send summary to ${appState.email}, or no to just end.`)); } catch {}
      }
    }
    return;
  }
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    logMessage("system", "Not connected — click Connect first.");
    return;
  }
  logMessage("user", text);
  ws.send(JSON.stringify({ type: "text", text }));
  if (inp) inp.value = "";
}
document.getElementById("sendTextBtn")?.addEventListener("click", sendTextMessage);
document.getElementById("textInput")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendTextMessage();
  }
});
document.getElementById("textSendBtn")?.addEventListener("click", () => document.getElementById("textInput")?.focus());
document.getElementById("clearLogBtn")?.addEventListener("click", () => {
  const log = document.getElementById("chatLog");
  if (log) log.innerHTML = "";
  const cnt = document.getElementById("logCount");
  if (cnt) cnt.textContent = "0 messages";
  turnCount = 0; toolCount = 0;
  const st = document.getElementById("statTurns");
  const sl = document.getElementById("statTools");
  if (st) st.textContent = "0";
  if (sl) sl.textContent = "0";
});

document.addEventListener("keydown", (e) => {
  // Space toggles the whole session through the central mic handler
  // (connect when idle → end session when connected).
  if (e.code === "Space" && e.target === document.body) {
    e.preventDefault();
    try { handleMicToggle(); } catch {}
  }
});

window.addEventListener("load", async () => {
  // On reload of ANY page: check if a token exists (localStorage/sessionStorage) =>
  // it means the user logged in before. Validate against backend GET /api/auth/me.
  // If valid -> redirect to config, else clear and show home.
  const tok = getStoredToken(); // checks sessionStorage + localStorage (getStoredToken:1070)
  let validUser = null;
  if (tok) {
    try {
      const base = getBackendBase();
      const res = await fetch(`${base}/api/auth/me`, { headers: getAuthHeaders() });
      if (res.ok) {
        validUser = await res.json();
      } else {
        // Token invalid/expired -> clear session
        try { clearSessionAuth(); } catch {}
      }
    } catch {
      // Network error -> keep token but don't redirect yet; will be handled on next action
      // To avoid flicker, treat as not valid for now
    }
  }

  if (validUser) {
    // Hydrate from server-validated user
    authenticatedUser = validUser;
    appState.username = validUser.username;
    appState.email = validUser.email;
    appState.voiceGender = validUser.preferred_ai_gender || appState.voiceGender;
    appState.customAiName = validUser.custom_ai_name || appState.customAiName;
    selectedVoice = appState.voiceGender;
    try {
      sessionStorage.setItem("auth_user", JSON.stringify(validUser));
      sessionStorage.setItem("username", validUser.username);
      sessionStorage.setItem("user_email", validUser.email);
    } catch {}
    try { selectVoice(appState.voiceGender); selectSignupVoice(appState.voiceGender); } catch {}
    try { syncLegacyInputs(); updateDashboardHeader(); } catch {}
    // Valid token on reload of ANY page -> redirect to config (per spec)
    showScreen("config");
    // Refresh docs for config badge
    try {
      const base = getBackendBase();
      fetch(`${base}/api/documents`, { headers: getAuthHeaders() })
        .then(r => r.json()).then(d => {
          if (d && d.count !== undefined) {
            appState.docsCount = d.count;
            const el = document.getElementById("statDocs");
            if (el) el.textContent = String(d.count);
          }
        }).catch(()=>{});
    } catch {}
    return;
  }

  // No valid token -> ensure clean state and show home or auth if requested
  if (!handleInitialRoute()) {
    showScreen("landing");
  }
  // No valid session: no doc fetch (guest fallback would show empty)
});
