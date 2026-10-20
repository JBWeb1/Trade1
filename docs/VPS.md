# Running Trade1 24/5 on a Windows VPS

Your home PC sleeps, restarts for updates and loses Wi-Fi. A forward test needs a machine that
stays on while the markets are open, from Sunday evening to Friday evening.

## Important: MT5's built-in "Virtual Hosting" cannot run Trade1

The VPS you can rent from inside MetaTrader 5 (Tools → Virtual Hosting) only migrates MQL5 programs:
Expert Advisors, indicators and scripts. Trade1 is a Python program that talks to the MT5 terminal,
so it will **not** run there. You need a full **Windows** VPS that you can log in to with Remote Desktop.

Options:
* **Exness VPS:** Exness has offered a free VPS to clients who meet deposit or volume conditions. Check
  the current terms in your Exness Personal Area. A VPS hosted near Exness's servers has low latency.
* **Any Windows cloud VPS** (Windows Server 2019/2022): 2 vCPU, 4 GB RAM and 60 GB disk are enough.
  Prefer a data centre in the same region as your Exness trade server (shown in MT5 as a ping).

## Setup (about 30 minutes)

1. Connect with Remote Desktop.
2. Install the Exness MetaTrader 5 terminal and log in to your **demo** account.
   In MT5: **Tools → Options → Expert Advisors → Allow algorithmic trading**.
3. Copy Trade1 to `C:\Trade1` (Git or the ZIP download), then run `scripts\setup_windows.bat`.
4. Run `scripts\run_checks.bat` and confirm **VERIFIED DEMO ACCOUNT**.
5. In `backend\.env`, set:
   * `TRADE1_EXECUTION_MODE=DEMO`, only once you have a strategy that PASSED validation;
   * `TRADE1_TELEGRAM_BOT_TOKEN` and `TRADE1_TELEGRAM_CHAT_ID`, for phone alerts (see below).
6. Run `scripts\install_autostart.bat` and answer **y** to "start the DEMO runner automatically".
7. Make MT5 start with Windows: put a shortcut to `terminal64.exe` in `shell:startup`.
8. Make Windows log in automatically after a reboot, so the logon tasks start. Use the `netplwiz`
   setting, or Sysinternals Autologon, which stores the password encrypted.
9. Open the dashboard on the VPS (`http://127.0.0.1:3000`), enable demo trading from the PASSED
   validation report, then **disconnect** Remote Desktop. Don't log off: logging off closes MT5.

The dashboard listens on the VPS itself only (127.0.0.1). To view it from your PC, use Remote Desktop,
or an SSH tunnel if you know how. Never expose port 3000 or 8000 to the internet.

## Phone alerts (Telegram)

1. In Telegram, talk to **@BotFather** → `/newbot` → copy the token.
2. Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `"chat":{"id": ...}`.
3. Put both values in `backend\.env` and restart the backend and runner.

You will get messages for: fills, broker rejections, uncertain orders and reconciliation issues
(new entries are blocked), emergency stops, account changes, repeated MT5 connection problems,
runner errors, and the daily report at 23:05. The token is never written to logs.

## Daily routine

* Read the daily report: demo results compared with what validation predicted.
* If there is a reconciliation alert, open MT5 and the Demo automation page, check what MT5 actually
  holds, then resolve the event with a note.
* Weekly: confirm that backups appear in `C:\Trade1\backups`.

## Before any real money

Forward-test for at least 2–3 months and compare the demo trades with the backtest: win rate,
expectancy in R, slippage and trade frequency. This release cannot trade live. A live release would be
a separate, deliberate step, starting with the smallest possible size.
