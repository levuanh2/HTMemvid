const VARIANTS = {
  primary: "btn-primary",   // ink fill (letterpress)
  seal: "btn-seal",         // seal-red stamped action
  secondary: "btn-secondary",
  ghost: "btn-secondary",
  danger: "btn-danger",
};

export default function Button({ variant = "primary", className = "", children, ...rest }) {
  return (
    <button
      className={`${VARIANTS[variant] || VARIANTS.primary} inline-flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}
