import { callable, definePlugin, toaster } from "@decky/api";
import {
  Focusable,
  PanelSection,
  PanelSectionRow,
  Spinner,
  staticClasses,
} from "@decky/ui";
import { ReactNode, useCallback, useEffect, useState } from "react";
import { BsArrowRepeat, BsBug, BsChevronDown, BsChevronUp, BsClipboard2Check, BsDisplay, BsMic, BsPower, BsSliders, BsVolumeUp } from "react-icons/bs";
import { FaDisplay } from "react-icons/fa6";

type Device = { id: string; name: string; active: boolean };
type AudioProfiles = { card: string; active?: string; profiles: { id: string; name: string }[] };
type DeviceState = {
  ok: boolean;
  error?: string;
  displays: Device[];
  sinks: Device[];
  sources: Device[];
  sinkProfiles?: AudioProfiles | null;
  sourceProfiles?: AudioProfiles | null;
  network: { connected: boolean; type: "wifi" | "ethernet" | "none"; label: string; interface?: string };
  wake: { ok: boolean; applicable?: boolean; mode?: "ethernet" | "wifi"; enabled: boolean; ip?: string; mac?: string; reason?: string; error?: string };
  server: { active: boolean; state: string; error?: string };
};
type Result = { ok: boolean; error?: string; method?: string; restarting?: boolean };
type WakeResult = Result & { changed?: boolean; enabled?: boolean; mode?: "ethernet" | "wifi"; persistent?: boolean; interface?: string; mac?: string; ip?: string; diagnostics?: string };

const getDevices = callable<[], DeviceState>("get_devices");
const setDisplay = callable<[connector: string], Result>("set_display");
const setAudioDevice = callable<[kind: "sink" | "source", id: string], Result>("set_audio_device");
const setAudioProfile = callable<[kind: "sink" | "source", card: string, profileId: string], Result>("set_audio_profile");
const getDiagnostics = callable<[], string>("get_diagnostics");
const ensureWakeOnLan = callable<[], WakeResult>("ensure_wake_on_lan");

const selected = (devices: Device[]) => devices.find((item) => item.active)?.id;
const options = (devices: Device[]) => devices.map((item) => ({ data: item.id, label: item.name }));

type SelectOption = { data: string; label: string };

function InlineSelect({
  icon,
  title,
  items,
  value,
  disabled,
  onChange,
}: {
  icon: ReactNode;
  title: string;
  items: SelectOption[];
  value?: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [focused, setFocused] = useState(false);
  const current = (items.find((item) => item.data === value)?.label ?? "Не выбрано")
    .replace(/^(?:Звук|Микрофон):\s*/i, "");
  return (
    <PanelSectionRow>
      <div style={{ width: "100%", maxWidth: "100%", minWidth: 0, overflow: "hidden", boxSizing: "border-box", marginBottom: 8 }}>
        <Focusable
          noFocusRing
          title={title}
          onClick={() => { if (!disabled) setOpen((shown) => !shown); }}
          onActivate={() => { if (!disabled) setOpen((shown) => !shown); }}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onGamepadFocus={() => setFocused(true)}
          onGamepadBlur={() => setFocused(false)}
          style={{
            width: "100%", maxWidth: "100%", minWidth: 0, minHeight: 48,
            boxSizing: "border-box", display: "flex", alignItems: "center", gap: 11,
            padding: "8px 12px", borderRadius: 10, overflow: "hidden",
            opacity: disabled ? .35 : 1,
            background: open ? "rgba(88,101,242,.42)" : focused ? "rgba(88,101,242,.26)" : "rgba(255,255,255,.065)",
            outline: focused ? "2px solid rgba(255,255,255,.9)" : "2px solid transparent",
            outlineOffset: -2,
          }}
        >
          <span style={{ width: 25, flexShrink: 0, display: "flex", justifyContent: "center", fontSize: 20, opacity: .9 }}>{icon}</span>
          <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 13, fontWeight: 600, textAlign: "left" }}>
            {current}
          </span>
          <span style={{ flexShrink: 0, display: "flex", opacity: .55 }}>{open ? <BsChevronUp /> : <BsChevronDown />}</span>
        </Focusable>
        {open && (
          <div style={{ width: "100%", maxWidth: "100%", boxSizing: "border-box", padding: "4px 0 0 14px", borderLeft: "2px solid rgba(88,101,242,.75)" }}>
            {items.map((item) => (
              <SelectChoice
                key={item.data}
                active={item.data === value}
                disabled={disabled}
                label={item.label.replace(/^(?:Звук|Микрофон):\s*/i, "")}
                onClick={() => {
                  setOpen(false);
                  if (item.data !== value) onChange(item.data);
                }}
              />
            ))}
          </div>
        )}
      </div>
    </PanelSectionRow>
  );
}

function SelectChoice({ label, active, disabled, onClick }: { label: string; active: boolean; disabled?: boolean; onClick: () => void }) {
  const [focused, setFocused] = useState(false);
  return (
    <Focusable
      noFocusRing
      onClick={() => { if (!disabled) onClick(); }}
      onActivate={() => { if (!disabled) onClick(); }}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      onGamepadFocus={() => setFocused(true)}
      onGamepadBlur={() => setFocused(false)}
      style={{
        width: "100%", maxWidth: "100%", minWidth: 0, height: 40,
        boxSizing: "border-box", display: "flex", alignItems: "center", gap: 8,
        padding: "0 11px", marginBottom: 8, borderRadius: 9, overflow: "hidden",
        background: active ? "rgba(88,101,242,.48)" : focused ? "rgba(88,101,242,.25)" : "rgba(255,255,255,.045)",
        outline: focused ? "2px solid rgba(255,255,255,.85)" : "2px solid transparent",
        outlineOffset: -2, opacity: disabled ? .35 : 1,
      }}
    >
      <span style={{ width: 14, flexShrink: 0, fontSize: 12, opacity: active ? 1 : 0 }}>{active ? "✓" : ""}</span>
      <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", textAlign: "left", fontSize: 12, fontWeight: active ? 700 : 500 }}>
        {label}
      </span>
    </Focusable>
  );
}

function ActionButton({ icon, children, disabled, onClick }: { icon: ReactNode; children: ReactNode; disabled?: boolean; onClick: () => void }) {
  const [focused, setFocused] = useState(false);
  return (
    <Focusable
      noFocusRing
      onClick={() => { if (!disabled) onClick(); }}
      onActivate={() => { if (!disabled) onClick(); }}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      onGamepadFocus={() => setFocused(true)}
      onGamepadBlur={() => setFocused(false)}
      style={{
        width: "100%", maxWidth: "100%", minWidth: 0, minHeight: 48,
        boxSizing: "border-box", display: "flex", alignItems: "center", justifyContent: "flex-start", gap: 11,
        padding: "8px 12px", marginBottom: 8, borderRadius: 10, overflow: "hidden",
        opacity: disabled ? .35 : 1,
        background: focused ? "rgba(88,101,242,.26)" : "rgba(255,255,255,.065)",
        outline: focused ? "2px solid rgba(255,255,255,.9)" : "2px solid transparent",
        outlineOffset: -2,
      }}
    >
      <span style={{ width: 25, flexShrink: 0, display: "flex", justifyContent: "center", fontSize: 19, opacity: .9 }}>{icon}</span>
      <span style={{ flex: 1, minWidth: 0, textAlign: "left", fontSize: 13, fontWeight: 600 }}>{children}</span>
    </Focusable>
  );
}

async function copyToSystemClipboard(text: string): Promise<boolean> {
  // In Steam Gaming Mode the modern Clipboard API can resolve successfully
  // without updating the desktop clipboard. The classic selection command is
  // what clipboard-oriented Decky plugins use reliably inside the QAM CEF view.
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.left = "-10000px";
  field.style.top = "0";
  document.body.appendChild(field);
  field.focus();
  field.select();
  field.setSelectionRange(0, text.length);
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } finally {
    document.body.removeChild(field);
  }
  if (!copied && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
    } catch {
      copied = false;
    }
  }
  return copied;
}

function Content() {
  const [state, setState] = useState<DeviceState>();
  const [busy, setBusy] = useState(false);
  const [diagnostics, setDiagnostics] = useState("");
  const [wakeSummary, setWakeSummary] = useState<{ ok: boolean; applicable?: boolean; mode?: "ethernet" | "wifi"; enabled: boolean; ip?: string; mac?: string; reason?: string; error?: string }>();
  const [clientLog, setClientLog] = useState<string[]>([]);

  const logResponse = (name: string, value: unknown) => {
    const stamp = new Date().toLocaleString();
    setClientLog((entries) => [...entries.slice(-39), `[${stamp}] ${name}\n${JSON.stringify(value, null, 2)}`]);
  };

  const refresh = useCallback(async () => {
    try {
      const next = await getDevices();
      setState(next);
      if (next.wake?.ok) {
        setWakeSummary({
          ok: true,
          applicable: next.wake.applicable,
          mode: next.wake.mode,
          enabled: next.wake.enabled,
          ip: next.wake.ip ?? "не назначен",
          mac: next.wake.mac ?? "неизвестен",
          reason: next.wake.reason,
        });
      } else if (next.wake?.error) {
        setWakeSummary({ ok: false, enabled: false, error: next.wake.error });
      }
      if (!next.ok) toaster.toast({ title: "AV Switcher", body: next.error ?? "Ошибка опроса устройств" });
    } catch (error) {
      toaster.toast({ title: "AV Switcher", body: String(error), critical: true });
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const run = async (action: () => Promise<Result>, success: string) => {
    setBusy(true);
    try {
      const result = await action();
      logResponse(success, result);
      toaster.toast({
        title: result.ok ? success : "Не удалось переключить",
        body: result.ok ? undefined : result.error,
        critical: !result.ok,
      });
      if (!result.restarting) await refresh();
    } catch (error) {
      logResponse(`${success}: исключение`, String(error));
      toaster.toast({ title: "Не удалось переключить", body: String(error), critical: true });
    } finally {
      setBusy(false);
    }
  };

  const checkWakeOnLan = async () => {
    setBusy(true);
    try {
      const result = await ensureWakeOnLan();
      logResponse("Wake-on-LAN", result);
      setWakeSummary(result.ok
        ? { ok: true, applicable: true, mode: result.mode, enabled: true, ip: result.ip ?? "не назначен", mac: result.mac ?? "неизвестен" }
        : { ok: false, enabled: false, error: result.error ?? "Неизвестная ошибка" });
      const details = result.ok ? `IP: ${result.ip ?? "не назначен"}\nMAC: ${result.mac ?? "неизвестен"}` : result.error;
      toaster.toast({
        title: result.ok
          ? (result.changed
            ? `${result.mode === "wifi" ? "Wake-on-WLAN" : "Wake-on-LAN"} был отключён — теперь включён`
            : `${result.mode === "wifi" ? "Wake-on-WLAN" : "Wake-on-LAN"} работает`)
          : "Не удалось проверить Wake-on-LAN",
        body: details,
        critical: !result.ok,
      });
    } catch (error) {
      logResponse("Wake-on-LAN: исключение", String(error));
      setWakeSummary({ ok: false, enabled: false, error: String(error) });
      toaster.toast({ title: "Не удалось проверить Wake-on-LAN", body: String(error), critical: true });
    } finally {
      setBusy(false);
    }
  };

  if (!state) {
    return <PanelSection><PanelSectionRow><Spinner /></PanelSectionRow></PanelSection>;
  }

  return (
    <>
      <PanelSection title="Экран">
        <InlineSelect
          icon={<BsDisplay />}
          title="Активный монитор"
          items={options(state.displays)}
          value={selected(state.displays)}
          disabled={busy || state.displays.length === 0}
          onChange={(value) => void run(() => setDisplay(value), "Экран переключён")}
        />
      </PanelSection>

      <PanelSection title="Сеть">
        <PanelSectionRow>
          <div style={{ width: "100%", minWidth: 0 }}>
            <div style={{ fontSize: "15px", fontWeight: 600 }}>Активное подключение</div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginTop: 5, fontSize: "13px", lineHeight: 1.3 }}>
            {state.network.interface && (
                <span style={{ opacity: .72 }}>{state.network.interface}</span>
            )}
              <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "#3fda7a", fontWeight: 700 }}>{state.network.label}</span>
            </div>
            {wakeSummary && (
              <div style={{ fontSize: "10px", fontWeight: 500, lineHeight: 1.3, marginTop: 4, opacity: .82 }}>
                {wakeSummary.ok && wakeSummary.applicable === false ? (
                  <>
                    <div>Wake-on-LAN: <span style={{ opacity: .75, fontWeight: 700 }}>{wakeSummary.reason ?? "недоступен по Wi‑Fi"}</span></div>
                    <div>IP: {wakeSummary.ip}</div>
                    <div>MAC: {wakeSummary.mac}</div>
                  </>
                ) : wakeSummary.ok ? (
                  <>
                    <div>{wakeSummary.mode === "wifi" ? "Wake-on-WLAN" : "Wake-on-LAN"}: <span style={{ color: wakeSummary.enabled ? "#3fda7a" : "#ffb454", fontWeight: 700 }}>{wakeSummary.enabled ? "включён" : "выключен"}</span></div>
                    <div>IP: {wakeSummary.ip}</div>
                    <div>MAC: {wakeSummary.mac}</div>
                  </>
                ) : (
                  <><div style={{ color: "#ff7474", fontWeight: 700 }}>Wake-on-LAN: ошибка</div><div>{wakeSummary.error}</div></>
                )}
                <div>Сервер: <span style={{ color: state.server?.active ? "#3fda7a" : "#ffb454", fontWeight: 700 }}>{state.server?.active ? "активен" : "не активен"}</span></div>
              </div>
            )}
          </div>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Звук">
        <InlineSelect
          icon={<BsVolumeUp />}
          title="Аудиовыход"
          items={options(state.sinks)}
          value={selected(state.sinks)}
          disabled={busy || state.sinks.length === 0}
          onChange={(value) => void run(() => setAudioDevice("sink", value), "Аудиовыход переключён")}
        />
        {state.sinkProfiles && <InlineSelect
          icon={<BsSliders />}
          title="Профиль / кодек"
          items={state.sinkProfiles.profiles.map((item) => ({ data: item.id, label: item.name }))}
          value={state.sinkProfiles.active}
          disabled={busy}
          onChange={(value) => void run(
            () => setAudioProfile("sink", state.sinkProfiles!.card, value),
            "Профиль аудиовыхода изменён",
          )}
        />}
        <InlineSelect
          icon={<BsMic />}
          title="Микрофон"
          items={options(state.sources)}
          value={selected(state.sources)}
          disabled={busy || state.sources.length === 0}
          onChange={(value) => void run(() => setAudioDevice("source", value), "Микрофон переключён")}
        />
        {state.sourceProfiles && <InlineSelect
          icon={<BsSliders />}
          title="Профиль микрофона"
          items={state.sourceProfiles.profiles.map((item) => ({ data: item.id, label: item.name }))}
          value={state.sourceProfiles.active}
          disabled={busy}
          onChange={(value) => void run(
            () => setAudioProfile("source", state.sourceProfiles!.card, value),
            "Профиль микрофона изменён",
          )}
        />}
      </PanelSection>

      <PanelSection>
        {state.network.type !== "none" && (!wakeSummary?.ok || !wakeSummary.enabled) && (
          <PanelSectionRow>
            <ActionButton icon={<BsPower />} disabled={busy} onClick={() => void checkWakeOnLan()}>
              Включить {state.network.type === "wifi" ? "Wake-on-WLAN" : "Wake-on-LAN"}
            </ActionButton>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ActionButton icon={<BsArrowRepeat />} disabled={busy} onClick={() => void refresh()}>
            Обновить список устройств
          </ActionButton>
        </PanelSectionRow>
        <PanelSectionRow>
          <ActionButton
            icon={<BsBug />}
            onClick={() => {
              if (diagnostics) {
                setDiagnostics("");
                return;
              }
              void (async () => {
              try {
                const backend = await getDiagnostics();
                const complete = `${backend}\n\n=== ОТВЕТЫ ИНТЕРФЕЙСА ===\n${clientLog.join("\n\n") || "Ответов пока нет."}`;
                setDiagnostics(complete);
              } catch (error) {
                const message = `Не удалось получить диагностику: ${String(error)}`;
                logResponse("Диагностика: исключение", String(error));
                setDiagnostics(message);
              }
              })();
            }}
          >
            {diagnostics ? "Скрыть полную диагностику" : "Показать полную диагностику"}
          </ActionButton>
        </PanelSectionRow>
        {diagnostics && (
          <>
            <PanelSectionRow>
              <ActionButton
                icon={<BsClipboard2Check />}
                onClick={() => void (async () => {
                  try {
                    const copied = await copyToSystemClipboard(diagnostics);
                    if (!copied) throw new Error("Steam не подтвердил операцию копирования");
                    toaster.toast({ title: "Скопировано в системный буфер", body: "Текст можно вставить в браузер, чат или текстовый редактор." });
                  } catch (error) {
                    toaster.toast({ title: "Не удалось скопировать", body: String(error), critical: true });
                  }
                })()}
              >
                Копировать в системный буфер
              </ActionButton>
            </PanelSectionRow>
            <PanelSectionRow>
              <div style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: "10px", fontWeight: 400, lineHeight: 1.35, width: "100%" }}>
                {diagnostics}
              </div>
            </PanelSectionRow>
          </>
        )}
      </PanelSection>

    </>
  );
}

export default definePlugin(() => ({
  name: "AV Switcher",
  titleView: <div className={staticClasses.Title}>AV Switcher</div>,
  content: <Content />,
  icon: <FaDisplay />,
}));
