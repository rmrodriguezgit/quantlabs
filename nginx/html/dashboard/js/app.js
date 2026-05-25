// Clock
function tick() {
  const now = new Date();

  document.getElementById('clock').textContent =
    now.toLocaleTimeString('es-MX', {
      hour12: false
    });
}

tick();

setInterval(tick, 1000);
