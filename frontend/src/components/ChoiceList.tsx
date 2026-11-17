import type { Choice } from '../types'

interface Props {
  choices: Choice[]
  disabled?: boolean
  onPick: (choice: Choice) => void
}

export default function ChoiceList({ choices, disabled, onPick }: Props) {
  return (
    <div className="choice-list">
      {choices.map((choice) => (
        <button key={choice.id} className="choice-btn" disabled={disabled}
          onClick={() => onPick(choice)}>
          <span className="letter">{choice.id}</span>
          <span>{choice.text}</span>
        </button>
      ))}
    </div>
  )
}
