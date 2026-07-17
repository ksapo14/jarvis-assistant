import { useEffect, useState, type InputHTMLAttributes } from "react";

interface DraftTextInputProps extends Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "defaultValue" | "onBlur" | "onChange" | "onInvalid" | "onKeyDown" | "value"
> {
  value: string;
  onCommit: (value: string) => Promise<void> | void;
  validate?: (value: string) => string | null;
  onValidationError?: (message: string) => void;
}

export function DraftTextInput({
  value,
  onCommit,
  validate,
  onValidationError,
  ...inputProps
}: DraftTextInputProps) {
  const [draft, setDraft] = useState(value);
  const validationError = validate?.(draft) ?? null;

  useEffect(() => setDraft(value), [value]);

  const commit = () => {
    if (draft === value) return;
    if (validationError) {
      onValidationError?.(validationError);
      return;
    }
    void onCommit(draft);
  };

  return (
    <input
      {...inputProps}
      value={draft}
      aria-invalid={validationError ? true : undefined}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          event.currentTarget.blur();
        }
      }}
    />
  );
}
