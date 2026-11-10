import { memo, useEffect, useRef, useState, type CSSProperties, type ToggleEvent } from 'react';
import type { ChatSessionModel } from '../../chat/chatModel';
import { formatCost, formatTokens } from '../../chat/chatFormat';
import type {
  ChatConfigChoice,
  ChatConfigOption,
  ChatSelectOption,
  ChatUsage,
} from '../../chat/chatWire';
import { MenuButton, type MenuGroup } from '../../components/MenuButton';
import { isPrimaryPress } from '../../lib/pointer';
import { BrainIcon, ChevronIcon, ChipIcon, GearIcon, ShieldIcon } from './ChatIcons';
import { toastFailure } from './DockPanels';

const CHIP_CATEGORIES = ['mode', 'model', 'thought_level'] as const;

type Tone = 'info' | 'danger' | 'warning' | 'muted';

/** The mode chip's colour hint, by what the mode id says about autonomy. */
function modeTone(value: string): Tone {
  const id = value.toLowerCase();
  if (id.includes('plan')) return 'info';
  if (id.includes('bypass') || id.includes('yolo')) return 'danger';
  if (id.includes('accept') || id.includes('auto')) return 'warning';
  return 'muted';
}

function selectGroups(
  option: ChatSelectOption,
  current: string,
  disabled: boolean,
  onPick: (value: string) => void,
): MenuGroup[] {
  const groups = new Map<string, ChatConfigChoice[]>();
  for (const choice of option.options) {
    const key = choice.group ?? '';
    const list = groups.get(key);
    if (list === undefined) groups.set(key, [choice]);
    else list.push(choice);
  }
  return [...groups.entries()].map(([group, choices]) => ({
    id: `${option.id}:${group}`,
    label: group === '' ? option.name : group,
    items: choices.map((choice) => ({
      id: choice.value,
      label: choice.name === '' ? choice.value : choice.name,
      description: choice.description ?? undefined,
      checked: choice.value === current,
      disabled,
      onSelect: () => {
        if (choice.value !== current) onPick(choice.value);
      },
    })),
  }));
}

interface ConfigChipsProps {
  model: ChatSessionModel;
  config: readonly ChatConfigOption[];
  overrides: Readonly<Record<string, string | boolean>>;
  live: boolean;
}

export const ConfigChips = memo(function ConfigChips({
  model,
  config,
  overrides,
  live,
}: ConfigChipsProps) {
  const chips: ChatSelectOption[] = [];
  for (const category of CHIP_CATEGORIES) {
    const option = config.find(
      (candidate): candidate is ChatSelectOption =>
        candidate.type === 'select' && candidate.category === category,
    );
    if (option !== undefined) chips.push(option);
  }
  const rest = config.filter((option) => !chips.includes(option as ChatSelectOption));
  if (chips.length === 0 && rest.length === 0) return null;

  const valueOf = (option: ChatConfigOption): string | boolean => overrides[option.id] ?? option.value;
  const set = (option: ChatConfigOption, value: string | boolean) => {
    void model.setConfig(option.id, value).then((result) => {
      toastFailure(result, `Could not change ${option.name === '' ? 'the setting' : option.name}`);
    });
  };

  const restGroups: MenuGroup[] = [];
  const toggles = rest.filter((option) => option.type === 'boolean');
  for (const option of rest) {
    if (option.type !== 'select') continue;
    restGroups.push(
      ...selectGroups(option, String(valueOf(option)), !live, (value) => {
        set(option, value);
      }),
    );
  }
  if (toggles.length > 0) {
    restGroups.push({
      id: 'toggles',
      label: 'Options',
      items: toggles.map((option) => {
        const on = valueOf(option) === true;
        return {
          id: option.id,
          label: option.name === '' ? option.id : option.name,
          description: option.description ?? undefined,
          checked: on,
          toggle: true,
          disabled: !live,
          onSelect: () => {
            set(option, !on);
          },
        };
      }),
    });
  }

  return (
    <div className="chat-chips" role="group" aria-label="Session settings">
      {chips.map((option) => {
        const current = String(valueOf(option));
        const choice = option.options.find((entry) => entry.value === current);
        const shown = choice?.name ?? current;
        const tone = option.category === 'mode' ? modeTone(current) : 'muted';
        return (
          <MenuButton
            key={option.id}
            label={`${option.name === '' ? 'Setting' : option.name}: ${shown}`}
            className={`chat-chip chat-chip-${tone}`}
            menuClassName="chat-menu"
            placement="above"
            groups={selectGroups(option, current, !live, (value) => {
              set(option, value);
            })}
          >
            {option.category === 'mode' && <ShieldIcon />}
            {option.category === 'model' && <ChipIcon />}
            {option.category === 'thought_level' && <BrainIcon />}
            <span className="chat-chip-text">{shown}</span>
            <ChevronIcon />
          </MenuButton>
        );
      })}
      {restGroups.length > 0 && (
        <MenuButton
          label="More settings"
          className="chat-chip chat-chip-muted"
          menuClassName="chat-menu"
          placement="above"
          groups={restGroups}
        >
          <GearIcon />
        </MenuButton>
      )}
    </div>
  );
});

/** Context use in the top bar; hidden until the agent reports it. */
export function UsageIndicator({ usage }: { usage: ChatUsage | null }) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<CSSProperties>({});
  // A press on the trigger light-dismisses the popover before the click lands.
  const openAtPress = useRef(false);
  // The top bar scrolls sideways without a light dismiss (as in MenuButton).
  useEffect(() => {
    if (!open) return;
    const onScroll = (): void => {
      popoverRef.current?.hidePopover();
    };
    document.addEventListener('scroll', onScroll, { capture: true, passive: true });
    return () => {
      document.removeEventListener('scroll', onScroll, { capture: true });
    };
  }, [open]);
  if (usage === null || usage.size <= 0) return null;
  const fraction = Math.min(1, Math.max(0, usage.used / usage.size));
  const percent = Math.round(fraction * 100);
  const tone = fraction >= 0.95 ? 'danger' : fraction >= 0.8 ? 'warning' : 'ok';
  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={`button chat-usage chat-usage-${tone}`}
        aria-label={`Context used: ${String(percent)}%`}
        aria-expanded={open}
        title="Context used"
        onPointerDown={(event) => {
          if (isPrimaryPress(event)) openAtPress.current = open;
        }}
        onPointerCancel={() => {
          openAtPress.current = false;
        }}
        onClick={() => {
          const popover = popoverRef.current;
          const wasOpen = openAtPress.current || open;
          openAtPress.current = false;
          if (popover === null) return;
          if (wasOpen) {
            if (open) popover.hidePopover();
            return;
          }
          const rect = triggerRef.current?.getBoundingClientRect();
          if (rect !== undefined) {
            setPosition({ top: rect.bottom + 4, right: Math.max(0, window.innerWidth - rect.right) });
          }
          popover.showPopover();
        }}
        onKeyDown={() => {
          openAtPress.current = false;
        }}
      >
        <span className="chat-usage-meter" aria-hidden="true">
          <span style={{ width: `${String(percent)}%` }} />
        </span>
        <span className="chat-usage-percent" aria-hidden="true">
          {percent}%
        </span>
      </button>
      <div
        ref={popoverRef}
        popover="auto"
        className="chat-popover"
        style={position}
        onBeforeToggle={(event: ToggleEvent<HTMLDivElement>) => {
          setOpen(event.newState === 'open');
        }}
      >
        <p>
          {formatTokens(usage.used)} of {formatTokens(usage.size)} tokens ({percent}%)
        </p>
        {usage.cost !== null && usage.cost !== undefined && (
          <p className="chat-popover-muted">≈ {formatCost(usage.cost.amount, usage.cost.currency)}</p>
        )}
      </div>
    </>
  );
}
