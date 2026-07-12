import { createContext } from "react";

// Kept in its own module so AuthContext.jsx only exports components (react-refresh).
export const AuthContext = createContext(null);
