async function hydrateSessionUser() {
  try {
    const r = await fetch('/auth/userinfo', { credentials: 'same-origin', cache: 'no-store' });
    if (!r.ok) return;
    const user = await r.json();
    if (!user.username) return;
    document.querySelectorAll('#session-username').forEach(el => el.textContent = user.username);
    document.querySelectorAll('#session-user-pill').forEach(el => el.textContent = user.username);
  } catch (_) {}
}
hydrateSessionUser();
