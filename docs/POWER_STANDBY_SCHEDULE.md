# QuantLabs power schedule

This server uses a conservative low-power schedule instead of full suspend/shutdown.

## Why not full suspend yet

The host supports Linux sleep states including `mem`, but Wake-on-LAN was not enabled on the active interface during setup. A full suspend or shutdown can cut SSH, Tailscale, Cloudflare access, and the dashboard until someone wakes the machine physically or BIOS/RTC wake is confirmed.

The safe first step is a night low-power mode:

- 23:00: stop heavy containers and trading timers.
- 07:00: start the QuantLabs stack again.

## Timers

Installed systemd units:

- `quantlab-night-mode.timer`
- `quantlab-night-mode.service`
- `quantlab-day-mode.timer`
- `quantlab-day-mode.service`

Schedule:

```text
23:00 -> quantlab-power-mode.sh night
07:00 -> quantlab-power-mode.sh day
```

## Night mode stops

- `quantlab-paper-trading.timer`
- `quantlab-paper-trading.service`
- `quantlab-gpu-idle-governor.timer`
- `quantlab-gpu-idle-governor.service`
- `quantlab_llm`
- `quantlab_ollama`
- `quantlab_market_gpu`
- `jupyter_quantlab_gpu`
- `jupyter_quantlab_lite`
- `quantlab_file_analyst`
- `quantlab_harness`
- `quantlab_websocket`
- `bitcoind`

Core host services, Docker, nginx/auth/API base services are left available so the machine remains reachable.

## Day mode starts

- `quantlab_postgres_auth`
- `quantlab_auth`
- `quantlab_nginx`
- `quantlab_api`
- `quantlab_websocket`
- `quantlab_harness`
- `quantlab_file_analyst`
- `quantlab_llm`
- `quantlab_ollama`
- `quantlab_market_gpu`
- `jupyter_quantlab_gpu`
- `jupyter_quantlab_lite`
- `bitcoind`
- `quantlab-gpu-idle-governor.timer`
- `quantlab-paper-trading.timer`

## Commands

Check schedule:

```bash
systemctl list-timers quantlab-night-mode.timer quantlab-day-mode.timer --no-pager
```

Check current status:

```bash
sudo /home/quantlab/quantlab-ai-capital/deploy/systemd/quantlab-power-mode.sh status
```

Enter night mode manually:

```bash
sudo systemctl start quantlab-night-mode.service
```

Return to day mode manually:

```bash
sudo systemctl start quantlab-day-mode.service
```

Disable schedule:

```bash
sudo systemctl disable --now quantlab-night-mode.timer quantlab-day-mode.timer
```

View log:

```bash
tail -200 /home/quantlab/quantlab-ai-capital/logs/power-mode.log
```

## Future full standby/shutdown

Full suspend can be considered after validating:

- BIOS/UEFI allows Wake-on-LAN or RTC wake.
- `eno1` has Wake-on-LAN enabled and persistent.
- The server can wake at 07:00 without manual intervention.
- Tailscale/SSH reconnects automatically after wake.

Until then, this schedule minimizes power draw without risking unattended lockout.
