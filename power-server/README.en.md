# Power control server for iPhone

[Русский](README.md) | **English**

Restored from the user's original script: HTTP on port **5154**, API version **1.1.0**, with sleep, shutdown, and reboot commands. Formatting has been corrected, invalid JSON/timer validation added, and request body size limited. The service file was prepared for this repository; the original unit from the device was not retrieved.

The server runs on the Linux computer being controlled and requires Python 3 and systemd. No additional Python packages are needed. It does not wake the computer: the iPhone uses Wake-on-LAN for that. The server only accepts commands while the computer is awake.

**The original protocol has no password or encryption. Any device with access to TCP 5154 can put the computer to sleep, shut it down, or reboot it. Use only a trusted local network; do not forward this port to the internet.**

## Installation on SteamOS / Linux

Copy the `power-server` directory to the computer you want to control. In Desktop Mode, open a terminal in that directory. Run these commands as your regular user, not root:

```sh
mkdir -p ~/Applications/MagicPacket/custom ~/.config/systemd/user
cp server.py ~/Applications/MagicPacket/custom/server.py
cp magicpacket.service ~/.config/systemd/user/magicpacket.service
systemctl --user daemon-reload
systemctl --user enable --now magicpacket.service
systemctl --user restart magicpacket.service
systemctl --user status magicpacket.service
```

If the server is already installed, back up the existing `server.py` and unit file first: these commands replace them. Port 5154 must be available. If a firewall is enabled, allow incoming TCP 5154 only from your trusted network.

The service starts with the user session. If it needs to run before login, you can enable `sudo loginctl enable-linger "$USER"`; this also keeps other user services running after logout.

Permission to run `systemctl suspend`, `poweroff`, and `reboot` depends on your system's systemd-logind/Polkit configuration. The server does not bypass these permissions. If a request is accepted but nothing happens, check the service logs and the user's permissions. A running service does not by itself confirm that power actions are permitted.

## iPhone setup

The phone and computer must be on the same reachable local network. Reserving the computer's IP address in your router's DHCP settings is recommended. In a Magic Packet client that supports this HTTP API, enter the computer's IP address and server port **5154**. Field names depend on the client. Use the MAC address shown by AV Switcher for waking; the server's HTTP port is separate from the Wake-on-LAN UDP port.

You can also use the iOS Shortcuts app directly:

1. Create a shortcut and add the URL `http://COMPUTER_IP:5154/control/sleep`.
2. Add “Get Contents of URL”, select **POST**, and set the request body to **JSON**.
3. Add a numeric `timeout` field with a value of `0` to act after approximately one second, or, for example, `30` for a delay.
4. Use `/control/shutdown` for shutdown or `/control/reboot` for reboot. Optionally add a confirmation before the request and a shortcut to your Home Screen.

Save your documents and games before testing. A `{"status": true}` response means the request was accepted, not that the action completed successfully. A delayed command is held in memory and is lost if the server stops; every repeated POST schedules another action.

## API

| Method | Path | Response / action |
| --- | --- | --- |
| GET | `/info/version` | `{"version": "1.1.0"}` |
| GET | `/info/status` | `{"status": true}` |
| POST | `/control/sleep` | `systemctl suspend` |
| POST | `/control/shutdown` | `systemctl poweroff` |
| POST | `/control/reboot` | `systemctl reboot` |

POST accepts a JSON object such as `{"timeout": 0}` or an empty body. The delay is clamped to 0–300 seconds, with an effective minimum of 1 second. Invalid bodies return HTTP 400; unknown paths return 404.

To check the server without changing the computer's power state:

```sh
curl http://127.0.0.1:5154/info/version
curl http://127.0.0.1:5154/info/status
```

To check from another device, replace `127.0.0.1` with the computer's IP address.

## Maintenance

```sh
journalctl --user -u magicpacket.service -n 50
systemctl --user restart magicpacket.service
systemctl --user disable --now magicpacket.service
```

The last command stops the server and disables automatic startup. You can then remove the installed files. AV Switcher continues working without the server and displays it as inactive.
