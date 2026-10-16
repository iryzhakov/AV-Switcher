import asyncio
import ctypes
import datetime
import fcntl
import glob
import json
import os
import pwd
import re
import shutil
import socket
from typing import Any, Optional

import decky


class CommandError(RuntimeError):
    pass


class Plugin:
    async def _main(self):
        self._events: list[str] = []
        self._last_wol_diagnostics = "Проверка Wake-on-LAN ещё не запускалась."
        decky.logger.info("AV Switcher started")
        self._log_event("Плагин запущен", f"euid={os.geteuid()}, root={'да' if os.geteuid() == 0 else 'нет'}")
        # Older 0.5.4 builds could leave a syntactically malformed -O token in
        # the wrapper. Repair it as soon as the corrected root plugin loads, so
        # the next Gaming Mode start is safe even before the user changes output.
        try:
            account = self._account()
            wrapper = os.path.join(account.pw_dir, ".local", "bin", "av-switcher-gamescope-session")
            original = "/usr/lib/steamos/gamescope-session"
            if os.path.isfile(wrapper) and os.path.isfile(original):
                with open(wrapper, encoding="utf-8", errors="replace") as handle:
                    match = re.search(r"^# AV_SWITCHER_TARGET=([A-Za-z0-9_.-]+)$", handle.read(), re.MULTILINE)
                if match:
                    self._install_gamescope_override(match.group(1), original)
                    decky.logger.info("Repaired Gamescope wrapper for %s", match.group(1))
        except Exception as error:
            decky.logger.warning("Could not repair Gamescope wrapper: %s", error)

    async def _unload(self):
        decky.logger.info("AV Switcher stopped")

    def _log_event(self, title: str, details: str = ""):
        if not hasattr(self, "_events"):
            self._events = []
        stamp = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        entry = f"[{stamp}] {title}"
        if details:
            entry += "\n" + details.strip()
        self._events.append(entry)
        self._events = self._events[-80:]
        decky.logger.info("%s%s", title, f": {details}" if details else "")

    def _session(self) -> tuple[list[str], dict[str, str]]:
        user = getattr(decky, "DECKY_USER", None) or os.environ.get("SUDO_USER") or "deck"
        try:
            account = pwd.getpwnam(user)
        except KeyError:
            account = pwd.getpwuid(os.getuid())
            user = account.pw_name

        env = self._clean_environment()
        runtime = f"/run/user/{account.pw_uid}"
        env.update({
            "HOME": account.pw_dir,
            "USER": user,
            "LOGNAME": user,
            "XDG_RUNTIME_DIR": runtime,
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
        })
        if not env.get("DISPLAY"):
            sockets = sorted(glob.glob("/tmp/.X11-unix/X*"))
            env["DISPLAY"] = f":{os.path.basename(sockets[-1])[1:]}" if sockets else ":0"

        prefix: list[str] = []
        if os.geteuid() == 0 and account.pw_uid != 0 and shutil.which("runuser"):
            prefix = ["runuser", "-u", user, "--"]
        return prefix, env

    @staticmethod
    def _clean_environment() -> dict[str, str]:
        env = os.environ.copy()
        # Decky's frozen Python backend may run from a PyInstaller _MEI directory.
        # Passing its private libraries to SteamOS binaries breaks systemctl with
        # symbol/version errors, so external commands must use the system runtime.
        for variable in (
            "LD_LIBRARY_PATH",
            "LD_PRELOAD",
            "PYTHONHOME",
            "PYTHONPATH",
            "PYTHONEXECUTABLE",
        ):
            env.pop(variable, None)
        return env

    def _account(self):
        user = getattr(decky, "DECKY_USER", None) or os.environ.get("SUDO_USER") or "deck"
        try:
            return pwd.getpwnam(user)
        except KeyError:
            return pwd.getpwuid(os.getuid())

    async def _run(self, *command: str, timeout: float = 12) -> str:
        prefix, env = self._session()
        decky.logger.info("Running: %s", " ".join(command))
        process = await asyncio.create_subprocess_exec(
            *prefix,
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise CommandError(f"Команда {command[0]} не ответила вовремя")
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        if process.returncode != 0:
            raise CommandError(err or out or f"{command[0]} завершилась с кодом {process.returncode}")
        return out

    async def _run_direct(self, *command: str, timeout: float = 12) -> str:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._clean_environment(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise CommandError(f"Команда {command[0]} не ответила вовремя")
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        if process.returncode != 0:
            raise CommandError(err or out or f"{command[0]} завершилась с кодом {process.returncode}")
        return out

    async def _systemctl_user(self, *arguments: str) -> str:
        # Decky runs root plugins outside the user's login session. Addressing the
        # user's systemd instance explicitly is more reliable than runuser alone.
        account = self._account()
        errors = []
        if os.geteuid() == 0:
            try:
                return await self._run_direct(
                    "systemctl", "--user", f"--machine={account.pw_name}@.host", *arguments
                )
            except Exception as error:
                errors.append(f"machine bus: {error}")
        try:
            return await self._run("systemctl", "--user", *arguments)
        except Exception as error:
            errors.append(f"session bus: {error}")
        raise CommandError("; ".join(errors))

    async def _magicpacket_server_status(self) -> dict[str, Any]:
        """Read the user service that handles remote sleep and shutdown."""
        try:
            state = await self._systemctl_user(
                "show", "--property=ActiveState", "--value", "magicpacket.service"
            )
            state = state.strip().lower() or "inactive"
            return {"active": state == "active", "state": state}
        except Exception as error:
            # A missing or stopped service must not break discovery of displays,
            # audio devices, network information, or Wake-on-LAN.
            decky.logger.debug("Cannot inspect magicpacket.service: %s", error)
            return {"active": False, "state": "unavailable", "error": str(error)}

    @staticmethod
    def _display_name(path: str) -> str:
        base = os.path.basename(path)
        return re.sub(r"^card\d+-", "", base)

    def _displays(self) -> list[dict[str, Any]]:
        result = []
        for status_path in sorted(glob.glob("/sys/class/drm/card*-*/status")):
            try:
                folder = os.path.dirname(status_path)
                name = self._display_name(folder)
                # Kernel writeback/virtual connectors are not physical screens
                # and must never be offered as switch targets.
                if name.startswith(("Writeback-", "Virtual-")):
                    continue
                status = open(status_path, encoding="utf-8").read().strip()
                edid_path = os.path.join(folder, "edid")
                has_edid = os.path.exists(edid_path) and os.path.getsize(edid_path) > 0
                modes_path = os.path.join(folder, "modes")
                has_modes = os.path.exists(modes_path) and os.path.getsize(modes_path) > 0
                if status != "connected" and not has_edid and not has_modes:
                    continue
                enabled_path = os.path.join(folder, "enabled")
                active = os.path.exists(enabled_path) and open(enabled_path, encoding="utf-8").read().strip() == "enabled"
                kind = "Встроенный экран" if name.startswith(("eDP", "DSI")) else "Внешний экран"
                result.append({"id": name, "name": f"{kind} · {name}", "active": active})
            except OSError as error:
                decky.logger.warning("Cannot inspect %s: %s", status_path, error)
        return result

    def _display_folder(self, connector: str) -> Optional[str]:
        for status_path in glob.glob("/sys/class/drm/card*-*/status"):
            folder = os.path.dirname(status_path)
            if self._display_name(folder) == connector:
                return folder
        return None

    def _display_active(self, connector: str) -> bool:
        folder = self._display_folder(connector)
        if not folder:
            return False
        try:
            return open(os.path.join(folder, "enabled"), encoding="ascii").read().strip() == "enabled"
        except OSError:
            return False

    async def _wait_display_active(self, connector: str, seconds: float = 5.0) -> bool:
        for _ in range(max(1, int(seconds / 0.25))):
            if self._display_active(connector):
                return True
            await asyncio.sleep(0.25)
        return False

    async def _gamescope_dirty(self):
        if shutil.which("gamescopectl"):
            try:
                await self._run("gamescopectl", "backend_set_dirty")
            except Exception as error:
                decky.logger.debug("gamescopectl backend_set_dirty failed: %s", error)

    async def _gamescope_runtime_output(self, connector: str) -> bool:
        if not shutil.which("gamescopectl"):
            return False
        try:
            await self._run("gamescopectl", "drm_preferred_output", f"{connector},*")
        except Exception as error:
            decky.logger.info("Runtime output override unavailable: %s", error)
            return False
        return await self._wait_display_active(connector, 4.0)

    async def _gamescope_drm_hotplug(self, connector: str, names: list[str]) -> bool:
        folders = {name: self._display_folder(name) for name in names}
        if not folders.get(connector):
            return False
        changed = []
        try:
            target_status = os.path.join(folders[connector], "status")
            with open(target_status, "w", encoding="ascii") as handle:
                handle.write("on\n")
            changed.append(target_status)
            for name, folder in folders.items():
                if name == connector or not folder:
                    continue
                status_path = os.path.join(folder, "status")
                with open(status_path, "w", encoding="ascii") as handle:
                    handle.write("off\n")
                changed.append(status_path)
            await self._gamescope_dirty()
            if await self._wait_display_active(connector, 6.0):
                try:
                    with open(target_status, "w", encoding="ascii") as handle:
                        handle.write("detect\n")
                except OSError:
                    pass
                return True
        except OSError as error:
            decky.logger.warning("DRM live switch failed: %s", error)

        for status_path in changed:
            try:
                with open(status_path, "w", encoding="ascii") as handle:
                    handle.write("detect\n")
            except OSError:
                pass
        await self._gamescope_dirty()
        return False

    @staticmethod
    def _friendly_audio_name(raw_name: str, section_name: str) -> str:
        lowered = raw_name.lower()
        prefix = "Микрофон" if section_name == "Sources" else "Звук"

        if "hdmi" in lowered or "displayport" in lowered or "display port" in lowered:
            connection = "HDMI / DisplayPort"
        elif "usb" in lowered:
            connection = "USB"
        elif "bluez" in lowered or "bluetooth" in lowered:
            connection = "Bluetooth"
        elif "headphone" in lowered or "headset" in lowered:
            connection = "наушники"
        elif "speaker" in lowered:
            connection = "встроенные динамики"
        elif "analog" in lowered or "pci" in lowered or "built-in" in lowered:
            connection = "встроенный" if section_name == "Sources" else "аналоговый выход"
        else:
            connection = "устройство"

        cleaned = raw_name.replace("_", " ")
        cleaned = re.sub(r"^(?:alsa|bluez)[.\w-]+\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:analog|digital|stereo|surround|output|input)\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-·")
        if len(cleaned) > 42:
            cleaned = cleaned[:39].rstrip() + "…"

        label = f"{prefix}: {connection}"
        if cleaned and cleaned.lower() not in connection.lower():
            label += f" · {cleaned}"
        return label

    async def _pipewire_devices(self, section_name: str) -> list[dict[str, Any]]:
        if not shutil.which("wpctl"):
            return []
        output = await self._run("wpctl", "status")
        devices: list[dict[str, Any]] = []
        section = ""
        for raw_line in output.splitlines():
            heading = re.search(r"\b(Sinks|Sources):\s*$", raw_line)
            if heading:
                section = heading.group(1)
                continue
            if re.search(r"\b(?:Filters|Streams|Devices|Video):\s*$", raw_line):
                section = ""
                continue
            if section != section_name:
                continue
            clean = re.sub(r"^[\s│├└─]+", "", raw_line)
            match = re.match(r"(?P<default>\*)?\s*(?P<id>\d+)\.\s+(?P<name>.+?)(?:\s+\[vol:.*)?$", clean)
            if match:
                devices.append({
                    "id": match.group("id"),
                    "name": self._friendly_audio_name(match.group("name").strip(), section_name),
                    "active": bool(match.group("default")),
                })
        return devices

    async def _audio_profiles(self, kind: str) -> Optional[dict[str, Any]]:
        """Return Bluetooth card profiles for the current default sink/source."""
        if not shutil.which("pactl"):
            return None
        noun = "sink" if kind == "sink" else "source"
        try:
            default_name = await self._run("pactl", f"get-default-{noun}")
            objects = json.loads(await self._run("pactl", "-f", "json", "list", f"{noun}s"))
            cards = json.loads(await self._run("pactl", "-f", "json", "list", "cards"))
        except Exception as error:
            decky.logger.debug("Cannot inspect audio profiles: %s", error)
            return None

        node = next((item for item in objects if item.get("name") == default_name), None)
        if not node or node.get("card") is None:
            return None
        card = next((item for item in cards if str(item.get("index")) == str(node.get("card"))), None)
        if not card:
            return None
        properties = card.get("properties") or {}
        card_name = str(card.get("name", ""))
        bluetooth = (
            "bluez" in card_name.lower()
            or str(properties.get("device.api", "")).lower() == "bluez5"
            or "bluetooth" in str(properties.get("device.bus", "")).lower()
        )
        if not bluetooth:
            return None

        raw_profiles = card.get("profiles") or []
        if isinstance(raw_profiles, dict):
            raw_profiles = [dict(value, name=name) for name, value in raw_profiles.items()]
        profiles = []
        for profile in raw_profiles:
            name = str(profile.get("name", ""))
            available = str(profile.get("available", "unknown")).lower()
            if not name or name == "off" or available == "no":
                continue
            if not any(marker in name.lower() for marker in ("a2dp", "hfp", "hsp", "bap")):
                continue
            profiles.append({"id": name, "name": str(profile.get("description") or name)})
        if len(profiles) < 2:
            return None
        active = card.get("active_profile")
        if isinstance(active, dict):
            active = active.get("name")
        return {"card": card_name, "active": active, "profiles": profiles}

    async def get_devices(self) -> dict[str, Any]:
        try:
            sinks, sources, sink_profiles, source_profiles, wake, server = await asyncio.gather(
                self._pipewire_devices("Sinks"), self._pipewire_devices("Sources"),
                self._audio_profiles("sink"), self._audio_profiles("source"),
                self.get_wake_status(),
                self._magicpacket_server_status(),
            )
            return {
                "ok": True,
                "displays": self._displays(),
                "sinks": sinks,
                "sources": sources,
                "sinkProfiles": sink_profiles,
                "sourceProfiles": source_profiles,
                "network": self._active_network(),
                "wake": wake,
                "server": server,
            }
        except Exception as error:
            decky.logger.exception("Device discovery failed")
            return {
                "ok": False,
                "error": str(error),
                "displays": self._displays(),
                "sinks": [],
                "sources": [],
                "sinkProfiles": None,
                "sourceProfiles": None,
                "network": self._active_network(),
                "wake": await self.get_wake_status(),
                "server": await self._magicpacket_server_status(),
            }

    @staticmethod
    def _active_network() -> dict[str, Any]:
        """Return the interface carrying the preferred IPv4 default route."""
        routes = []
        try:
            with open("/proc/net/route", encoding="ascii", errors="replace") as handle:
                next(handle, None)
                for line in handle:
                    fields = line.split()
                    if len(fields) < 8 or fields[1] != "00000000":
                        continue
                    interface = fields[0]
                    try:
                        metric = int(fields[6])
                        flags = int(fields[3], 16)
                    except ValueError:
                        continue
                    if flags & 0x1:
                        routes.append((metric, interface))
        except OSError:
            pass

        if not routes:
            return {"connected": False, "type": "none", "label": "Нет подключения"}

        _, interface = min(routes)
        wireless = os.path.isdir(f"/sys/class/net/{interface}/wireless") or interface.startswith("wl")
        return {
            "connected": True,
            "type": "wifi" if wireless else "ethernet",
            "interface": interface,
            "label": "Wi‑Fi" if wireless else "Сетевой кабель",
        }

    @staticmethod
    def _wake_interface() -> str:
        """Prefer the wired default route, then the first physical Ethernet NIC."""
        active = Plugin._active_network()
        interface = str(active.get("interface", ""))
        if interface and active.get("type") == "ethernet":
            return interface
        candidates = []
        for path in sorted(glob.glob("/sys/class/net/*")):
            name = os.path.basename(path)
            if name == "lo" or os.path.isdir(os.path.join(path, "wireless")):
                continue
            if not os.path.exists(os.path.join(path, "device")):
                continue
            try:
                mac = open(os.path.join(path, "address"), encoding="ascii").read().strip()
            except OSError:
                continue
            if re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac) and mac != "00:00:00:00:00:00":
                candidates.append(name)
        if not candidates:
            raise CommandError("Проводной сетевой адаптер не найден")
        return candidates[0]

    async def _wake_details(self, interface: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", interface):
            raise CommandError("Некорректное имя сетевого интерфейса")
        address_path = f"/sys/class/net/{interface}/address"
        try:
            mac = open(address_path, encoding="ascii").read().strip().upper()
        except OSError as error:
            raise CommandError(f"Не удалось прочитать MAC-адрес: {error}") from error

        ip_address = "не назначен"
        if shutil.which("ip"):
            try:
                output = await self._run("ip", "-4", "-o", "addr", "show", "dev", interface)
                match = re.search(r"\binet\s+([0-9.]+)/", output)
                if match:
                    ip_address = match.group(1)
            except Exception:
                pass

        power_path = f"/sys/class/net/{interface}/device/power/wakeup"
        try:
            power_wakeup = open(power_path, encoding="ascii").read().strip().lower()
        except OSError:
            power_wakeup = "недоступно"
        device_path = f"/sys/class/net/{interface}/device"
        driver_path = os.path.join(device_path, "driver")
        driver = os.path.basename(os.path.realpath(driver_path)) if os.path.exists(driver_path) else "неизвестен"
        identifiers = {}
        for field in ("vendor", "device", "subsystem_vendor", "subsystem_device"):
            try:
                identifiers[field] = open(os.path.join(device_path, field), encoding="ascii").read().strip()
            except OSError:
                identifiers[field] = "?"
        return {
            "interface": interface,
            "mac": mac,
            "ip": ip_address,
            "powerWakeup": power_wakeup,
            "driver": driver,
            "pci": identifiers,
        }

    @staticmethod
    def _wifi_phy(interface: str) -> str:
        phy_path = f"/sys/class/net/{interface}/phy80211"
        if not os.path.exists(phy_path):
            raise CommandError(f"Для {interface} не найден беспроводной PHY")
        phy = os.path.basename(os.path.realpath(phy_path))
        if not re.fullmatch(r"phy[0-9]+", phy):
            raise CommandError(f"Некорректное имя беспроводного PHY: {phy}")
        return phy

    async def _wifi_wowlan_state(self, interface: str) -> dict[str, Any]:
        phy = self._wifi_phy(interface)
        if not shutil.which("iw"):
            raise CommandError("В системе не найдена утилита iw")
        output = await self._run_direct("iw", "phy", phy, "wowlan", "show")
        enabled = bool(re.search(r"wake up on magic packet|magic-packet", output, re.IGNORECASE))
        return {"phy": phy, "enabled": enabled, "raw": output or "WoWLAN is disabled"}

    async def _ensure_wifi_wowlan(self, interface: str, report: list[str]) -> dict[str, Any]:
        """Enable Wi-Fi magic-packet wake now and persist it in NetworkManager."""
        report[0] = "Проверка Wake-on-WLAN"
        report[2] = "Метод: NetworkManager + iw wowlan magic-packet"
        report.append(f"Беспроводной интерфейс: {interface}")
        details = await self._wake_details(interface)
        if not shutil.which("nmcli"):
            raise CommandError("В системе не найдена утилита nmcli")
        connection = (await self._run_direct(
            "nmcli", "-g", "GENERAL.CONNECTION", "device", "show", interface
        )).strip()
        if not connection or connection == "--":
            raise CommandError(f"Для {interface} не найден активный профиль NetworkManager")
        setting_before = (await self._run_direct(
            "nmcli", "-g", "802-11-wireless.wake-on-wlan", "connection", "show", connection
        )).strip().lower()
        runtime_before = await self._wifi_wowlan_state(interface)
        report += [
            f"IP: {details['ip']}",
            f"MAC: {details['mac']}",
            f"PHY: {runtime_before['phy']}",
            f"Профиль NetworkManager: {connection}",
            f"Настройка до проверки: {setting_before or 'не указана'}",
            f"Состояние драйвера до проверки: {runtime_before['raw']}",
        ]
        changed = "magic" not in setting_before or not runtime_before["enabled"]
        if changed:
            if os.geteuid() != 0:
                raise CommandError("Плагин запущен без root-прав")
            if "magic" not in setting_before:
                report.append("Действие: сохраняю wake-on-wlan=magic в активном Wi-Fi-профиле")
                await self._run_direct(
                    "nmcli", "connection", "modify", connection,
                    "802-11-wireless.wake-on-wlan", "magic",
                )
            if not runtime_before["enabled"]:
                report.append("Действие: включаю magic-packet в беспроводном драйвере")
                await self._run_direct(
                    "iw", "phy", runtime_before["phy"], "wowlan", "enable", "magic-packet"
                )
        setting_after = (await self._run_direct(
            "nmcli", "-g", "802-11-wireless.wake-on-wlan", "connection", "show", connection
        )).strip().lower()
        runtime_after = await self._wifi_wowlan_state(interface)
        report += [
            f"Настройка после проверки: {setting_after or 'не указана'}",
            f"Состояние драйвера после проверки: {runtime_after['raw']}",
        ]
        if "magic" not in setting_after:
            raise CommandError("NetworkManager не сохранил Wake-on-WLAN magic")
        if not runtime_after["enabled"]:
            raise CommandError("Драйвер Wi-Fi не включил пробуждение по Magic Packet")
        return {
            "ok": True,
            "applicable": True,
            "mode": "wifi",
            "enabled": True,
            "changed": changed,
            "persistent": True,
            "connection": connection,
            **details,
        }

    @staticmethod
    def _native_wol(interface: str, enable: bool) -> tuple[bool, bool]:
        """Read or enable WAKE_MAGIC directly through the kernel ethtool ioctl.

        SteamOS images do not consistently ship the ethtool command-line tool.
        Using the same kernel API directly removes that external dependency.
        """
        class WolInfo(ctypes.Structure):
            _fields_ = [
                ("cmd", ctypes.c_uint32),
                ("supported", ctypes.c_uint32),
                ("wolopts", ctypes.c_uint32),
                ("sopass", ctypes.c_ubyte * 6),
            ]

        class IfReq(ctypes.Structure):
            # Linux struct ifreq is IFNAMSIZ plus a 24-byte union on 64-bit
            # Steam hardware. `data` is the first member of that union.
            _fields_ = [
                ("name", ctypes.c_char * 16),
                ("data", ctypes.c_void_p),
                ("padding", ctypes.c_ubyte * 16),
            ]

        encoded = interface.encode("ascii")
        if len(encoded) >= 16:
            raise CommandError("Слишком длинное имя сетевого интерфейса")
        ETHTOOL_GWOL = 0x0000000A
        ETHTOOL_SWOL = 0x0000000B
        WAKE_MAGIC = 1 << 5
        SIOCETHTOOL = 0x8946

        def request(info: WolInfo):
            ifreq = IfReq(encoded, ctypes.addressof(info))
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as descriptor:
                fcntl.ioctl(descriptor.fileno(), SIOCETHTOOL, bytes(ifreq))

        info = WolInfo(cmd=ETHTOOL_GWOL)
        try:
            request(info)
        except OSError as error:
            raise CommandError(f"Ядро не отдало состояние Wake-on-LAN: {error}") from error
        supported = bool(info.supported & WAKE_MAGIC)
        active = bool(info.wolopts & WAKE_MAGIC)
        if enable and supported and not active:
            info.cmd = ETHTOOL_SWOL
            info.wolopts |= WAKE_MAGIC
            try:
                request(info)
            except OSError as error:
                raise CommandError(f"Ядро не разрешило включить Wake-on-LAN: {error}") from error
            verify = WolInfo(cmd=ETHTOOL_GWOL)
            request(verify)
            active = bool(verify.wolopts & WAKE_MAGIC)
        return supported, active

    async def ensure_wake_on_lan(self) -> dict[str, Any]:
        """Report Wake-on-LAN state and enable Magic Packet mode when needed."""
        report = [
            "Проверка Wake-on-LAN",
            f"Права root: {'да' if os.geteuid() == 0 else 'нет'}",
            "Метод: профиль NetworkManager + sysfs power/wakeup",
        ]
        try:
            active = self._active_network()
            active_interface = str(active.get("interface", ""))
            if active.get("type") == "wifi" and active_interface:
                result = await self._ensure_wifi_wowlan(active_interface, report)
                diagnostics = "\n".join(report)
                self._last_wol_diagnostics = diagnostics
                self._log_event("Wake-on-WLAN: успешно", diagnostics)
                return {"diagnostics": diagnostics, **result}
            interface = self._wake_interface()
            report.append(f"Проводной интерфейс: {interface}")
            before = await self._wake_details(interface)
            if not shutil.which("nmcli"):
                raise CommandError("В системе не найдена утилита nmcli")
            connection = (await self._run_direct(
                "nmcli", "-g", "GENERAL.CONNECTION", "device", "show", interface
            )).strip()
            if not connection or connection == "--":
                raise CommandError(f"Для {interface} не найден активный профиль NetworkManager")
            setting_before = (await self._run_direct(
                "nmcli", "-g", "802-3-ethernet.wake-on-lan", "connection", "show", connection
            )).strip().lower()
            report += [
                f"IP: {before['ip']}",
                f"MAC: {before['mac']}",
                f"Драйвер: {before['driver']}",
                "PCI: " + ", ".join(f"{key}={value}" for key, value in before["pci"].items()),
                f"Профиль NetworkManager: {connection}",
                f"Настройка до проверки: {setting_before or 'не указана'}",
                f"Питание устройства до проверки: {before['powerWakeup']}",
            ]
            changed = setting_before != "magic" or before["powerWakeup"] != "enabled"
            if changed:
                if os.geteuid() != 0:
                    raise CommandError("Плагин запущен без root-прав; переустановите сборку 0.5.4.3")
                report.append("Действие: записываю wake-on-lan=magic в активный профиль")
                await self._run_direct(
                    "nmcli", "connection", "modify", connection,
                    "802-3-ethernet.wake-on-lan", "magic",
                )
                # The Steam Machine's r8169 driver applies this property when
                # NetworkManager activates the profile. `device reapply` may
                # report success without reprogramming the NIC, so follow the
                # working SteamOS procedure and reactivate it explicitly.
                report.append("NetworkManager: повторно активирую профиль (сеть кратко переподключится)")
                await self._run_direct("nmcli", "connection", "up", connection, timeout=30)

                power_path = f"/sys/class/net/{interface}/device/power/wakeup"
                try:
                    with open(power_path, "w", encoding="ascii") as handle:
                        handle.write("enabled\n")
                    report.append("sysfs: пробуждение устройства включено")
                except OSError as error:
                    report.append(f"sysfs: запись недоступна ({error}); полагаюсь на NetworkManager")

            after = await self._wake_details(interface)
            setting_after = (await self._run_direct(
                "nmcli", "-g", "802-3-ethernet.wake-on-lan", "connection", "show", connection
            )).strip().lower()
            report += [
                f"Настройка после проверки: {setting_after or 'не указана'}",
                f"Питание устройства после проверки: {after['powerWakeup']}",
            ]
            if setting_after != "magic":
                raise CommandError("NetworkManager не сохранил режим magic")
            if after["powerWakeup"] not in ("enabled", "недоступно"):
                raise CommandError("Профиль сохранён, но ядро не разрешило устройству будить систему")
            persistent = True
            after["enabled"] = True
            diagnostics = "\n".join(report)
            self._last_wol_diagnostics = diagnostics
            self._log_event("Wake-on-LAN: успешно", diagnostics)
            return {"ok": True, "applicable": True, "mode": "ethernet", "changed": changed, "persistent": persistent, "diagnostics": diagnostics, **after}
        except Exception as error:
            decky.logger.exception("Wake-on-LAN check failed")
            report.append(f"ОШИБКА: {error}")
            diagnostics = "\n".join(report)
            self._last_wol_diagnostics = diagnostics
            self._log_event("Wake-on-LAN: ошибка", diagnostics)
            return {"ok": False, "error": str(error), "diagnostics": diagnostics}

    async def get_wake_status(self) -> dict[str, Any]:
        """Read the persistent WOL state without changing the connection."""
        try:
            active = self._active_network()
            active_interface = str(active.get("interface", ""))
            if active.get("type") == "wifi" and active_interface:
                details = await self._wake_details(active_interface)
                runtime = await self._wifi_wowlan_state(active_interface)
                setting = "неизвестно"
                connection = ""
                if shutil.which("nmcli"):
                    connection = (await self._run_direct(
                        "nmcli", "-g", "GENERAL.CONNECTION", "device", "show", active_interface
                    )).strip()
                    if connection and connection != "--":
                        setting = (await self._run_direct(
                            "nmcli", "-g", "802-11-wireless.wake-on-wlan",
                            "connection", "show", connection
                        )).strip().lower()
                return {
                    "ok": True,
                    "applicable": True,
                    "mode": "wifi",
                    "enabled": runtime["enabled"],
                    "setting": setting,
                    "connection": connection,
                    "phy": runtime["phy"],
                    **details,
                }
            interface = self._wake_interface()
            details = await self._wake_details(interface)
            setting = "неизвестно"
            connection = ""
            if shutil.which("nmcli"):
                connection = (await self._run_direct(
                    "nmcli", "-g", "GENERAL.CONNECTION", "device", "show", interface
                )).strip()
                if connection and connection != "--":
                    setting = (await self._run_direct(
                        "nmcli", "-g", "802-3-ethernet.wake-on-lan", "connection", "show", connection
                    )).strip().lower()
            enabled = setting == "magic" and details["powerWakeup"] in ("enabled", "недоступно")
            return {
                "ok": True,
                "applicable": True,
                "mode": "ethernet",
                "enabled": enabled,
                "setting": setting,
                "connection": connection,
                **details,
            }
        except Exception as error:
            return {"ok": False, "enabled": False, "error": str(error)}

    async def set_audio_device(self, kind: str, device_id: str) -> dict[str, Any]:
        if kind not in ("sink", "source") or not device_id.isdigit():
            return {"ok": False, "error": "Некорректное аудиоустройство"}
        try:
            await self._run("wpctl", "set-default", device_id)
            return {"ok": True}
        except Exception as error:
            decky.logger.exception("Audio switch failed")
            return {"ok": False, "error": str(error)}

    async def set_audio_profile(self, kind: str, card: str, profile_id: str) -> dict[str, Any]:
        if kind not in ("sink", "source"):
            return {"ok": False, "error": "Некорректный тип устройства"}
        try:
            current = await self._audio_profiles(kind)
            valid = current and current.get("card") == card and any(
                item.get("id") == profile_id for item in current.get("profiles", [])
            )
            if not valid:
                return {"ok": False, "error": "Этот профиль больше не доступен"}
            await self._run("pactl", "set-card-profile", card, profile_id)
            await asyncio.sleep(0.7)
            return {"ok": True}
        except Exception as error:
            decky.logger.exception("Audio profile switch failed")
            return {"ok": False, "error": str(error)}

    async def set_display(self, connector: str) -> dict[str, Any]:
        displays = self._displays()
        names = [display["id"] for display in displays]
        if connector not in names:
            return {"ok": False, "error": "Экран отключён или больше не доступен"}

        if not re.fullmatch(r"[A-Za-z0-9_.-]+", connector):
            return {"ok": False, "error": "Некорректное имя видеовыхода"}

        gamescope_session = "/usr/lib/steamos/gamescope-session"
        if os.path.isfile(gamescope_session):
            try:
                if self._display_active(connector):
                    self._install_gamescope_override(connector, gamescope_session)
                    await self._systemctl_user("daemon-reload")
                    return {"ok": True, "method": "already-active", "restarting": False}

                switched = await self._gamescope_runtime_output(connector)
                method = "gamescopectl"
                if not switched:
                    switched = await self._gamescope_drm_hotplug(connector, names)
                    method = "drm-hotplug"
                if not switched:
                    return {
                        "ok": False,
                        "error": "Gamescope не поддержал живое переключение. Исходный экран восстановлен.",
                    }

                self._install_gamescope_override(connector, gamescope_session)
                await self._systemctl_user("daemon-reload")
                return {"ok": True, "method": method, "restarting": False}
            except Exception as error:
                decky.logger.exception("Gamescope display switch failed")
                return {"ok": False, "error": f"Gamescope: {error}"}

        errors = []
        if shutil.which("kscreen-doctor"):
            command = ["kscreen-doctor", f"output.{connector}.enable", f"output.{connector}.primary"]
            command.extend(f"output.{name}.disable" for name in names if name != connector)
            try:
                await self._run(*command)
                return {"ok": True, "method": "kscreen-doctor"}
            except Exception as error:
                errors.append(f"KScreen: {error}")

        if shutil.which("xrandr"):
            command = ["xrandr", "--output", connector, "--auto", "--primary"]
            for name in names:
                if name != connector:
                    command.extend(["--output", name, "--off"])
            try:
                await self._run(*command)
                return {"ok": True, "method": "xrandr"}
            except Exception as error:
                errors.append(f"XRandR: {error}")

        message = "; ".join(errors) or "Не найдены kscreen-doctor и xrandr"
        decky.logger.error("Display switch failed: %s", message)
        return {"ok": False, "error": message}

    def _write_user_file(self, path: str, content: str, executable: bool = False):
        account = self._account()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = f"{path}.av-switcher.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(content)
        mode = 0o755 if executable else 0o644
        os.chmod(temporary, mode)
        if os.geteuid() == 0:
            os.chown(temporary, account.pw_uid, account.pw_gid)
            os.chown(os.path.dirname(path), account.pw_uid, account.pw_gid)
        os.replace(temporary, path)

    def _install_gamescope_override(self, connector: str, original_script: str):
        account = self._account()
        wrapper = os.path.join(account.pw_dir, ".local", "bin", "av-switcher-gamescope-session")
        override = os.path.join(
            account.pw_dir,
            ".config",
            "systemd",
            "user",
            "gamescope-session.service.d",
            "90-av-switcher.conf",
        )

        # Build a complete, inspectable copy instead of patching through process
        # substitution at boot. This also lets diagnostics prove the exact -O value.
        with open(original_script, encoding="utf-8", errors="strict") as handle:
            script = handle.read()
        # -O takes one comma-separated argument. The old implementation mixed
        # quoted and unquoted shell fragments and produced DP-1,*',eDP-1.
        # Quote the whole value so the wrapper and the resulting argv agree.
        internal = connector.startswith(("eDP", "DSI"))
        priority = f"{connector},*" if internal else f"{connector},*,eDP-1"
        pattern = re.compile(r"-O[ \t]+[^\s\\]+")
        script, count = pattern.subn(f"-O '{priority}'", script, count=1)
        if count == 0:
            raise CommandError("В gamescope-session не найден параметр -O")
        marker = f"# AV_SWITCHER_TARGET={connector}\n"
        if script.startswith("#!"):
            first_line, remainder = script.split("\n", 1)
            script = first_line + "\n" + marker + remainder
        else:
            script = marker + script
        service = f'''[Service]
ExecStart=
ExecStart=/bin/bash -c 'if [ -x "{wrapper}" ]; then exec "{wrapper}"; else exec "{original_script}"; fi'
'''
        self._write_user_file(wrapper, script, executable=True)
        self._write_user_file(override, service)
        decky.logger.info("Gamescope preferred display set to %s", connector)

    async def get_diagnostics(self) -> str:
        account = self._account()
        wrapper = os.path.join(account.pw_dir, ".local", "bin", "av-switcher-gamescope-session")
        override = os.path.join(
            account.pw_dir, ".config", "systemd", "user",
            "gamescope-session.service.d", "90-av-switcher.conf"
        )
        lines = [
            f"Пользователь: {account.pw_name} (uid {account.pw_uid})",
            f"Decky root: {'да' if os.geteuid() == 0 else 'нет'}",
            f"gamescopectl: {'есть' if shutil.which('gamescopectl') else 'не найден'}",
            "Экраны: " + ", ".join(
                f"{item['id']} ({'активен' if item['active'] else 'неактивен'})"
                for item in self._displays()
            ),
            f"gamescope-session: {'есть' if os.path.isfile('/usr/lib/steamos/gamescope-session') else 'не найден'}",
            f"wrapper: {'есть' if os.path.isfile(wrapper) else 'не создан'}",
            f"override: {'есть' if os.path.isfile(override) else 'не создан'}",
        ]
        try:
            with open("/usr/lib/steamos/gamescope-session", encoding="utf-8", errors="replace") as handle:
                output_lines = [line.strip() for line in handle if " -O " in line or line.lstrip().startswith("-O ")]
            lines.append("Параметр Gamescope: " + (" | ".join(output_lines) or "-O не найден"))
        except OSError as error:
            lines.append(f"Чтение Gamescope: {error}")
        try:
            with open(wrapper, encoding="utf-8", errors="replace") as handle:
                patched_lines = [
                    line.strip() for line in handle
                    if "AV_SWITCHER_TARGET=" in line or " -O " in line or line.lstrip().startswith("-O ")
                ]
            lines.append("Скрипт AV Switcher: " + (" | ".join(patched_lines) or "приоритет не найден"))
        except OSError as error:
            lines.append(f"Чтение wrapper: {error}")
        active_commands = []
        for command_path in glob.glob("/proc/[0-9]*/cmdline"):
            try:
                raw = open(command_path, "rb").read().replace(b"\0", b" ").decode(errors="replace").strip()
                if re.search(r"(?:^|/)gamescope(?:\s|$)", raw):
                    active_commands.append(raw[:500])
            except OSError:
                pass
        lines.append("Активный Gamescope: " + (" | ".join(active_commands) or "процесс не найден"))
        try:
            state = await self._systemctl_user("is-active", "gamescope-session.service")
            lines.append(f"Сервис: {state}")
            exec_start = await self._systemctl_user(
                "show", "--property=ExecStart", "gamescope-session.service"
            )
            lines.append(exec_start[:800])
        except Exception as error:
            lines.append(f"Systemd: {error}")
        server = await self._magicpacket_server_status()
        lines.append(
            "Magic Packet server: "
            + ("активен" if server.get("active") else "не активен")
            + f" (systemd: {server.get('state', 'неизвестно')})"
        )
        if server.get("error"):
            lines.append(f"Magic Packet server check: {server['error']}")
        lines += [
            "",
            "=== WAKE-ON-LAN ===",
            getattr(self, "_last_wol_diagnostics", "Проверка Wake-on-LAN ещё не запускалась."),
            "",
            "=== ЖУРНАЛ ДЕЙСТВИЙ ===",
            "\n\n".join(getattr(self, "_events", [])) or "Журнал пока пуст.",
        ]
        return "\n".join(lines)
