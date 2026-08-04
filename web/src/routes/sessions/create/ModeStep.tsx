import { useId } from 'react';
import type { ModeCard } from './modeCatalog';
import type { WizardMode } from './wizardState';

interface ModeStepProps {
  cards: readonly ModeCard[];
  onSelect: (mode: WizardMode) => void;
  onAddCustomType: () => void;
  onDeleteCustomType: (mode: WizardMode) => void;
  onBack: () => void;
}

/**
 * One click both selects the mode and advances — no confirm button, matching
 * both mobile clients.
 */
export function ModeStep({
  cards,
  onSelect,
  onAddCustomType,
  onDeleteCustomType,
  onBack,
}: ModeStepProps) {
  const reasonIdPrefix = useId();

  return (
    <div className="wizard-body">
      <h2>What should it run?</h2>

      <ul className="mode-list">
        {cards.map((card) => {
          const reasonId = `${reasonIdPrefix}-${card.mode}`;
          return (
            <li key={card.mode} className="mode-item">
              {/*
                `aria-disabled` rather than `disabled`: a disabled button cannot
                be focused, so a keyboard or screen-reader user would never hear
                why the mode is unavailable. The click is guarded instead.
              */}
              <button
                type="button"
                className="mode-card"
                aria-disabled={!card.enabled}
                aria-describedby={card.disabledReason !== undefined ? reasonId : undefined}
                onClick={() => {
                  if (card.enabled) onSelect(card.mode);
                }}
              >
                <span className="mode-title">{card.title}</span>
                {card.command !== undefined ? (
                  <span className="mode-command">{card.command}</span>
                ) : (
                  <span className="mode-subtitle">{card.subtitle}</span>
                )}
                {card.disabledReason !== undefined && (
                  <span className="mode-subtitle-warning" id={reasonId}>
                    {card.disabledReason}
                  </span>
                )}
              </button>
              {card.command !== undefined && (
                <button
                  type="button"
                  className="button mode-delete"
                  onClick={() => {
                    onDeleteCustomType(card.mode);
                  }}
                >
                  Delete
                </button>
              )}
            </li>
          );
        })}

        <li className="mode-item">
          <button type="button" className="mode-card" onClick={onAddCustomType}>
            <span className="mode-title">Add custom agent…</span>
            <span className="mode-subtitle">Name + launch command, synced to your account</span>
          </button>
        </li>
      </ul>

      <div className="wizard-actions">
        <button type="button" className="button" onClick={onBack}>
          Back
        </button>
      </div>
    </div>
  );
}
