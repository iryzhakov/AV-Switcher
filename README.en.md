# AV Switcher for Decky Loader

[Русский](README.md) | **English**

Version 0.6.0.

View and switch devices in Gaming Mode:

- A single active display (built-in screen / HDMI / DisplayPort).
- Audio output (HDMI, speakers, USB headset, Bluetooth, etc.).
- Recording input / microphone.
- Active network connection: Ethernet or Wi-Fi.
- Check and enable Wake-on-LAN, with IP and MAC address information.
- View the status of the `magicpacket.service` sleep and shutdown server.

## Build

```sh
pnpm install
pnpm run build
```

To install manually, copy `plugin.json`, `package.json`, `main.py`, `LICENSE`, and the `dist` directory into `~/homebrew/plugins/av-switcher` on your Steam Deck, then restart Decky Loader.

## How it works

Audio devices are switched through PipeWire/WirePlumber (`wpctl`). Technical PipeWire device names are shortened to readable labels based on connection type.

For the selected Bluetooth device, the plugin also shows available profiles/codecs exposed by PipeWire through its PulseAudio-compatible interface (`pactl`). Switching between A2DP and HFP/HSP affects both headphones and microphone: A2DP offers better playback quality, while a headset profile enables the microphone at the cost of playback quality. If the device does not report multiple Bluetooth profiles, the codec menu is hidden.

The network type is determined from the interface used by the default route to the internet. It refreshes automatically every 10 seconds.

The network section also shows the status of the user service `magicpacket.service`, which handles HTTP sleep and shutdown commands. Its status refreshes automatically alongside the other devices.

## Wake-on-LAN

Wake-on-LAN lets you wake your computer from an iPhone before gaming or using a TV, without reaching for the power button. The phone sends a special Magic Packet targeting the network adapter's MAC address. AV Switcher configures the receiving computer; a separate WOL app on the phone sends the packet.

### Using it with an iPhone

1. Connect the computer via Ethernet, open AV Switcher in Decky, and enable Wake-on-LAN if the corresponding button appears.
2. Note the displayed IP and MAC addresses. Ethernet and Wi-Fi have different MAC addresses: use the address of the intended adapter.
3. Connect your iPhone to the same local network. In a Wake-on-LAN app, enter the computer's MAC address, your subnet's broadcast address, and a UDP port, usually 9. For example, the broadcast address for `192.168.0.0/24` is `192.168.0.255`; it may differ with another subnet mask.
4. Put the computer to sleep and send a Magic Packet. Test waking from sleep first, then from shutdown if needed.

Support depends on the network adapter, whether it stays powered during sleep, its driver, BIOS/UEFI, and the sleep mode. A USB dock may lose power; waking after a full shutdown is not guaranteed. Wake-on-WLAN requires support from the Wi-Fi adapter and driver. An “enabled” indicator confirms the configuration, but does not replace an actual wake test. Guest Wi-Fi networks, client isolation, and some VPNs block broadcast packets.

### What the plugin configures

On startup, the plugin automatically checks the wired adapter's wake settings and displays its current IP and MAC addresses. If Magic Packet wake is disabled, an “Enable Wake-on-LAN” button appears. After successful activation, the setting is saved in the active NetworkManager profile and the button disappears.

When Wi-Fi is active, the plugin displays the wireless interface's IP and MAC addresses and checks Wake-on-WLAN separately. The enable button saves `802-11-wireless.wake-on-wlan=magic` in the active NetworkManager profile and immediately enables the `magic-packet` trigger in the wireless driver through `iw`.

## Power control server

The plugin checks the user service `magicpacket.service`, which allows an iPhone to send sleep and shutdown commands to the computer. This is a separate component: Wake-on-LAN wakes the computer, while the running HTTP server lets you put it back to sleep or shut it down. The HTTP server is unavailable while the computer is asleep.

The restored server source is in [`power-server`](power-server/README.en.md), along with installation, startup, iPhone setup, and HTTP API instructions for port **5154**, including reboot support. The plugin itself does not install or start this service; device switching and Wake-on-LAN configuration work without it.

The server preserves the original protocol without authentication: use it only on a trusted local network and do not expose the port to the internet.

## Display switching

Gamescope manages displays in SteamOS Gaming Mode, so XRandR does not work there. The plugin first attempts Gamescope runtime switching. If the installed version does not support it, the plugin uses controlled DRM hotplug switching and automatically restores the original connectors if the attempt fails. The selected output is also saved as the preferred output for the next startup, but the current session is not restarted — running games and Kodi should remain open.
